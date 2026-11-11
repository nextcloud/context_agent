# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timedelta, timezone

def find_available_slots(start_time, end_time, busy_intervals, slot_duration=timedelta(hours=1), max_slots=3):
	"""
	Finds up to `max_slots` available time slots of `slot_duration` within `start_time` and `end_time`,
	avoiding the `busy_intervals`.

	:param start_time: The start of the overall time range (datetime)
	:param end_time: The end of the overall time range (datetime)
	:param busy_intervals: List of (start, end) datetime tuples representing busy periods
	:param slot_duration: Duration of each available slot (default: 1 hour)
	:param max_slots: Maximum number of slots to return (default: 3)
	:return: List of available (start, end) datetime tuples
	"""
	# Sort busy intervals by start time
	busy_intervals.sort()

	available_slots = []
	current_time = start_time

	for busy_start, busy_end in busy_intervals:
		# If there's a gap between the current time and the next busy period
		while current_time + slot_duration <= busy_start:
			# Ensure the slot is within the given time range
			if current_time + slot_duration > end_time:
				break

			available_slots.append((current_time, current_time + slot_duration))

			# Stop if we've found enough slots
			if len(available_slots) >= max_slots:
				return available_slots

			# Move to the next possible slot
			current_time += slot_duration

		# Move the current time forward to the end of this busy period
		current_time = max(current_time, busy_end)

	# Check for available slots after the last busy period
	while current_time + slot_duration <= end_time:
		available_slots.append((current_time, current_time + slot_duration))

		if len(available_slots) >= max_slots:
			break

		current_time += slot_duration

	return available_slots


def round_to_nearest_half_hour(dt=None):
	"""
	Rounds the given datetime (or current time) to the nearest full or half-hour.

	:param dt: A datetime object (default: current UTC time)
	:return: A rounded datetime object
	"""
	if dt is None:
		dt = datetime.now(timezone.utc)

	# Get minutes and determine rounding
	minutes = dt.minute
	if minutes < 15:
		rounded_minutes = 0
	elif minutes < 45:
		rounded_minutes = 30
	else:
		rounded_minutes = 0
		dt += timedelta(hours=1)  # Round up to next hour if > 45 min

	return dt.replace(minute=rounded_minutes, second=0, microsecond=0)


def freebusy_range(start_date, end_date, tz, now=None):
	"""
	Resolves the range to search for free slots in. Dates are interpreted in `tz`; the end date is inclusive.

	:param start_date: Start date as 'YYYY-MM-DD' (default: now, rounded to the nearest half-hour). Never earlier than now.
	:param end_date: End date as 'YYYY-MM-DD', inclusive (default: 7 days after the start)
	:param tz: A pytz timezone
	:param now: The current time, timezone-aware (default: datetime.now(tz))
	:return: (start, end) timezone-aware datetimes
	"""
	earliest = tz.normalize(round_to_nearest_half_hour(now or datetime.now(tz)))
	if start_date is None:
		start_time = earliest
	else:
		# slots in the past are of no use
		start_time = max(tz.localize(datetime.strptime(start_date, "%Y-%m-%d")), earliest)
	# add days in wall-clock time, so a DST change in between does not shift the end by an hour
	default_end = tz.localize(start_time.replace(tzinfo=None) + timedelta(days=7))
	if end_date is None:
		return start_time, default_end
	# end at midnight after the end date, so the end date itself is included
	end_time = tz.localize(datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1))
	if start_time >= end_time:
		end_time = default_end
	return start_time, end_time
