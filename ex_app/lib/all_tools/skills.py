# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
from urllib.parse import quote

from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api._exceptions import NextcloudExceptionNotFound
from packaging.version import Version

from ex_app.lib.all_tools.lib.decorator import dangerous_tool, safe_tool, timed_memoize
from ex_app.lib.logger import log

# Skills follow the agentskills.io spec: each skill is a folder under
# "<Assistant folder>/Context Agent/Skills/<skillName>/" with a SKILL.md file
# containing YAML frontmatter (name, description) plus a markdown body.
# The Assistant app exposes OCS endpoints to list/load/store skills.

MINIMUM_ASSISTANT_VERSION = '3.5.0'
LIST_STORE_SKILLS_URL = '/ocs/v2.php/apps/assistant/api/v1/skills'
LOAD_SKILL_URL = '/ocs/v2.php/apps/assistant/api/v1/skills/{skillName}'
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_CONTENT_LENGTH = 50_000

# skills are cached in the Assistant side for 24 hours backed by etags
# this cache interval is only a delay in cases where the skill is manually
# updated or added in the filesystem
SKILLS_CACHE_TTL = 5 * 60


class AgentFacingError(Exception):
	"""This exception's message would be returned to the agent to understand its mistake"""
	...


def __validate_skill_name(skill_name: str) -> str:
	if not skill_name or not skill_name.strip():
		raise AgentFacingError('Skill name cannot be empty')
	skill_name = skill_name.strip()

	if len(skill_name) > MAX_SKILL_NAME_LENGTH:
		raise AgentFacingError(
			f'Skill name exceeds {MAX_SKILL_NAME_LENGTH} character limit '
			f'({len(skill_name)} chars)'
		)
	return skill_name


async def __assistant_supports_skills(nc: AsyncNextcloudApp) -> bool:
	try:
		caps = await nc.capabilities
		version = caps['assistant']['version']
		return Version(version) >= Version(MINIMUM_ASSISTANT_VERSION)
	except Exception:
		return False


@timed_memoize(SKILLS_CACHE_TTL)
async def list_skills_metadata(nc: AsyncNextcloudApp) -> list[dict[str, str]]:
	"""
	Fetch each skill's name and description from the assistant app.
	Returns an empty list if the endpoint is unavailable or no skills exist.
	"""
	try:
		res = await nc.ocs('GET', LIST_STORE_SKILLS_URL)
	except NextcloudExceptionNotFound:
		return []
	except Exception as e:
		return []

	skills = res.get('skills') if isinstance(res, dict) else None
	if not isinstance(skills, list):
		return []
	return [
		{'name': str(s.get('name', '')), 'description': str(s.get('description', ''))}
		for s in skills
		if isinstance(s, dict) and s.get('name')
	]


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def load_skill(skill_name: str):
		"""
		Load the full content of a skill (frontmatter + markdown body) by name.
		Use this when one of the skills listed in the system prompt is relevant to
		the user's request and you need its detailed instructions/procedure.
		:param skill_name: The name of the skill (matches the `name` from the system prompt)
		:return: The full SKILL.md content as a string.
		"""
		try:
			skill_name = __validate_skill_name(skill_name)
		except AgentFacingError as e:
			return {'error': str(e)}

		try:
			res = await nc.ocs('GET', LOAD_SKILL_URL.format(skillName=quote(skill_name, safe='')))
		except NextcloudExceptionNotFound:
			return {'error': f'Skill "{skill_name}" not found'}
		except Exception as e:
			await log(
				nc,
				logging.WARNING,
				f"Failed to load skill {skill_name} from user: {await nc.user}'s skills folder, exc: {e}",
			)
			return {'error': f'Failed to load the skill "{skill_name}"'}

		if not isinstance(res, dict) or 'content' not in res:
			return {'error': 'Malformed skill payload returned by the server'}
		return res['content']

	@tool
	@dangerous_tool
	async def store_skill(skill_name: str, description: str, content: str):
		"""
		Create or overwrite a skill. A skill is a reusable, self-contained markdown
		document teaching the agent how to perform a particular task (procedure,
		checklist, style guide, etc.). Only create a skill when the user explicitly
		asks for one, or when the procedure is clearly reusable across future
		conversations.

		The `description` is shown to the agent in every future system prompt, so it
		should be short and start with "Use this skill when ...".

		The `content` is the markdown body of the skill (frontmatter is added by the
		server automatically from `skill_name` and `description`).

		:param skill_name: A short, human-readable name (max 64 chars)
		:param description: One- or two-sentence description of when this skill applies (max 1024 chars)
		:param content: The markdown body of the skill (max 50,000 chars)
		:return: Status of the operation.
		"""
		try:
			skill_name = __validate_skill_name(skill_name)
		except AgentFacingError as e:
			return {'error': str(e)}

		if not description or not description.strip():
			return {'error': 'Skill description cannot be empty'}
		if len(description) > MAX_DESCRIPTION_LENGTH:
			return {
				'error': f'Skill description exceeds {MAX_DESCRIPTION_LENGTH} character limit '
				f'({len(description)} chars)',
			}
		if not content or not content.strip():
			return {'error': 'Skill content cannot be empty'}
		if len(content) > MAX_CONTENT_LENGTH:
			return {
				'error': f'Skill content exceeds {MAX_CONTENT_LENGTH} character limit '
				f'({len(content)} chars)',
			}

		try:
			res = await nc.ocs(
				'POST',
				LIST_STORE_SKILLS_URL,
				json={
					'skillName': skill_name,
					'description': description,
					'content': content,
				},
				response_type='json',
			)
		except Exception as e:
			await log(
				nc,
				logging.WARNING,
				f"Failed to store skill {skill_name} in user: {await nc.user}'s skills folder, exc: {e}",
			)
			return {'error': f'Failed to store the skill {skill_name}'}

		# invalidate the cached skills list for this user so the next system
		# prompt reflects the new/updated skill
		await list_skills_metadata.cache_invalidate(nc)

		action = res.get('action') if isinstance(res, dict) else None
		return {'status': 'success', 'action': action or 'stored', 'skill_name': skill_name}

	return [load_skill, store_skill]


def get_category_name():
	return 'Skills'


async def is_available(nc: AsyncNextcloudApp):
	return await __assistant_supports_skills(nc)
