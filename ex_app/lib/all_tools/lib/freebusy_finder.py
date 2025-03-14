# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timedelta

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
		dt = datetime.utcnow()

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