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
		"""
		
		return nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')


	@tool
	@safe_tool
	def get_mail_folder_list(account_id: int):
		"""
		Lists all mail folders for an account. You need to get the correct account id matching the request first before using this tool. 
		:param account_id: The id of the account to list as integer, obtainable via get_mail_account_list
		"""
		
		return nc.ocs('GET', '/ocs/v2.php/apps/mail/mailbox/list', json={'accountId': account_id})


	@tool
	@safe_tool
	def list_mails(folder_id: int, n_mails: int = 30):
		"""
		Lists all messages in a mailbox folder. You need to get the correct folder id matching the request first before using this tool. 
		:param folder_id: The id of the folder to list as integer, obtainable via get_mail_folder_list
		:param n_mails: The number of mails to receive. Optional, default is 30
		:return: a list of mails/messages, including timestamps
		"""
		return nc.ocs('GET', '/ocs/v2.php/apps/mail/mailbox/messages/list', json={'mailboxId': folder_id, 'limit': n_mails})
		

	return [
		send_email,
		get_mail_account_list,
		get_mail_folder_list,
		list_mails,
	]

def get_category_name():
	return "Mail"

def is_available(nc: Nextcloud):
	try: 
		res = nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')
	except:
		return False
	return True