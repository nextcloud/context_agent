# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fitting a conversation into the language model's context window

The context window of the model behind the TaskProcessing provider is unknown, so the
request is fitted into a budget the admin can configure. Sizes are estimated from
characters, as there is no tokenizer for the unknown model either.

The instructions, the tool descriptions and the summary of the earlier turns are sent
with every request, so they come off the budget first. The conversation gets the rest:

- The current turn (from the user's latest message on) is sent in full, as the model
  needs every tool result of it to finish its work, except that a single tool result
  gets no more than half the budget. Only if the turn on its own outgrows the budget
  are its earlier tool results shortened and the budget is shared out among the tool
  results the model has not answered yet. If that is not enough, tool results are left
  out, oldest first, as a request over the budget is likely to fail altogether.
- If the conversation does not fit, tool results of earlier turns are shortened, keeping
  their beginning and their end, oldest turn first, until it does.
- If the conversation still does not fit, whole turns are dropped, oldest first, so a
  tool call is never separated from its result. The caller summarizes them. Of the
  remaining turns, only the tool results that still do not fit are shortened.
"""
import json
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage

from ex_app.lib.nc_model import extract_file_ids, extract_text_content

# Rough average for the languages and JSON the model gets to see
CHARS_PER_TOKEN = 4

CONTEXT_BUDGET_SETTING_ID = 'context_budget'
# In tokens. With all tools enabled, their descriptions alone take up more than 20000.
DEFAULT_CONTEXT_BUDGET = 40000
# The conversation gets at least this much, whatever the rest of the request takes up
MIN_CONTEXT_BUDGET = 2000

# Once the budget is exceeded, the conversation is trimmed down to this share of it.
# Trimming further than needed leaves room for the next turns, so the conversation
# is not summarized again on every turn.
TRIM_TARGET = 0.6

# Tool results of earlier turns are shortened to this many characters, once the conversation does not fit
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


def _size_tree(value) -> tuple:
	"""The sizes of a parsed JSON value, so shortening can be planned without serializing it again

	Returns the node and the serialized size. A scalar node is its serialized length,
	a dict node [fixed size, child nodes], a list node [the list, child nodes, its size].
	"""
	if isinstance(value, list):
		children, sizes = zip(*(_size_tree(item) for item in value), strict=True) if value else ((), ())
		size = 2 + sum(sizes) + 2 * max(len(sizes) - 1, 0)
		return [value, list(children), size], size
	if isinstance(value, dict):
		fixed = 2 + 2 * max(len(value) - 1, 0) + sum(len(json.dumps(key, ensure_ascii=False)) + 2 for key in value)
		children, sizes = zip(*(_size_tree(item) for item in value.values()), strict=True) if value else ((), ())
		return [fixed, list(children)], fixed + sum(sizes)
	size = len(json.dumps(value, ensure_ascii=False))
	return size, size


def _keep_ends(sizes: list[int], budget: int) -> tuple[int, int]:
	"""How many items to keep from the start and from the end of a list so they fit into budget"""
	used = 2
	i, j = 0, len(sizes) - 1
	while i <= j and used + sizes[i] + 2 <= budget * 2 // 3:
		used += sizes[i] + 2
		i += 1
	while j >= i and used + sizes[j] + 2 <= budget:
		used += sizes[j] + 2
		j -= 1
	return i, len(sizes) - 1 - j


def _shortened_size(node, share: float | None, hint: str = '', apply: bool = False) -> tuple[int, int]:
	"""The serialized size of a node once every list in it is shortened to share of its original size

	Returns the size and how much of it the markers take up. Inner lists are shortened first, and their
	markers do not count against the share of the lists around them, so a list only leaves out whole items
	if shortening the lists inside them is not enough. Every list keeps at least one item.
	With share None, nothing is shortened. With apply, the lists are shortened in place.
	"""
	if isinstance(node, int):
		return node, 0
	if len(node) == 2:
		results = [_shortened_size(child, share, hint, apply) for child in node[1]]
		return node[0] + sum(size for size, _ in results), sum(markers for _, markers in results)
	items, children, original = node
	results = [_shortened_size(child, share, hint, apply) for child in children]
	sizes = [size for size, _ in results]
	size = 2 + sum(sizes) + 2 * max(len(sizes) - 1, 0)
	markers = sum(markers for _, markers in results)
	if share is None or len(sizes) < 2 or size - markers <= share * original:
		return size, markers
	# The marker is no longer than with every item left out
	longest_marker = len(json.dumps(f'… {len(sizes)} of {len(sizes)} items omitted{hint} …', ensure_ascii=False)) + 2
	# Items are chosen by their size without the markers inside them
	head, tail = _keep_ends([size - inner for size, inner in results], int(share * original) - longest_marker)
	if head + tail == 0:
		head = 1
	omitted = len(sizes) - head - tail
	marker_size = len(json.dumps(f'… {omitted} of {len(sizes)} items omitted{hint} …', ensure_ascii=False)) + 2
	kept = results[:head] + results[len(results) - tail:]
	new_size = sum(size for size, _ in kept) + 2 * len(kept) + marker_size
	if new_size >= size:
		return size, markers
	if apply:
		marker = f'… {omitted} of {len(sizes)} items omitted{hint} …'
		items[:] = [*items[:head], marker, *items[len(items) - tail:]]
	return new_size, sum(markers for _, markers in kept) + marker_size


def _truncate_json(text: str, max_chars: int, tool_name: str | None, can_refetch: bool) -> str | None:
	"""Shorten the lists in a JSON value by leaving out items in their middle, so the result is still valid JSON

	Tools often answer with an object that carries its items in a field, e.g. {"tickets": [...], "count": 10},
	possibly nested deeper. Cutting such a result by characters would leave invalid JSON, so its lists are
	shortened instead, each to the same share of its size, the largest share that fits. Everything else is kept.
	Returns None if the text is not JSON, or it does not fit even with one item left in every list.
	"""
	hint = _omission_hint(tool_name, can_refetch)
	try:
		data = json.loads(text)
		tree, _ = _size_tree(data)
		if _shortened_size(tree, 0.0, hint)[0] > max_chars:
			return None
		# The largest share that fits, close enough once the result uses most of max_chars
		low, high = 0.0, 1.0
		for _ in range(12):
			share = (low + high) / 2
			size = _shortened_size(tree, share, hint)[0]
			if size <= max_chars:
				low = share
				if size >= max_chars * 0.95:
					break
			else:
				high = share
		_shortened_size(tree, low, hint, apply=True)
		return json.dumps(data, ensure_ascii=False)
	except (ValueError, TypeError, RecursionError):
		# Nesting deeper than Python's recursion limit is cut by characters instead
		return None


def truncate_text(text: str, max_chars: int, tool_name: str | None = None, can_refetch: bool = True) -> str:
	"""Shorten a text to about max_chars, keeping its beginning and its end

	The end is often where the answer is, e.g. the latest entries of a list. The lists in JSON,
	at any depth, are shortened by leaving out whole items. The marker says how much was left out and,
	given the tool's name, that the model can call the tool again to see all of it.
	With can_refetch=False it says that the result is too large instead, for results
	that would come back shortened the same way.
	"""
	if len(text) <= max_chars:
		return text

	truncated = _truncate_json(text, max_chars, tool_name, can_refetch)
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
	if message.type != 'tool' or not isinstance(message.content, str) or message_size(message) <= max_chars:
		return message
	limit = max_chars
	# Escaping makes a message larger than its text, so it can take a second round
	for _ in range(2):
		content = truncate_text(message.content, limit, getattr(message, 'name', None), can_refetch)
		truncated = message.model_copy(update={'content': content})
		excess = message_size(truncated) - max_chars
		if excess <= 0:
			break
		limit -= excess
	return truncated


def _omit_tool_message(message: BaseMessage, can_refetch: bool = True) -> BaseMessage:
	"""Leave out a tool result, keeping the message, as every tool call needs its result"""
	hint = _omission_hint(getattr(message, 'name', None), can_refetch)
	omitted = message.model_copy(update={'content': f'[… result omitted to stay within the context window{hint}]'})
	return omitted if message_size(omitted) < message_size(message) else message


def _compact_old_turn(turn: list[BaseMessage]) -> list[BaseMessage]:
	return [_truncate_tool_message(message, OLD_TOOL_OUTPUT_MAX_LENGTH) for message in turn]


def _shorten_until_fits(
		turns: list[list[BaseMessage]], fixed_size: int, budget_chars: int,
) -> tuple[list[list[BaseMessage]], list[int], int]:
	"""Shorten the tool results of earlier turns until they fit into the budget along with fixed_size

	The tool results of the latest turns are the ones most likely to be needed again, so the
	oldest are shortened first. Returns the turns, their sizes and the total size, which is
	still over the budget if shortening all of them is not enough.
	"""
	shortened = list(turns)
	sizes = [_turn_size(turn) for turn in turns]
	total = sum(sizes) + fixed_size
	for i, turn in enumerate(turns):
		if total <= budget_chars:
			break
		shortened[i] = _compact_old_turn(turn)
		total -= sizes[i]
		sizes[i] = _turn_size(shortened[i])
		total += sizes[i]
	return shortened, sizes, total


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
	pending = [i for i, message in enumerate(compacted) if i > last_ai and message.type == 'tool']
	pending_sizes = [message_size(compacted[i]) for i in pending]
	# The earlier tool results are left out, oldest first, if there is no room for the pending ones otherwise
	needed = sum(min(size, OLD_TOOL_OUTPUT_MAX_LENGTH) for size in pending_sizes)
	used = _turn_size(compacted) - sum(pending_sizes)
	for i, message in enumerate(compacted):
		if used + needed <= budget_chars:
			break
		if i < last_ai and message.type == 'tool':
			compacted[i] = _omit_tool_message(message)
			used -= message_size(message) - message_size(compacted[i])
	# The tool results the model has not answered yet share what is left of the budget
	shares = _share_out(pending_sizes, max(budget_chars - used, 0), OLD_TOOL_OUTPUT_MAX_LENGTH, max_result_chars)
	for i, share in zip(pending, shares):
		compacted[i] = _truncate_tool_message(compacted[i], share, can_refetch=False)
	# Rather than going over the budget, the pending ones are left out as well, oldest first
	total = _turn_size(compacted)
	for i in pending:
		if total <= budget_chars:
			break
		omitted = _omit_tool_message(compacted[i], can_refetch=False)
		total -= message_size(compacted[i]) - message_size(omitted)
		compacted[i] = omitted
	return compacted


@dataclass
class ContextSelection:
	# Turns that do not fit anymore, to be summarized
	dropped: list[BaseMessage]
	# The rest of the conversation, unchanged, to be kept in the state
	kept: list[BaseMessage]
	# The rest of the conversation as it is sent to the model, with shortened tool results
	model_messages: list[BaseMessage]


def conversation_budget(budget_tokens: int, reserved_chars: int = 0) -> int:
	"""What is left of the budget for the conversation, in characters

	reserved_chars is the size of what is sent along with the conversation.
	"""
	return max(budget_tokens * CHARS_PER_TOKEN - reserved_chars, MIN_CONTEXT_BUDGET * CHARS_PER_TOKEN)


def select_context(messages: list[BaseMessage], budget_tokens: int, reserved_chars: int = 0) -> ContextSelection:
	"""Pick the part of the conversation that is sent to the model

	Together with the reserved_chars sent along with it, it stays within the budget, unless
	the current turn outgrows it without its tool results or less than the minimum is left.
	"""
	budget_chars = conversation_budget(budget_tokens, reserved_chars)
	turns = split_turns(list(messages))
	if not turns:
		return ContextSelection([], [], [])

	older, current = turns[:-1], turns[-1]
	current_compacted = _compact_current_turn(current, budget_chars)
	current_size = _turn_size(current_compacted)

	older_compacted, sizes, total = _shorten_until_fits(older, current_size, budget_chars)
	first_kept = 0
	if total > budget_chars:
		while first_kept < len(older) and total > budget_chars * TRIM_TARGET:
			total -= sizes[first_kept]
			first_kept += 1
		# The dropped turns made room, so only what the remaining turns still need is shortened. This is
		# what the next step makes of them as well, so a tool result is not shortened in one request and
		# sent in full in the next.
		older_compacted, _, _ = _shorten_until_fits(older[first_kept:], current_size, budget_chars)

	return ContextSelection(
		dropped=[message for turn in older[:first_kept] for message in turn],
		kept=[message for turn in older[first_kept:] for message in turn] + current,
		model_messages=[message for turn in older_compacted for message in turn] + current_compacted,
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


def render_transcripts(messages: list[BaseMessage], max_chars: int, max_count: int) -> list[str]:
	"""Write out a part of the conversation as plain texts of at most max_chars each, for it to be summarized in steps

	Turns are kept whole, so nothing is cut out of the middle, unless a single turn is longer
	than max_chars. If there are more texts than max_count, only the latest ones are returned,
	as the oldest turns matter the least.
	"""
	transcripts: list[str] = []
	for turn in split_turns(messages):
		rendered = render_transcript(turn, max_chars)
		if transcripts and len(transcripts[-1]) + 2 + len(rendered) <= max_chars:
			transcripts[-1] += '\n\n' + rendered
		else:
			transcripts.append(rendered)
	return transcripts[-max_count:]
