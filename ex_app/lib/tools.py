import time
import typing
from datetime import datetime
from time import sleep
from typing import Optional

import httpx
import pytz
from ics import Calendar, Event, Attendee, Organizer
from langchain_core.tools import tool
from nc_py_api import Nextcloud, NextcloudException
from nc_py_api.ex_app import LogLvl
from pydantic import BaseModel, ValidationError
import xml.etree.ElementTree as ET
import vobject
from ics.grammar.parse import ContentLine

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
				log(nc, LogLvl.DEBUG, "Ignored error during task polling: "+e.message)
				i += 1
				sleep(5)
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
		Retrieve all vcards in the current user's contacts that contain the given name
		:param name: the name to search for
		:return: the CardDAV xml response from the CardDAV addressbook-query for contacts matching the given name with their Full Name
		"""
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
		username = nc._session.user
		response = nc._session._create_adapter(True).request('REPORT', f"{nc.app_cfg.endpoint}/remote.php/dav/addressbooks/users/{username}/contacts/", headers={
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
		contacts = []
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
				log(nc, LogLvl.DEBUG, "Ignored error during task polling: "+e.message)
				i += 1
				sleep(5)
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
					log(nc, LogLvl.DEBUG, "Ignored error during task polling: "+e.message)
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
	]
	safe_tools = [
		list_calendars,
		list_talk_conversations,
		list_messages_in_conversation,
		ask_context_chat,
		get_coordinates_for_address,
		get_current_weather_for_coordinates,
		find_person_in_contacts,
	]

	return safe_tools, dangerous_tools
