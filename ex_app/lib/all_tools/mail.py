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
				sleep(1)
				continue

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
		

	return [
		send_email,
		get_mail_account_list
	]

def get_category_name():
	return "Mail"

async def is_available(nc: AsyncNextcloudApp):
	try: 
		res = await nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')
	except:
		return False
	return True