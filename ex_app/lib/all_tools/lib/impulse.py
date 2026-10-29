# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Impulse radius classification for tools.

Every tool carries an *impulse radius hook*: a callable that receives the same
parameters the tool itself was called with and answers a single question --
**who gains access to the item this call touches?**

	``SELF``        nobody new gains access (reads, edits of the user's own data)
	``INDIVIDUALS`` a bounded, named set of people (a direct share, an invite)
	``GROUP``       a group, team, board or conversation audience
	``EXTERNAL``    anyone outside this Nextcloud (mail, public link, the internet)

Because the hook sees the arguments, one tool can land in different radii
depending on how it is called: ``schedule_event`` without attendees is ``SELF``,
with attendees it is ``INDIVIDUALS``. Hooks may be async and may call the
Nextcloud API, so ``send_message_to_conversation`` can look the conversation up
and answer ``INDIVIDUALS`` for a one-to-one chat but ``EXTERNAL`` for a public
room.

For actions that modify an item which already has an audience (editing a team
wiki page, posting in a conversation) the radius is that existing audience -- the
audience is who the action reaches.

Actions that *withdraw* access (deleting a share, removing a team member) disclose
nothing, but they are :func:`destructive`, and the radius of a destructive call is
read as how far the thing being lost reached. So a withdrawal reports the reach of
the access it takes away: revoking a group share is ``GROUP``, because a group is
what loses something. ``SELF`` is for a call that takes away nothing anybody else
had -- deleting a private note, not evicting a team from a folder.

Radius answers who a call reaches, which says nothing about whether it takes
something away. That is the second dimension: a tool marked :func:`destructive`
deletes or overwrites something, and those are confirmed on their own threshold,
so emptying a folder of your own files can still be worth asking about even
though it discloses nothing. Like the radius, it can depend on the arguments --
:func:`destructive_if` takes a hook, because copying onto a free path destroys
nothing while copying onto a taken one replaces a file.

The third dimension is whether a call can be taken back. :func:`reversibility`
answers it in three steps: ``REVERSIBLE`` when the user (or the agent) can
simply undo it -- rename it back, revoke the share; ``RECOVERABLE`` when it can
only be fished out of a trash bin or a version history, which may expire;
``IRREVERSIBLE`` when it is gone for good. That covers disclosure as much as
deletion: a sent email or a posted message cannot be unsent. Notifications a call
sets off along the way do not count -- assigning a card notifies the assignee,
but unassigning them takes the assignment back all the same.

The fourth is how many items a call changes, adds, deletes or sends to --
:func:`item_count`. It counts recursively, so deleting a folder counts what is in
it, and emailing forty people counts forty. Hooks are told how far to count
(``count_limit``) and may stop there, since past the admin's bar the exact number
no longer changes the answer. Tools that only read are :func:`read_only`: they
can always be taken back and change nothing.

None of the dimensions says anything about what a call lets the agent do
*later*. A handful of tools hand the agent a lever it can pull unattended
afterwards -- a scheduled task runs on its own, with its own prompt, reaching
wherever its tools reach. Their reach is not knowable at classification time, so
they carry :func:`always_confirm` and are asked about no matter how the
thresholds are set.

Every tool is expected to answer all of these. A tool that does not -- MCP tools
cannot be decorated -- or whose hook fails is assumed to be as bad as it can be:
the widest radius, irreversible, and an unknown number of items.

All of them feed a :class:`ConfirmationPolicy`, which compares them against
their admin-configured thresholds and honours :func:`always_confirm`
unconditionally.
"""
import inspect
from dataclasses import dataclass
from enum import IntEnum


class ImpulseRadius(IntEnum):
	"""Who gains access through a tool call, ordered by how far the call reaches."""

	SELF = 0
	"""Nobody new gains access."""
	INDIVIDUALS = 1
	"""A bounded list of named people gains access."""
	GROUP = 2
	"""A group, team, board or conversation audience gains access."""
	EXTERNAL = 3
	"""Someone outside this Nextcloud instance gains access."""


class Reversibility(IntEnum):
	"""Whether a tool call can be taken back, ordered by how hard that is."""

	REVERSIBLE = 0
	"""The user can undo it directly: rename it back, revoke the share."""
	RECOVERABLE = 1
	"""It can only be restored from a trash bin or a version history."""
	IRREVERSIBLE = 2
	"""It cannot be taken back: a permanent deletion, a sent message."""


# Used when a tool carries no hook at all (MCP tools cannot be decorated) and
# when a hook fails: assume the widest reach so the user is always asked.
DEFAULT_IMPULSE_RADIUS = ImpulseRadius.EXTERNAL

DEFAULT_IMPULSE_THRESHOLD = ImpulseRadius.INDIVIDUALS

IMPULSE_THRESHOLD_SETTING_ID = 'impulse_radius_threshold'

# Deletions are confirmed from this radius on. SELF means every deletion is
# confirmed, since no call reaches less far than that.
DEFAULT_DESTRUCTIVE_THRESHOLD = ImpulseRadius.SELF

DESTRUCTIVE_THRESHOLD_SETTING_ID = 'destructive_radius_threshold'

# Used like DEFAULT_IMPULSE_RADIUS: a call nobody can vouch for cannot be undone.
DEFAULT_REVERSIBILITY = Reversibility.IRREVERSIBLE

# Calls that cannot be taken back are confirmed from this radius on.
DEFAULT_IRREVERSIBLE_THRESHOLD = ImpulseRadius.INDIVIDUALS

IRREVERSIBLE_THRESHOLD_SETTING_ID = 'irreversible_radius_threshold'

# Which calls the irreversible threshold applies to: only those that are gone for
# good, or also those that have to be restored from a trash bin.
DEFAULT_IRREVERSIBLE_FROM = Reversibility.IRREVERSIBLE

IRREVERSIBLE_FROM_SETTING_ID = 'irreversible_from'

# Whether a deletion that can be undone directly skips the deletion threshold.
DEFAULT_RELAX_REVERSIBLE_DELETIONS = False

RELAX_REVERSIBLE_DELETIONS_SETTING_ID = 'relax_reversible_deletions'

# A turn changing more items than this, all its calls together, is confirmed.
DEFAULT_ITEM_COUNT_THRESHOLD = 10

ITEM_COUNT_THRESHOLD_SETTING_ID = 'item_count_threshold'

_ATTR = 'impulse_hook'
_DESTRUCTIVE_ATTR = 'impulse_destructive'
_ALWAYS_CONFIRM_ATTR = 'impulse_always_confirm'
_REVERSIBILITY_ATTR = 'impulse_reversibility'
_ITEM_COUNT_ATTR = 'impulse_item_count'

# The parameter an item count hook declares to be told how far to count.
COUNT_LIMIT_PARAM = 'count_limit'


def parse_impulse_radius(value, default=DEFAULT_IMPULSE_RADIUS) -> ImpulseRadius:
	"""Turn a setting value ('self', 'group', 2, ...) into an ImpulseRadius."""
	if isinstance(value, ImpulseRadius):
		return value
	if isinstance(value, int):
		try:
			return ImpulseRadius(value)
		except ValueError:
			return default
	if isinstance(value, str):
		try:
			return ImpulseRadius[value.strip().upper()]
		except KeyError:
			return default
	return default


def parse_reversibility(value, default=DEFAULT_REVERSIBILITY) -> Reversibility:
	"""Turn a setting or hook value ('recoverable', 1, ...) into a Reversibility."""
	if isinstance(value, Reversibility):
		return value
	if isinstance(value, int) and not isinstance(value, bool):
		try:
			return Reversibility(value)
		except ValueError:
			return default
	if isinstance(value, str):
		try:
			return Reversibility[value.strip().upper()]
		except KeyError:
			return default
	return default


def impulse(radius_or_hook):
	"""Attach an impulse radius hook to a tool function.

	Pass a constant radius when the reach never depends on the arguments::

		@tool
		@impulse(ImpulseRadius.EXTERNAL)
		async def send_email(...): ...

	or a hook taking (a subset of) the tool's parameters when it does. The hook
	may be sync or async and may hit the Nextcloud API::

		async def _share_radius(share_with_group=False, **kwargs):
			return ImpulseRadius.GROUP if share_with_group else ImpulseRadius.INDIVIDUALS

		@tool
		@impulse(_share_radius)
		async def share(...): ...
	"""
	if callable(radius_or_hook):
		hook = radius_or_hook
	else:
		radius = parse_impulse_radius(radius_or_hook)

		async def hook(**_kwargs):
			return radius

	def decorator(tool_func):
		setattr(tool_func, _ATTR, hook)
		return tool_func

	return decorator


def destructive(tool_func):
	"""Mark a tool as destroying content the user had.

	Applied to the tool function like :func:`impulse`, and independent of it: a
	deletion can reach anyone at all, from a note only the user can see to a page
	in a team wiki.

		@tool
		@impulse(ImpulseRadius.SELF)
		@destructive
		async def delete_memory(path: str): ...

	Deleting is the obvious case, but overwriting is the same loss by another
	name: writing a file that already exists leaves the user just as short of what
	was there before. Use :func:`destructive_if` when only some arguments do that.
	"""
	setattr(tool_func, _DESTRUCTIVE_ATTR, True)
	return tool_func


def destructive_if(hook):
	"""Mark a tool as destroying something only for certain arguments.

	Some tools overwrite or create depending on what is already there: copying
	onto a free path costs nothing, copying onto a taken one replaces a file. The
	hook takes (a subset of) the tool's parameters, exactly like an impulse hook,
	and answers whether *this* call destroys something::

		async def _overwrites(destination_path=None):
			return await file_exists(nc, destination_path)

		@tool
		@impulse(transfer_radius)
		@destructive_if(_overwrites)
		async def copy_file(...): ...

	A hook that raises is read as destroying something, so a question that cannot
	be answered still reaches the user.
	"""
	def decorator(tool_func):
		setattr(tool_func, _DESTRUCTIVE_ATTR, hook)
		return tool_func

	return decorator


async def classify_destructive(tool, tool_args: dict) -> bool:
	"""Whether this call deletes or overwrites something."""
	tool_action = _tool_action(tool)
	if tool_action is None:
		return False
	marker = getattr(tool_action, _DESTRUCTIVE_ATTR, False)
	if not callable(marker):
		return bool(marker)
	try:
		return bool(await _run_hook(marker, tool_args))
	except Exception as e:  # noqa: BLE001 - a hook must never break a tool call
		print(f"Destructiveness hook for '{getattr(tool, 'name', tool)}' failed ({e!r}), assuming it destroys something")
		return True


def reversibility(level_or_hook):
	"""Attach how far a tool call can be taken back.

	A constant when it never depends on the arguments::

		@tool
		@impulse(ImpulseRadius.EXTERNAL)
		@reversibility(Reversibility.IRREVERSIBLE)
		async def send_email(...): ...

	or a hook taking (a subset of) the tool's parameters, like an impulse hook,
	when it does -- creating a file on a free path can be undone by deleting it,
	writing over a taken one can only be undone from its versions::

		async def _write_reversibility(path=None):
			return Reversibility.RECOVERABLE if await file_exists(nc, path) else Reversibility.REVERSIBLE

	A hook that raises is read as :attr:`Reversibility.IRREVERSIBLE`.
	"""
	if callable(level_or_hook):
		marker = level_or_hook
	else:
		marker = parse_reversibility(level_or_hook)

	def decorator(tool_func):
		setattr(tool_func, _REVERSIBILITY_ATTR, marker)
		return tool_func

	return decorator


def item_count(count_or_hook):
	"""Attach how many items a tool call changes, adds, deletes or sends to.

	A constant for the common case of a tool that touches a single item::

		@tool
		@impulse(path_radius)
		@reversibility(Reversibility.REVERSIBLE)
		@item_count(1)
		async def create_folder(path: str): ...

	or a hook taking (a subset of) the tool's parameters when it depends on them.
	Count recursively: deleting a folder deletes everything in it. A hook may
	declare ``count_limit`` to be told how far counting is worth it -- past that
	the call is confirmed anyway, so it can stop walking a large tree there::

		async def _tree_size(path=None, count_limit=None):
			return await count_nodes(nc, path, stop_at=count_limit)

	A hook that raises is read as an unknown count, which is always confirmed.
	"""
	marker = count_or_hook if callable(count_or_hook) else int(count_or_hook)

	def decorator(tool_func):
		setattr(tool_func, _ITEM_COUNT_ATTR, marker)
		return tool_func

	return decorator


def read_only(tool_func):
	"""Mark a tool as only reading: there is nothing to take back, nothing changes.

	Shorthand for ``@reversibility(Reversibility.REVERSIBLE)`` and
	``@item_count(0)``. The radius still needs its own :func:`impulse` -- reading
	something to the model is not the same as reading it out to a group.
	"""
	setattr(tool_func, _REVERSIBILITY_ATTR, Reversibility.REVERSIBLE)
	setattr(tool_func, _ITEM_COUNT_ATTR, 0)
	return tool_func


def _tool_action(tool):
	return getattr(tool, 'coroutine', None) or getattr(tool, 'func', None)


async def _run_hook(hook, tool_args: dict, extra: dict | None = None):
	kwargs = _select_hook_kwargs(hook, tool_args or {})
	if extra:
		kwargs.update(_select_hook_kwargs(hook, extra))
	result = hook(**kwargs)
	if inspect.isawaitable(result):
		result = await result
	return result


async def classify_reversibility(tool, tool_args: dict) -> Reversibility:
	"""How far this call can be taken back."""
	tool_action = _tool_action(tool)
	marker = getattr(tool_action, _REVERSIBILITY_ATTR, None) if tool_action is not None else None
	if marker is None:
		print(f"No reversibility on tool '{getattr(tool, 'name', tool)}', assuming {DEFAULT_REVERSIBILITY.name}")
		return DEFAULT_REVERSIBILITY
	if not callable(marker):
		return marker
	try:
		return parse_reversibility(await _run_hook(marker, tool_args))
	except Exception as e:  # noqa: BLE001 - a hook must never break a tool call
		print(f"Reversibility hook for '{getattr(tool, 'name', tool)}' failed ({e!r}), assuming {DEFAULT_REVERSIBILITY.name}")
		return DEFAULT_REVERSIBILITY


async def classify_item_count(tool, tool_args: dict, count_limit: int) -> int | None:
	"""How many items this call touches, no higher than ``count_limit``, or None
	when nobody can say.
	"""
	tool_action = _tool_action(tool)
	marker = getattr(tool_action, _ITEM_COUNT_ATTR, None) if tool_action is not None else None
	if marker is None:
		print(f"No item count on tool '{getattr(tool, 'name', tool)}', assuming it is unknown")
		return None
	try:
		count = await _run_hook(marker, tool_args, {COUNT_LIMIT_PARAM: count_limit}) if callable(marker) else marker
		if count is None:
			return None
		return min(max(int(count), 0), count_limit)
	except Exception as e:  # noqa: BLE001 - a hook must never break a tool call
		print(f"Item count hook for '{getattr(tool, 'name', tool)}' failed ({e!r}), assuming it is unknown")
		return None


def always_confirm(tool_func):
	"""Mark a tool as needing confirmation whatever the thresholds are set to.

	For the few tools whose reach is not the reach of this call: scheduling a task
	discloses nothing now, but hands the agent a prompt it will run unattended
	later, with whatever radius the tools it then picks happen to have. There is
	no radius that describes that honestly, so these opt out of the comparison
	instead of being given an inflated one.

		@tool
		@impulse(ImpulseRadius.SELF)
		@always_confirm
		async def create_scheduled_task(...): ...
	"""
	setattr(tool_func, _ALWAYS_CONFIRM_ATTR, True)
	return tool_func


def is_always_confirmed(tool) -> bool:
	"""Whether this tool is confirmed regardless of its radius."""
	tool_action = _tool_action(tool)
	if tool_action is None:
		return False
	return bool(getattr(tool_action, _ALWAYS_CONFIRM_ATTR, False))


def get_impulse_hook(tool):
	"""Return the impulse hook of a LangChain tool, or None if it carries none."""
	tool_action = _tool_action(tool)
	if tool_action is None:
		return None
	return getattr(tool_action, _ATTR, None)


def _select_hook_kwargs(hook, tool_args: dict) -> dict:
	"""Pass only what the hook declares, unless it takes **kwargs.

	Tool arguments come from a model, so they can be incomplete or carry keys the
	hook never asked about. Filtering here keeps hooks free of defensive noise.
	Shared by all hooks: impulse radius, :func:`destructive_if`,
	:func:`reversibility` and :func:`item_count`.
	"""
	try:
		parameters = inspect.signature(hook).parameters
	except (TypeError, ValueError):
		return dict(tool_args)
	if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
		return dict(tool_args)
	accepted = {
		name for name, p in parameters.items()
		if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
	}
	return {k: v for k, v in tool_args.items() if k in accepted}


async def classify_tool_call(tool, tool_args: dict) -> ImpulseRadius:
	"""Run a tool's impulse hook over the arguments it is about to be called with."""
	hook = get_impulse_hook(tool)
	if hook is None:
		print(f"No impulse hook on tool '{getattr(tool, 'name', tool)}', assuming {DEFAULT_IMPULSE_RADIUS.name}")
		return DEFAULT_IMPULSE_RADIUS
	try:
		return parse_impulse_radius(await _run_hook(hook, tool_args))
	except Exception as e:  # noqa: BLE001 - a hook must never break a tool call
		print(f"Impulse hook for '{getattr(tool, 'name', tool)}' failed ({e!r}), assuming {DEFAULT_IMPULSE_RADIUS.name}")
		return DEFAULT_IMPULSE_RADIUS


@dataclass(frozen=True)
class CallClassification:
	"""Everything the hooks answered about one tool call."""

	radius: ImpulseRadius
	destroys: bool
	reversibility: Reversibility
	item_count: int | None
	"""How many items it touches, capped at the policy's count limit; None if unknown."""
	always: bool = False


@dataclass(frozen=True)
class ConfirmationPolicy:
	"""The admin's thresholds, and what they make of a batch of tool calls.

	Each threshold is a gate of its own, and a batch is confirmed as soon as one of
	them is reached by one of its calls:

	- any call reaching the impulse threshold
	- a deletion or overwrite reaching the deletion threshold -- unless it can be
	  undone directly and the admin chose to let those through
	- a call that cannot be taken back reaching the irreversible threshold; what
	  counts as "cannot be taken back" is :attr:`irreversible_from`
	- more items than :attr:`item_count_threshold`, all calls of the batch together,
	  since the user confirms them together
	- a call marked :func:`always_confirm`
	"""

	impulse_threshold: ImpulseRadius = DEFAULT_IMPULSE_THRESHOLD
	destructive_threshold: ImpulseRadius = DEFAULT_DESTRUCTIVE_THRESHOLD
	irreversible_threshold: ImpulseRadius = DEFAULT_IRREVERSIBLE_THRESHOLD
	irreversible_from: Reversibility = DEFAULT_IRREVERSIBLE_FROM
	relax_reversible_deletions: bool = DEFAULT_RELAX_REVERSIBLE_DELETIONS
	item_count_threshold: int = DEFAULT_ITEM_COUNT_THRESHOLD

	@property
	def count_limit(self) -> int:
		"""How far a single call needs counting: one past the bar settles it."""
		return self.item_count_threshold + 1

	def _destroys(self, call: CallClassification) -> bool:
		if not call.destroys:
			return False
		return not (self.relax_reversible_deletions and call.reversibility == Reversibility.REVERSIBLE)

	def reasons(self, calls: list[CallClassification]) -> list[str]:
		"""Why this batch has to be confirmed, one line per gate it reaches; empty
		when it can run right away.
		"""
		reasons = []
		if any(call.always for call in calls):
			reasons.append('a call is always confirmed')
		radius = max((call.radius for call in calls), default=None)
		if radius is not None and radius >= self.impulse_threshold:
			reasons.append(f'impulse radius {radius.name} reaches {self.impulse_threshold.name}')
		radius = max((call.radius for call in calls if self._destroys(call)), default=None)
		if radius is not None and radius >= self.destructive_threshold:
			reasons.append(f'a deletion reaching {radius.name} reaches {self.destructive_threshold.name}')
		radius = max((call.radius for call in calls if call.reversibility >= self.irreversible_from), default=None)
		if radius is not None and radius >= self.irreversible_threshold:
			reasons.append(f'an irreversible call reaching {radius.name} reaches {self.irreversible_threshold.name}')
		counts = [call.item_count for call in calls]
		if any(count is None for count in counts):
			reasons.append('a call touches an unknown number of items')
		elif sum(counts) > self.item_count_threshold:
			reasons.append(f'{sum(counts)} items exceed {self.item_count_threshold}')
		return reasons

	def needs_confirmation(self, calls: list[CallClassification]) -> bool:
		"""Whether this batch of calls must be confirmed by the user."""
		return bool(self.reasons(calls))
