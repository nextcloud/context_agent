# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import typing
from typing import Optional
import datetime
import urllib.parse
from time import sleep

import httpx
from langchain_core.tools import tool
from nc_py_api import Nextcloud

from ex_app.lib.all_tools.lib.decorator import safe_tool


def get_tools(nc: Nextcloud):
	@tool
	@safe_tool
	def get_coordinates_for_address(address: str) -> (str, str):
		"""
		Calculates the coordinates for a given address
		When using this tool, you must let the user know that the internet service Open Street Map was used.
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
	@safe_tool
	def get_current_weather_for_coordinates(lat: str, lon: str) -> dict[str, typing.Any]:
		"""
		Retrieve the current weather for a given latitude and longitude
		When using this tool, you must let the user know that the internet service met.no was used.
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
		get_coordinates_for_address,
		get_current_weather_for_coordinates,
		get_public_transport_route_for_coordinates
	]