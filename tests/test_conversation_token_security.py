# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing of conversation tokens and the types their deserialization may revive.

Run standalone:

	PYTHONPATH=. python tests/test_conversation_token_security.py

or via pytest when available.
"""
import json
import os
import stat
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ormsgpack  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from ex_app.lib.jsonplus import (  # noqa: E402
	EXT_CONSTRUCTOR_POS_ARGS,
	EXT_METHOD_SINGLE_ARG,
	EXT_PYDANTIC_V2,
	JsonPlusSerializer,
	_msgpack_enc,
)
from ex_app.lib.signature import (  # noqa: E402
	TOKEN_PREFIX,
	add_signature,
	create_legacy_hash,
	load_legacy_key,
	load_or_create_key,
	verify_signature,
)

KEY = 'a' * 128
LEGACY_KEY = 'b' * 256
PAYLOAD = '{"last_config": {}}'


def raises(fn, *args):
	try:
		fn(*args)
	except Exception:
		return True
	return False


def test_token_round_trips():
	token = add_signature(PAYLOAD, KEY)
	assert token.startswith(TOKEN_PREFIX)
	assert verify_signature(token, KEY) == PAYLOAD


def test_tampered_token_is_rejected():
	token = add_signature(PAYLOAD, KEY)
	assert raises(verify_signature, token[:-1] + ' ', KEY)
	assert raises(verify_signature, token, 'c' * 128)
	assert raises(verify_signature, TOKEN_PREFIX + '0' * 128 + PAYLOAD, KEY)
	assert raises(verify_signature, TOKEN_PREFIX, KEY)


def test_legacy_token_needs_legacy_key():
	token = create_legacy_hash(PAYLOAD, LEGACY_KEY) + PAYLOAD
	assert verify_signature(token, KEY, LEGACY_KEY) == PAYLOAD
	assert raises(verify_signature, token, KEY)
	assert raises(verify_signature, token, KEY, 'c' * 256)
	# a new token can not be passed off as a legacy one or the other way round
	assert raises(verify_signature, add_signature(PAYLOAD, KEY)[len(TOKEN_PREFIX):], KEY, LEGACY_KEY)
	assert raises(verify_signature, TOKEN_PREFIX + token, KEY, LEGACY_KEY)


def test_key_is_created_once_and_private():
	with tempfile.TemporaryDirectory() as tmp:
		path = os.path.join(tmp, 'key.txt')
		key = load_or_create_key(path)
		assert len(key) == 128 and int(key, 16) > 0
		assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
		assert load_or_create_key(path) == key
		assert os.listdir(tmp) == ['key.txt']


def test_key_is_created_without_hard_links():
	with tempfile.TemporaryDirectory() as tmp, \
			mock.patch('os.link', side_effect=PermissionError(1, 'Operation not permitted')):
		path = os.path.join(tmp, 'key.txt')
		key = load_or_create_key(path)
		assert len(key) == 128 and int(key, 16) > 0
		assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
		assert load_or_create_key(path) == key
		assert os.listdir(tmp) == ['key.txt']


def test_key_created_concurrently_without_hard_links_is_shared():
	with tempfile.TemporaryDirectory() as tmp, \
			mock.patch('os.link', side_effect=PermissionError(1, 'Operation not permitted')):
		path = os.path.join(tmp, 'key.txt')
		with open(path, 'w') as f:
			f.write(KEY)
		# another process created the key after this one checked for it
		with mock.patch('os.path.exists', return_value=False):
			assert load_or_create_key(path) == KEY
		assert os.listdir(tmp) == ['key.txt']


def test_corrupt_key_is_refused():
	with tempfile.TemporaryDirectory() as tmp:
		path = os.path.join(tmp, 'key.txt')
		for content in ('', 'abc', 'g' * 128):
			with open(path, 'w') as f:
				f.write(content)
			assert raises(load_or_create_key, path)


def test_key_written_by_hand_is_accepted():
	with tempfile.TemporaryDirectory() as tmp:
		path = os.path.join(tmp, 'key.txt')
		with open(path, 'w') as f:
			f.write(KEY + '\n')
		assert load_or_create_key(path) == KEY


def test_corrupt_legacy_key_is_ignored():
	with tempfile.TemporaryDirectory() as tmp:
		path = os.path.join(tmp, 'secret_key.txt')
		assert load_legacy_key(path) is None
		for content in ('', LEGACY_KEY[:100]):
			with open(path, 'w') as f:
				f.write(content)
			assert load_legacy_key(path) is None
		with open(path, 'w') as f:
			f.write(LEGACY_KEY)
		assert load_legacy_key(path) == LEGACY_KEY


def test_json_revives_only_safe_types():
	serde = JsonPlusSerializer()
	evil = [
		{'lc': 2, 'type': 'constructor', 'id': ['os', 'system'], 'args': ['true']},
		{'lc': 2, 'type': 'constructor', 'id': ['builtins', 'eval'], 'args': ['1']},
		{'lc': 2, 'type': 'constructor', 'id': ['subprocess', 'Popen'], 'args': [['true']]},
		# a safe type with a method that is not allowed
		{'lc': 2, 'type': 'constructor', 'id': ['builtins', 'bytes'], 'method': 'maketrans', 'args': ['a', 'b']},
		{'lc': 2, 'type': 'constructor', 'id': ['pathlib', 'Path'], 'method': [None, 'write_text'], 'args': ['x']},
	]
	for value in evil:
		assert serde.loads(json.dumps(value).encode()) is None, value
	safe = {'lc': 2, 'type': 'constructor', 'id': ['builtins', 'bytes'], 'method': 'fromhex', 'args': ['6869']}
	assert serde.loads(json.dumps(safe).encode()) == b'hi'


def test_msgpack_revives_only_safe_types():
	serde = JsonPlusSerializer()
	evil = [
		ormsgpack.Ext(EXT_CONSTRUCTOR_POS_ARGS, _msgpack_enc(('os', 'system', ('true',)))),
		ormsgpack.Ext(EXT_METHOD_SINGLE_ARG, _msgpack_enc(('builtins', 'bytes', 'a', 'maketrans'))),
		ormsgpack.Ext(EXT_METHOD_SINGLE_ARG, _msgpack_enc(('os', 'system', 'true', '__call__'))),
	]
	for ext in evil:
		assert serde.loads_typed(('msgpack', ormsgpack.packb(ext))) is None
	# a pydantic model of a type that is not allowed stays a plain dict
	ext = ormsgpack.Ext(EXT_PYDANTIC_V2, _msgpack_enc(('os', 'system', {'a': 1}, 'model_validate_json')))
	assert serde.loads_typed(('msgpack', ormsgpack.packb(ext))) == {'a': 1}
	message = AIMessage(content='hi')
	assert serde.loads_typed(serde.dumps_typed(message)) == message


if __name__ == "__main__":
	failures = 0
	for name, fn in list(globals().items()):
		if not name.startswith("test_") or not callable(fn):
			continue
		try:
			fn()
			print(f"ok    {name}")
		except Exception as e:
			failures += 1
			print(f"FAIL  {name}: {e!r}")
	raise SystemExit(1 if failures else 0)
