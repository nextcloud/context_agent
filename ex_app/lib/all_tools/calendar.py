# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone, timedelta
from time import sleep
from typing import Optional

import httpx
import pytz
from ics import Calendar, Event, Attendee, Organizer, Todo
from langchain_core.tools import tool
from nc_py_api import Nextcloud
from nc_py_api.ex_app import LogLvl
import xml.etree.ElementTree as ET
import vobject

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool, timed_memoize
from ex_app.lib.all_tools.lib.freebusy_finder import find_available_slots, round_to_nearest_half_hour
from ex_app.lib.logger import log


def get_tools(nc: Nextcloud):

	@tool
	@safe_tool
	def list_calendars():
		"""
		List all existing calendars by name
		:return:
		"""
		principal = nc.cal.principal()
		calendars = principal.calendars()
		return ", ".join([cal.name for cal in calendars])

	@tool
	@dangerous_tool
	def schedule_event(calendar_name: str, title: str, description: str, start_date: str, end_date: str, attendees: Optional[list[str]], start_time: Optional[str], end_time: Optional[str], location: Optional[str], timezone: Optional[str]):
		"""
		Crete a new event or meeting in a calendar. Omit start_time and end_time parameters to create an all-day event.
		:param calendar_name: The name of the calendar to add the event to
		:param title: The title of the event
		:param description: The description of the event
		:param start_date: the start date of the event in the following form: YYYY-MM-DD e.g. '2024-12-01'
		:param end_date: the end date of the event in the following form: YYYY-MM-DD e.g. '2024-12-01'
		:param attendees: the list of attendees to add to the event (as email addresses)
		:param start_time: the start time in the following form: HH:MM AM/PM e.g. '3:00 PM'
		:param end_time: the start time in the following form: HH:MM AM/PM e.g. '4:00 PM'
		:param location: The location of the event
		:param timezone: Timezone (e.g., 'America/New_York').
		:return: bool
		"""

		# Parse date and times
		start_date = datetime.strptime(start_date, "%Y-%m-%d")
		end_date = datetime.strptime(end_date, "%Y-%m-%d")

		# Combine date and time
		if start_time and end_time:
			start_time = datetime.strptime(start_time, "%I:%M %p").time()
			end_time = datetime.strptime(end_time, "%I:%M %p").time()

			start_datetime = datetime.combine(start_date, start_time)
			end_datetime = datetime.combine(end_date, end_time)
		else:
			start_datetime = start_date
			end_datetime = end_date

		# Set timezone
		if timezone != None:
			tz = pytz.timezone(timezone)
			start_datetime = tz.localize(start_datetime)
			end_datetime = tz.localize(end_datetime)


		# Create event
		c = Calendar()
		e = Event()
		e.name = title
		e.begin = start_datetime
		e.end = end_datetime
		e.description = description
		e.location = location
		if attendees is not None:
			for attendee in attendees:
				e.add_attendee(Attendee(common_name=attendee, email=attendee, partstat='NEEDS-ACTION', role='REQ-PARTICIPANT', cutype='INDIVIDUAL'))

		# let's check who we are...
		i = 0
		while i < 20:
			try:
				json = nc.ocs('GET', '/ocs/v2.php/cloud/user')
				break
			except (
					httpx.RemoteProtocolError,
					httpx.ReadError,
					httpx.LocalProtocolError,
					httpx.PoolTimeout,
			) as e:
				log(nc, LogLvl.DEBUG, "Ignored error during task polling")
				i += 1
				sleep(1)
				continue

		# ...and set the organizer
		e.organizer = Organizer(common_name=json['displayname'], email=json['email'])

		# Add event to calendar
		c.events.add(e)

		principal = nc.cal.principal()
		calendars = principal.calendars()
		calendar = {cal.name: cal for cal in calendars}[calendar_name]
		calendar.add_event(str(c))

		return True


	@tool
	@safe_tool
	def find_free_time_slot_in_calendar(participants: list[str], slot_duration: Optional[float], start_time: Optional[str], end_time: Optional[str]):
		"""
		Finds a free time slot where all participants have time
		:param participants: The list of participants to find a free slot for (These should be email addresses. If possible use the email addresses from contacts)
		:param slot_duration: How long the time slot should be in hours, defaults to one hour
		:param start_time: the start time of the range within which to check for free slots (by default this will be now; use the following format: 2025-01-31)
		:param end_time: the end time of the range within which to check for free slots (by default this will be 7 days after start_time; use the following format: 2025-01-31)
		:return:
		"""
		me = nc.ocs('GET', '/ocs/v2.php/cloud/user')

		attendees = 'ORGANIZER:mailto:'+me['email']+'\n'
		attendees += 'ATTENDEE:mailto:'+me['email']+'\n'
		for attendee in participants:
			attendees += f"ATTENDEE:mailto:{attendee}\n"

		if start_time is None:
			start_time = round_to_nearest_half_hour(datetime.now(timezone.utc))
		else:
			start_time = datetime.combine(datetime.strptime(start_time, "%Y-%m-%d").date(), datetime.min.time(), timezone.utc)
		if end_time is None:
			end_time = start_time + timedelta(days=7)
		else:
			end_time = datetime.combine(datetime.strptime(end_time, "%Y-%m-%d").date(), datetime.min.time(), timezone.utc)
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
		username = nc._session.user
		response = nc._session._create_adapter(True).request('POST', f"{nc.app_cfg.endpoint}/remote.php/dav/calendars/{username}/outbox/", headers={
			"Content-Type": "text/calendar; charset=utf-8",
			"Depth": "0",
		}, content=freebusyRequest)
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
		print('busy times', busy_times)
		available_slots = find_available_slots(start_time, end_time, busy_times, timedelta(hours=slot_duration))
		print('available_slots', available_slots)
		return available_slots


	@tool
	@dangerous_tool
	def add_task(calendar_name: str, title: str, description: str, due_date: Optional[str], due_time: Optional[str], timezone: Optional[str],):
		"""
		Crete a new task in a calendar. 
		:param calendar_name: The name of the calendar to add the task to
		:param title: The title of the task
		:param description: The description of the task
		:param due_date: the due date of the event in the following form: YYYY-MM-DD e.g. '2024-12-01'
		:param due_time: the due time in the following form: HH:MM AM/PM e.g. '3:00 PM'
		:param timezone: Timezone (e.g., 'America/New_York'). Is required if there is a specified due date. 
		:return: bool
		"""

		# Create task
		c = Calendar()
		t = Todo()
		t.name = title
		t.description = description

		# Parse date and times
		if due_date:
			due_date = datetime.strptime(due_date, "%Y-%m-%d")

			# Combine date and time
			if due_time:
				due_time = datetime.strptime(due_time, "%I:%M %p").time()

				due_datetime = datetime.combine(due_date, due_time)
			else:
				due_datetime = due_date

			# Set timezone
			if timezone != None:
				tz = pytz.timezone(timezone)
				due_datetime = tz.localize(due_datetime)

			t.due = due_datetime
		
		# Add event to calendar
		c.todos.add(t)

		principal = nc.cal.principal()
		calendars = principal.calendars()
		calendar = {cal.name: cal for cal in calendars}[calendar_name]
		calendar.add_todo(t.serialize())

		return True

	return [
		list_calendars,
		schedule_event,
		find_free_time_slot_in_calendar,
		add_task
	]

def get_category_name():
	return "Calendar and Tasks"

def is_available(nc: Nextcloud):
	return 'calendar' in nc.apps.get_list()