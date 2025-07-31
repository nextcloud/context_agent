# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import functools
import time
from functools import wraps

def safe_tool(tool):
	setattr(tool, 'safe', True)
	return tool

def dangerous_tool(tool):
	setattr(tool, 'safe', False)
	return tool

# cache for get_tools
# needs NextcloudApp as first arg in the cached function
def timed_memoize(timeout):
	def decorator(func):
		cached_result = {}
		timestamp = {}

		@wraps(func)
		async def wrapper(*args): # needs NextcloudApp as first arg
			nonlocal cached_result
			nonlocal timestamp
			user_id = args[0].user # cache result saved per user
			current_time = time.time()
			if user_id in cached_result:
				if current_time - timestamp[user_id] < timeout:
					return cached_result[user_id]
				else:
					# Cache expired
					del cached_result[user_id]
					timestamp[user_id] = 0
			# Call the function and cache the result
			result = await func(*args)
			cached_result[user_id] = result
			timestamp[user_id] = current_time
			return result

		return wrapper
	return decorator
