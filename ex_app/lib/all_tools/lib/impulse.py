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

For actions that *withdraw* rather than grant access (deleting a share, removing
a team member) nobody gains anything, so they are ``SELF``. For actions that
modify an item which already has an audience (editing a team wiki page, posting
in a conversation) the radius is that existing audience -- the audience is who
the action reaches.

Radius answers who a call reaches, which says nothing about whether it takes
something away. That is the second dimension: a tool marked :func:`destructive`
deletes something, and deletions are confirmed on their own threshold, so
emptying a folder of your own files can still be worth asking about even though
it discloses nothing.

Both dimensions feed :func:`needs_confirmation`, which compares each against its
admin-configured threshold.
"""
import inspect
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


# Used when a tool carries no hook at all (MCP tools cannot be decorated) and
# when a hook fails: assume the widest reach so the user is always asked.
DEFAULT_IMPULSE_RADIUS = ImpulseRadius.EXTERNAL

DEFAULT_IMPULSE_THRESHOLD = ImpulseRadius.INDIVIDUALS

IMPULSE_THRESHOLD_SETTING_ID = 'impulse_radius_threshold'

# Deletions are confirmed from this radius on. SELF means every deletion is
# confirmed, since no call reaches less far than that.
DEFAULT_DESTRUCTIVE_THRESHOLD = ImpulseRadius.SELF

DESTRUCTIVE_THRESHOLD_SETTING_ID = 'destructive_radius_threshold'

_ATTR = 'impulse_hook'
_DESTRUCTIVE_ATTR = 'impulse_destructive'


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
	"""Mark a tool as deleting something.

	Applied to the tool function like :func:`impulse`, and independent of it: a
	deletion can reach anyone at all, from a note only the user can see to a page
	in a team wiki.

		@tool
		@impulse(ImpulseRadius.SELF)
		@destructive
		async def delete_memory(path: str): ...
	"""
	setattr(tool_func, _DESTRUCTIVE_ATTR, True)
	return tool_func


def is_destructive(tool) -> bool:
	"""Whether this tool deletes something."""
	tool_action = getattr(tool, 'coroutine', None) or getattr(tool, 'func', None)
	if tool_action is None:
		return False
	return bool(getattr(tool_action, _DESTRUCTIVE_ATTR, False))


def get_impulse_hook(tool):
	"""Return the impulse hook of a LangChain tool, or None if it carries none."""
	tool_action = getattr(tool, 'coroutine', None) or getattr(tool, 'func', None)
	if tool_action is None:
		return None
	return getattr(tool_action, _ATTR, None)


def _select_hook_kwargs(hook, tool_args: dict) -> dict:
	"""Pass only what the hook declares, unless it takes **kwargs.

	Tool arguments come from a model, so they can be incomplete or carry keys the
	hook never asked about. Filtering here keeps hooks free of defensive noise.
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
		result = hook(**_select_hook_kwargs(hook, tool_args or {}))
		if inspect.isawaitable(result):
			result = await result
		return parse_impulse_radius(result)
	except Exception as e:  # noqa: BLE001 - a hook must never break a tool call
		print(f"Impulse hook for '{getattr(tool, 'name', tool)}' failed ({e!r}), assuming {DEFAULT_IMPULSE_RADIUS.name}")
		return DEFAULT_IMPULSE_RADIUS


def needs_confirmation(
	radius: ImpulseRadius,
	threshold: ImpulseRadius,
	destroys: bool = False,
	destructive_threshold: ImpulseRadius = DEFAULT_DESTRUCTIVE_THRESHOLD,
) -> bool:
	"""Whether a call of this reach must be confirmed by the user.

	A call is confirmed when it reaches at least as far as the threshold, and a
	deletion is confirmed when it reaches at least as far as the deletion
	threshold -- which is the lower of the two bars in any sane configuration.
	"""
	if radius >= threshold:
		return True
	return destroys and radius >= destructive_threshold
