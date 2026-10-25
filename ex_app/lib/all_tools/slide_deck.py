# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Slide deck generation through the marp_slides ExApp."""
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse
from ex_app.lib.all_tools.lib.task_processing import run_task

TASK_TYPE_ID = "marp_slides:slide_deck_generation"


async def get_tools(nc: AsyncNextcloudApp):

    @tool
    @impulse(ImpulseRadius.SELF)
    async def create_slide_deck(prompt: str, title: str = "Slide deck") -> str:
        """Create a presentation as a PPTX slide deck from a deck brief.

        The deck itself is written by the presentation tool's own AI, so pass a
        brief and not slides, and no Marp or Markdown. Expand the user's request
        into a self-contained brief: the topic and what it should cover, the
        audience, the desired number of slides when the user indicated one, the
        tone, and the language the user writes in, which is also the language
        the deck and your reply should be in. Derive a short file title that
        names the deck's subject instead of a generic one.
        :param prompt: the deck brief: topic, audience, desired slide count if known, tone, language
        :param title: a good file title for the generated deck, derived from the user's request
        :return: a download link to the generated presentation
        """
        url = (
            await nc.ocs(
                "GET",
                "/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute",
                json={"url": "ocs/v2.php/apps/assistant/api/v1/task"},
            )
        )["absolute_url"]

        # The task input shape has no title slot, so the title rides at the top
        # of the brief: the provider's AI names the deck and its file from it.
        task = await run_task(
            nc,
            TASK_TYPE_ID,
            {
                "prompt": f"Deck title: {title}\n\n{prompt}",
                "output_format": "pptx",
            },
        )
        return f"{url}/{task.id}/output-file/{task.output['slide_deck']}/download"

    return [
        create_slide_deck,
    ]


def get_category_name():
    return "Slide deck generation"


async def is_available(nc: AsyncNextcloudApp):
    tasktypes = (await nc.ocs("GET", "/ocs/v2.php/taskprocessing/tasktypes"))["types"].keys()
    return TASK_TYPE_ID in tasktypes
