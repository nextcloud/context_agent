# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import hmac
import os
import secrets
import tempfile
import time

# Tokens signed with HMAC-SHA512 start with this prefix, followed by the hex signature and the payload
TOKEN_PREFIX = 'v2:'
SIGNATURE_LENGTH = 128
# Size of the HMAC key in bytes, stored hex encoded
KEY_BYTES = 64
# The legacy key was always generated with 256 characters
LEGACY_KEY_LENGTH = 256

def create_hash(input_string: str, key: str):
	"""
	Create an HMAC-SHA512 of the input string.

	:param input_string: The string to sign
	:param key: the secret key
	:return: The HMAC as a hexadecimal string
	"""
	return hmac.new(key.encode('utf-8'), input_string.encode('utf-8'), hashlib.sha512).hexdigest()

def create_legacy_hash(input_string: str, key: str):
	"""
	Create the SHA-512 hash of the input string followed by the key, as signed by releases before HMAC was used
	"""
	return hashlib.sha512((input_string + key).encode('utf-8')).hexdigest()

def add_signature(input_string: str, key: str):
	return TOKEN_PREFIX + create_hash(input_string, key) + input_string

def verify_signature(input_string: str, key: str, legacy_key: str | None = None):
	"""
	Verify a signed token and return its payload

	:param input_string: The signed token
	:param key: the key tokens are signed with
	:param legacy_key: the key of tokens from before HMAC was used, these are rejected if it is None
	"""
	if input_string.startswith(TOKEN_PREFIX):
		signed = input_string[len(TOKEN_PREFIX):]
		expected_hash = create_hash(signed[SIGNATURE_LENGTH:], key)
	elif legacy_key is not None:
		signed = input_string
		expected_hash = create_legacy_hash(signed[SIGNATURE_LENGTH:], legacy_key)
	else:
		raise Exception("Signature verification failed")
	if not hmac.compare_digest(signed[:SIGNATURE_LENGTH].encode('utf-8'), expected_hash.encode('utf-8')):
		raise Exception("Signature verification failed")
	return signed[SIGNATURE_LENGTH:]

def _read_key(path: str):
	with open(path, 'r') as file:
		# tolerate a trailing newline from writing the key by hand
		key = file.read().strip()
	if len(key) != KEY_BYTES * 2 or any(c not in '0123456789abcdef' for c in key):
		raise RuntimeError(f"The signing key in '{path}' is corrupt, delete it to generate a new one (this invalidates all conversation tokens)")
	return key

def load_or_create_key(path: str):
	"""
	Read the signing key from path, generating it first if it does not exist yet

	The key is written to a temporary file and linked into place only once complete,
	so a crash never leaves a partial key behind and concurrent starts agree on one key.
	On filesystems without hard links the key file is created exclusively instead.
	"""
	if os.path.exists(path):
		return _read_key(path)
	key = secrets.token_hex(KEY_BYTES)
	directory = os.path.dirname(path) or '.'
	# mkstemp creates the file with mode 0600
	fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.signing_key.')
	try:
		_write_key(fd, key)
		try:
			os.link(tmp_path, path)
			created = True
		except FileExistsError:
			# another process created the key in the meantime
			created = False
		except OSError:
			# the filesystem does not support hard links
			created = _create_key_exclusively(path, key)
	finally:
		os.unlink(tmp_path)
	if created:
		print(f"The file '{path}' has been created.")
		return _read_key(path)
	return _wait_for_key(path)

def _write_key(fd: int, key: str):
	with os.fdopen(fd, 'w') as file:
		file.write(key)
		file.flush()
		os.fsync(file.fileno())

def _create_key_exclusively(path: str, key: str):
	"""
	Create the key file only if it does not exist yet, returning whether this process created it
	"""
	try:
		fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
	except FileExistsError:
		return False
	_write_key(fd, key)
	return True

def _wait_for_key(path: str, attempts: int = 10):
	"""
	Read a key that another process created, giving it time to finish writing it
	"""
	for _ in range(attempts - 1):
		try:
			return _read_key(path)
		except RuntimeError:
			time.sleep(0.1)
	return _read_key(path)

def load_legacy_key(path: str):
	"""
	Read the key that releases before HMAC signed tokens with, if there is a usable one
	"""
	try:
		with open(path, 'r') as file:
			key = file.read()
	except FileNotFoundError:
		return None
	if len(key) != LEGACY_KEY_LENGTH:
		# a truncated or empty key would make the signatures forgeable
		print(f"Ignoring the legacy key in '{path}' as it is corrupt, old conversation tokens are rejected.")
		return None
	return key
