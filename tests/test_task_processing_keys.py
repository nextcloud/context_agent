# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Output-key whitelist of run_task: which task output keys are accepted.

Run standalone:

    PYTHONPATH=. python tests/test_task_processing_keys.py

or via pytest when available. The network layer is stubbed: the fake app
serves one already-successful task, so run_task finishes after a single
scheduling call and reaches the output-key check under test.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ex_app.lib.all_tools.lib.task_processing import run_task  # noqa: E402


class _FakeNextcloudApp:
    """Serves one immediately-successful scheduled task with the given output dict."""

    def __init__(self, output):
        self.output = output

    async def log(self, level, content):
        pass

    async def ocs(self, method, path, json=None):
        return {
            "task": {
                "id": 7,
                "status": "STATUS_SUCCESSFUL",
                "output": self.output,
            }
        }


def _run_task_with_output(output):
    nc = _FakeNextcloudApp(output)
    return asyncio.run(run_task(nc, "richdocuments:slide_deck_generation", {"text": "a deck"}))


def test_accepted_output_keys_pass():
    for key in ("file", "output", "images", "slide_deck", "sources"):
        task = _run_task_with_output({key: 42})
        assert task.output == {key: 42}, key


def test_slide_deck_source_passes_alone():
    # before the whitelist learned this key, an output carrying only it was rejected
    task = _run_task_with_output({"slide_deck_source": 43})
    assert task.output == {"slide_deck_source": 43}


def test_slide_deck_source_passes_alongside_slide_deck():
    task = _run_task_with_output({"slide_deck": 1, "slide_deck_source": 2})
    assert task.output == {"slide_deck": 1, "slide_deck_source": 2}


def test_unknown_output_key_still_raises():
    try:
        _run_task_with_output({"definitely_not_an_output_key": 1})
    except Exception as exc:
        assert "Expected one of" in str(exc)
        return
    raise AssertionError("expected run_task to reject an output with no accepted key")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failed else 0)
