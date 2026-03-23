# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Optional
from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


async def get_tools(nc: AsyncNextcloudApp):
	@tool
	@safe_tool
	async def list_recipes(category: Optional[str] = None):
		"""
		List all recipes or filter by category
		:param category: optional category to filter recipes
		:return: list of recipes with their id, name, and basic info
		"""
		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/cookbook/api/v1/recipes", headers={
			"Content-Type": "application/json",
		})
		print(response.text)
		recipes = response.json()

		if category:
			recipes = [r for r in recipes if r.get('recipeCategory', '').lower() == category.lower()]

		return recipes

	@tool
	@safe_tool
	async def search_recipes(search_term: str):
		"""
		Search for recipes by keyword
		:param search_term: text to search for in recipe names and descriptions
		:return: list of matching recipes
		"""
		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/cookbook/api/v1/search/{search_term}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def get_recipe_details(recipe_id: int):
		"""
		Get complete details of a recipe including ingredients and instructions
		:param recipe_id: the id of the recipe (obtainable via list_recipes or search_recipes)
		:return: complete recipe information
		"""
		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/cookbook/api/v1/recipes/{recipe_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@dangerous_tool
	async def create_recipe(name: str, description: Optional[str] = None, ingredients: Optional[list[str]] = None, instructions: Optional[str] = None, prep_time: Optional[str] = None, cook_time: Optional[str] = None, category: Optional[str] = None):
		"""
		Create a new recipe
		:param name: name of the recipe
		:param description: optional description
		:param ingredients: list of ingredients (e.g., ["2 cups flour", "1 tsp salt"])
		:param instructions: cooking instructions
		:param prep_time: preparation time in ISO 8601 duration format (e.g., "PT30M" for 30 minutes)
		:param cook_time: cooking time in ISO 8601 duration format
		:param category: recipe category (e.g., "Dessert", "Main Course")
		:return: the created recipe
		"""
		description_with_ai_note = f"{description or ''}\n\nCreated by Nextcloud AI Assistant."

		recipe_data = {
			'name': name,
			'description': description_with_ai_note
		}

		if ingredients:
			recipe_data['recipeIngredient'] = ingredients
		if instructions:
			recipe_data['recipeInstructions'] = instructions
		if prep_time:
			recipe_data['prepTime'] = prep_time
		if cook_time:
			recipe_data['cookTime'] = cook_time
		if category:
			recipe_data['recipeCategory'] = category

		response = await nc._session._create_adapter().request('POST', f"{nc.app_cfg.endpoint}/index.php/apps/cookbook/api/v1/recipes", headers={
			"Content-Type": "application/json",
		}, json=recipe_data)
		return response.json()


	@tool
	@dangerous_tool
	async def delete_recipe(recipe_id: int):
		"""
		Delete a recipe
		:param recipe_id: the id of the recipe to delete (obtainable via list_recipes)
		:return: confirmation of deletion
		"""
		response = await nc._session._create_adapter().request('DELETE', f"{nc.app_cfg.endpoint}/index.php/apps/cookbook/api/v1/recipes/{recipe_id}", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	@tool
	@safe_tool
	async def list_recipe_categories():
		"""
		List all recipe categories
		:return: list of category names
		"""
		response = await nc._session._create_adapter().request('GET', f"{nc.app_cfg.endpoint}/index.php/apps/cookbook/api/v1/categories", headers={
			"Content-Type": "application/json",
		})
		return response.json()

	return [
		list_recipes,
		search_recipes,
		get_recipe_details,
		create_recipe,
		delete_recipe,
		list_recipe_categories
	]

def get_category_name():
	return "Cookbook"

async def is_available(nc: AsyncNextcloudApp):
	return 'cookbook' in await nc.capabilities
