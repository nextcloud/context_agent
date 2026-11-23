# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse, read_only
from ex_app.lib.all_tools.lib.task_processing import run_task

# Which registered task type(s) can serve each output format. The taskprocessing
# registry is the authoritative source for this: an app capability alone does
# not prove that the app's provider registered its task types on this instance.
FORMAT_TASK_TYPES = {
    "text document": ("richdocuments:text_to_text_document",),
    "pdf": ("richdocuments:text_to_text_document",),
    "spreadsheet": ("richdocuments:text_to_spreadsheet_document",),
    "excel spreadsheet": ("richdocuments:text_to_spreadsheet_document",),
	"slides": ("slideprepper:slide_deck_generation", "richdocuments:slide_deck_generation"),
}

# The slideprepper deck task answers with one file id per selected output
# format under the "slide_decks" output key, in generation order. That
# LIST_OF_FILES shape carries no per-file format metadata, so the reply
# labels can only come from what was requested: "all" delivers one
# PowerPoint, HTML and PDF file, in that order.
SLIDEPREPPER_ALL_FORMATS = ("pptx", "html", "pdf")
SLIDEPREPPER_FORMAT_LABELS = {
    "pptx": "PowerPoint (pptx)",
    "html": "HTML",
    "pdf": "PDF",
}
SLIDEPREPPER_FALLBACK_LABEL = "Slide deck"


def servable_formats(tasktypes):
    """The formats for which at least one of the required task types is registered."""
    return [
        name
        for name, task_types in FORMAT_TASK_TYPES.items()
        if any(task_type in tasktypes for task_type in task_types)
    ]


def quoted_formats(formats):
    """The formats as a quoted list: "text document", "pdf" and "slides"."""
    if len(formats) < 2:
        return f'"{formats[0]}"' if formats else ""
    return ", ".join(f'"{name}"' for name in formats[:-1]) + f' and "{formats[-1]}"'


def slideprepper_reply(
    url, task_id, deck_file_ids, formats=SLIDEPREPPER_ALL_FORMATS
) -> str:
    """Returns a relay instruction line first, then one labeled download link per deck file.

    The task reports bare file ids, so a file's format is only known from
    the one-file-per-requested-format contract: the labels are positional
    while the file count matches the requested format count. Any other
    count means the task broke that contract, and every link then gets the
    plain fallback label instead of a guessed format name (the only file
    of an HTML-only result must not be labeled "PowerPoint (pptx)").
    """
    # A missing or empty "slide_decks" leaves nothing to link, so fail
    # loudly like the task_processing output gate instead of replying "".
    if not deck_file_ids:
        raise Exception(
            'Expected "slide_decks" in the slideprepper:slide_deck_generation task result'
        )
    # The model relays this result into its reply and compresses it freely,
    # so what a compressed reply must not lose comes first: an explicit
    # instruction to keep every link.
    lines = ["The deck was generated. Include every link below in your reply."]
    labels = (
        [
            SLIDEPREPPER_FORMAT_LABELS.get(fmt, SLIDEPREPPER_FALLBACK_LABEL)
            for fmt in formats
        ]
        if len(deck_file_ids) == len(formats)
        else [SLIDEPREPPER_FALLBACK_LABEL] * len(deck_file_ids)
    )
    for label, file_id in zip(labels, deck_file_ids):
        lines.append(f"{label}: {url}/{task_id}/output-file/{file_id}/download")
    return "\n".join(lines)


async def get_tools(nc: AsyncNextcloudApp):
    tasktypes = (await nc.ocs("GET", "/ocs/v2.php/taskprocessing/tasktypes"))["types"].keys()
    available_formats = servable_formats(tasktypes)
    if not available_formats:
        # No task type on this instance can generate any of the formats.
        return []

    async def generate_document(input: str, format: str) -> str:
        if format not in available_formats:
            return (
                f'The requested format "{format}" is not available for document generation on this instance. '
                f"Available formats: {quoted_formats(available_formats)}."
            )

        url = (
            await nc.ocs(
                "GET",
                "/ocs/v2.php/apps/app_api/api/v1/info/nextcloud_url/absolute",
                json={"url": "ocs/v2.php/apps/assistant/api/v1/task"},
            )
        )["absolute_url"]

        match format:
            case "text document":
                tasktype = "richdocuments:text_to_text_document"
                task_input = {"text": input, "target_format": "docx"}

            case "pdf":
                tasktype = "richdocuments:text_to_text_document"
                task_input = {"text": input, "target_format": "pdf"}

            case "spreadsheet":
                tasktype = "richdocuments:text_to_spreadsheet_document"
                task_input = {"text": input, "target_format": "ods"}

            case "excel spreadsheet":
                tasktype = "richdocuments:text_to_spreadsheet_document"
                task_input = {"text": input, "target_format": "xlsx"}

            case "slides":
                # Prefer the slides ExApp's deck task when its type is registered
                # server-side, fall back to richdocuments' deck task otherwise.
                # The two speak different payload shapes: the slideprepper task
                # takes an "input" slot and answers with one file per output
                # format under "slide_decks", richdocuments takes "text" and
                # answers a single "slide_deck".
                tasktypes = (await nc.ocs("GET", "/ocs/v2.php/taskprocessing/tasktypes"))["types"].keys()
                if "slideprepper:slide_deck_generation" in tasktypes:
                    tasktype = "slideprepper:slide_deck_generation"
                    task_input = {
                        "input": input,
                        # Requested explicitly, not left to the task type's
                        # registered default: the reply labels are only
                        # honest when every format is generated, and that
                        # default lives in another app's registration, free
                        # to change without this one noticing.
                        "output_format": "all",
                    }
                    task = await run_task(nc, tasktype, task_input)
                    return slideprepper_reply(url, task.id, task.output.get("slide_decks"))
                else:
                    tasktype = "richdocuments:slide_deck_generation"
                    task_input = {
                        "text": input,
                    }
                task = await run_task(nc, tasktype, task_input)
                return f"{url}/{task.id}/output-file/{task.output['slide_deck']}/download"

        task = await run_task(nc, tasktype, task_input)
        return f"{url}/{task.id}/output-file/{task.output['file']}/download"

    # The docstring is the model-facing contract, so it only offers the formats
    # this instance can actually generate.
    generate_document.__doc__ = (
        f"Generate an office document based on a description of what it should contain\n"
        f":param input: the instructions for what the document should contain\n"
        f":param format: the format of the generated file, allowed values are {quoted_formats(available_formats)}\n"
        f":return: a download link to the generated document"
    )

    return [
        tool(impulse(ImpulseRadius.SELF)(read_only(generate_document))),
    ]


def get_category_name():
    return "Office document generation"


async def is_available(nc: AsyncNextcloudApp):
    tasktypes = (await nc.ocs("GET", "/ocs/v2.php/taskprocessing/tasktypes"))["types"].keys()
    return bool(servable_formats(tasktypes))
