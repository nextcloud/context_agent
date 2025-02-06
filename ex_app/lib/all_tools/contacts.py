import typing

from langchain_core.tools import tool
from nc_py_api import Nextcloud
import xml.etree.ElementTree as ET
import vobject

from ex_app.lib.all_tools.lib.decorator import safe_tool


def get_tools(nc: Nextcloud):
	@tool
	@safe_tool
	def find_person_in_contacts(name: str) -> list[dict[str, typing.Any]]:
		"""
		Find a person's contact information from their name
		:param name: the name to search for
		:return: a dictionary with the person's email, phone and address
		"""
		username = nc._session.user
		response = nc._session._create_adapter(True).request('PROPFIND', f"{nc.app_cfg.endpoint}/remote.php/dav/addressbooks/users/{username}/", headers={
			"Content-Type": "application/xml; charset=utf-8",
		})
		print(response.text)
		namespace = {"DAV": "DAV:"}  # Define the namespace
		root = ET.fromstring(response.text)
		hrefs = root.findall(".//DAV:href", namespace)

		contacts = []

		for href in hrefs:
			link = href.text.strip()
			if not link.startswith(f"/remote.php/dav/addressbooks/users/{username}/") or not link != f"/remote.php/dav/addressbooks/users/{username}/":
				continue

			# XML body for the search
			xml_body = """<?xml version="1.0" encoding="UTF-8" ?>
	<C:addressbook-query xmlns:C="urn:ietf:params:xml:ns:carddav">
	  <D:prop xmlns:D="DAV:">
		<D:getetag/>
		<C:address-data/>
	  </D:prop>
	  <C:filter>
		<C:prop-filter name="FN">
		  <C:text-match collation="i;unicode-casemap" match-type="contains">{NAME}</C:text-match>
		</C:prop-filter>
	  </C:filter>
	</C:addressbook-query>
	""".replace('{NAME}', name)
			response = nc._session._create_adapter(True).request('REPORT', f"{nc.app_cfg.endpoint}{link}", headers={
				"Content-Type": "application/xml; charset=utf-8",
				"Depth": "1",
			}, content=xml_body)
			print(response.text)
			if response.status_code != 207:  # Multi-Status
				raise Exception(f"Error: {response.status_code} - {response.reason_phrase}")

			# Parse the XML response to extract vCard data
			namespace = {"DAV": "urn:ietf:params:xml:ns:carddav"}  # Define the namespace
			root = ET.fromstring(response.text)
			vcard_elements = root.findall(".//DAV:address-data", namespace)
			# Parse vCard strings into dictionaries
			for vcard_element in vcard_elements:
				vcard_text = vcard_element.text.strip()  # Get the raw vCard data
				vcard = vobject.readOne(vcard_text)      # Parse vCard using vobject

				# Extract fields and add to the contacts list
				contact = {
					"full_name": getattr(vcard, "fn", None).value if hasattr(vcard, "fn") else None,
					"email": getattr(vcard.email, "value", None) if hasattr(vcard, "email") else None,
					"phone": getattr(vcard.tel, "value", None) if hasattr(vcard, "tel") else None,
					"address": getattr(vcard.adr, "value", None) if hasattr(vcard, "adr") else None,
				}
				contacts.append(contact)
		return contacts

	@tool
	@safe_tool
	def find_details_of_current_user() -> dict[str, typing.Any]:
		"""
		Find the user's personal information
		:return: a dictionary with the person's personal information
		"""

		return nc.ocs('GET', '/ocs/v2.php/cloud/user')


	return [
		find_person_in_contacts, find_details_of_current_user
	]