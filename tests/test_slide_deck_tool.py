# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hermetic tests for the slide_deck tool module.

Everything runs against a fake AsyncNextcloudApp that records ocs() calls and
serves canned task-processing responses: no network, no Nextcloud server, and
no environment outside this worktree. The repo-wide ruff config selects S101
(no assert statements) without a test file exemption, and the shared config
must not be edited for this tool, so the checks below raise AssertionError
from small helpers instead of using the assert keyword.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ex_app.lib.all_tools import slide_deck  # noqa: E402


class FakeNextcloudApp:
    """Records ocs() calls and answers the endpoints the module touches."""

    def __init__(self, tasktypes=None, nextcloud_url="https://nc.example.test", task_id=42, file_id=1337):
        self.tasktypes = dict(tasktypes or {})
        self.nextcloud_url = nextcloud_url
        self.task_id = task_id
        self.file_id = file_id
        self.ocs_calls = []

    async def ocs(self, method, endpoint, json=None, **_kwargs):
        self.ocs_calls.append((method, endpoint, json))
        if endpoint == "/ocs/v2.php/taskprocessing/tasktypes":
            return {"types": self.tasktypes}
        if endpoint == "/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute":
            # real endpoint returns a dict (app_api OCSApiController), not a bare string
            return {"absolute_url": self.nextcloud_url}
        if endpoint == "/ocs/v1.php/taskprocessing/schedule":
            return {
                "task": {
                    "id": self.task_id,
                    "status": "STATUS_SUCCESSFUL",
                    "output": {"slide_deck": self.file_id},
                }
            }
        raise AssertionError(f"unexpected ocs call: {method} {endpoint}")

    async def log(self, level, content):
        """Sink for run_task's debug logging."""

    def schedule_calls(self):
        return [call for call in self.ocs_calls if call[1] == "/ocs/v1.php/taskprocessing/schedule"]


def expect_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


async def call_slide_deck(nc, **tool_args):
    tools = await slide_deck.get_tools(nc)
    expect_equal([t.name for t in tools], ["create_slide_deck"], "tool names")
    return await tools[0].ainvoke(tool_args)


def test_schedules_brief_task_with_marp_task_type():
    nc = FakeNextcloudApp()
    brief = "A deck about tending bonsai for beginners, 8 slides, calm tone, German"
    asyncio.run(call_slide_deck(nc, prompt=brief, title="Bonsai basics"))

    expect_equal(len(nc.schedule_calls()), 1, "number of schedule calls")
    (schedule,) = nc.schedule_calls()
    method, endpoint, payload = schedule
    expect_equal(method, "POST", "schedule method")
    expect_equal(endpoint, "/ocs/v1.php/taskprocessing/schedule", "schedule endpoint")
    expect_equal(payload["type"], "marp_slides:slide_deck_generation", "task type id")
    expect_equal(payload["appId"], "context_agent", "scheduling app id")
    expect_equal(
        payload["input"],
        {
            "prompt": f"Deck title: Bonsai basics\n\n{brief}",
            "output_format": "pptx",
        },
        "task input payload",
    )


def test_builds_download_link_from_slide_deck_output():
    nc = FakeNextcloudApp(task_id=57, file_id=909)
    link = asyncio.run(call_slide_deck(nc, prompt="Topic", title="T"))
    expect_equal(link, "https://nc.example.test/57/output-file/909/download", "download link")


def test_default_title_rides_in_the_brief():
    nc = FakeNextcloudApp()
    asyncio.run(call_slide_deck(nc, prompt="Topic"))
    ((_, _, payload),) = nc.schedule_calls()
    expect_equal(payload["input"]["prompt"].splitlines()[0], "Deck title: Slide deck", "default title line")
    expect_equal(payload["input"]["output_format"], "pptx", "output format with default title")


def test_is_available_true_when_task_type_registered():
    nc = FakeNextcloudApp(tasktypes={"marp_slides:slide_deck_generation": {}, "core:text2text": {}})
    expect_equal(asyncio.run(slide_deck.is_available(nc)), True, "availability with the ExApp registered")


def test_is_available_false_without_task_type():
    nc = FakeNextcloudApp(tasktypes={"core:text2text": {}})
    expect_equal(asyncio.run(slide_deck.is_available(nc)), False, "availability without the ExApp")
