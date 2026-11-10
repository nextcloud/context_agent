# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import LogLvl

from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, impulse, read_only
from ex_app.lib.all_tools.lib.task_processing import run_task
from ex_app.lib.attachments import current_input_attachments
from ex_app.lib.logger import log

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

# The deck task's image rules, mirrored from the marp deck task's "images"
# slot: PNG or JPEG, at most 5 MB each, no SVG. A file the deck could never
# place must not ride into it, because the deck's own AI is told a count of
# placeable images and weaves every one of them into the deck.
DECK_IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/webp")
DECK_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# The marp deck task answers with one file id per selected output format
# under the "slide_decks" output key, in generation order: the default
# "all" delivers PowerPoint, HTML and PDF. The links are labeled in that
# documented order; a file beyond the known formats gets the plain
# fallback label instead of a guessed one.
DECK_OUTPUT_LABELS = ("PowerPoint (pptx)", "HTML", "PDF")
DECK_FALLBACK_LABEL = "Slide deck"


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


def attachment_count_phrase(count):
    """Build "2 attached images" or "1 attached image" for a count."""
    return f"{count} attached image{'' if count == 1 else 's'}"


def deck_images_note(images, unforwarded) -> str | None:
    """State how many attached images the deck holds, and why any is missing.

    With omissions the placed count is stated against the image-candidate
    total ("1 of the 3 attached images"), so a relaying model cannot
    mistake it for the number of attached images; the reason appears once,
    in the omission sentence.
    """
    if not images and not unforwarded:
        return None
    sentences = []
    if images:
        verb = "was" if len(images) == 1 else "were"
        count = (
            f"{len(images)} of the {len(images) + unforwarded} attached images"
            if unforwarded
            else attachment_count_phrase(len(images))
        )
        sentences.append(f"{count} {verb} included in the deck.")
    if unforwarded:
        sentences.append(
            f"{attachment_count_phrase(unforwarded)} could not be included"
            " (only PNG, JPEG and WebP images up to 5 MB each can be"
            " placed in a deck)."
        )
    return " ".join(sentences)


def deck_reply(url, task_id, deck_file_ids, note=None) -> str:
    """A relay instruction line first, then one labeled download link per deck file."""
    # A missing or empty "slide_decks" leaves nothing to link, so fail
    # loudly like the task_processing output gate instead of replying "".
    if not deck_file_ids:
        raise Exception(
            'Expected "slide_decks" in the slideprepper:slide_deck_generation task result'
        )
    # The model relays this result into its reply and compresses it freely,
    # so what a compressed reply must not lose comes first: the image count,
    # with an explicit instruction to keep that count and every link.
    if note:
        lead = f"{note} Include this image count and every link below in your reply."
    else:
        lead = "The deck was generated. Include every link below in your reply."
    # One file id per selected output format, in the task's documented
    # generation order: label each link by that order, with the plain
    # fallback label should a narrower or wider selection answer.
    lines = [lead]
    for index, file_id in enumerate(deck_file_ids):
        label = (
            DECK_OUTPUT_LABELS[index]
            if index < len(DECK_OUTPUT_LABELS)
            else DECK_FALLBACK_LABEL
        )
        lines.append(f"{label}: {url}/{task_id}/output-file/{file_id}/download")
    return "\n".join(lines)


async def conversation_deck_images(nc: AsyncNextcloudApp) -> tuple[list[int], int]:
    """Collect the conversation's attachments the deck task can place.

    Returns the placeable file ids and the count of attachments that might
    have been images but could not be forwarded. Non-image attachments are
    ignored for decks by design. An attachment is counted as not forwarded
    when it could have been an image and was not: unresolvable (deleted,
    inaccessible, lookup error) or deck-unusable (an image type the deck
    cannot place, such as svg or gif, or an image over the task's size
    cap). Nothing here may raise: when the attachment bridge has a
    problem the deck still generates, without the images and with the
    omission said out loud.
    """
    images: list[int] = []
    unforwarded = 0
    for file_id in current_input_attachments.get():
        try:
            node = await nc.files.by_id(file_id)
            if node is None:
                unforwarded += 1
                continue
            mimetype = node.info.mimetype or ""
            if mimetype not in DECK_IMAGE_MIME_TYPES:
                # Honesty rule: an image the deck cannot place (svg,
                # gif) was still an image the user attached, so its omission
                # is counted and said out loud. A non-image attachment (a
                # PDF) was never an image for the deck and stays silent.
                if mimetype.startswith("image/"):
                    unforwarded += 1
                continue
            if node.info.content_length and node.info.content_length > DECK_MAX_IMAGE_BYTES:
                unforwarded += 1
                continue
            images.append(file_id)
        except Exception as e:  # noqa
            unforwarded += 1
            await log(
                nc,
                LogLvl.WARNING,
                f"Could not check the conversation attachment {file_id} for deck inclusion: {e!r}",
            )
    return images, unforwarded


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
                # The two speak different payload shapes: the marp task takes
                # an "input" slot and answers with one file per output format,
                # richdocuments takes "text" and answers a single "slide_deck".
                tasktypes = (await nc.ocs("GET", "/ocs/v2.php/taskprocessing/tasktypes"))["types"].keys()
                note = None
                if "slideprepper:slide_deck_generation" in tasktypes:
                    tasktype = "slideprepper:slide_deck_generation"
                    task_input = {
                        "input": input,
                    }
                    # The conversation's image attachments ride along as file
                    # ids, which the model cannot hand over itself: it only
                    # ever saw pixels. The deck task places them by its own
                    # "#file:N" numbering and counts them into its outline.
                    images, unforwarded = await conversation_deck_images(nc)
                    if images:
                        task_input["images"] = images
                    note = deck_images_note(images, unforwarded)
                    task = await run_task(nc, tasktype, task_input)
                    return deck_reply(url, task.id, task.output.get("slide_decks"), note)
                else:
                    tasktype = "richdocuments:slide_deck_generation"
                    task_input = {
                        "text": input,
                    }
                task = await run_task(nc, tasktype, task_input)
                link = f"{url}/{task.id}/output-file/{task.output['slide_deck']}/download"
                return f"{link}\n{note}" if note else link

        task = await run_task(nc, tasktype, task_input)
        return f"{url}/{task.id}/output-file/{task.output['file']}/download"

    # The docstring is the model-facing contract, so it only offers the formats
    # this instance can actually generate.
    generate_document.__doc__ = (
        f"Generate an office document based on a description of what it should contain\n"
        + (
            # Only the deck task takes images, so the contract is honest about
            # where attachments ride and that no image parameter exists; the
            # relay sentence makes the model repeat the tool's own count,
            # which may be fewer than the attachments it saw.
            "Image files attached to the conversation are included in slide decks"
            " automatically; there is no parameter for passing images. The result"
            " states exactly how many attached images the deck contains; relay that"
            " count in your reply, since it may be fewer than were attached.\n"
            if "slides" in available_formats
            else ""
        )
        + f":param input: the instructions for what the document should contain\n"
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
