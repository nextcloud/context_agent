# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib

def create_hash(input_string: str, key: str):
	"""
	Create a SHA-512 hash of the input string.

	:param input_string: The string to hash
	:param key: the key to add to the hash
	:return: The SHA-512 hash as a hexadecimal string
	"""
	# Encode the input string and key to bytes
	encoded_string = (input_string + key).encode('utf-8')

	# Create a SHA-512 hash object
	sha512_hash = hashlib.sha512(encoded_string)

	# Return the hexadecimal representation of the hash
	return sha512_hash.hexdigest()

def add_signature(input_string: str, key: str):
	return create_hash(input_string, key) + input_string

def verify_signature(input_string: str, key: str):
	original_hash = input_string[:128]
	created_hash = create_hash(input_string[128:], key)
	if original_hash != created_hash:
		raise Exception("Signature verification failed")
	return input_string[128:]