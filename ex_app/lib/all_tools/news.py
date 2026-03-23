# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_news_feeds():
		"""
		List all RSS/news feeds
		:return: list of feeds with their id, title, and URL
		"""
		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/feeds", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def list_news_folders():
		"""
		List all news feed folders
		:return: list of folders with their id and name
		"""
		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/folders", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def get_unread_articles(limit: int = 50, feed_id: Optional[int] = None):
		"""
		Get unread news articles
		:param limit: maximum number of articles to return (default 50)
		:param feed_id: optional feed id to filter articles from a specific feed (obtainable via list_news_feeds)
		:return: list of unread articles with title, body, URL, etc.
		"""
		params = {
			'batchSize': limit,
			'type': 3,  # 3 = all items
			'getRead': 'false'
		}
		if feed_id:
			params['id'] = feed_id

		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/items", headers={
			"Content-Type": "application/json",
		}, params=params)
		return response.json()

	@tool
	@safe_tool
	async def get_articles(limit: int = 50, feed_id: Optional[int] = None, unread_only: bool = False):
		"""
		Get news articles with optional filtering
		:param limit: maximum number of articles to return (default 50)
		:param feed_id: optional feed id to filter articles (obtainable via list_news_feeds)
		:param unread_only: if True, returns only unread articles
		:return: list of articles
		"""
		params = {
			'batchSize': limit,
			'type': 3,  # 3 = all items
			'getRead': 'false' if unread_only else 'true'
		}
		if feed_id:
			params['id'] = feed_id

		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/items", headers={
			"Content-Type": "application/json",
		}, params=params)
		return response.json()

	@tool
	@dangerous_tool
	async def add_news_feed(url: str, folder_id: Optional[int] = None):
		"""
		Add a new RSS/news feed
		:param url: the URL of the RSS/Atom feed
		:param folder_id: optional folder id to organize the feed (obtainable via list_news_folders)
		:return: the created feed
		"""
		payload = {
			'url': url
		}
		if folder_id is not None:
			payload['folderId'] = folder_id

		response = await nc._session._create_adapter().request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/feeds", headers={
			"Content-Type": "application/json",
		}, json=payload)
		return response.json()

	@tool
	@dangerous_tool
	async def mark_article_as_read(article_id: int):
		"""
		Mark an article as read
		:param article_id: the id of the article (obtainable via get_unread_articles or get_articles)
		:return: confirmation
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/items/{article_id}/read", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def mark_article_as_unread(article_id: int):
		"""
		Mark an article as unread
		:param article_id: the id of the article (obtainable via get_articles)
		:return: confirmation
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/items/{article_id}/unread", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def mark_feed_as_read(feed_id: int):
		"""
		Mark all articles in a feed as read
		:param feed_id: the id of the feed (obtainable via list_news_feeds)
		:return: confirmation
		"""
		response = await nc._session._create_adapter().request('PUT', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/feeds/{feed_id}/read", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def delete_news_feed(feed_id: int):
		"""
		Delete a news feed
		:param feed_id: the id of the feed to delete (obtainable via list_news_feeds)
		:return: confirmation of deletion
		"""
		response = await nc._session._create_adapter().request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/feeds/{feed_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def create_news_folder(name: str):
		"""
		Create a new folder to organize news feeds
		:param name: name for the folder
		:return: the created folder
		"""
		response = await nc._session._create_adapter().request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/news/api/v1-3/folders", headers={
			"Content-Type": "application/json",
		}, json={'name': name})
		return response.json()

	return [
		list_news_feeds,
		list_news_folders,
		get_unread_articles,
		get_articles,
		add_news_feed,
		mark_article_as_read,
		mark_article_as_unread,
		mark_feed_as_read,
		delete_news_feed,
		create_news_folder
	]

def get_category_name():
	return "News/RSS"

async def is_available(nc: AsyncNextcloudApp):
	return 'news' in await nc.capabilities
