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

	@tool
	@safe_tool
	def get_osm_route(profile: str, origin_lat: str, origin_lon: str, destination_lat: str, destination_lon: str,):
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
		url = f'https://routing.openstreetmap.de/{profile}/route/v1/driving/{origin_lon},{origin_lat};{destination_lon},{destination_lat}?overview=false&steps=true'
		map_url = f' https://routing.openstreetmap.de/?loc={origin_lat}%2C{origin_lon}&loc={destination_lat}%2C{destination_lon}&srv={profile_num}'
		res = httpx.get(url)
		json = res.json()
		return {'route_json_description': json, 'map_url': map_url}

	
	@tool
	@safe_tool
	def get_osm_link(location: str):
		"""
		Retrieve a URL for a map of a given location. 
		:param location: location name or address
		:return: URL
		"""

		res = httpx.get('https://nominatim.openstreetmap.org/search', params={'q': location, 'format': 'json','limit': '1'})
		json = res.json()
		if 'error' in json:
			raise Exception(json['error'])
		if len(json) == 0:
			raise Exception(f'No results for address {address}')
		osm_id = json[0]['osm_id']
		osm_type = json[0]['osm_type']
		link = f'https://www.openstreetmap.org/{osm_type}/{osm_id}'
		return link
	

	return [
		get_coordinates_for_address,
		get_current_weather_for_coordinates,
		get_public_transport_route_for_coordinates,
		get_osm_route,
		get_osm_link,
	]