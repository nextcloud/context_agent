# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Formatting of the admin-provided user instructions appended to the system prompt.

Run standalone:

	PYTHONPATH=. python tests/test_user_instructions.py

or via pytest when available.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# agent.py stores its signing key in the persistent storage on import
os.environ.setdefault("APP_PERSISTENT_STORAGE", tempfile.mkdtemp())

from ex_app.lib.agent import format_user_instructions  # noqa: E402
from ex_app.lib.provider import multimodal_provider, provider  # noqa: E402


def test_missing_or_blank_instructions_add_nothing():
	assert format_user_instructions(None) == ""
	assert format_user_instructions("") == ""
	assert format_user_instructions("  \n\t ") == ""


def test_instructions_are_appended_with_precedence_note():
	text = format_user_instructions("  Always answer like a pirate.\n")
	assert text.startswith("\n")
	assert text.endswith("Always answer like a pirate.\n")
	assert "unless they conflict with the instructions above" in text


def test_providers_declare_optional_input():
	for p in (provider, multimodal_provider):
		names = [shape.name for shape in p.optional_input_shape]
		assert "user_instructions" in names


if __name__ == "__main__":
	for name, fn in list(globals().items()):
		if name.startswith("test_") and callable(fn):
			fn()
			print(f"ok  {name}")
