# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import Nextcloud
from nc_py_api.talk import ConversationType

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


def get_tools(nc: Nextcloud):
	@tool
	@safe_tool
	def list_talk_conversations():
		"""
		List all conversations in talk
		:return: returns a list of conversation names, e.g. ["Conversation 1", "Conversation 2"]
		"""
		conversations = nc.talk.get_user_conversations()

		return [conv.display_name for conv in conversations]

	@tool
	@dangerous_tool
	def create_public_conversation(conversation_name: str) -> str:
		"""
		Create a new talk conversation
		:param conversation_name: The name of the conversation to create
		:return: The URL of the new conversation
		"""
		conversation = nc.talk.create_conversation(ConversationType.PUBLIC, room_name=conversation_name)

		return f"{nc.app_cfg.endpoint}/index.php/call/{conversation.token}"


	@tool
	@dangerous_tool
	def send_message_to_conversation(conversation_name: str, message: str):
		"""
		List all conversations in talk
		:param message: The message to send
		:param conversation_name: The name of the conversation to send a message to
		:return:
		"""
		conversations = nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}[conversation_name]
		nc.talk.send_message(message, conversation)

		return True

	@tool
	@safe_tool
	def list_messages_in_conversation(conversation_name: str, n_messages: int = 30):
		"""
		List messages of a conversation in talk
		:param conversation_name: The name of the conversation to list messages of
		:param n_messages: The number of messages to receive
		:return: A list of messages
		"""
		conversations = nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}[conversation_name]
		return [f"{m.timestamp} {m.actor_display_name}: {m.message}" for m in nc.talk.receive_messages(conversation, False, n_messages)]

	return [
		list_talk_conversations,
		list_messages_in_conversation,
		send_message_to_conversation,
		create_public_conversation,
	]