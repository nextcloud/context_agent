# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger('context_agent')
logger.setLevel(logging.INFO)

async def log(nc, level, content):
	logger.log((level+1)*10, content)
	try:
		await nc.log(level, content)
	except asyncio.CancelledError:
		raise
	except Exception:
		pass