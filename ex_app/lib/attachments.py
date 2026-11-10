# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bridge from a conversation's attachments to the tools that run for it.

The model never sees file ids: a vision model receives pixels, a text-only
model receives nothing at all, and tool payloads need file ids. Attachments
therefore belong to the tool runtime, not the model context. The file ids
attached to the chat message a task answers are published here, set per task
before the agent graph runs, and tools that can use the conversation's files
read them at call time. Nothing about them is visible to the model, so no
tool ever grows an attachment parameter.
"""
from contextvars import ContextVar

#: The file ids attached to the chat message of the task currently running.
#: Empty outside a task (the MCP server path has no conversation) and for
#: tasks whose input carries no attachments. Each task's asyncio context is
#: a copy, so a value set while handling one task never leaks into another.
current_input_attachments: ContextVar[tuple[int, ...]] = ContextVar(
	"current_input_attachments", default=()
)


def task_input_attachments(task: dict) -> tuple[int, ...]:
	"""The file ids a task's input carries in ``input_attachments``.

	Junk entries are skipped rather than raising: the list crosses OCS
	boundaries, so a stray string must not sink the task. Order is kept and
	duplicates dropped, so a tool forwarding them stays predictable.
	"""
	raw = (task.get("input") or {}).get("input_attachments") or []
	file_ids: list[int] = []
	for value in raw:
		try:
			file_id = int(value)
		except (TypeError, ValueError):
			continue
		if file_id > 0 and file_id not in file_ids:
			file_ids.append(file_id)
	return tuple(file_ids)
