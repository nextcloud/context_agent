# SPDX-FileCopyrightText: 2026 Dick Tump
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Read-only, bounded calendar event search tools."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.calendar_search import (
    MAX_CALENDARS,
    CalendarCollection,
    SearchBounds,
    calendar_home_propfind_body,
    calendar_query_body,
    current_user_principal_propfind_body,
    event_identity,
    event_sort_key,
    expand_and_filter_events,
    parse_calendar_collections,
    parse_calendar_data,
    parse_calendar_home,
    parse_current_user_principal,
    principal_calendar_home_propfind_body,
    validate_search,
)
from ex_app.lib.all_tools.lib.decorator import safe_tool


class CalendarRequestError(RuntimeError):
    def __init__(self, status_code: int, request_stage: str):
        super().__init__(f"Unexpected HTTP status {status_code}")
        self.status_code = status_code
        self.request_stage = request_stage


async def get_tools(nc: AsyncNextcloudApp):
    @tool
    @safe_tool
    async def search_calendar_events(
        range_start: str,
        range_end: str,
        calendar_names: list[str] | None = None,
        text_term_groups: list[list[str]] | None = None,
        limit: int = 50,
    ):
        """Search the current user's calendar events in a required, bounded time range.

        Use ISO 8601 date-times with a UTC offset or Z. range_end is exclusive.
        Recurrences are expanded, moved exceptions replace their original occurrence, and cancellations are omitted.
        Use text_term_groups to search summary, description, location and categories before events are returned.
        Terms within one group are alternatives (OR), while every group must match (AND).
        Supply likely synonyms or translations as alternatives when the user's wording and calendar language may differ.
        An empty complete result proves no matching events. Never infer absence when complete is false.
        :param range_start: Inclusive range start, for example 2026-10-01T00:00:00+02:00.
        :param range_end: Exclusive range end, no more than 370 days after range_start.
        :param calendar_names: Optional exact calendar display names. Searches every event calendar when omitted.
        :param text_term_groups: Optional groups of case-insensitive substring alternatives.
        :param limit: Maximum events returned, from 1 to 100.
        :return: Matching event fields plus explicit completeness, truncation and failure metadata.
        """
        return await _search_calendar_events(
            nc,
            range_start=range_start,
            range_end=range_end,
            calendar_names=calendar_names,
            text_term_groups=text_term_groups,
            limit=limit,
        )

    return [search_calendar_events]


async def _search_calendar_events(
    nc: AsyncNextcloudApp,
    *,
    range_start: str,
    range_end: str,
    calendar_names: list[str] | None,
    text_term_groups: list[list[str]] | None,
    limit: int,
) -> dict:
    bounds, requested_names, term_groups, result_limit = validate_search(
        range_start,
        range_end,
        calendar_names,
        text_term_groups,
        limit,
    )
    failures = []
    try:
        calendars, failed_discovery_responses = await _list_event_calendars(nc)
    except Exception as exception:
        return _failed_result(bounds, _failure_entry("calendar_discovery", exception))
    if failed_discovery_responses:
        failures.append(
            {
                "stage": "calendar_discovery",
                "error": "Some calendar collections could not be inspected",
                "count": failed_discovery_responses,
            }
        )

    selected_calendars, missing_names = _select_calendars(calendars, requested_names)
    if missing_names:
        failures.append(
            {
                "stage": "calendar_selection",
                "error": "Requested calendars were not found",
                "calendars": missing_names,
            }
        )

    selected_calendars, calendar_limit_failure = _apply_calendar_limit(selected_calendars)
    if calendar_limit_failure:
        failures.append(calendar_limit_failure)

    events, search_failures, resource_truncated = await _search_selected_calendars(
        nc,
        selected_calendars,
        bounds,
        term_groups,
    )
    failures.extend(search_failures)
    resource_truncated = resource_truncated or calendar_limit_failure is not None

    unique_events = {event_identity(event): event for event in events}
    sorted_events = sorted(
        unique_events.values(),
        key=lambda event: event_sort_key(event, bounds.start.tzinfo),
    )
    for event in sorted_events:
        event.pop("_uid", None)
        event.pop("_calendar_href", None)
    result_truncated = len(sorted_events) > result_limit
    truncated = resource_truncated or result_truncated
    complete = not failures and not truncated
    result = {
        "range": {
            "start": bounds.start.isoformat(),
            "end": bounds.end.isoformat(),
            "end_exclusive": True,
        },
        "complete": complete,
        "truncated": truncated,
        "calendars_searched": [calendar.name for calendar in selected_calendars],
        "matches_found": len(sorted_events),
        "returned": min(len(sorted_events), result_limit),
        "events": sorted_events[:result_limit],
        "failures": failures,
    }
    if not complete:
        result["completeness_warning"] = "The search was incomplete. Do not infer that an event is absent."
    return result


def _apply_calendar_limit(
    calendars: list[CalendarCollection],
) -> tuple[list[CalendarCollection], dict | None]:
    if len(calendars) <= MAX_CALENDARS:
        return calendars, None
    return calendars[:MAX_CALENDARS], {
        "stage": "calendar_limit",
        "error": "Calendar processing limit reached",
        "limit": MAX_CALENDARS,
    }


