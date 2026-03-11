# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_forms():
		"""
		List all forms created by the current user
		:return: a list of forms with their id, title, and state
		"""
		return await nc.ocs('GET', '/ocs/v2.php/apps/forms/api/v2.4/forms')

	@tool
	@safe_tool
	async def get_form_details(form_id: int):
		"""
		Get detailed information about a specific form including questions
		:param form_id: the id of the form (obtainable via list_forms)
		:return: complete form structure with all questions and settings
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/forms/api/v2.4/forms/{form_id}')

	@tool
	@dangerous_tool
	async def create_form(title: str, description: Optional[str] = None):
		"""
		Create a new form
		:param title: the title of the form
		:param description: optional description for the form
		:return: the created form with its id
		"""
		description_with_ai_note = f"{description or ''}\n\n---\n\nThis form was created by Nextcloud AI Assistant."

		payload = {
			'title': title,
			'description': description_with_ai_note
		}
		return await nc.ocs('POST', '/ocs/v2.php/apps/forms/api/v2.4/form', json=payload)

	@tool
	@dangerous_tool
	async def add_question_to_form(form_id: int, question_text: str, question_type: str, is_required: bool = False, options: Optional[list[str]] = None):
		"""
		Add a question to an existing form
		:param form_id: the id of the form to add the question to (obtainable via list_forms)
		:param question_text: the text of the question
		:param question_type: type of question - one of: 'multiple', 'multiple_unique', 'dropdown', 'short', 'long', 'date', 'datetime', 'time'
		:param is_required: whether the question is required
		:param options: list of options for multiple choice, dropdown, etc. (required for multiple/dropdown types)
		:return: the created question
		"""
		payload = {
			'type': question_type,
			'text': question_text,
			'isRequired': is_required
		}

		question = await nc.ocs('POST', f'/ocs/v2.php/apps/forms/api/v2.4/form/{form_id}/question', json=payload)

		# Add options if provided and question type supports them
		if options and question_type in ['multiple', 'multiple_unique', 'dropdown']:
			question_id = question.get('id')
			for option_text in options:
				await nc.ocs('POST', f'/ocs/v2.php/apps/forms/api/v2.4/question/{question_id}/option', json={'text': option_text})

		return question

	@tool
	@safe_tool
	async def get_form_responses(form_id: int):
		"""
		Get all responses/submissions for a form
		:param form_id: the id of the form (obtainable via list_forms)
		:return: all responses with answers
		"""
		return await nc.ocs('GET', f'/ocs/v2.php/apps/forms/api/v2.4/submissions/{form_id}')

	@tool
	@dangerous_tool
	async def delete_form(form_id: int):
		"""
		Delete a form
		:param form_id: the id of the form to delete (obtainable via list_forms)
		:return: confirmation of deletion
		"""
		return await nc.ocs('DELETE', f'/ocs/v2.php/apps/forms/api/v2.4/form/{form_id}')

	@tool
	@dangerous_tool
	async def update_form_settings(form_id: int, is_anonymous: Optional[bool] = None, submit_multiple: Optional[bool] = None, show_expiration: Optional[bool] = None, expires: Optional[int] = None):
		"""
		Update form settings
		:param form_id: the id of the form to update (obtainable via list_forms)
		:param is_anonymous: whether responses should be anonymous
		:param submit_multiple: whether users can submit multiple times
		:param show_expiration: whether to show when the form expires
		:param expires: expiration timestamp (unix time)
		:return: the updated form
		"""
		payload = {}
		if is_anonymous is not None:
			payload['isAnonymous'] = is_anonymous
		if submit_multiple is not None:
			payload['submitMultiple'] = submit_multiple
		if show_expiration is not None:
			payload['showExpiration'] = show_expiration
		if expires is not None:
			payload['expires'] = expires

		return await nc.ocs('PATCH', f'/ocs/v2.php/apps/forms/api/v2.4/form/update/{form_id}', json=payload)

	return [
		list_forms,
		get_form_details,
		create_form,
		add_question_to_form,
		get_form_responses,
		delete_form,
		update_form_settings
	]

def get_category_name():
	return "Forms"

async def is_available(nc: AsyncNextcloudApp):
	return 'forms' in await nc.capabilities
