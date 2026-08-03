# SPDX-FileCopyrightText: 2026 Dick Tump
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Parsing, validation and recurrence helpers for calendar event search."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

import recurring_ical_events
from icalendar import Calendar
from lxml import etree as ET

DAV_NAMESPACE = "DAV:"
CALDAV_NAMESPACE = "urn:ietf:params:xml:ns:caldav"
MAX_CALENDARS = 50
MAX_CALENDAR_NAMES = 20
MAX_GROUPS = 4
MAX_TERMS_PER_GROUP = 8
MAX_TERM_LENGTH = 64
MAX_RANGE_DAYS = 370
MAX_RESULT_LIMIT = 100
MAX_RESOURCES_PER_CALENDAR = 2_000
MAX_EXPANDED_OCCURRENCES_PER_RESOURCE = 5_000
MAX_XML_BYTES = 10 * 1024 * 1024
MAX_ICALENDAR_BYTES = 512 * 1024
RECURRENCE_UNIT_SECONDS = {
    "SECONDLY": 1,
    "MINUTELY": 60,
    "HOURLY": 60 * 60,
    "DAILY": 24 * 60 * 60,
    "WEEKLY": 7 * 24 * 60 * 60,
    "MONTHLY": 28 * 24 * 60 * 60,
    "YEARLY": 365 * 24 * 60 * 60,
}

NAMESPACES = {"d": DAV_NAMESPACE, "c": CALDAV_NAMESPACE}


@dataclass(frozen=True)
class CalendarCollection:
    name: str
    href: str


@dataclass(frozen=True)
class SearchBounds:
    start: datetime
    end: datetime


def validate_search(
    range_start: str,
    range_end: str,
    calendar_names: list[str] | None,
    text_term_groups: list[list[str]] | None,
    limit: int,
) -> tuple[SearchBounds, list[str] | None, list[list[str]], int]:
    start = _parse_bound(range_start, "range_start")
    end = _parse_bound(range_end, "range_end")
    if start >= end:
        raise ValueError("range_start must be before range_end")
    if end - start > timedelta(days=MAX_RANGE_DAYS):
        raise ValueError(f"Calendar searches may span at most {MAX_RANGE_DAYS} days")

    result_limit = _validate_result_limit(limit)
    validated_names = _validate_calendar_names(calendar_names)
    groups = _validate_text_term_groups(text_term_groups)
    return SearchBounds(start=start, end=end), validated_names, groups, result_limit


def _validate_result_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULT_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RESULT_LIMIT}")
    return limit


def _validate_calendar_names(calendar_names: list[str] | None) -> list[str] | None:
    if calendar_names is None:
        return None
    if not isinstance(calendar_names, list) or not calendar_names or len(calendar_names) > MAX_CALENDAR_NAMES:
        raise ValueError(f"calendar_names must contain between 1 and {MAX_CALENDAR_NAMES} names")
    validated_names = []
    for name in calendar_names:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
            raise ValueError("Each calendar name must be a non-empty string of at most 128 characters")
        validated_names.append(name.strip())
    return validated_names


def _validate_text_term_groups(text_term_groups: list[list[str]] | None) -> list[list[str]]:
    if text_term_groups is None:
        return []
    if not isinstance(text_term_groups, list) or not text_term_groups or len(text_term_groups) > MAX_GROUPS:
        raise ValueError(f"text_term_groups must contain between 1 and {MAX_GROUPS} groups")
    return [_validate_text_term_group(group) for group in text_term_groups]


def _validate_text_term_group(group: list[str]) -> list[str]:
    if not isinstance(group, list) or not group or len(group) > MAX_TERMS_PER_GROUP:
        raise ValueError(f"Each text term group must contain between 1 and {MAX_TERMS_PER_GROUP} alternatives")
    validated_group = []
    for term in group:
        if not isinstance(term, str) or not term.strip() or len(term.strip()) > MAX_TERM_LENGTH:
            raise ValueError(f"Each text term must be a non-empty string of at most {MAX_TERM_LENGTH} characters")
        validated_group.append(term.strip())
    return validated_group


