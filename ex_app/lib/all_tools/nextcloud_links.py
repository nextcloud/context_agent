# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import binascii
import re
from urllib.parse import parse_qs, unquote, urlparse

from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool

# The user-facing absolute base URL is stable for the app's lifetime; resolve it once.
_absolute_base_url: str | None = None


async def get_absolute_base_url(nc: AsyncNextcloudApp) -> str | None:
	"""
	Return the user-facing absolute base URL of the Nextcloud instance, without a trailing slash.

	``nc.app_cfg.endpoint`` may be an internal address (reverse-proxy / docker host), so
	ask app_api for the public URL. A successful lookup is cached module-wide to avoid an
	HTTP request on every conversation turn. Returns ``None`` when the public URL cannot be
	determined - nothing is cached in that case, so a later call can still pick it up.
	"""
	global _absolute_base_url
	if _absolute_base_url is None:
		try:
			absolute_url = (await nc.ocs(
				'GET', '/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute', params={'url': '/'}
			))['absolute_url'].rstrip('/')
			# app_api builds this from `overwrite.cli.url`, which may be unset - then we
			# only get back the '/' we passed in.
			if absolute_url.startswith(('http://', 'https://')):
				_absolute_base_url = absolute_url
			else:
				print(f"app_api returned no usable absolute Nextcloud URL: {absolute_url!r}")
		except Exception as e:
			print(f"Could not resolve the absolute Nextcloud URL: {e}")
	return _absolute_base_url


# Hint telling the agent which tools can act on the parsed entity.
_TOOL_HINTS = {
	'files': 'Use get_file_content_by_file_link / get_file_path_by_id with the file_id.',
	'talk': 'Use the Talk tools; resolve the token via list_talk_conversations (match on token).',
	'collectives': 'Collective pages are Markdown files; if a file_id is present use the Files tools, otherwise open the collective by name.',
	'deck': 'Use the Deck tools with board_id / card_id.',
	'mail': 'Use the Mail tools; mailbox_id maps to a folder_id, thread_id identifies the conversation.',
	'calendar': 'Use the Calendar tools; the object is identified by calendar + object href/token.',
	'bookmarks': 'Use the Bookmarks tools; filter by folder_id if present.',
	'cookbook': 'Use get_recipe_details with recipe_id.',
	'forms': 'Call list_forms and match its `hash` field to map a form hash to a form_id; a share_hash does not appear there.',
	'tables': 'Use the Tables tools with table_id (or list rows of the view_id).',
}


def _clean_path(path: str) -> str:
	"""Strip the optional /index.php segment from a Nextcloud path."""
	return re.sub(r'/index\.php(?=/|$)', '', path or '')


def _int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return value


def _decode_calendar_object(object_id: str) -> dict:
	"""
	Decode a Calendar app ``object`` param into its CalDAV path parts.

	The param is ``base64("/remote.php/dav/calendars/<user>/<calendar-uri>/<uid>.ics")``.
	Returns a dict with ``dav_path``, ``calendar_uri`` and ``event_uid`` (best effort),
	or ``{}`` when it does not decode to a plausible path. The raw ``object_id`` is kept
	by the caller so nothing is lost when decoding fails.
	"""
	if not object_id:
		return {}
	padded = object_id + '=' * (-len(object_id) % 4)
	try:
		path = base64.b64decode(padded, validate=True).decode('utf-8')
	except (binascii.Error, UnicodeDecodeError, ValueError):
		return {}
	# A real object path is absolute and points at a .ics file; bail out otherwise.
	if '/' not in path or not path.isprintable():
		return {}
	calendar_path, _, filename = path.rpartition('/')
	calendar_uri = calendar_path.rsplit('/', 1)[-1] or None
	decoded = {'dav_path': path}
	if calendar_uri:
		decoded['calendar_uri'] = calendar_uri
	if filename:
		decoded['event_uid'] = filename[:-4] if filename.endswith('.ics') else filename
	return decoded


def _same_host(url: str, base_url: str) -> bool:
	"""Whether ``url`` is relative or points at the same host as ``base_url``."""
	def host(value):
		return urlparse(value.strip()).netloc.rsplit('@', 1)[-1].lower()
	return not host(url) or host(url) == host(base_url)


