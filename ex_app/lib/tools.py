from datetime import datetime
from typing import Optional

import pytz
from ics import Calendar, Event
from langchain_core.tools import tool
from nc_py_api import Nextcloud


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
	def schedule_event(calendar_name: str, title: str, description: str, start_date: str, end_date: str, start_time: Optional[str], end_time: Optional[str], location: Optional[str], timezone: Optional[str]):
		"""
		Crete a new event in a calendar. Omit start_time and end_time parameters to create an all-day event.
		:param calendar_name: The name of the calendar to add the event to
		:param title: The title of the event
		:param description: The description of the event
		:param start_date: the start date of the event in the following form: YYYY-MM-DD e.g. '2024-12-01'
		:param end_date: the end date of the event in the following form: YYYY-MM-DD e.g. '2024-12-01'
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

	dangerous_tools = [
		schedule_event,
		send_message_to_conversation
	]
	safe_tools = [
		list_calendars,
		list_talk_conversations,
		list_messages_in_conversation,
		ask_context_chat,
	]

	return safe_tools, dangerous_tools
