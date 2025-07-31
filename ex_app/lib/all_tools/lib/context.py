# SPDX-FileCopyrightText: 2024 LangChain, Inc.
# SPDX-License-Identifier: MIT
from fastmcp.server.dependencies import get_context


def get_nextcloud(nc):
	try:
		ctx = get_context()
		return ctx.get_state('nextcloud')
	except RuntimeError:
		return nc