def _first(query: dict, *keys):
	"""Return the first value of the first present key in a parsed query string."""
	for key in keys:
		if key in query and query[key]:
			return query[key][0]
	return None


def _parse_nextcloud_url(url: str) -> dict:
	"""
	Parse a Nextcloud deep-link URL into its app, entity type and identifiers.

	Returns a dict with at least ``app`` and ``entity_type`` and, when found, an
	``ids`` mapping. ``entity_type`` is ``None`` (and app may be ``unknown``) when
	the URL does not match any known Nextcloud app route.
	"""
	if not isinstance(url, str) or not url.strip():
		raise ValueError('A non-empty URL string is required')

	parsed = urlparse(url.strip())
	path = _clean_path(unquote(parsed.path or ''))
	fragment = unquote(parsed.fragment or '')
	query = parse_qs(parsed.query or '')
	# Hash-router apps (deck, tables, cookbook, ...) carry the real route in the
	# fragment, e.g. /apps/tables#/table/3 . Search path and fragment together.
	hay = f'{path}#{fragment}'

	result = {'app': 'unknown', 'entity_type': None, 'ids': {}, 'url': url}

	def done(app, entity_type, ids=None, **extra):
		result.update(app=app, entity_type=entity_type, ids={k: v for k, v in (ids or {}).items() if v is not None})
		result.update({k: v for k, v in extra.items() if v is not None})
		hint = _TOOL_HINTS.get(app)
		if hint:
			result['hint'] = hint
		return result

	# --- Files (files) ------------------------------------------------------
	# https://host/f/123  |  /index.php/f/123
	# https://host/apps/files/files/123?dir=/&editing=false&openfile=true
	# https://host/apps/files/?dir=/path&fileid=123
	m = re.search(r'/f/(\d+)/?$', path)
	if m:
		return done('files', 'file', {'file_id': _int(m.group(1))})
	m = re.search(r'/apps/files/files/(\d+)', path)
	if m:
		return done('files', 'file', {'file_id': _int(m.group(1))})
	# openfile is a boolean flag (openfile=true), not an id
	file_id = _first(query, 'fileid', 'fileId')

	# public share of a file/folder: /s/{token}  (optionally /s/{token}/download etc.)
	# Other apps have their own /s/{hash} routes below /apps/, don't claim those.
	m = re.search(r'/s/([A-Za-z0-9_-]+)(?:/|$)', path)
	if m and '/apps/' not in path:
		return done('files', 'public_share', {'token': m.group(1)})

	# --- Talk (spreed) ------------------------------------------------------
	# /call/{token}  optionally  #message_{id}
	m = re.search(r'/(?:apps/spreed/)?call/([A-Za-z0-9]+)', path)
	if m:
		msg = re.search(r'message_(\d+)', fragment)
		return done('talk', 'conversation', {'token': m.group(1)},
					message_id=_int(msg.group(1)) if msg else None)

	# --- App-scoped routes: /apps/{app}/... --------------------------------
	# Not anchored: Nextcloud may be installed in a webroot subdirectory
	# (https://host/nextcloud/apps/deck/...).
	app_match = re.search(r'/apps/([^/?#]+)(/.*)?$', path)
	app = app_match.group(1) if app_match else None
	app_rest = (app_match.group(2) if app_match else '') or ''
	scope = f'{app_rest}#{fragment}'  # everything after the app name

	if app == 'collectives':
		# Routes (relative to /apps/collectives), see collectives/src/router.js:
		#   /{collective}[/{page...}]                    authenticated
		#   /p/{token}/{collective}[/{page...}]          public share
		#   /_/print/{collective} , /p/{token}/print/... print views (stripped)
		# {collective} and each {page} segment is either a verbatim encodeURIComponent'd
		# name/title or the modern "{slug}-{id}" form; the trailing {id} is authoritative
		# (for a page it equals its file id). Work on the still-encoded path so an encoded
		# slash inside a title does not split it, then unquote each segment.
		raw_collectives = re.search(r'/apps/collectives(/.*)?$', _clean_path(parsed.path or ''))
		raw_rest = (raw_collectives.group(1) if raw_collectives else '') or ''
		token = None
		m = re.match(r'^/p/([^/]+)(/.*)?$', raw_rest)
		if m:
			token = unquote(m.group(1))
			raw_rest = m.group(2) or ''
		# print prefix: /_/print (authenticated) or /print (public, after the token)
		raw_rest = re.sub(r'^/(?:_/)?print(?=/|$)', '', raw_rest)
		segments = [unquote(s) for s in raw_rest.split('/') if s]

		ids = {'token': token}
		if segments:
			ids['collective'] = segments[0]
			m = re.match(r'^(.+)-(\d+)$', segments[0])
			if m:
				ids['collective_id'] = _int(m.group(2))
		page_segs = segments[1:]
		page_id = None
		if page_segs:
			ids['page_path'] = '/'.join(page_segs)
			m = re.match(r'^(.+)-(\d+)$', page_segs[-1])
			page_id = _int(m.group(2)) if m else None
		# a collectives page is a file: its id (from ?fileId= or the "{slug}-{id}" page
		# segment) is the file_id usable with the Files tools.
		ids['file_id'] = _int(file_id) if file_id else page_id

		entity_type = 'page' if page_segs else 'collective' if segments else ('public_share' if token else None)
		return done('collectives', entity_type, ids)

	if app == 'deck':
		# /board/{id}[/card/{id}] , plus the legacy /boards/{id}[/cards/{id}] and
		# /!/board/{id} aliases that the deck router redirects.
		board = re.search(r'boards?/(\d+)', scope)
		card = re.search(r'cards?/(\d+)', scope)
		if card and board:
			return done('deck', 'card', {'board_id': _int(board.group(1)), 'card_id': _int(card.group(1))})
		if card:
			return done('deck', 'card', {'card_id': _int(card.group(1))})
		if board:
			return done('deck', 'board', {'board_id': _int(board.group(1))})
		return done('deck', None)

	if app == 'mail':
		# an optional filter segment may sit between box/ and the id, e.g. box/starred/345
		box = re.search(r'box/(?:[a-zA-Z][^/]*/)?(\d+)', scope)
		thread = re.search(r'thread/(\d+)', scope)
		if thread:
			return done('mail', 'thread', {'mailbox_id': _int(box.group(1)) if box else None,
											'thread_id': _int(thread.group(1))})
		if box:
			return done('mail', 'mailbox', {'mailbox_id': _int(box.group(1))})
		return done('mail', None)

	_CAL_OBJECT_NOTE = ('object_id is base64("/remote.php/dav/calendars/<user>/<calendar>/<uid>.ics"), '
						'also decoded into dav_path, calendar_uri and event_uid. Match calendar_uri / '
						'event_uid against list_calendars / search_calendar_events (calendar_uri is the URL '
						'slug and may differ from the display name). recurrence_id is a unix timestamp '
						'(seconds) or the literal "next".')
	if app == 'calendar':
		# The base64 {object} param may contain '/' and '+', which arrive percent-encoded
		# (%2F, %2B) in the real URL. Match against the still-encoded path so a '/' inside
		# the object does not truncate it, then unquote only the captured object segment.
		raw_scope = f'{_clean_path(parsed.path or "")}#{parsed.fragment or ""}'
		# public share / embed: /p/{tokens}/... , /public/{tokens}/... , /embed/{tokens}/...
		# {tokens} may be several share tokens joined with '-'.
		# Anchored: the base64 {object} of an event link may itself contain '/p/'.
		share = re.match(r'^/(p|public|embed)/([^/?#]+)', scope)
		if share:
			ids = {'token': share.group(2)}
			# optional event object: .../view/{popover|full}/{object}/{recurrenceId}
			ev = re.search(r'/view/(?:popover|full)/([^/?#]+)(?:/([^/?#]+))?', raw_scope)
			if ev:
				object_id = unquote(ev.group(1))
				ids['object_id'] = object_id
				ids['recurrence_id'] = ev.group(2)
				ids.update(_decode_calendar_object(object_id))
			extra = {'embed': True} if share.group(1) == 'embed' else {}
			return done('calendar', 'public_share', ids,
						note=_CAL_OBJECT_NOTE if ev else None, **extra)
		# event editor: .../{view}/{firstDay}/edit/{popover|full|sidebar}/{object}/{recurrenceId}
		# or the short redirect link: /edit/{object}[/{recurrenceId}]
		obj = re.search(r'/edit/(?:popover|full|sidebar)/([^/?#]+)(?:/([^/?#]+))?', raw_scope) \
			or re.search(r'/edit/([^/?#]+)(?:/([^/?#]+))?', raw_scope)
		if obj:
			object_id = unquote(obj.group(1))
			ids = {'object_id': object_id, 'recurrence_id': obj.group(2)}
			ids.update(_decode_calendar_object(object_id))
			return done('calendar', 'event', ids, note=_CAL_OBJECT_NOTE)
		# plain calendar view: /{view}/{firstDay}  (firstDay is 'now' or an ISO date)
		date = re.search(r'/(\d{4}-\d{2}-\d{2})', app_rest)
		view = re.match(r'^/([a-zA-Z]+)', app_rest)
		return done('calendar', 'view', {'view': view.group(1) if view else None,
										'date': date.group(1) if date else None})

	if app == 'bookmarks':
		folder_match = re.search(r'/folders?/(\d+)', scope)
		folder = _first(query, 'folder') or (folder_match.group(1) if folder_match else None)
		token = re.search(r'/public/([^/?#]+)', scope)
		return done('bookmarks', 'public_share' if token else 'folder' if folder else 'app',
					{'folder_id': _int(folder) if folder else None,
						'token': token.group(1) if token else None})

	if app == 'cookbook':
		recipe = re.search(r'recipe/(\d+)', scope)
		if recipe:
			return done('cookbook', 'recipe', {'recipe_id': _int(recipe.group(1))})
		category = re.search(r'category/([^/?#]+)', scope)
		if category:
			return done('cookbook', 'category', {'category': category.group(1)})
		return done('cookbook', None)

	if app == 'forms':
		# /apps/forms/{hash} (fill) | /apps/forms/{hash}/{edit,results,submit}
		# public link: /apps/forms/s/{share_hash} | embedded: /apps/forms/embed/{share_hash}
		m = re.match(r'^/(s|embed)/([^/?#]+)', app_rest)
		if m:
			return done('forms', 'public_share', {'share_hash': m.group(2)},
						note='share_hash identifies the public share, not the form itself.',
						**({'embed': True} if m.group(1) == 'embed' else {}))
		m = re.match(r'^/([^/?#]+)(?:/(edit|results|submit))?', app_rest)
		if m:
			return done('forms', m.group(2) or 'form', {'hash': m.group(1)})
		return done('forms', None)

	if app == 'tables':
		view = re.search(r'view/(\d+)', scope)
		table = re.search(r'table/(\d+)', scope)
		if table:
			return done('tables', 'table', {'table_id': _int(table.group(1))})
		if view:
			return done('tables', 'view', {'view_id': _int(view.group(1))})
		return done('tables', None)

	# --- Fallbacks ----------------------------------------------------------
	if file_id:
		return done('files', 'file', {'file_id': _int(file_id)})
	if app:
		return done(app, None)
	return result


