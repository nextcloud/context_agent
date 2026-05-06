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
	async def get_mail_folder_list(account_id: int):
		"""
		Lists all mail folders for an account. You need to get the correct account id matching the request first before using this tool. 
		:param account_id: The id of the account to list as integer, obtainable via get_mail_account_list
		"""
		return await nc.ocs('GET', '/ocs/v2.php/apps/mail/ocs/mailboxes', json={'accountId': account_id})


	@tool
	@safe_tool
	async def list_mails(folder_id: int, n_mails: int = 30):
		"""
		Lists all messages in a mailbox folder. You need to get the correct folder id matching the request first before using this tool. 
		:param folder_id: The id of the folder to list as integer, obtainable via get_mail_folder_list
		:param n_mails: The number of mails to receive. Optional, default is 30
		:return: a list of mails/messages, including timestamps
		"""
		print(await nc.ocs('GET', f'/ocs/v2.php/apps/mail/ocs/mailboxes/{folder_id}/messages', json={'limit': n_mails}))
		return await nc.ocs('GET', f'/ocs/v2.php/apps/mail/ocs/mailboxes/{folder_id}/messages', json={'limit': n_mails})
		

	return [
		send_email,
		get_mail_account_list,
		get_mail_folder_list,
		list_mails,
	]

def get_category_name():
	return "Mail"

async def is_available(nc: AsyncNextcloudApp):
	try: 
		await nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')
	except:
		return False
	return True