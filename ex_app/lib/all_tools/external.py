# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import typing
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

	return [
		get_coordinates_for_address,
		get_current_weather_for_coordinates,
	]