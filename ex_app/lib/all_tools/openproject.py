# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from langchain_core.tools import tool
from nc_py_api import Nextcloud

from typing import Optional

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


def get_tools(nc: Nextcloud):
	@tool
	@safe_tool
	def list_projects():
		"""
		List all projects in OpenProject
		:return: list of projects including project IDs
		""" 
		
		return nc.ocs('GET', '/ocs/v2.php/apps/integration_openproject/api/v1/projects')

	@tool
	@safe_tool
	def list_assignees(project_id: int):
		"""
		List all available assignees of a project in OpenProject
		:param project_id: the ID of the project
		:return: list of users that can be assigned, including user IDs
		""" 
		
		return nc.ocs('GET', f'/ocs/v2.php/apps/integration_openproject/api/v1/projects/{project_id}/available-assignees')

	@tool
	@dangerous_tool
	def create_work_package(project_id: int, title: str, description: Optional[str], assignee_id: Optional[int]):
		"""
		Create a new work package in a given project in OpenProject
		:param project_id: the ID of the project the work package should be created in, obtainable with list_projects
		:param title: The title of the work package
		:param description: The description of the work package
		:param assignee_id: The ID of the user the work package should be assigned to, obtainable via list_assignees
		:return: 
		""" 

		links = {
							"project": {
								"href": f"/api/v3/projects/{project_id}",
							},
							"status": {
								"href": "/api/v3/statuses/1",
							},
							"type": {
								"href": "/api/v3/types/1",
							}
		}

		if assignee_id:
			links["assignee"] = {
				"href": f"/api/v3/users/{assignee_id}",
			}
		

		json= {
					"body": {
						"_links": links,
						"subject": title,
					},
		}

		if description:
			json["body"]["description"] = {
                        "format": "markdown",
                        "html": "",
                        "raw": description,
                    }
		
		response = nc.ocs('POST', '/ocs/v2.php/apps/integration_openproject/api/v1/create/work-packages', json=json)

		return True



	return [
		list_projects,
		list_assignees,
		create_work_package
	]