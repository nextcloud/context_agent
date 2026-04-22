# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.talk import ConversationType

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):

	async def _get_token(conversation_name: str) -> str:
		conversations = await nc.talk.get_user_conversations()
		conv_map = {conv.display_name: conv for conv in conversations}
		return conv_map[conversation_name].token

	# --- Conversations & Messages (enhanced existing tools) ---

	@tool
	@safe_tool
	async def list_talk_conversations():
		"""
		List all conversations of the current user in the Nextcloud Talk app.
		Returns conversation names and tokens. The token is needed for other Talk tools.
		:return: list of conversations with name, token, type, and unread message count
		"""
		conversations = await nc.talk.get_user_conversations()
		return json.dumps([{
			'name': conv.display_name,
			'token': conv.token,
			'type': conv.conversation_type,
			'unread_messages': conv.unread_messages_count,
		} for conv in conversations])

	@tool
	@dangerous_tool
	async def create_public_conversation(conversation_name: str) -> str:
		"""
		Create a new public conversation in the Nextcloud Talk app
		:param conversation_name: The name of the conversation to create
		:return: The URL of the new conversation
		"""
		conversation = await nc.talk.create_conversation(ConversationType.PUBLIC, room_name=conversation_name)
		return f"{nc.app_cfg.endpoint}/index.php/call/{conversation.token}"

	@tool
	@dangerous_tool
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
		return True

	@tool
	@safe_tool
	async def list_messages_in_conversation(conversation_name: str, n_messages: int = 30):
		"""
		List messages of a conversation in the Nextcloud Talk app.
		Each message includes its id (needed for reactions and replies) and whether it can be replied to.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param n_messages: The number of messages to receive
		:return: list of messages with id, timestamp, actor, message text, and reply status
		"""
		conversations = await nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}[conversation_name]
		messages = await nc.talk.receive_messages(conversation, False, n_messages)
		return json.dumps([{
			'id': m.message_id,
			'timestamp': m.timestamp,
			'actor': m.actor_display_name,
			'message': m.message,
			'is_replyable': m.is_replyable,
			'reactions': m.reactions,
		} for m in messages])

	# --- Reactions ---

	@tool
	@dangerous_tool
	async def add_reaction(conversation_name: str, message_id: int, reaction: str):
		"""
		Add an emoji reaction to a message in a Talk conversation
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param message_id: The id of the message to react to (obtainable via list_messages_in_conversation)
		:param reaction: The reaction emoji (e.g. "\U0001f44d", "❤️", "\U0001f389")
		:return: all reactions on the message grouped by emoji
		"""
		token = await _get_token(conversation_name)
		return await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/reaction/{token}/{message_id}', json={
			'reaction': reaction,
		})

	@tool
	@dangerous_tool
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
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/spreed/api/v1/reaction/{token}/{message_id}', json={
			'reaction': reaction,
		})

	@tool
	@safe_tool
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
		return await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/reaction/{token}/{message_id}', params=params)

	# --- Reply to message ---

	@tool
	@dangerous_tool
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
		return await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/chat/{token}', json={
			'message': message_with_ai_note,
			'replyTo': message_id,
			'silent': silent,
		})

	# --- Polls ---

	@tool
	@dangerous_tool
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
		return await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}', json={
			'question': question,
			'options': options,
			'resultMode': result_mode,
			'maxVotes': max_votes,
		})

	@tool
	@safe_tool
	async def get_poll(conversation_name: str, poll_id: int):
		"""
		Get the current state and results of a poll
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param poll_id: The id of the poll (obtainable from create_poll or from message parameters in list_messages_in_conversation)
		:return: poll data including question, options (0-based, e.g. [0] refers to the first option, [2] refers to the third option, etc.), votes, status, and who voted
		"""
		token = await _get_token(conversation_name)
		return await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}/{poll_id}')

	@tool
	@dangerous_tool
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
		return await nc.ocs('POST', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}/{poll_id}', json={
			'optionIds': option_ids,
		})

	@tool
	@dangerous_tool
	async def close_poll(conversation_name: str, poll_id: int):
		"""
		Close a poll so no more votes can be cast. Only the poll creator or a moderator can close a poll.
		Once closed, full results become visible to all participants.
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param poll_id: The id of the poll to close
		:return: final poll data with complete vote counts and details
		"""
		token = await _get_token(conversation_name)
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/spreed/api/v1/poll/{token}/{poll_id}')

	# --- File sharing ---

	@tool
	@dangerous_tool
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
		return await nc.ocs('POST', '/ocs/v2.php/apps/files_sharing/api/v1/shares', json=payload)

	@tool
	@safe_tool
	async def list_shared_files(conversation_name: str, limit: int = 100):
		"""
		List files that have been shared in a Talk conversation
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param limit: Maximum number of results (default 100, max 200)
		:return: list of chat messages containing shared files with file metadata (name, size, mimetype, link)
		"""
		token = await _get_token(conversation_name)
		return await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/chat/{token}/share', params={
			'objectType': 'file',
			'limit': limit,
		})

	@tool
	@safe_tool
	async def list_shared_items_overview(conversation_name: str, limit: int = 7):
		"""
		Get an overview of all types of shared items in a Talk conversation (files, media, polls, etc.)
		:param conversation_name: The name of the conversation (obtainable via list_talk_conversations)
		:param limit: Maximum items per category (default 7)
		:return: shared items grouped by type (audio, file, media, poll, etc.)
		"""
		token = await _get_token(conversation_name)
		return await nc.ocs('GET', f'/ocs/v2.php/apps/spreed/api/v1/chat/{token}/share/overview', params={
			'limit': limit,
		})

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
		list_shared_files,
		list_shared_items_overview,
	]

def get_category_name():
	return "Talk"

async def is_available(nc: AsyncNextcloudApp):
	return 'spreed' in await nc.capabilities