def parse_calendar_collections(xml_text: str) -> tuple[list[CalendarCollection], int]:
    _check_xml_size(xml_text)
    root = _parse_xml(xml_text)
    calendars = []
    failed_responses = 0
    for response in root.findall("d:response", NAMESPACES):
        href = response.findtext("d:href", default="", namespaces=NAMESPACES).strip()
        response_succeeded = False
        for propstat in response.findall("d:propstat", NAMESPACES):
            status = propstat.findtext("d:status", default="", namespaces=NAMESPACES)
            if " 200 " not in status:
                continue
            response_succeeded = True
            prop = propstat.find("d:prop", NAMESPACES)
            if prop is None:
                continue
            resource_type = prop.find("d:resourcetype", NAMESPACES)
            if resource_type is None or resource_type.find("c:calendar", NAMESPACES) is None:
                continue
            component_set = prop.find("c:supported-calendar-component-set", NAMESPACES)
            if component_set is not None:
                component_names = {
                    component.attrib.get("name", "").upper()
                    for component in component_set.findall("c:comp", NAMESPACES)
                }
                if component_names and "VEVENT" not in component_names:
                    continue
            name = prop.findtext("d:displayname", default="", namespaces=NAMESPACES).strip()
            if href and name:
                calendars.append(CalendarCollection(name=name, href=href))
        if not response_succeeded:
            failed_responses += 1
    return calendars, failed_responses


def parse_current_user_principal(xml_text: str) -> str:
    return _parse_href_property(xml_text, "d:current-user-principal")


def parse_calendar_home(xml_text: str) -> str:
    return _parse_href_property(xml_text, "c:calendar-home-set")


def parse_calendar_data(xml_text: str) -> tuple[list[str], int, bool]:
    _check_xml_size(xml_text)
    root = _parse_xml(xml_text)
    resources = []
    failed_resources = 0
    truncated = False
    for response in root.findall("d:response", NAMESPACES):
        calendar_data = None
        for propstat in response.findall("d:propstat", NAMESPACES):
            status = propstat.findtext("d:status", default="", namespaces=NAMESPACES)
            if " 200 " not in status:
                continue
            prop = propstat.find("d:prop", NAMESPACES)
            if prop is None:
                continue
            data_element = prop.find("c:calendar-data", NAMESPACES)
            if data_element is not None and data_element.text:
                calendar_data = data_element.text
        if calendar_data is None:
            failed_resources += 1
            continue
        if len(calendar_data.encode("utf-8")) > MAX_ICALENDAR_BYTES:
            failed_resources += 1
            continue
        if len(resources) >= MAX_RESOURCES_PER_CALENDAR:
            truncated = True
            continue
        resources.append(calendar_data)
    return resources, failed_resources, truncated


def expand_and_filter_events(
    icalendar_text: str,
    calendar_name: str,
    bounds: SearchBounds,
    text_term_groups: list[list[str]],
) -> list[dict[str, Any]]:
    calendar = Calendar.from_ical(icalendar_text)
    _validate_expansion_limits(calendar, bounds)
    recurrence_by_uid = _recurrence_metadata(calendar)
    occurrences = recurring_ical_events.of(calendar, components=["VEVENT"]).between(bounds.start, bounds.end)
    results = []
    for component in occurrences:
        event = _event_from_component(component, calendar_name, recurrence_by_uid, text_term_groups)
        if event is not None:
            results.append(event)
    return results


def _validate_expansion_limits(calendar: Calendar, bounds: SearchBounds) -> None:
    estimated_occurrences = 0
    for component in calendar.walk("VEVENT"):
        rrule = component.get("RRULE")
        expansion_margin = timedelta(0)
        if rrule is not None or component.get("RECURRENCE-ID") is not None:
            expansion_margin = _validate_recurrence_duration_and_shift(component, bounds)
        if rrule is None:
            estimated_occurrences += 1
        else:
            estimated_occurrences += _estimate_rrule_occurrences(component, rrule, bounds, expansion_margin)
        estimated_occurrences += _rdate_count(component.get("RDATE"))
        if estimated_occurrences > MAX_EXPANDED_OCCURRENCES_PER_RESOURCE:
            raise ValueError("Calendar resource recurrence expansion exceeded the processing limit")


