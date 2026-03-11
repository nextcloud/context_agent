# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from asyncio import sleep
from typing import Optional

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
		:param subject: The subject of the email
		:param body: The body of the email
		:param account_id: The id of the account to send from
		:param to_emails: The emails to send
		"""

		return await nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')
		

	@tool
	@safe_tool
	async def list_mail_folders(account_id: int):
		"""
		List all mail folders/mailboxes for an account
		:param account_id: The id of the account (obtainable via get_mail_account_list)
		:return: list of folders with their names and message counts
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/mail/account/{account_id}/mailboxes')

	@tool
	@safe_tool
	async def search_emails(account_id: int, search_term: str, mailbox_name: Optional[str] = None, limit: int = 20):
		"""
		Search for emails in an account
		:param account_id: The id of the account (obtainable via get_mail_account_list)
		:param search_term: The text to search for in emails
		:param mailbox_name: Optional mailbox/folder to search in (e.g., "INBOX", "Sent")
		:param limit: Maximum number of results to return (default 20)
		:return: list of matching emails
		"""
		params = {
			'searchQuery': search_term,
			'limit': limit
		}
		if mailbox_name:
			params['mailboxName'] = mailbox_name

		return await nc.ocs('GET', f'/ocs/v2.php/apps/mail/account/{account_id}/messages', params=params)

	@tool
	@safe_tool
	async def get_email_messages(account_id: int, mailbox_name: str = 'INBOX', limit: int = 20):
		"""
		Get messages from a specific mailbox
		:param account_id: The id of the account (obtainable via get_mail_account_list)
		:param mailbox_name: The mailbox/folder name (default "INBOX")
		:param limit: Maximum number of messages to return (default 20)
		:return: list of email messages
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/mail/account/{account_id}/mailboxes/{mailbox_name}/messages', params={'limit': limit})

	@tool
	@dangerous_tool
	async def move_email_to_folder(account_id: int, message_id: int, target_mailbox: str):
		"""
		Move an email to a different folder
		:param account_id: The id of the account (obtainable via get_mail_account_list)
		:param message_id: The id of the message to move
		:param target_mailbox: The name of the destination folder (obtainable via list_mail_folders)
		:return: confirmation
		"""
		return await nc.ocs('POST', f'/ocs/v2.php/apps/mail/account/{account_id}/message/{message_id}/move', json={
			'mailboxName': target_mailbox
		})

	@tool
	@dangerous_tool
	async def delete_email(account_id: int, message_id: int):
		"""
		Delete an email message
		:param account_id: The id of the account (obtainable via get_mail_account_list)
		:param message_id: The id of the message to delete
		:return: confirmation
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/mail/account/{account_id}/message/{message_id}')

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