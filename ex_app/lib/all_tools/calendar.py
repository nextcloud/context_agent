# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from niquests import ConnectionError, Timeout
import pytz
from icalendar import Event as ICalendarEvent, vCalAddress, vText
from ics import Calendar, Todo
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp, NextcloudApp
import xml.etree.ElementTree as ET
import vobject

from ex_app.lib.all_tools.lib.audience import principal_radius
from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, destructive, impulse
from ex_app.lib.all_tools.lib.tz import get_timezone, user_timezone
from ex_app.lib.all_tools.lib.freebusy_finder import find_available_slots, round_to_nearest_half_hour


async def get_tools(nc: AsyncNextcloudApp):
	ncSync = NextcloudApp()
	ncSync.set_user(await nc.user)

	CALENDAR_PROPFIND = (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
		'<d:prop><d:displayname/><d:owner/><oc:invite/></d:prop>'
		'</d:propfind>'
	)

	async def calendar_radius(calendar_name):
		"""Who can reach this calendar: is it shared out, or owned by somebody else?

		Calendars are not private by default -- one can be shared with a user, a group
		or a team, and one shared with the current user is owned by somebody who sees
		everything put into it. Both are read off the calendar's DAV properties.
		"""
		user_id = await nc.user
		response = await nc._session._create_adapter(True).request(
			'PROPFIND',
			f"{nc.app_cfg.endpoint}/remote.php/dav/calendars/{user_id}/",
			headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
			data=CALENDAR_PROPFIND,
		)
		for entry in ET.fromstring(response.text).findall('{DAV:}response'):
			displayname = entry.find('.//{DAV:}displayname')
			if displayname is None or displayname.text != calendar_name:
				continue
			radius = ImpulseRadius.SELF
			owner = entry.find('.//{DAV:}owner/{DAV:}href')
			if owner is not None and (owner.text or '').rstrip('/').rsplit('/', 1)[-1] != user_id:
				# Somebody else owns it, so they see whatever we put in it.
				radius = max(radius, ImpulseRadius.INDIVIDUALS)
			# <oc:invite> holds one <oc:user> per sharee, each with the principal it
			# was shared to; an <oc:organizer> sibling is the owner, not a sharee.
			for sharee in entry.findall('.//{http://owncloud.org/ns}invite/{http://owncloud.org/ns}user/{DAV:}href'):
				radius = max(radius, principal_radius(sharee.text))
			return radius
		raise ValueError(f'No calendar named {calendar_name!r}')

	async def event_radius(calendar_name, attendees=None):
		"""An event reaches its attendees, plus whoever else can see the calendar."""
		attendee_radius = ImpulseRadius.INDIVIDUALS if attendees else ImpulseRadius.SELF
		return max(attendee_radius, await calendar_radius(calendar_name))

	def list_calendars_sync():
		principal = ncSync.cal.principal()
		calendars = principal.calendars()
		return ", ".join([cal.name for cal in calendars])

	@tool
	@impulse(ImpulseRadius.SELF)
	async def list_calendars():
		"""
		List all existing calendars by name
		:return: a comma-separated list of calendar names
		"""
		return await asyncio.to_thread(list_calendars_sync)

	def schedule_event_sync(calendar_name: str, title: str, description: str, start_date: str, end_date: str, attendees: Optional[list[str]] = None, start_time: Optional[str] = None, end_time: Optional[str] = None, location: Optional[str] = None, timezone: Optional[str] = None):
		# Parse date and times
		start_date = datetime.strptime(start_date, "%Y-%m-%d")
		end_date = datetime.strptime(end_date, "%Y-%m-%d")

		# Set timezone (defaults to the user's profile timezone); validated even for
		# all-day events so a bad value errors instead of being silently ignored
		timezone = timezone or user_timezone(ncSync)
		tz = pytz.timezone(get_timezone(timezone))

		# Combine date and time
		if start_time and end_time:
			start_time = datetime.strptime(start_time, "%I:%M %p").time()
			end_time = datetime.strptime(end_time, "%I:%M %p").time()

			# icalendar keeps the timezone as a TZID on datetime values
			start_datetime = tz.localize(datetime.combine(start_date, start_time))
			end_datetime = tz.localize(datetime.combine(end_date, end_time))
		else:
			# All-day events are DATE values; DTEND is exclusive, so the day after
			start_datetime = start_date.date()
			end_datetime = (end_date + timedelta(days=1)).date()

		description_with_ai_note = f"{description}\n\n---\n\nThis event was scheduled by Nextcloud AI Assistant."

		# Create event (icalendar keeps the event's timezone as a TZID; the ics library would serialize it to UTC)
		e = ICalendarEvent()
		e.add('summary', title)
		e.add('dtstart', start_datetime)
		e.add('dtend', end_datetime)
		e.add('description', description_with_ai_note)
		if location:
			e.add('location', location)
		e.add('uid', str(uuid.uuid4()) + '@context-agent')
		e.add('dtstamp', datetime.now(pytz.UTC))
		for attendee in attendees or []:
			a = vCalAddress('mailto:' + attendee)
			a.params['CN'] = vText(attendee)
			a.params['PARTSTAT'] = vText('NEEDS-ACTION')
			a.params['ROLE'] = vText('REQ-PARTICIPANT')
			a.params['CUTYPE'] = vText('INDIVIDUAL')
			e.add('attendee', a)

		# let's check who we are...
		i = 0
		while i < 20:
			try:
				json = ncSync.ocs('GET', '/ocs/v2.php/cloud/user')
				break
			except (
					ConnectionError,
					Timeout
			) as e:
				i += 1
				time.sleep(1)
				continue

		if i >= 20:
			raise Exception('Error fetching current user information')

		# ...and set the organizer
		organizer = vCalAddress('mailto:' + (json['email'] or ''))
		organizer.params['CN'] = vText(json['displayname'])
		e.add('organizer', organizer)

		# Add event to calendar

		principal = ncSync.cal.principal()
		calendars = principal.calendars()
		calendar = {cal.name: cal for cal in calendars}[calendar_name]
		calendar.add_event(e.to_ical().decode())

	@tool
	@impulse(event_radius)
	async def schedule_event(calendar_name: str, title: str, description: str, start_date: str, end_date: str, attendees: Optional[list[str]] = None, start_time: Optional[str] = None, end_time: Optional[str] = None, location: Optional[str] = None, timezone: Optional[str] = None):
		"""
		Crete a new event or meeting in a calendar. Omit start_time and end_time parameters to create an all-day event.
		:param calendar_name: The name of the calendar to add the event to
		:param title: The title of the event
		:param description: The description of the event
		:param start_date: the start date of the event in the following form: YYYY-MM-DD e.g. '2024-12-01'
		:param end_date: the last day of the event, inclusive: for a single-day event pass the
			same date as start_date. Form: YYYY-MM-DD e.g. '2024-12-01'
		:param attendees: the list of attendees to add to the event (as email addresses)
		:param start_time: the start time in the following form: HH:MM AM/PM e.g. '3:00 PM'
		:param end_time: the start time in the following form: HH:MM AM/PM e.g. '4:00 PM'
		:param location: The location of the event
		:param timezone: Timezone (e.g., 'America/New_York'). Defaults to the user's profile timezone. Must be a valid IANA timezone name.
		:return: bool
		"""
		await asyncio.to_thread(schedule_event_sync, calendar_name, title, description, start_date, end_date, attendees, start_time, end_time, location, timezone)

		return True

	def find_free_time_slot_in_calendar_sync(participants: list[str], slot_duration: Optional[float],
												  start_time: Optional[str], end_time: Optional[str]):
			me = ncSync.ocs('GET', '/ocs/v2.php/cloud/user')

			attendees = 'ORGANIZER:mailto:' + me['email'] + '\n'
			attendees += 'ATTENDEE:mailto:' + me['email'] + '\n'
			for attendee in participants:
				attendees += f"ATTENDEE:mailto:{attendee}\n"

			if start_time is None:
				start_time = round_to_nearest_half_hour(datetime.now(timezone.utc))
			else:
				start_time = datetime.combine(datetime.strptime(start_time, "%Y-%m-%d").date(), datetime.min.time(),
											  timezone.utc)
			if end_time is None:
				end_time = start_time + timedelta(days=7)
			else:
				end_time = datetime.combine(datetime.strptime(end_time, "%Y-%m-%d").date(), datetime.min.time(),
											timezone.utc)
				if start_time >= end_time:
					end_time = start_time + timedelta(days=7)

			dtstart = start_time.strftime("%Y%m%dT%H%M%SZ")
			dtend = end_time.strftime("%Y%m%dT%H%M%SZ")

			freebusyRequest = """
BEGIN:VCALENDAR
PRODID:-//IDN nextcloud.com//Calendar app 5.1.0-beta.2//EN
CALSCALE:GREGORIAN
VERSION:2.0
METHOD:REQUEST
BEGIN:VFREEBUSY
DTSTAMP:20250131T123029Z
UID:03c8f220-d313-4c86-ae06-19fbae157079
DTSTART:{DTSTART}
DTEND:{DTEND}
{ATTENDEES}END:VFREEBUSY
END:VCALENDAR
""".replace('{ATTENDEES}', attendees).replace('{DTSTART}', dtstart).replace('{DTEND}', dtend)
			username = ncSync._session.user
			response = ncSync._session._create_adapter(True).request('POST',
																	   f"{nc.app_cfg.endpoint}/remote.php/dav/calendars/{username}/outbox/",
																	   headers={
																		   "Content-Type": "text/calendar; charset=utf-8",
																		   "Depth": "0",
																	   }, data=freebusyRequest)
			print(freebusyRequest)
			print(response.text)

			# Parse the XML response to extract vCard data
			namespace = {"CAL": "urn:ietf:params:xml:ns:caldav"}  # Define the namespace
			root = ET.fromstring(response.text)
			vcal_elements = root.findall(".//CAL:calendar-data", namespace)
			# Parse vcal strings into dictionaries
			busy_times = []
			for vcal_element in vcal_elements:
				vcal_text = vcal_element.text.strip()
				vcal = vobject.readOne(vcal_text)
				for fb in vcal.vfreebusy.contents.get("freebusy", []):
					busy_times.append(fb.value[0])
			available_slots = find_available_slots(start_time, end_time, busy_times, timedelta(hours=slot_duration))
			return available_slots

	@tool
	@impulse(ImpulseRadius.SELF)
	async def find_free_time_slot_in_calendar(participants: list[str], slot_duration: Optional[float], start_time: Optional[str], end_time: Optional[str]):
		"""
		Finds a free time slot where all participants have time
		:param participants: The list of participants to find a free slot for (These should be email addresses. If possible use the email addresses from contacts)
		:param slot_duration: How long the time slot should be in hours, defaults to one hour
		:param start_time: the start time of the range within which to check for free slots (by default this will be now; use the following format: 2025-01-31)
		:param end_time: the end time of the range within which to check for free slots (by default this will be 7 days after start_time; use the following format: 2025-01-31)
		:return:
		"""
		available_slots = await asyncio.to_thread(find_free_time_slot_in_calendar_sync, participants, slot_duration, start_time, end_time)
		return available_slots

	def add_task_sync(calendar_name: str, title: str, description: str, due_date: Optional[str] = None,
					   due_time: Optional[str] = None, timezone: Optional[str] = None):
		description_with_ai_note = f"{description}\n\n---\n\nThis task was scheduled by Nextcloud AI Assistant."

		# Create task
		c = Calendar()
		t = Todo()
		t.name = title
		t.description = description_with_ai_note

		# Parse date and times
		if due_date:
			due_date = datetime.strptime(due_date, "%Y-%m-%d")

			# Combine date and time
			if due_time:
				due_time = datetime.strptime(due_time, "%I:%M %p").time()

				due_datetime = datetime.combine(due_date, due_time)
			else:
				due_datetime = due_date

			# Set timezone (defaults to the user's profile timezone)
			timezone = timezone or user_timezone(ncSync)
			due_datetime = pytz.timezone(get_timezone(timezone)).localize(due_datetime)

			t.due = due_datetime

		# Add event to calendar
		c.todos.add(t)

		principal = ncSync.cal.principal()
		calendars = principal.calendars()
		calendar = {cal.name: cal for cal in calendars}[calendar_name]
		calendar.add_todo(t.serialize())

		return True

	@tool
	@impulse(calendar_radius)
	async def add_task(calendar_name: str, title: str, description: str, due_date: Optional[str] = None, due_time: Optional[str] = None, timezone: Optional[str] = None):
		"""
		Crete a new task in a calendar.
		:param calendar_name: The name of the calendar to add the task to
		:param title: The title of the task
		:param description: The description of the task
		:param due_date: the due date of the event in the following form: YYYY-MM-DD e.g. '2024-12-01'
		:param due_time: the due time in the following form: HH:MM AM/PM e.g. '3:00 PM'
		:param timezone: Timezone (e.g., 'America/New_York'). Defaults to the user's profile timezone. Must be a valid IANA timezone name.
		:return: bool
		"""
		await asyncio.to_thread(add_task_sync, calendar_name, title, description, due_date, due_time, timezone)

		return True

	def list_tasks_sync(calendar_name: Optional[str] = None):
		principal = ncSync.cal.principal()
		calendars = principal.calendars()

		tasks = []
		if calendar_name:
			calendar = {cal.name: cal for cal in calendars}[calendar_name]
			calendars_to_check = [calendar]
		else:
			calendars_to_check = calendars

		for cal in calendars_to_check:
			todos = cal.todos()
			for todo in todos:
				# Parse the todo data using ics library
				try:
					ical_data = todo.data
					parsed_cal = Calendar(ical_data)

					for ics_todo in parsed_cal.todos:
						task_data = {
							'calendar': cal.name,
							'summary': ics_todo.name or '',
							'uid': ics_todo.uid or '',
							'status': ics_todo.status or 'NEEDS-ACTION',
							'due': str(ics_todo.due) if ics_todo.due else None,
							'priority': ics_todo.priority,
							'description': ics_todo.description or '',
						}
						tasks.append(task_data)
				except:
					# Fallback if parsing fails
					continue

		return tasks

	@tool
	@impulse(ImpulseRadius.SELF)
	async def list_tasks(calendar_name: Optional[str] = None, filter_status: Optional[str] = None):
		"""
		List tasks from calendars. Can filter by calendar name and status.
		:param calendar_name: Optional name of the calendar to list tasks from (obtainable via list_calendars). If not provided, lists from all calendars.
		:param filter_status: Optional filter by status - one of: 'NEEDS-ACTION', 'COMPLETED', 'IN-PROCESS', 'CANCELLED'
		:return: list of tasks with their details
		"""
		tasks = await asyncio.to_thread(list_tasks_sync, calendar_name)

		if filter_status:
			tasks = [t for t in tasks if t.get('status') == filter_status]

		return tasks

	def complete_task_sync(calendar_name: str, task_uid: str):
		principal = ncSync.cal.principal()
		calendars = principal.calendars()
		calendar = {cal.name: cal for cal in calendars}[calendar_name]

		todos = calendar.todos()
		for todo in todos:
			# Parse the todo data using ics library
			try:
				ical_data = todo.data
				parsed_cal = Calendar(ical_data)

				for ics_todo in parsed_cal.todos:
					if ics_todo.uid == task_uid:
						# Mark as completed
						ics_todo.status = 'COMPLETED'
						ics_todo.completed = datetime.now(timezone.utc)

						# Serialize and save
						todo.data = str(parsed_cal)
						todo.save()
						return True
			except:
				continue

		return False

	@tool
	@impulse(calendar_radius)
	async def complete_task(calendar_name: str, task_uid: str):
		"""
		Mark a task as completed
		:param calendar_name: The name of the calendar containing the task (obtainable via list_calendars)
		:param task_uid: The UID of the task to complete (obtainable via list_tasks)
		:return: bool indicating success
		"""
		return await asyncio.to_thread(complete_task_sync, calendar_name, task_uid)

	def update_task_sync(calendar_name: str, task_uid: str, title: Optional[str] = None, description: Optional[str] = None, due_date: Optional[str] = None, due_time: Optional[str] = None, priority: Optional[int] = None, timezone: Optional[str] = None):
		principal = ncSync.cal.principal()
		calendars = principal.calendars()
		calendar = {cal.name: cal for cal in calendars}[calendar_name]

		todos = calendar.todos()
		for todo in todos:
			# Parse the todo data using ics library
			try:
				ical_data = todo.data
				parsed_cal = Calendar(ical_data)

				for ics_todo in parsed_cal.todos:
					if ics_todo.uid == task_uid:
						# Update fields if provided
						if title:
							ics_todo.name = title
						if description:
							ics_todo.description = description
						if priority is not None:
							ics_todo.priority = priority
						if due_date:
							parsed_date = datetime.strptime(due_date, "%Y-%m-%d")
							if due_time:
								parsed_time = datetime.strptime(due_time, "%I:%M %p").time()
								due_datetime = datetime.combine(parsed_date, parsed_time)
							else:
								due_datetime = parsed_date

							timezone = timezone or user_timezone(ncSync)
							due_datetime = pytz.timezone(get_timezone(timezone)).localize(due_datetime)

							ics_todo.due = due_datetime

						# Serialize and save
						todo.data = str(parsed_cal)
						todo.save()
						return True
			except:
				continue

		return False

	@tool
	@impulse(calendar_radius)
	async def update_task(calendar_name: str, task_uid: str, title: Optional[str] = None, description: Optional[str] = None, due_date: Optional[str] = None, due_time: Optional[str] = None, priority: Optional[int] = None, timezone: Optional[str] = None):
		"""
		Update an existing task
		:param calendar_name: The name of the calendar containing the task (obtainable via list_calendars)
		:param task_uid: The UID of the task to update (obtainable via list_tasks)
		:param title: New title for the task
		:param description: New description for the task
		:param due_date: New due date in the form: YYYY-MM-DD e.g. '2024-12-01'
		:param due_time: New due time in the form: HH:MM AM/PM e.g. '3:00 PM'
		:param priority: Priority from 0 (undefined) to 9 (lowest), where 1 is highest priority
		:param timezone: Timezone (e.g., 'America/New_York'). Defaults to the user's profile timezone. Must be a valid IANA timezone name.
		:return: bool indicating success
		"""
		return await asyncio.to_thread(update_task_sync, calendar_name, task_uid, title, description, due_date, due_time, priority, timezone)

	def delete_task_sync(calendar_name: str, task_uid: str):
		principal = ncSync.cal.principal()
		calendars = principal.calendars()
		calendar = {cal.name: cal for cal in calendars}[calendar_name]

		todos = calendar.todos()
		for todo in todos:
			# Parse the todo data using ics library to find the right one
			try:
				ical_data = todo.data
				parsed_cal = Calendar(ical_data)

				for ics_todo in parsed_cal.todos:
					if ics_todo.uid == task_uid:
						# Delete the todo
						todo.delete()
						return True
			except:
				continue

		return False

	@tool
	@impulse(calendar_radius)
	@destructive
	async def delete_task(calendar_name: str, task_uid: str):
		"""
		Delete a task
		:param calendar_name: The name of the calendar containing the task (obtainable via list_calendars)
		:param task_uid: The UID of the task to delete (obtainable via list_tasks)
		:return: bool indicating success
		"""
		return await asyncio.to_thread(delete_task_sync, calendar_name, task_uid)

	return [
		list_calendars,
		schedule_event,
		find_free_time_slot_in_calendar,
		add_task,
		list_tasks,
		complete_task,
		update_task,
		delete_task
	]

def get_category_name():
	return "Calendar and Tasks"

async def is_available(nc: AsyncNextcloudApp):
	return True