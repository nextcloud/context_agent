# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import typing
import datetime
import urllib.parse

import httpx
from langchain_core.tools import tool
from nc_py_api import Nextcloud

from ex_app.lib.all_tools.lib.decorator import safe_tool


async def get_tools(nc: Nextcloud):

	@tool
	@safe_tool
	def get_public_transport_route_for_coordinates(origin_lat: str, origin_lon: str, destination_lat: str, destination_lon: str, routes: int, departure_time: str | None = None):
		"""
		Retrieve a public transport route between two coordinates
		:param origin_lat: Latitude of the starting point
		:param origin_lon: Longitude of the starting point
		:param destination_lat: Latitude of the destination
		:param destination_lon: Longitude of the destination
		:param routes: the number of routes returned
		:param departure_time: time of departure, formatted like '2019-06-24T01:23:45'. Optional, leave empty for the next routes from now
		:return: the routes, times are given in local time according to origin and destination
		"""

		if departure_time is None:
			departure_time = urllib.parse.quote_plus(datetime.datetime.now(datetime.UTC).isoformat())
		api_key = nc.appconfig_ex.get_value('here_api')
		res = httpx.get('https://transit.hereapi.com/v8/routes?transportMode=car&origin=' 
			+ origin_lat + ',' + origin_lon + '&destination=' + destination_lat + ',' + destination_lon 
			+ '&alternatives=' + str(routes-1) + '&departureTime=' + departure_time + '&apikey=' + api_key)
		json = res.json()
		return json

	return [
		get_public_transport_route_for_coordinates,
	]

def get_category_name():
	return "Public transport"

def is_available(nc: Nextcloud):
	return nc.appconfig_ex.get_value('here_api') != ''