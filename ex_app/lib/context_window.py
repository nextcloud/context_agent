# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fitting a conversation into the language model's context window

The context window of the model behind the TaskProcessing provider is unknown, so the
conversation is fitted into a budget the admin can configure. Sizes are estimated from
characters, as there is no tokenizer for the unknown model either.

- The current turn (from the user's latest message on) is sent in full, as the model
  needs every tool result of it to finish its work, except that a single tool result
  gets no more than half the budget. Only if the turn on its own outgrows the budget
  are its earlier tool results shortened and the budget is shared out among the tool
  results the model has not answered yet.
- Tool results of earlier turns are shortened, keeping their beginning and their end.
- If the conversation still does not fit, whole turns are dropped, oldest first, so a
  tool call is never separated from its result. The caller summarizes them.
"""
import json
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage

from ex_app.lib.nc_model import extract_file_ids, extract_text_content

# Rough average for the languages and JSON the model gets to see
CHARS_PER_TOKEN = 4

CONTEXT_BUDGET_SETTING_ID = 'context_budget'
# In tokens
DEFAULT_CONTEXT_BUDGET = 16000
MIN_CONTEXT_BUDGET = 2000

# Once the budget is exceeded, the conversation is trimmed down to this share of it.
# Trimming further than needed leaves room for the next turns, so the conversation
# is not summarized again on every turn.
TRIM_TARGET = 0.6

# Tool results of earlier turns are shortened to this many characters
OLD_TOOL_OUTPUT_MAX_LENGTH = 2000

# Room reserved for the marker that replaces the omitted part
MARKER_RESERVE = 160


def _omission_hint(tool_name: str | None, can_refetch: bool) -> str:
	if not can_refetch:
		# Calling the tool again would give the same result, shortened the same way
		return '; the result is too large to be shown in full'
	if not tool_name:
		return ''
	return f'; call {tool_name} again if you need the omitted part'


def _truncate_json_list(text: str, max_chars: int, tool_name: str | None, can_refetch: bool) -> str | None:
	"""Shorten a JSON list by leaving out items, so the result is still valid JSON

	Returns None if the text is no JSON list or not even a single item fits.
	"""
	try:
		data = json.loads(text)
	except (ValueError, TypeError):
		return None
	if not isinstance(data, list) or len(data) < 2:
		return None

	items = [json.dumps(item, ensure_ascii=False) for item in data]
	compact = '[' + ', '.join(items) + ']'
	if len(compact) <= max_chars:
		return compact

	budget = max_chars - MARKER_RESERVE
	head: list[str] = []
	tail: list[str] = []
	used = 2
	i, j = 0, len(items) - 1
	while i <= j and used + len(items[i]) + 2 <= budget * 2 // 3:
		head.append(items[i])
		used += len(items[i]) + 2
		i += 1
	while j >= i and used + len(items[j]) + 2 <= budget:
		tail.insert(0, items[j])
		used += len(items[j]) + 2
		j -= 1
	if not head and not tail:
		return None

	omitted = j - i + 1
	marker = json.dumps(f'… {omitted} of {len(items)} items omitted{_omission_hint(tool_name, can_refetch)} …', ensure_ascii=False)
	return '[' + ', '.join(head + [marker] + tail) + ']'


def truncate_text(text: str, max_chars: int, tool_name: str | None = None, can_refetch: bool = True) -> str:
	"""Shorten a text to about max_chars, keeping its beginning and its end

	The end is often where the answer is, e.g. the latest entries of a list. JSON lists
	are shortened by leaving out whole items. The marker says how much was left out and,
	given the tool's name, that the model can call the tool again to see all of it.
	With can_refetch=False it says that the result is too large instead, for results
	that would come back shortened the same way.
	"""
	if len(text) <= max_chars:
		return text

	truncated = _truncate_json_list(text, max_chars, tool_name, can_refetch)
	if truncated is not None:
		return truncated

	keep = max(max_chars - MARKER_RESERVE, 0)
	head_len = keep * 2 // 3
	tail_len = keep - head_len
	head = text[:head_len]
	tail = text[len(text) - tail_len:] if tail_len > 0 else ''
	# Cut at line boundaries if there is one close by
	newline = head.rfind('\n')
	if newline >= head_len * 0.8:
		head = head[:newline + 1]
	newline = tail.find('\n')
	if 0 <= newline < tail_len * 0.2:
		tail = tail[newline + 1:]

	omitted = len(text) - len(head) - len(tail)
	return f'{head}\n[… {omitted} of {len(text)} characters omitted{_omission_hint(tool_name, can_refetch)}]\n{tail}'


def message_size(message: BaseMessage) -> int:
	"""The size of a message in characters, as it is sent to the model"""
	size = len(json.dumps(message.content, ensure_ascii=False))
	if isinstance(message, AIMessage) and message.tool_calls:
		size += len(json.dumps(message.tool_calls, ensure_ascii=False, default=str))
	return size


def _turn_size(turn: list[BaseMessage]) -> int:
	return sum(message_size(message) for message in turn)


def split_turns(messages: list[BaseMessage]) -> list[list[BaseMessage]]:
	"""Split a conversation into turns, each starting with a message of the user"""
	turns: list[list[BaseMessage]] = []
	for message in messages:
		if message.type == 'human' or not turns:
			turns.append([])
		turns[-1].append(message)
	return turns


def _truncate_tool_message(message: BaseMessage, max_chars: int, can_refetch: bool = True) -> BaseMessage:
	if message.type != 'tool' or not isinstance(message.content, str) or len(message.content) <= max_chars:
		return message
	content = truncate_text(message.content, max_chars, getattr(message, 'name', None), can_refetch)
	return message.model_copy(update={'content': content})


def _compact_old_turn(turn: list[BaseMessage]) -> list[BaseMessage]:
	return [_truncate_tool_message(message, OLD_TOOL_OUTPUT_MAX_LENGTH) for message in turn]


def _share_out(sizes: list[int], total: int, floor: int, ceiling: int) -> list[int]:
	"""Share out a number of characters among texts of the given sizes

	What a small text does not need of its share goes to the larger ones. No text gets
	less than floor, so many texts together can still exceed the total.
	"""
	shares = [0] * len(sizes)
	remaining = total
	smallest_first = sorted(range(len(sizes)), key=lambda i: sizes[i])
	for n, i in enumerate(smallest_first):
		shares[i] = min(max(remaining // (len(sizes) - n), floor), ceiling)
		remaining -= min(sizes[i], shares[i])
	return shares


def _compact_current_turn(turn: list[BaseMessage], budget_chars: int) -> list[BaseMessage]:
	# A single tool result must not take up the whole budget
	max_result_chars = budget_chars // 2
	capped = [_truncate_tool_message(message, max_result_chars, can_refetch=False) for message in turn]
	if _turn_size(capped) <= budget_chars:
		return capped
	# The earlier tool results of the turn, which the model has already seen, are shortened
	last_ai = max((i for i, message in enumerate(turn) if message.type == 'ai'), default=-1)
	compacted = [
		_truncate_tool_message(message, OLD_TOOL_OUTPUT_MAX_LENGTH) if i < last_ai else message
		for i, message in enumerate(turn)
	]
	# The tool results the model has not answered yet share what is left of the budget
	pending = [i for i, message in enumerate(compacted) if i > last_ai and message.type == 'tool']
	available = budget_chars - _turn_size([message for i, message in enumerate(compacted) if i not in pending])
	shares = _share_out(
		[message_size(compacted[i]) for i in pending], max(available, 0), OLD_TOOL_OUTPUT_MAX_LENGTH, max_result_chars,
	)
	for i, share in zip(pending, shares):
		compacted[i] = _truncate_tool_message(compacted[i], share, can_refetch=False)
	return compacted


@dataclass
class ContextSelection:
	# Turns that do not fit anymore, to be summarized
	dropped: list[BaseMessage]
	# The rest of the conversation, unchanged, to be kept in the state
	kept: list[BaseMessage]
	# The rest of the conversation as it is sent to the model, with shortened tool results
	model_messages: list[BaseMessage]


def select_context(messages: list[BaseMessage], budget_tokens: int) -> ContextSelection:
	"""Pick the part of the conversation that is sent to the model"""
	budget_chars = max(budget_tokens, MIN_CONTEXT_BUDGET) * CHARS_PER_TOKEN
	turns = split_turns(list(messages))
	if not turns:
		return ContextSelection([], [], [])

	older, current = turns[:-1], turns[-1]
	older_compacted = [_compact_old_turn(turn) for turn in older]
	current_compacted = _compact_current_turn(current, budget_chars)

	sizes = [_turn_size(turn) for turn in older_compacted]
	total = sum(sizes) + _turn_size(current_compacted)
	first_kept = 0
	if total > budget_chars:
		while first_kept < len(older) and total > budget_chars * TRIM_TARGET:
			total -= sizes[first_kept]
			first_kept += 1

	return ContextSelection(
		dropped=[message for turn in older[:first_kept] for message in turn],
		kept=[message for turn in older[first_kept:] for message in turn] + current,
		model_messages=[message for turn in older_compacted[first_kept:] for message in turn] + current_compacted,
	)


def render_transcript(messages: list[BaseMessage], max_chars: int) -> str:
	"""Write out a part of the conversation as plain text, for it to be summarized"""
	lines = []
	for message in messages:
		if message.type == 'human':
			line = 'User: ' + extract_text_content(message.content)
			file_ids = extract_file_ids(message.content)
			if file_ids:
				line += f' [attached files with the ids {", ".join(str(file_id) for file_id in file_ids)}]'
			lines.append(line)
		elif message.type == 'ai':
			text = extract_text_content(message.content)
			if text:
				lines.append('Assistant: ' + text)
			for tool_call in getattr(message, 'tool_calls', None) or []:
				lines.append(f'Assistant called the tool {tool_call["name"]} with {json.dumps(tool_call.get("args"), ensure_ascii=False, default=str)}')
		elif message.type == 'tool':
			content = message.content if isinstance(message.content, str) else extract_text_content(message.content)
			lines.append(f'Result of {getattr(message, "name", None) or "the tool"}: ' + truncate_text(content, OLD_TOOL_OUTPUT_MAX_LENGTH))
	return truncate_text('\n\n'.join(lines), max_chars)