def _estimate_rrule_occurrences(
    component: Any,
    rrule: Any,
    bounds: SearchBounds,
    expansion_margin: timedelta,
) -> int:
    """Estimate recurrence instances processed from DTSTART through the query stop.

    The recurrence library iterates from DTSTART rather than fast-forwarding to
    the query start. A finite series therefore stops at the earlier of UNTIL and
    the expanded query end, while still accounting for an expired active span.
    """
    frequency = str(_first_recurrence_value(rrule.get("FREQ")) or "").upper()
    unit_seconds = RECURRENCE_UNIT_SECONDS.get(frequency)
    if unit_seconds is None:
        raise ValueError(f"Unsupported recurrence frequency: {frequency or 'unknown'}")
    interval = int(_first_recurrence_value(rrule.get("INTERVAL")) or 1)
    if interval < 1:
        raise ValueError("Recurrence interval must be positive")
    count = _first_recurrence_value(rrule.get("COUNT"))
    count_limit = int(count) if count is not None else None
    if count_limit is not None and count_limit < 1:
        raise ValueError("Recurrence count must be positive")

    start = _decoded_datetime(component, "DTSTART")
    if start is None:
        raise ValueError("Recurring event is missing DTSTART")
    start_datetime = _temporal_to_datetime(start, bounds)
    until_datetime = _normalize_recurrence_until(start, rrule.get("UNTIL"), bounds)
    if until_datetime is not None and until_datetime < start_datetime:
        raise ValueError("Recurrence UNTIL is before DTSTART")
    processing_end = bounds.end + expansion_margin
    if until_datetime is not None:
        processing_end = min(processing_end, until_datetime)

    processed_seconds = max(0, (processing_end - start_datetime).total_seconds())
    estimated = int(processed_seconds // (unit_seconds * interval)) + 2
    estimated *= _recurrence_date_multiplier(rrule, frequency)
    estimated *= _recurrence_time_multiplier(rrule, frequency)
    return min(estimated, count_limit) if count_limit is not None else estimated


def _normalize_recurrence_until(
    start: date | datetime,
    until_value: Any,
    bounds: SearchBounds,
) -> datetime | None:
    if until_value is None:
        return None
    if isinstance(until_value, list):
        if len(until_value) != 1:
            raise ValueError("Recurrence UNTIL must contain exactly one value")
        until = until_value[0]
    else:
        until = until_value
    if not isinstance(until, date):
        raise ValueError("Recurrence UNTIL must be a date or datetime")

    start_is_datetime = isinstance(start, datetime)
    until_is_datetime = isinstance(until, datetime)
    if start_is_datetime != until_is_datetime:
        raise ValueError("Recurrence UNTIL type must match DTSTART")
    if start_is_datetime and until_is_datetime:
        start_is_aware = start.tzinfo is not None
        until_is_aware = until.tzinfo is not None
        if start_is_aware != until_is_aware:
            raise ValueError("Recurrence UNTIL timezone form must match DTSTART")
    return _temporal_to_datetime(until, bounds)


def _recurrence_date_multiplier(rrule: Any, frequency: str) -> int:
    if frequency == "WEEKLY":
        return _recurrence_value_count(rrule.get("BYDAY"))
    if frequency == "MONTHLY":
        return max(
            1,
            _recurrence_value_count(rrule.get("BYMONTHDAY"), default=0),
            _recurrence_value_count(rrule.get("BYDAY"), default=0) * 5,
        )
    if frequency == "YEARLY":
        months_for_month_days = _recurrence_value_count(
            rrule.get("BYMONTH"),
            default=12 if rrule.get("BYMONTHDAY") is not None else 1,
        )
        return max(
            1,
            _recurrence_value_count(rrule.get("BYYEARDAY"), default=0),
            _recurrence_value_count(rrule.get("BYWEEKNO"), default=0) * 7,
            _recurrence_value_count(rrule.get("BYMONTHDAY"), default=0) * months_for_month_days,
            _recurrence_value_count(rrule.get("BYDAY"), default=0) * 53,
            _recurrence_value_count(rrule.get("BYMONTH")),
        )
    return 1


def _recurrence_time_multiplier(rrule: Any, frequency: str) -> int:
    multiplier = 1
    if frequency in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
        multiplier *= _recurrence_value_count(rrule.get("BYHOUR"))
    if frequency in {"HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
        multiplier *= _recurrence_value_count(rrule.get("BYMINUTE"))
    if frequency != "SECONDLY":
        multiplier *= _recurrence_value_count(rrule.get("BYSECOND"))
    return multiplier


def _validate_recurrence_duration_and_shift(component: Any, bounds: SearchBounds) -> timedelta:
    start = _decoded_datetime(component, "DTSTART")
    if start is None:
        return timedelta(0)
    start_datetime = _temporal_to_datetime(start, bounds)
    end_datetime = _temporal_to_datetime(_event_end(component, start), bounds)
    duration = end_datetime - start_datetime
    if duration > timedelta(days=MAX_RANGE_DAYS):
        raise ValueError("Recurring event duration exceeded the processing limit")

    recurrence_id = _decoded_datetime(component, "RECURRENCE-ID")
    if recurrence_id is None:
        return max(duration, timedelta(0))
    recurrence_datetime = _temporal_to_datetime(recurrence_id, bounds)
    shift = abs(start_datetime - recurrence_datetime)
    if shift > timedelta(days=MAX_RANGE_DAYS):
        raise ValueError("Recurring event exception shift exceeded the processing limit")
    return max(duration, timedelta(0)) + shift


def _temporal_to_datetime(value: date | datetime, bounds: SearchBounds) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=bounds.start.tzinfo)
    return datetime.combine(value, time.min, bounds.start.tzinfo)


def _recurrence_value_count(value: Any, *, default: int = 1) -> int:
    if value is None:
        return default
    return max(1, len(value)) if isinstance(value, list) else 1


def _rdate_count(value: Any) -> int:
    if value is None:
        return 0
    values = value if isinstance(value, list) else [value]
    return sum(len(item.dts) if hasattr(item, "dts") else 1 for item in values)


def _event_from_component(
    component: Any,
    calendar_name: str,
    recurrence_by_uid: dict[str, dict[str, Any]],
    text_term_groups: list[list[str]],
) -> dict[str, Any] | None:
    status = _property_text(component, "STATUS").upper()
    if status == "CANCELLED":
        return None

    text_fields = {
        "summary": _property_text(component, "SUMMARY"),
        "description": _property_text(component, "DESCRIPTION"),
        "location": _property_text(component, "LOCATION"),
        "categories": _categories_text(component),
    }
    match = _match_text_groups(text_fields, text_term_groups)
    start = _decoded_datetime(component, "DTSTART")
    if match is None or start is None:
        return None

    uid = _property_text(component, "UID")
    event = {
        "_uid": uid,
        "calendar": calendar_name,
        "summary": text_fields["summary"],
        "start": _format_temporal(start),
        "end": _format_temporal(_event_end(component, start)),
        "all_day": isinstance(start, date) and not isinstance(start, datetime),
    }
    if event["all_day"]:
        event["end_exclusive"] = True
    timezone_name = _timezone_name(component, start)
    if timezone_name:
        event["timezone"] = timezone_name
    if text_fields["location"]:
        event["location"] = text_fields["location"]
    if status:
        event["status"] = status
    recurrence = recurrence_by_uid.get(uid)
    if recurrence:
        event["recurrence"] = recurrence
    if text_term_groups:
        event["matched_terms"] = match["terms"]
        event["matched_fields"] = match["fields"]
    return event


def event_sort_key(event: dict[str, Any], floating_timezone: tzinfo = UTC) -> tuple[datetime, str, str]:
    start = event["start"]
    if event["all_day"]:
        instant = datetime.combine(date.fromisoformat(start), time.min, UTC)
    else:
        instant = datetime.fromisoformat(start)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=floating_timezone)
        instant = instant.astimezone(UTC)
    return instant, event.get("calendar", "").casefold(), event.get("summary", "").casefold()


