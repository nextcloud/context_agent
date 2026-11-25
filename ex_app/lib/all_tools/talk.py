# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from datetime import datetime
from typing import Optional

import pytz
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.talk import ConversationType

from ex_app.lib.all_tools.lib.files import count_shared_tree
from ex_app.lib.all_tools.lib.impulse import (
	ImpulseRadius,
	Reversibility,
	destructive,
	impulse,
	item_count,
	read_only,
	reversibility,
)
from ex_app.lib.all_tools.lib.tz import user_timezone_async

TYPE_NAMES = {
	ConversationType.ONE_TO_ONE: 'one-to-one',
	ConversationType.GROUP: 'group',
	ConversationType.PUBLIC: 'public',
	ConversationType.CHANGELOG: 'changelog',
	ConversationType.FORMER: 'former one-to-one',
	ConversationType.NOTE_TO_SELF: 'note-to-self',
}

# Mentions of a group or team: whether they include the user would cost a membership request per message.
GROUP_MENTION_TYPES = ('user-group', 'group', 'team', 'circle')
# People and rooms are named in the text already; objects lists only things other tools act on, like files and polls.
NOT_OBJECTS = ('user', 'guest', 'email', 'call', *GROUP_MENTION_TYPES)


def type_name(conversation_type) -> str:
	return TYPE_NAMES.get(conversation_type, 'group')


def _parameters(message_parameters) -> dict:
	# Talk sends an empty JSON list instead of an object when a message has no parameters.
	return message_parameters if isinstance(message_parameters, dict) else {}


def resolve_mentions(message: str, message_parameters) -> str:
	"""Replace each {placeholder} with what the user sees: @name for mentions, the name for files and other objects."""
	for key, param in _parameters(message_parameters).items():
		if key.startswith('mention-'):
			name = '@all' if param.get('type') == 'call' else f"@{param.get('name', param.get('id', ''))}"
		else:
			name = param.get('name', key)
		message = message.replace(f'{{{key}}}', name)
	return message


def mentions_user(message_parameters, user_id: str):
	"""True if the message mentions the user or @all, 'group' if it mentions a group or team, else False."""
	group = False
	for key, param in _parameters(message_parameters).items():
		if not key.startswith('mention-'):
			continue
		kind = param.get('type')
		# A federated user may share the local id; 'server' marks them as someone else.
		if kind == 'call' or (kind == 'user' and param.get('id') == user_id and not param.get('server')):
			return True
		if kind in GROUP_MENTION_TYPES:
			group = True
	return 'group' if group else False


def shared_objects(message_parameters) -> list:
	"""Files, polls, deck cards and other objects in a message, with the ids other tools need."""
	return [
		{'type': param.get('type'), 'id': param.get('id'), 'name': param.get('name')}
		for key, param in _parameters(message_parameters).items()
		if not key.startswith('mention-') and param.get('type') not in NOT_OBJECTS
	]


def is_user(actor_type, actor_id, user_id: str) -> bool:
	return actor_type == 'users' and actor_id == user_id


