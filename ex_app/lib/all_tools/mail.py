# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from asyncio import sleep
from html.parser import HTMLParser

from niquests import ConnectionError, Timeout
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import LogLvl

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, Reversibility, impulse, item_count, read_only, reversibility
from ex_app.lib.logger import log

BLOCK_TAGS = {'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'table', 'hr'}
SKIPPED_TAGS = {'style', 'script', 'head', 'title'}


class _HtmlToText(HTMLParser):
	def __init__(self):
		super().__init__()
		self.parts = []
		self.skip_depth = 0

	def handle_starttag(self, tag, attrs):
		if tag in SKIPPED_TAGS:
			self.skip_depth += 1
		elif tag in BLOCK_TAGS:
			self.parts.append('\n')

	def handle_endtag(self, tag):
		if tag in SKIPPED_TAGS:
			self.skip_depth = max(0, self.skip_depth - 1)
		elif tag in BLOCK_TAGS:
			self.parts.append('\n')

	def handle_data(self, data):
		if self.skip_depth == 0:
			self.parts.append(data)


def html_to_text(html: str) -> str:
	parser = _HtmlToText()
	parser.feed(html)
	lines = [' '.join(line.split()) for line in ''.join(parser.parts).splitlines()]
	text = '\n'.join(lines)
	while '\n\n\n' in text:
		text = text.replace('\n\n\n', '\n\n')
	return text.strip()


async def get_tools(nc: AsyncNextcloudApp):
	def recipient_count(to_emails=None):
		return len(to_emails or [])

	@tool
	@impulse(ImpulseRadius.EXTERNAL)
	@reversibility(Reversibility.IRREVERSIBLE)
	@item_count(recipient_count)
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
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def get_mail_account_list():
		"""
		Lists all available email accounts of the current user including their account id
		:return: list of email accounts with their ids and configuration
		"""

		return await nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')


	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def get_mail_folder_list(account_id: int):
		"""
		Lists all mail folders for an email account 
		:param account_id: The id of the account to list as integer, obtainable via get_mail_account_list
		"""
		return await nc.ocs('GET', '/ocs/v2.php/apps/mail/ocs/mailboxes', json={'accountId': account_id})


	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def list_mails(folder_id: int, n_mails: int = 30):
		"""
		Lists all messages in a mailbox folder
		:param folder_id: The id of the folder to list as integer, obtainable via get_mail_folder_list
		:param n_mails: The number of mails to receive. Optional, default is 30
		:return: a list of mails/messages, including timestamps
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/mail/ocs/mailboxes/{folder_id}/messages', json={'limit': n_mails})


	@tool
	@impulse(ImpulseRadius.SELF)
	@read_only
	async def get_mail(message_id: int):
		"""
		Gets a single email message including its full body text
		:param message_id: The id of the message as integer, this is the databaseId obtainable via list_mails
		:return: the message with sender, recipients, subject, date, body and attachment names
		"""
		message = await nc.ocs('GET', f'/ocs/v2.php/apps/mail/message/{message_id}')
		body = message.get('body') or ''
		if message.get('hasHtmlBody'):
			body = html_to_text(body)
		return {
			'id': message.get('id'),
			'subject': message.get('subject'),
			'from': message.get('from'),
			'to': message.get('to'),
			'cc': message.get('cc'),
			'replyTo': message.get('replyTo'),
			'dateInt': message.get('dateInt'),
			'flags': message.get('flags'),
			'body': body,
			'attachments': [
				{'fileName': a.get('fileName'), 'mime': a.get('mime'), 'size': a.get('size')}
				for a in message.get('attachments') or []
			],
		}


	return [
		send_email,
		get_mail_account_list,
		get_mail_folder_list,
		list_mails,
		get_mail,
	]

def get_category_name():
	return "Mail"

async def is_available(nc: AsyncNextcloudApp):
	try: 
		await nc.ocs('GET', '/ocs/v2.php/apps/mail/account/list')
	except:
		return False
	return True