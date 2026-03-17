# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from asyncio import sleep

from niquests import ConnectionError, Timeout
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import LogLvl

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool
from ex_app.lib.logger import log


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@dangerous_tool
	async def send_email(subject: str, body: str, account_id: int, from_email: str, to_emails: list[str]):
		"""
		Send an email to a list of email addresses
		:param subject: The subject of the email
		:param body: The body of the email
		:param account_id: The id of the account to send from, obtainable via get_mail_account_list
		:param to_emails: The email addresses to send the message to
		"""
		i = 0
		body_with_ai_note = f"{body}\n\n---\n\nThis email was sent by Nextcloud AI Assistant."
		while i < 20:
			try:
				return await nc.ocs('POST', '/ocs/v2.php/apps/mail/message/send', json={
					'accountId': account_id,
					'fromEmail': from_email,
					'subject': subject,
					'body': body_with_ai_note,
					'isHtml': False,
					'to': [{'label': '', 'email': email} for email in to_emails],
				})
			except (
					ConnectionError,
					Timeout
			) as e:
				await log(nc, LogLvl.DEBUG, "Ignored error during task polling")
				i += 1
				await sleep(1)
				continue
		raise Exception("Failed to send email")

	@tool
	@safe_tool
	async def get_mail_account_list():
		"""
		Lists all available email accounts of the current user including their account id
		:return: list of email accounts with their ids and configuration
		"""

		return await nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')


	@tool
	@safe_tool
	async def list_mail_folders(account_id: int):
		"""
		List all mail folders/mailboxes for an account
		:param account_id: The id of the account (obtainable via get_mail_account_list)
		:return: list of folders with their ids, names, and message counts
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/mail/api/mailboxes", headers={
			"Content-Type": "application/json",
		}, params={'accountId': account_id})
		return response.json()

	@tool
	@safe_tool
	async def get_email_messages(mailbox_id: int, limit: int = 20):
		"""
		Get messages from a specific mailbox
		:param mailbox_id: The id of the mailbox (obtainable via list_mail_folders)
		:param limit: Maximum number of messages to return (default 20)
		:return: list of email messages
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/mail/api/messages", headers={
			"Content-Type": "application/json",
		}, params={'mailboxId': mailbox_id, 'limit': limit})
		return response.json()

	@tool
	@safe_tool
	async def search_emails(mailbox_id: int, search_term: str, limit: int = 20):
		"""
		Search for emails in a mailbox
		:param mailbox_id: The id of the mailbox to search in (obtainable via list_mail_folders)
		:param search_term: The text to search for in emails
		:param limit: Maximum number of results to return (default 20)
		:return: list of matching emails
		"""
		response = await nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/mail/api/messages", headers={
			"Content-Type": "application/json",
		}, params={'mailboxId': mailbox_id, 'filter': search_term, 'limit': limit})
		return response.json()

	@tool
	@dangerous_tool
	async def move_email_to_folder(message_id: int, dest_mailbox_id: int):
		"""
		Move an email to a different folder
		:param message_id: The id of the message to move (obtainable via get_email_messages or search_emails)
		:param dest_mailbox_id: The id of the destination mailbox (obtainable via list_mail_folders)
		:return: confirmation
		"""
		response = await nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/mail/api/messages/{message_id}/move", headers={
			"Content-Type": "application/json",
		}, json={
			'destFolderId': dest_mailbox_id
		})
		return response.json()

	@tool
	@dangerous_tool
	async def delete_email(message_id: int):
		"""
		Delete an email message
		:param message_id: The id of the message to delete (obtainable via get_email_messages or search_emails)
		:return: confirmation
		"""
		response = await nc._session._create_adapter(True).request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/mail/api/messages/{message_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	return [
		send_email,
		get_mail_account_list,
		list_mail_folders,
		search_emails,
		get_email_messages,
		move_email_to_folder,
		delete_email
	]

def get_category_name():
	return "Mail"

async def is_available(nc: AsyncNextcloudApp):
	try: 
		await nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')
	except:
		return False
	return True