async def get_tools(nc: AsyncNextcloudApp):

	async def conversation_radius(conversation_name=None):
		"""How far a conversation reaches: only the user, one person, its participants, or anyone with the link."""
		if conversation_name is None:
			return ImpulseRadius.EXTERNAL
		conversations = await nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}.get(conversation_name)
		if conversation is None:
			# The name does not resolve; the tool will fail on it, until then assume the worst.
			return ImpulseRadius.EXTERNAL
		if conversation.conversation_type == ConversationType.PUBLIC:
			# Anyone holding the link can read along, including guests without an account.
			return ImpulseRadius.EXTERNAL
		if conversation.conversation_type == ConversationType.NOTE_TO_SELF:
			# The user is its only participant, nobody else reads along.
			return ImpulseRadius.SELF
		if conversation.conversation_type in (ConversationType.ONE_TO_ONE, ConversationType.FORMER):
			return ImpulseRadius.INDIVIDUALS
		return ImpulseRadius.GROUP

	async def _get_token(conversation_name: str) -> str:
		conversations = await nc.talk.get_user_conversations()
		conv_map = {conv.display_name: conv for conv in conversations}
		return conv_map[conversation_name].token

	# --- Conversations & Messages (enhanced existing tools) ---

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_talk_conversations():
		"""
		List all conversations of the current user in the Nextcloud Talk app.
		Returns conversation names and tokens. The token is needed for other Talk tools.
		:return: list of conversations with name, token, type (one-to-one, group, public, note-to-self, changelog, former one-to-one), unread message count, and last activity (user's timezone)
		"""
		conversations = await nc.talk.get_user_conversations()
		tz_name = await user_timezone_async(nc)
		try:
			tz = pytz.timezone(tz_name) if tz_name else pytz.utc
		except pytz.UnknownTimeZoneError:
			tz = pytz.utc
		return json.dumps([{
			'name': conv.display_name,
			'token': conv.token,
			'type': type_name(conv.conversation_type),
			'unread_messages': conv.unread_messages_count,
			'last_activity': datetime.fromtimestamp(conv.last_activity, tz).isoformat(timespec='minutes'),
		} for conv in conversations])

	@tool
	@impulse(ImpulseRadius.EXTERNAL)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def create_public_conversation(conversation_name: str) -> str:
		"""
		Create a new public conversation in the Nextcloud Talk app
		:param conversation_name: The name of the conversation to create
		:return: The URL of the new conversation
		"""
		conversation = await nc.talk.create_conversation(ConversationType.PUBLIC, room_name=conversation_name)
		return f"{nc.app_cfg.endpoint}/index.php/call/{conversation.token}"

	@tool
	@impulse(conversation_radius)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def send_message_to_conversation(conversation_name: str, message: str):
		"""
		Send a message to a conversation in the Nextcloud Talk app
		:param message: The message to send
		:param conversation_name: The name of the conversation to send a message to (obtainable via list_talk_conversations)
		:return: success confirmation
		"""
		conversations = await nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}[conversation_name]
		message_with_ai_note = f"{message}\n\nThis message was sent by Nextcloud AI Assistant."
		await nc.talk.send_message(message_with_ai_note, conversation)
		return "Message sent successfully."

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_messages_in_conversation(conversation_name: str, n_messages: int = 30):
		"""
		List messages of a conversation in the Nextcloud Talk app.
		In a group or public conversation, "you" in a message means the user only when mentions_you or reply_to_you is true.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param n_messages: The number of messages to receive
		:return: the conversation name, its type, the user's id (you_are), and messages, each with:
			id (needed for reactions and replies);
			timestamp;
			actor and actor_id of the author;
			by_you: true if the user wrote it;
			message: text with mentions shown as @name;
			mentions_you: true if it mentions the user or @all, "group" if it mentions a group or team, else false;
			reply_to: display name of the author of the message this one replies to, or null;
			reply_to_you: true if it replies to a message of the user;
			objects: files, polls and other shared objects with their ids, if any;
			is_replyable and reactions
		"""
		conversations = await nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}[conversation_name]
		messages = await nc.talk.receive_messages(conversation, False, n_messages)
		user_id = await nc.user

		def to_dict(m):
			parent = m.parent if isinstance(m.parent, dict) else {}
			entry = {
				'id': m.message_id,
				'timestamp': m.timestamp,
				'actor': m.actor_display_name,
				'actor_id': m.actor_id,
				'by_you': is_user(m.actor_type, m.actor_id, user_id),
				'message': resolve_mentions(m.message, m.message_parameters),
				'mentions_you': mentions_user(m.message_parameters, user_id),
				'reply_to': parent.get('actorDisplayName'),
				'reply_to_you': is_user(parent.get('actorType'), parent.get('actorId'), user_id),
				'is_replyable': m.is_replyable,
				'reactions': m.reactions,
			}
			objects = shared_objects(m.message_parameters)
			if objects:
				entry['objects'] = objects
			return entry

		return json.dumps({
			'conversation': conversation.display_name,
			'type': type_name(conversation.conversation_type),
			'you_are': user_id,
			'messages': [to_dict(m) for m in messages],
		})

	# --- Reactions ---

	@tool
	@impulse(conversation_radius)
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def add_reaction(conversation_name: str, message_id: int, reaction: str):
		"""
		Add an emoji reaction to a message in a Talk conversation
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param message_id: The id of the message to react to (obtainable via list_messages_in_conversation)
		:param reaction: The reaction emoji (e.g. "\U0001f44d", "❤️", "\U0001f389")
		:return: all reactions on the message grouped by emoji
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/reaction/{token}/{message_id}', json={
			'reaction': reaction,
		}))

	@tool
	@impulse(conversation_radius)
	@destructive
	@reversibility(Reversibility.REVERSIBLE)
	@item_count(1)
	async def remove_reaction(conversation_name: str, message_id: int, reaction: str):
		"""
		Remove an emoji reaction from a message in a Talk conversation.
		Only your own reactions can be removed.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param message_id: The id of the message (obtainable via list_messages_in_conversation)
		:param reaction: The reaction emoji to remove
		:return: remaining reactions on the message grouped by emoji
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('DELETE', f'/ocs/v2.php/apps/spreed/api/v1/reaction/{token}/{message_id}', json={
			'reaction': reaction,
		}))

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_reactions(conversation_name: str, message_id: int, reaction: Optional[str] = None):
		"""
		List all reactions on a message in a Talk conversation
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param message_id: The id of the message (obtainable via list_messages_in_conversation)
		:param reaction: Optional emoji to filter for a specific reaction
		:return: reactions grouped by emoji, each containing a list of actors who reacted
		"""
		token = await _get_token(conversation_name)
		params = {}
		if reaction is not None:
			params['reaction'] = reaction
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/reaction/{token}/{message_id}', params=params))

	# --- Reply to message ---

	@tool
	@impulse(conversation_radius)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def reply_to_message(conversation_name: str, message_id: int, message: str, silent: bool = False):
		"""
		Send a message as a reply to another message in a Talk conversation.
		The reply will be visually linked to the original message.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param message_id: The id of the message to reply to (must have is_replyable=true, obtainable via list_messages_in_conversation)
		:param message: The reply text
		:param silent: If true, no chat notifications will be sent (default false)
		:return: the sent message with its id and parent reference
		"""
		token = await _get_token(conversation_name)
		message_with_ai_note = f"{message}\n\nThis message was sent by Nextcloud AI Assistant."
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/chat/{token}', json={
			'message': message_with_ai_note,
			'replyTo': message_id,
			'silent': silent,
		}))

	# --- Polls ---

	@tool
	@impulse(conversation_radius)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def create_poll(conversation_name: str, question: str, options: list[str], result_mode: int = 0, max_votes: int = 0):
		"""
		Create a poll in a Talk conversation
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param question: The poll question
		:param options: List of voting options (e.g. ["Yes", "No", "Maybe"])
		:param result_mode: 0 = public (results visible immediately), 1 = hidden (results shown only after closing). Default 0.
		:param max_votes: Maximum options a participant can vote for (0 = unlimited). Default 0.
		:return: the created poll with its id, question, options, and status
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}', json={
			'question': question,
			'options': options,
			'resultMode': result_mode,
			'maxVotes': max_votes,
		}))

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def get_poll(conversation_name: str, poll_id: int):
		"""
		Get the current state and results of a poll
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param poll_id: The id of the poll (obtainable from create_poll or from message parameters in list_messages_in_conversation)
		:return: poll data including question, options (0-based, e.g. [0] refers to the first option, [2] refers to the third option, etc.), votes, status, and who voted
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}/{poll_id}'))

	@tool
	@impulse(conversation_radius)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def vote_on_poll(conversation_name: str, poll_id: int, option_ids: list[int]):
		"""
		Vote on a poll in a Talk conversation.
		Voting replaces any previous votes by the current user.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param poll_id: The id of the poll
		:param option_ids: List of option indices to vote for (0-based, e.g. [0] to vote for the first option, [0, 2] to vote for first and third)
		:return: updated poll data with vote counts and own votes
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}/{poll_id}', json={
			'optionIds': option_ids,
		}))

	@tool
	@impulse(conversation_radius)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(1)
	async def close_poll(conversation_name: str, poll_id: int):
		"""
		Close a poll so no more votes can be cast. Only the poll creator or a moderator can close a poll.
		Once closed, full results become visible to all participants.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param poll_id: The id of the poll to close
		:return: final poll data with complete vote counts and details
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('DELETE', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}/{poll_id}'))

	# --- File sharing ---

	async def shared_tree_count(file_path=None, count_limit=None):
		"""Sharing a folder exposes everything in it."""
		return await count_shared_tree(nc, file_path, count_limit)

	@tool
	@impulse(conversation_radius)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(shared_tree_count)
	async def share_file_to_conversation(conversation_name: str, file_path: str, caption: Optional[str] = None):
		"""
		Share a file from Nextcloud Files into a Talk conversation.
		The file will appear as a rich message in the chat.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param file_path: Path to the file in the user's Nextcloud files (e.g. "/Documents/report.pdf")
		:param caption: Optional caption text to display with the shared file
		:return: the created share
		"""
		token = await _get_token(conversation_name)
		payload = {
			'shareType': 10,
			'shareWith': token,
			'path': file_path,
		}
		if caption is not None:
			caption_with_ai_note = f"{caption}\n\nShared by Nextcloud AI Assistant."
			payload['talkMetaData'] = json.dumps({'caption': caption_with_ai_note})
		return json.dumps(await nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json=payload))

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_shared_items(conversation_name: str, object_type: str, limit: int = 100):
		"""
		List items of a specific type that have been shared in a Talk conversation.
		For a grouped overview across all types, use list_shared_items_overview instead.
		Note: polls have their own dedicated tools (get_poll, vote_on_poll); for polls the
		dedicated tools are usually more direct than listing them through this one.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param object_type: The kind of shared item to list. One of "file", "media",
			"audio", "voice", "poll", "location", "deckcard", "recording", "o-talk", "other".
		:param limit: Maximum number of results (default 100, max 200)
		:return: list of chat messages containing shared items of the requested type with their metadata
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/chat/{token}/share', params={
			'objectType': object_type,
			'limit': limit,
		}))

	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_shared_items_overview(conversation_name: str, limit: int = 7):
		"""
		Get an overview of all types of shared items in a Talk conversation (files, media, polls, etc.)
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param limit: Maximum items per category (default 7)
		:return: shared items grouped by type (audio, file, media, poll, etc.)
		"""
		token = await _get_token(conversation_name)
		return json.dumps(await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/chat/{token}/share/overview', params={
			'limit': limit,
		}))

	return [
		list_talk_conversations,
		create_public_conversation,
		send_message_to_conversation,
		list_messages_in_conversation,
		add_reaction,
		remove_reaction,
		list_reactions,
		reply_to_message,
		create_poll,
		get_poll,
		vote_on_poll,
		close_poll,
		share_file_to_conversation,
		list_shared_items,
		list_shared_items_overview,
	]

def get_category_name():
	return "Talk"

async def is_available(nc: AsyncNextcloudApp):
	return 'spreed' in await nc.capabilities
