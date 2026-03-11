# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_polls():
		"""
		List all polls the current user has access to
		:return: a list of polls with their id, title, and status
		"""
		return await nc.ocs('GET', '/ocs/v2.php/apps/polls/polls')

	@tool
	@safe_tool
	async def get_poll_details(poll_id: int):
		"""
		Get detailed information about a specific poll
		:param poll_id: the id of the poll (obtainable via list_polls)
		:return: complete poll information including options and votes
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/polls/poll/{poll_id}')

	@tool
	@dangerous_tool
	async def create_poll(title: str, description: Optional[str] = None, poll_type: str = 'datePoll'):
		"""
		Create a new poll
		:param title: the title of the poll
		:param description: optional description for the poll
		:param poll_type: type of poll - 'datePoll' for date/time polls or 'textPoll' for text-based polls
		:return: the created poll with its id
		"""
		description_with_ai_note = f"{description or ''}\n\n---\n\nThis poll was created by Nextcloud AI Assistant."

		payload = {
			'title': title,
			'description': description_with_ai_note,
			'type': poll_type
		}
		return await nc.ocs('POST', '/ocs/v2.php/apps/polls/poll', json=payload)

	@tool
	@dangerous_tool
	async def add_poll_option(poll_id: int, option_text: str, timestamp: Optional[int] = None):
		"""
		Add an option to a poll
		:param poll_id: the id of the poll to add the option to (obtainable via list_polls)
		:param option_text: the text of the option
		:param timestamp: for date polls, the unix timestamp of the date/time option
		:return: the created option
		"""
		payload = {
			'pollOptionText': option_text
		}
		if timestamp is not None:
			payload['timestamp'] = timestamp

		return await nc.ocs('POST', f'/ocs/v2.php/apps/polls/option/{poll_id}', json=payload)

	@tool
	@safe_tool
	async def get_poll_votes(poll_id: int):
		"""
		Get all votes for a poll
		:param poll_id: the id of the poll (obtainable via list_polls)
		:return: all votes cast on the poll
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/polls/votes/{poll_id}')

	@tool
	@dangerous_tool
	async def vote_on_poll(poll_id: int, option_id: int, answer: str = 'yes'):
		"""
		Cast a vote on a poll option
		:param poll_id: the id of the poll (obtainable via list_polls)
		:param option_id: the id of the option to vote on (obtainable via get_poll_details)
		:param answer: the vote - 'yes', 'no', or 'maybe' (for polls that allow maybe)
		:return: the recorded vote
		"""
		payload = {
			'optionId': option_id,
			'setTo': answer
		}
		return await nc.ocs('PUT', f'/ocs/v2.php/apps/polls/vote/{poll_id}', json=payload)

	@tool
	@dangerous_tool
	async def delete_poll(poll_id: int):
		"""
		Delete a poll
		:param poll_id: the id of the poll to delete (obtainable via list_polls)
		:return: confirmation of deletion
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/polls/poll/{poll_id}')

	@tool
	@dangerous_tool
	async def close_poll(poll_id: int):
		"""
		Close a poll to prevent further voting
		:param poll_id: the id of the poll to close (obtainable via list_polls)
		:return: the updated poll
		"""
		return await nc.ocs('PUT', f'/ocs/v2.php/apps/polls/poll/{poll_id}/close')

	@tool
	@dangerous_tool
	async def reopen_poll(poll_id: int):
		"""
		Reopen a closed poll to allow voting again
		:param poll_id: the id of the poll to reopen (obtainable via list_polls)
		:return: the updated poll
		"""
		return await nc.ocs('PUT', f'/ocs/v2.php/apps/polls/poll/{poll_id}/reopen')

	return [
		list_polls,
		get_poll_details,
		create_poll,
		add_poll_option,
		get_poll_votes,
		vote_on_poll,
		delete_poll,
		close_poll,
		reopen_poll
	]

def get_category_name():
	return "Polls"

async def is_available(nc: AsyncNextcloudApp):
	return 'polls' in await nc.capabilities
