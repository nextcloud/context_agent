import time
import typing
from datetime import datetime, timezone, timedelta
from time import sleep
from typing import Optional

import httpx
import pytz
from ics import Calendar, Event, Attendee, Organizer
from langchain_core.tools import tool
from nc_py_api import Nextcloud, NextcloudException
from nc_py_api.ex_app import LogLvl
from nc_py_api.talk import ConversationType
from pydantic import BaseModel, ValidationError
import xml.etree.ElementTree as ET
import vobject

from freebusy_finder import find_available_slots, round_to_nearest_half_hour
from logger import log


def get_tools(nc: Nextcloud):

	@tool
	def list_calendars():
		"""
		List all existing calendars by name
		:return:
		"""
		principal = nc.cal.principal()
		calendars = principal.calendars()
		return ", ".join([cal.name for cal in calendars])

	@tool
	def schedule_event(calendar_name: str, title: str, description: str, start_date: str, end_date: str, attendees: Optional[list[str]], start_time: Optional[str], end_time: Optional[str], location: Optional[str], timezone: Optional[str]):
		"""
		Crete a new event in a calendar. Omit start_time and end_time parameters to create an all-day event.
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


	## Talk

	@tool
	def list_talk_conversations():
		"""
		List all conversations in talk
		:return:
		"""
		conversations = nc.talk.get_user_conversations()

		return ", ".join([conv.display_name for conv in conversations])

	@tool
	def create_public_conversation(conversation_name: str) -> str:
		"""
		Create a new talk conversation
		:param conversation_name: The name of the conversation to create
		:return: The URL of the new conversation
		"""
		conversation = nc.talk.create_conversation(ConversationType.PUBLIC, room_name=conversation_name)

		return f"{nc.app_cfg.endpoint}/index.php/call/{conversation.token}"


	@tool
	def send_message_to_conversation(conversation_name: str, message: str):
		"""
		List all conversations in talk
		:param message: The message to send
		:param conversation_name: The name of the conversation to send a message to
		:return:
		"""
		conversations = nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}[conversation_name]
		nc.talk.send_message(message, conversation)

		return True

	@tool
	def list_messages_in_conversation(conversation_name: str, n_messages: int = 30):
		"""
		List messages of a conversation in talk
		:param conversation_name: The name of the conversation to list messages of
		:param n_messages: The number of messages to receive
		:return:
		"""
		conversations = nc.talk.get_user_conversations()
		conversation = {conv.display_name: conv for conv in conversations}[conversation_name]
		return [f"{m.timestamp} {m.actor_display_name}: {m.message}" for m in nc.talk.receive_messages(conversation, False, n_messages)]

	@tool
	def find_person_in_contacts(name: str) -> list[dict[str, typing.Any]]:
		"""
		Find a person's contact information from their name
		:param name: the name to search for
		:return: a dictionary with the person's email, phone and address
		"""
		username = nc._session.user
		response = nc._session._create_adapter(True).request('PROPFIND', f"{nc.app_cfg.endpoint}/remote.php/dav/addressbooks/users/{username}/", headers={
			"Content-Type": "application/xml; charset=utf-8",
		})
		print(response.text)
		namespace = {"DAV": "DAV:"}  # Define the namespace
		root = ET.fromstring(response.text)
		hrefs = root.findall(".//DAV:href", namespace)

		contacts = []

		for href in hrefs:
			link = href.text.strip()
			if not link.startswith(f"/remote.php/dav/addressbooks/users/{username}/") or not link != f"/remote.php/dav/addressbooks/users/{username}/":
				continue

			# XML body for the search
			xml_body = """<?xml version="1.0" encoding="UTF-8" ?>
	<C:addressbook-query xmlns:C="urn:ietf:params:xml:ns:carddav">
	  <D:prop xmlns:D="DAV:">
		<D:getetag/>
		<C:address-data/>
	  </D:prop>
	  <C:filter>
		<C:prop-filter name="FN">
		  <C:text-match collation="i;unicode-casemap" match-type="contains">{NAME}</C:text-match>
		</C:prop-filter>
	  </C:filter>
	</C:addressbook-query>
	""".replace('{NAME}', name)
			response = nc._session._create_adapter(True).request('REPORT', f"{nc.app_cfg.endpoint}{link}", headers={
				"Content-Type": "application/xml; charset=utf-8",
				"Depth": "1",
			}, content=xml_body)
			print(response.text)
			if response.status_code != 207:  # Multi-Status
				raise Exception(f"Error: {response.status_code} - {response.reason_phrase}")

			# Parse the XML response to extract vCard data
			namespace = {"DAV": "urn:ietf:params:xml:ns:carddav"}  # Define the namespace
			root = ET.fromstring(response.text)
			vcard_elements = root.findall(".//DAV:address-data", namespace)
			# Parse vCard strings into dictionaries
			for vcard_element in vcard_elements:
				vcard_text = vcard_element.text.strip()  # Get the raw vCard data
				vcard = vobject.readOne(vcard_text)      # Parse vCard using vobject

				# Extract fields and add to the contacts list
				contact = {
					"full_name": getattr(vcard, "fn", None).value if hasattr(vcard, "fn") else None,
					"email": getattr(vcard.email, "value", None) if hasattr(vcard, "email") else None,
					"phone": getattr(vcard.tel, "value", None) if hasattr(vcard, "tel") else None,
					"address": getattr(vcard.adr, "value", None) if hasattr(vcard, "adr") else None,
				}
				contacts.append(contact)
		return contacts

	@tool
	def get_coordinates_for_address(address: str) -> (str, str):
		"""
		Calculates the coordinates for a given address
		:param address: the address to calculate the coordinates for
		:return: a tuple of latitude and longitude
		"""
		res = httpx.get('https://nominatim.openstreetmap.org/search', params={'q': address, 'format': 'json', 'addressdetails': '1', 'extratags': '1', 'namedetails': '1', 'limit': '1'})
		json = res.json()
		if 'error' in json:
			raise Exception(json['error'])
		if len(json) == 0:
			raise Exception(f'No results for address {address}')
		return json[0]['lat'], json[0]['lon']


	@tool
	def get_current_weather_for_coordinates(lat: str, lon: str) -> dict[str, typing.Any]:
		"""
		Retrieve the current weather for a given latitude and longitude
		:param lat: Latitude
		:param lon: Longitude
		:return:
		"""
		res = httpx.get('https://api.met.no/weatherapi/locationforecast/2.0/compact', params={
			'lat': lat,
			'lon': lon,
		},
		headers={
			'User-Agent': 'NextcloudWeatherStatus/ContextAgent nextcloud.com'
		})
		json = res.json()
		if not 'properties' in json or not 'timeseries' in json['properties'] or not json['properties']['timeseries']:
			raise Exception('Could not retrieve weather for coordinates')
		return json['properties']['timeseries'][0]['data']['instant']['details']

	@tool
	def send_email(subject: str, body: str, account_id: int, from_email: str, to_emails: list[str]):
		"""
		Send an email to a list of emails
		:param subject: The subject of the email
		:param body: The body of the email
		:param account_id: The id of the account to send from
		:param to_emails: The emails to send
		"""
		i = 0
		while i < 20:
			try:
				return nc.ocs('POST', '/ocs/v2.php/apps/mail/message/send', json={
					'accountId': account_id,
					'fromEmail': from_email,
					'subject': subject,
					'body': body,
					'isHtml': False,
					'to': [{'label': '', 'email': email} for email in to_emails],
				})
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

	class Task(BaseModel):
		id: int
		status: str
		output: dict[str, typing.Any] | None = None

	class Response(BaseModel):
		task: Task

	@tool
	def ask_context_chat(question: str):
		"""
		Ask the context chat oracle, which knows all of the user's documents, a question about them
		:param question: The question to ask
		:return: the answer from context chat
		"""

		task_input = {
			'prompt': question,
			'scopeType': 'none',
			'scopeList': [],
			'scopeListMeta': '',
		}
		response = nc.ocs(
			"POST",
			"/ocs/v1.php/taskprocessing/schedule",
			json={"type": "context_chat:context_chat", "appId": "context_agent", "input": task_input},
		)

		try:
			task = Response.model_validate(response).task
			log(nc, LogLvl.DEBUG, task)

			i = 0
			# wait for 5 seconds * 60 * 2 = 10 minutes (one i ^= 5 sec)
			while task.status != "STATUS_SUCCESSFUL" and task.status != "STATUS_FAILED" and i < 60 * 2:
				time.sleep(5)
				i += 1
				try:
					response = nc.ocs("GET", f"/ocs/v1.php/taskprocessing/task/{task.id}")
				except (
					httpx.RemoteProtocolError,
					httpx.ReadError,
					httpx.LocalProtocolError,
					httpx.PoolTimeout,
				) as e:
					log(nc, LogLvl.DEBUG, "Ignored error during task polling")
					time.sleep(5)
					i += 1
					continue
				except NextcloudException as e:
					if e.status_code == 429:
						log(nc, LogLvl.INFO, "Rate limited during task polling, waiting 10s more")
						time.sleep(10)
						i += 2
						continue
					raise Exception("Nextcloud error when polling task") from e
				task = Response.model_validate(response).task
				log(nc, LogLvl.DEBUG, task)
		except ValidationError as e:
			raise Exception("Failed to parse Nextcloud TaskProcessing task result") from e

		if task.status != "STATUS_SUCCESSFUL":
			raise Exception("Nextcloud TaskProcessing Task failed")

		if not isinstance(task.output, dict) or "output" not in task.output:
			raise Exception('"output" key not found in Nextcloud TaskProcessing task result')

		return task.output['output']

	dangerous_tools = [
		schedule_event,
		send_message_to_conversation,
		send_email,
		create_public_conversation,
	]
	safe_tools = [
		list_calendars,
		list_talk_conversations,
		list_messages_in_conversation,
		ask_context_chat,
		get_coordinates_for_address,
		get_current_weather_for_coordinates,
		find_person_in_contacts,
		find_free_time_slot_in_calendar,
	]

	return safe_tools, dangerous_tools