def event_identity(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        event.get("_calendar_href") or event.get("calendar"),
        event.get("_uid") or event.get("summary"),
        event.get("start"),
    )


def _parse_bound(value: str, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 date-time string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exception:
        raise ValueError(f"{field_name} must be an ISO 8601 date-time string") from exception
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset or Z")
    return parsed


def _check_xml_size(xml_text: str) -> None:
    if len(xml_text.encode("utf-8")) > MAX_XML_BYTES:
        raise ValueError("Calendar response exceeded the processing size limit")


def _parse_xml(xml_text: str):
    parser = ET.XMLParser(resolve_entities=False, no_network=True)
    # Entity resolution and network access are disabled explicitly above.
    return ET.fromstring(xml_text.encode("utf-8"), parser)  # noqa: S320


def _parse_href_property(xml_text: str, property_name: str) -> str:
    _check_xml_size(xml_text)
    root = _parse_xml(xml_text)
    for propstat in root.findall("d:response/d:propstat", NAMESPACES):
        status = propstat.findtext("d:status", default="", namespaces=NAMESPACES)
        if " 200 " not in status:
            continue
        prop = propstat.find("d:prop", NAMESPACES)
        if prop is None:
            continue
        value = prop.find(property_name, NAMESPACES)
        if value is None:
            continue
        href = value.findtext("d:href", default="", namespaces=NAMESPACES).strip()
        if href:
            return href
    raise ValueError(f"CalDAV discovery response did not contain {property_name}")


def _recurrence_metadata(calendar: Calendar) -> dict[str, dict[str, Any]]:
    recurrences = {}
    for component in calendar.walk("VEVENT"):
        uid = _property_text(component, "UID")
        rrule = component.get("RRULE")
        rdates = component.get("RDATE")
        if not uid or (rrule is None and rdates is None):
            continue
        metadata: dict[str, Any] = {"recurring": True}
        if rrule is not None:
            frequency = _first_recurrence_value(rrule.get("FREQ"))
            interval = _first_recurrence_value(rrule.get("INTERVAL"))
            if frequency:
                metadata["frequency"] = str(frequency).lower()
            if interval and int(interval) != 1:
                metadata["interval"] = int(interval)
        recurrences[uid] = metadata
    return recurrences


def _first_recurrence_value(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _property_text(component: Any, name: str) -> str:
    value = component.get(name)
    return "" if value is None else str(value)


def _categories_text(component: Any) -> str:
    categories = []
    values = component.get("CATEGORIES")
    if values is None:
        return ""
    if not isinstance(values, list):
        values = [values]
    for value in values:
        if hasattr(value, "cats"):
            categories.extend(str(category) for category in value.cats)
        else:
            categories.append(str(value))
    return ", ".join(categories)


def _match_text_groups(fields: dict[str, str], groups: list[list[str]]) -> dict[str, list[str]] | None:
    if not groups:
        return {"terms": [], "fields": []}
    normalized_fields = {name: _normalize_text(value) for name, value in fields.items()}
    matched_terms = []
    matched_fields = set()
    for group in groups:
        group_matches = []
        for term in group:
            normalized_term = _normalize_text(term)
            fields_for_term = [name for name, value in normalized_fields.items() if normalized_term in value]
            if fields_for_term:
                group_matches.append(term)
                matched_fields.update(fields_for_term)
        if not group_matches:
            return None
        matched_terms.extend(group_matches)
    return {"terms": matched_terms, "fields": sorted(matched_fields)}


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _decoded_datetime(component: Any, name: str) -> date | datetime | None:
    value = component.get(name)
    return None if value is None else value.dt


def _event_end(component: Any, start: date | datetime) -> date | datetime:
    end = _decoded_datetime(component, "DTEND")
    if end is not None:
        return end
    duration = component.get("DURATION")
    if duration is not None:
        return start + duration.dt
    if isinstance(start, datetime):
        return start
    return start + timedelta(days=1)


def _format_temporal(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value.isoformat()


def _timezone_name(component: Any, start: date | datetime) -> str | None:
    if not isinstance(start, datetime):
        return None
    tzid = component["DTSTART"].params.get("TZID")
    if tzid:
        return str(tzid)
    if start.tzinfo is None:
        return "floating"
    if start.utcoffset() == timedelta(0):
        return "UTC"
    timezone_key = getattr(start.tzinfo, "key", None)
    return timezone_key if isinstance(timezone_key, str) and timezone_key else None


def utc_caldav_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def calendar_home_propfind_body() -> str:
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="{DAV_NAMESPACE}" xmlns:c="{CALDAV_NAMESPACE}">
  <d:prop>
    <d:displayname />
    <d:resourcetype />
    <c:supported-calendar-component-set />
  </d:prop>
</d:propfind>"""


def current_user_principal_propfind_body() -> str:
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="{DAV_NAMESPACE}">
  <d:prop>
    <d:current-user-principal />
  </d:prop>
</d:propfind>"""


def principal_calendar_home_propfind_body() -> str:
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="{DAV_NAMESPACE}" xmlns:c="{CALDAV_NAMESPACE}">
  <d:prop>
    <c:calendar-home-set />
  </d:prop>
</d:propfind>"""


def calendar_query_body(bounds: SearchBounds) -> str:
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<c:calendar-query xmlns:d="{DAV_NAMESPACE}" xmlns:c="{CALDAV_NAMESPACE}">
  <d:prop>
    <d:getetag />
    <c:calendar-data />
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{utc_caldav_timestamp(bounds.start)}" end="{utc_caldav_timestamp(bounds.end)}" />
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
