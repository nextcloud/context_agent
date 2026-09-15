# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later


class UserFacingError(Exception):
	"""An error carrying a message that is safe to show to the end user.

	Nextcloud TaskProcessing keeps two error strings per task: `errorMessage` for
	admins/logs and `userFacingErrorMessage` for the user. Sub-tasks we schedule
	(text2text chat, image generation, …) only expose the latter over the OCS API,
	so we carry it along and report it on our own task as well.
	"""

	def __init__(self, message: str, user_facing_message: str | None = None):
		super().__init__(message)
		self.user_facing_message = user_facing_message