async def _search_selected_calendars(
    nc: AsyncNextcloudApp,
    calendars: list[CalendarCollection],
    bounds: SearchBounds,
    term_groups: list[list[str]],
) -> tuple[list[dict], list[dict], bool]:
    events = []
    failures = []
    resource_truncated = False
    for calendar in calendars:
        try:
            xml_text = await _calendar_report(nc, calendar, calendar_query_body(bounds))
            calendar_events, calendar_failures, calendar_truncated = await asyncio.to_thread(
                _process_calendar_response,
                xml_text,
                calendar,
                bounds,
                term_groups,
            )
        except Exception as exception:
            failure = _failure_entry("calendar_query", exception)
            failure["calendar"] = calendar.name
            failures.append(failure)
            continue
        events.extend(calendar_events)
        failures.extend(calendar_failures)
        resource_truncated = resource_truncated or calendar_truncated
    return events, failures, resource_truncated


def _process_calendar_response(
    xml_text: str,
    calendar: CalendarCollection,
    bounds: SearchBounds,
    term_groups: list[list[str]],
) -> tuple[list[dict], list[dict], bool]:
    resources, failed_resources, resource_truncated = parse_calendar_data(xml_text)
    failures = []
    if failed_resources:
        failures.append(
            {
                "calendar": calendar.name,
                "stage": "resource_read",
                "error": "Some calendar resources could not be read",
                "count": failed_resources,
            }
        )
    if resource_truncated:
        failures.append(
            {
                "calendar": calendar.name,
                "stage": "resource_limit",
                "error": "Calendar resource processing limit reached",
            }
        )

    events = []
    parse_failures = 0
    for resource in resources:
        try:
            resource_events = expand_and_filter_events(resource, calendar.name, bounds, term_groups)
            for event in resource_events:
                event["_calendar_href"] = calendar.href
            events.extend(resource_events)
        except Exception:
            parse_failures += 1
    if parse_failures:
        failures.append(
            {
                "calendar": calendar.name,
                "stage": "event_parsing",
                "error": "Some calendar resources contained invalid or unsupported event data",
                "count": parse_failures,
            }
        )
    return events, failures, resource_truncated


def get_category_name():
    return "Calendar: Advanced Search"


async def is_available(nc: AsyncNextcloudApp):
    return True


async def _list_event_calendars(nc: AsyncNextcloudApp) -> tuple[list[CalendarCollection], int]:
    principal_response = await nc._session.adapter_dav.request(
        "PROPFIND",
        "/",
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "0"},
        data=current_user_principal_propfind_body(),
    )
    _require_success(principal_response, {207}, "current_user_principal")
    principal_path = _same_origin_dav_path(nc, parse_current_user_principal(principal_response.text))

    home_response = await nc._session.adapter_dav.request(
        "PROPFIND",
        principal_path,
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "0"},
        data=principal_calendar_home_propfind_body(),
    )
    _require_success(home_response, {207}, "calendar_home")
    home_path = _same_origin_dav_path(nc, parse_calendar_home(home_response.text))

    calendars_response = await nc._session.adapter_dav.request(
        "PROPFIND",
        home_path,
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
        data=calendar_home_propfind_body(),
    )
    _require_success(calendars_response, {207}, "calendar_collections")
    return parse_calendar_collections(calendars_response.text)


async def _calendar_report(nc: AsyncNextcloudApp, calendar: CalendarCollection, body: str) -> str:
    request_path = _same_origin_dav_path(nc, calendar.href)
    response = await nc._session.adapter_dav.request(
        "REPORT",
        request_path,
        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
        data=body,
    )
    _require_success(response, {207}, "calendar_query")
    return response.text


def _same_origin_dav_path(nc: AsyncNextcloudApp, href: str) -> str:
    target = urlsplit(href)
    endpoint = urlsplit(nc._session.cfg.endpoint)
    if target.scheme and (target.scheme, target.netloc) != (endpoint.scheme, endpoint.netloc):
        raise ValueError("Calendar collection URL does not belong to this Nextcloud server")
    dav_path = urlsplit(nc._session.cfg.dav_endpoint).path.rstrip("/")
    if target.path == dav_path:
        relative_path = "/"
    elif target.path.startswith(f"{dav_path}/"):
        relative_path = target.path[len(dav_path) :]
    else:
        raise ValueError("Calendar collection URL is outside the Nextcloud DAV endpoint")
    return relative_path + (f"?{target.query}" if target.query else "")


def _require_success(response, allowed_statuses: set[int], request_stage: str) -> None:
    if response.status_code not in allowed_statuses:
        raise CalendarRequestError(response.status_code, request_stage)


def _select_calendars(
    calendars: list[CalendarCollection],
    requested_names: list[str] | None,
) -> tuple[list[CalendarCollection], list[str]]:
    if requested_names is None:
        return calendars, []
    requested = {name.casefold(): name for name in requested_names}
    selected = [calendar for calendar in calendars if calendar.name.casefold() in requested]
    found = {calendar.name.casefold() for calendar in selected}
    missing = [name for name in requested_names if name.casefold() not in found]
    return selected, missing


def _failure_entry(stage: str, exception: Exception) -> dict:
    failure = {
        "stage": stage,
        "error": f"{stage.replace('_', ' ').capitalize()} failed ({type(exception).__name__})",
    }
    if isinstance(exception, CalendarRequestError):
        failure["http_status"] = exception.status_code
        failure["request_stage"] = exception.request_stage
    return failure


def _failed_result(bounds, failure: dict) -> dict:
    return {
        "range": {
            "start": bounds.start.isoformat(),
            "end": bounds.end.isoformat(),
            "end_exclusive": True,
        },
        "complete": False,
        "truncated": False,
        "calendars_searched": [],
        "matches_found": 0,
        "returned": 0,
        "events": [],
        "failures": [failure],
        "completeness_warning": "The search was incomplete. Do not infer that an event is absent.",
    }