async def get_tools(nc: AsyncNextcloudApp):

	@tool
	@safe_tool
	async def parse_nextcloud_url(url: str):
		"""
		Parse a Nextcloud deep-link URL and extract which app it belongs to, the
		entity type it points at, and the identifiers needed to fetch that entity
		with the matching tools. Use this to turn a link a user pasted (e.g. a Talk
		room, a Deck card, a Collectives page, a Tables table, a recipe, ...) into
		concrete ids before calling the relevant app tools.

		Supports the Files, Talk (spreed), Collectives, Deck, Mail, Calendar,
		Bookmarks, Cookbook, Forms and Tables apps.
		:param url: a Nextcloud URL (with or without the /index.php prefix)
		:return: a dict with keys `app`, `entity_type`, `ids`, and a `hint` on which
			tools to use. `entity_type` is null when the URL is not a recognized route.
		"""
		result = _parse_nextcloud_url(url)
		# Only known-foreign hosts are flagged: without a confirmed public URL we cannot
		# tell an external link from a legitimate one and stay quiet instead.
		base_url = await get_absolute_base_url(nc)
		if base_url and not _same_host(url, base_url):
			result['warning'] = (f'This URL is not on this Nextcloud instance ({base_url}). The ids below '
									'were read off the URL shape alone and may denote unrelated entities here. '
									'Do not look them up without checking back with the user.')
		return result

	return [parse_nextcloud_url]


def get_category_name():
	return "Nextcloud Links"


async def is_available(nc: AsyncNextcloudApp):
	return True
