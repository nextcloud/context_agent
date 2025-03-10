from langchain_core.tools import tool
from nc_py_api import Nextcloud

from typing import Optional

from ex_app.lib.all_tools.lib.decorator import safe_tool, dangerous_tool


def get_tools(nc: Nextcloud):

	@tool
	@safe_tool
	def get_file_content(file_path: str):
		"""
		Get the content of a file
		:param file_path: the path of the file
		:return: 
		""" 

		user_id = nc.ocs('GET', '/ocs/v2.php/cloud/user')["id"]
		
		response = nc._session._create_adapter(True).request('GET', f"{nc.app_cfg.endpoint}/remote.php/dav/files/{user_id}/{file_path}", headers={
			"Content-Type": "application/json",
		})

		return response.text

	return [
		get_file_content
	]