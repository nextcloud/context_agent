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

def timed_memoize(timeout):
	def decorator(func):
		cached_result = None
		timestamp = 0

		@wraps(func)
		def wrapper(*args):
			nonlocal cached_result
			nonlocal timestamp
			current_time = time.time()
			if cached_result != None:
				if current_time - timestamp < timeout:
					return cached_result
				else:
					# Cache expired
					cached_result = None
					timestamp = 0
			# Call the function and cache the result
			result = func(*args)
			cached_result = result
			timestamp = current_time
			return result

		return wrapper
	return decorator
