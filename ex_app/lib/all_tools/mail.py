# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from time import sleep

import httpx
from langchain_core.tools import tool
from nc_py_api import Nextcloud
from nc_py_api.ex_app import LogLvl

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool
from ex_app.lib.logger import log


def get_tools(nc: Nextcloud):
	@tool
	@dangerous_tool
	def send_email(subject: str, body: str, account_id: int, from_email: str, to_emails: list[str]):
		"""
		Send an email to a list of emails
		:param subject: The subject of the email
		:param body: The body of the email
		:param account_id: The id of the account to send from, obtainable via get_mail_account_list
		:param to_emails: The emails to send
		"""
		i = 0
		while i < 20:
			try:
				return nc.ocs('POST', '/ocs/v2.php/apps/mail/message/send', json={
					'accountId': account_id,
					'fromEmail': from_email,
					'subject': subject,
					'body': body,
					'isHtml': False,
					'to': [{'label': '', 'email': email} for email in to_emails],
				})
			except (
					httpx.RemoteProtocolError,
					httpx.ReadError,
					httpx.LocalProtocolError,
					httpx.PoolTimeout,
			) as e:
				log(nc, LogLvl.DEBUG, "Ignored error during task polling")
				i += 1
				sleep(1)
				continue

	@tool
	@safe_tool
	def get_mail_account_list():
		"""
		Lists all available email accounts including their account id
		:param subject: The subject of the email
		:param body: The body of the email
		:param account_id: The id of the account to send from
		:param to_emails: The emails to send
		"""
		
		return nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')
		

	return [
		send_email,
		get_mail_account_list
	]