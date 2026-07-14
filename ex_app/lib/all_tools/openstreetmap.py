# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import urllib.parse

import niquests
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def get_coordinates_for_address(address: str) -> (str, str):
		"""
		Calculates the coordinates for a given address
		:param address: the address to calculate the coordinates for
		:return: a tuple of latitude and longitude
		"""
		res = await niquests.async_api.get('https://nominatim.openstreetmap.org/search', params={'q': address, 'format': 'json', 'addressdetails': '1', 'extratags': '1', 'namedetails': '1', 'limit': '1'})
		json = res.json()
		if 'error' in json:
			raise Exception(json['error'])
		if len(json) == 0:
			raise Exception(f'No results for address {address}')
		return json[0]['lat'], json[0]['lon']


	@tool
	@safe_tool
	async def get_osm_route(profile: str, origin_lat: str, origin_lon: str, destination_lat: str, destination_lon: str,):
		"""
		Retrieve a route between two coordinates traveled by foot, car or bike
		:param profile: the kind of transport used to travel the route. Available are 'routed-bike', 'routed-foot', 'routed-car'
		:param origin_lat: Latitude of the starting point
		:param origin_lon: Longitude of the starting point
		:param destination_lat: Latitude of the destination
		:param destination_lon: Longitude of the destination
		:return: a JSON description of the route and a URL to show the route on a map
		"""

		match profile:
			case "routed-car":
				profile_num = "0"
			case "routed-bike":
				profile_num = "1"
			case "routed-foot":
				profile_num = "2"
			case _:
				profile = "routed-foot"
				profile_num = "2"
		# URL-encode each coordinate so untrusted values can't break out of the
		# path segment (commas/semicolons are intentionally kept as separators
		# expected by the OSRM route endpoint).
		o_lat = urllib.parse.quote(origin_lat, safe='')
		o_lon = urllib.parse.quote(origin_lon, safe='')
		d_lat = urllib.parse.quote(destination_lat, safe='')
		d_lon = urllib.parse.quote(destination_lon, safe='')
		url = f'https://routing.openstreetmap.de/{profile}/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}'
		map_url = 'https://routing.openstreetmap.de/?' + urllib.parse.urlencode([
			('loc', f'{origin_lat},{origin_lon}'),
			('loc', f'{destination_lat},{destination_lon}'),
			('srv', profile_num),
		])
		res = await niquests.async_api.get(url, params={'overview': 'false', 'steps': 'true'})
		json = res.json()
		return {'route_json_description': json, 'map_url': map_url}

	
	@tool
	@safe_tool
	async def get_osm_link(location: str):
		"""
		Retrieve a URL for a map of a given location.
		:param location: location name or address
		:return: URL
		"""

		res = await niquests.async_api.get('https://nominatim.openstreetmap.org/search', params={'q': location, 'format': 'json','limit': '1'})
		json = res.json()
		if 'error' in json:
			raise Exception(json['error'])
		if len(json) == 0:
			raise Exception(f'No results for address {location}')
		osm_id = json[0]['osm_id']
		osm_type = json[0]['osm_type']
		link = f'https://www.openstreetmap.org/{osm_type}/{osm_id}'
		return link
	

	return [
		get_coordinates_for_address,
		get_osm_route,
		get_osm_link,
	]

def get_category_name():
	return "OpenStreetMap"

async def is_available(nc: AsyncNextcloudApp):
	return True