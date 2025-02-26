from langchain_core.tools import tool
from nc_py_api import Nextcloud

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


def get_tools(nc: Nextcloud):
	@tool
	@safe_tool
	def list_projects():
		"""
		List all projects in OpenProject
		:return: list of projects
		""" 
		
		return nc.ocs('GET', '/ocs/v2.php/apps/integration_openproject/api/v1/projects')


	return [
		list_projects,
	]