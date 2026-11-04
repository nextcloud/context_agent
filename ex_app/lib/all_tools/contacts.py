# SPDX-FileCopyrightText: 2025 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import typing
import uuid
from xml.sax.saxutils import escape

from langchain_core.tools import tool
from nc_py_api import AsyncNextcloudApp
import xml.etree.ElementTree as ET
import vobject

from ex_app.lib.all_tools.lib.audience import principal_radius
from ex_app.lib.all_tools.lib.impulse import ImpulseRadius, destructive, impulse


def _format_address(value) -> str | None:
	parts = [getattr(value, field, "") for field in ("box", "extended", "street", "city", "region", "code", "country")]
	formatted = ", ".join(str(part).strip() for part in parts if str(part).strip())
	return formatted or None


def _contact_fields(vcard) -> dict[str, typing.Any]:
	return {
		"uid": getattr(vcard, "uid", None).value if hasattr(vcard, "uid") else None,
		"full_name": getattr(vcard, "fn", None).value if hasattr(vcard, "fn") else None,
		"email": getattr(vcard.email, "value", None) if hasattr(vcard, "email") else None,
		"phone": getattr(vcard.tel, "value", None) if hasattr(vcard, "tel") else None,
		"address": _format_address(vcard.adr.value) if hasattr(vcard, "adr") else None,
	}


async def get_tools(nc: AsyncNextcloudApp):
	ADDRESSBOOK_PROPFIND = (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
		'<d:prop><d:displayname/><d:resourcetype/><d:owner/><oc:invite/></d:prop>'
		'</d:propfind>'
	)

	async def list_addressbooks() -> list[dict[str, typing.Any]]:
		username = await nc._session.user
		response = await nc._session._create_adapter(True).request(
			"PROPFIND",
			f"{nc.app_cfg.endpoint}/remote.php/dav/addressbooks/users/{username}/",
			headers={
				"Content-Type": "application/xml; charset=utf-8",
				"Depth": "1",
			},
			data=ADDRESSBOOK_PROPFIND,
		)
		if response.status_code != 207:
			raise ValueError(f"Unable to list address books: HTTP {response.status_code}")

		namespaces = {
			"DAV": "DAV:",
			"CARD": "urn:ietf:params:xml:ns:carddav",
			"OC": "http://owncloud.org/ns",
		}
		root = ET.fromstring(response.text)  # noqa: S314 - trusted CardDAV server response
		addressbooks = []
		for item in root.findall("DAV:response", namespaces):
			if item.find(".//CARD:addressbook", namespaces) is None:
				continue
			href = item.findtext("DAV:href", default="", namespaces=namespaces).strip()
			display_name = item.findtext(".//DAV:displayname", default="", namespaces=namespaces).strip()
			if href:
				radius = ImpulseRadius.SELF
				owner = item.find(".//DAV:owner/DAV:href", namespaces)
				if owner is not None and (owner.text or "").rstrip("/").rsplit("/", 1)[-1] != username:
					radius = max(radius, ImpulseRadius.INDIVIDUALS)
				for sharee in item.findall(".//OC:invite/OC:user/DAV:href", namespaces):
					radius = max(radius, principal_radius(sharee.text))
				addressbooks.append(
					{
						"href": href,
						"name": display_name or href.rstrip("/").rsplit("/", 1)[-1],
						"radius": radius,
					}
				)
		return addressbooks

	def select_addressbook(addressbooks, addressbook_name):
		if addressbook_name is None:
			if not addressbooks:
				raise ValueError("No writable address book was found")
			return addressbooks[0]
		addressbook = next(
			(book for book in addressbooks if book["name"].casefold() == addressbook_name.casefold()),
			None,
		)
		if addressbook is None:
			raise ValueError(f"Address book not found: {addressbook_name}")
		return addressbook

	async def addressbook_radius(addressbook_name: str | None = None):
		return select_addressbook(await list_addressbooks(), addressbook_name)["radius"]

	async def search_contacts(name: str) -> list[dict[str, typing.Any]]:
		found_contacts = []
		for addressbook in await list_addressbooks():
			xml_body = (
				'<?xml version="1.0" encoding="UTF-8" ?>'
				'<C:addressbook-query xmlns:C="urn:ietf:params:xml:ns:carddav">'
				'<D:prop xmlns:D="DAV:"><D:getetag/><C:address-data/></D:prop>'
				'<C:filter><C:prop-filter name="FN">'
				'<C:text-match collation="i;unicode-casemap" match-type="contains">'
				f"{escape(name)}"
				"</C:text-match></C:prop-filter></C:filter>"
				"</C:addressbook-query>"
			)
			response = await nc._session._create_adapter(True).request(
				"REPORT",
				f"{nc.app_cfg.endpoint}{addressbook['href']}",
				headers={
					"Content-Type": "application/xml; charset=utf-8",
					"Depth": "1",
				},
				data=xml_body,
			)

			if response.status_code != 207:
				raise ValueError(f"Unable to search contacts: HTTP {response.status_code}")

			namespaces = {
				"DAV": "DAV:",
				"CARD": "urn:ietf:params:xml:ns:carddav",
			}
			root = ET.fromstring(response.text)  # noqa: S314 - trusted CardDAV server response
			for item in root.findall("DAV:response", namespaces):
				vcard_element = item.find(".//CARD:address-data", namespaces)
				if vcard_element is None or not vcard_element.text:
					continue
				vcard = vobject.readOne(vcard_element.text.strip())
				found_contacts.append(
					{
						**_contact_fields(vcard),
						"href": item.findtext("DAV:href", default="", namespaces=namespaces).strip(),
						"etag": item.findtext(".//DAV:getetag", default="", namespaces=namespaces).strip(),
						"addressbook": addressbook["name"],
						"radius": addressbook["radius"],
						"_vcard": vcard,
					}
				)
		return found_contacts

	async def contact_radius(name: str):
		matches = await search_contacts(name)
		if not matches:
			raise ValueError(f"No contact found for name: {name}")
		if len(matches) > 1:
			raise ValueError(f"Multiple contacts found for name: {name}")
		return matches[0]["radius"]

	@tool
	@impulse(ImpulseRadius.SELF)
	async def find_person_in_users(search_term: str):
		"""
		Search for users
		:param search_term: The search term to find the user by. Use this to find a user's ID
		Returns:
		"""
		users = await nc.ocs('GET', '/ocs/v2.php/apps/files_sharing/api/v1/sharees', params={
			'search': search_term,
			'shareType': 0,
			'itemType': 'user',
		})
		dict = {}
		for user in users.get('exact', {}).get('users', []) + users.get('users', []):
			dict[user.get('label')] = user.get('value', {}).get('shareWith')
		return json.dumps(dict)

	@tool
	@impulse(ImpulseRadius.SELF)
	async def find_person_in_contacts(name: str) -> list[dict[str, typing.Any]]:
		"""
		Find a person's contact information from their name
		:param name: the name to search for
		:return: a dictionary with the person's email, phone and address
		"""
		return [
			{key: contact[key] for key in ("full_name", "email", "phone", "address")}
			for contact in await search_contacts(name)
		]

	@tool
	@impulse(ImpulseRadius.SELF)
	async def find_details_of_current_user() -> dict[str, typing.Any]:
		"""
		Find the current user's personal information, such as name, location, timezone, language
		:return: a dictionary with the person's personal information
		"""

		return await nc.ocs('GET', '/ocs/v2.php/cloud/user')

	@tool
	@impulse(addressbook_radius)
	async def create_contact(
		full_name: str,
		email: str | None = None,
		phone: str | None = None,
		address: str | None = None,
		addressbook_name: str | None = None,
	) -> dict[str, str]:
		"""
		Create a contact in one of the current user's address books
		:param full_name: the contact's full name
		:param email: optional email address
		:param phone: optional phone number
		:param address: optional street address
		:param addressbook_name: optional address book name; the first available address book is used by default
		:return: the created contact's UID, name, and address book
		"""
		if not full_name.strip():
			raise ValueError("The contact's full name cannot be empty")

		addressbook = select_addressbook(await list_addressbooks(), addressbook_name)

		contact_uid = str(uuid.uuid4())
		vcard = vobject.vCard()
		vcard.add("uid").value = contact_uid
		vcard.add("fn").value = full_name.strip()
		vcard.add("n").value = vobject.vcard.Name(given=full_name.strip())
		if email:
			vcard.add("email").value = email
		if phone:
			vcard.add("tel").value = phone
		if address:
			vcard.add("adr").value = vobject.vcard.Address(street=address)

		href = f"{addressbook['href'].rstrip('/')}/{contact_uid}.vcf"
		response = await nc._session._create_adapter(True).request(
			"PUT",
			f"{nc.app_cfg.endpoint}{href}",
			headers={
				"Content-Type": "text/vcard; charset=utf-8",
				"If-None-Match": "*",
			},
			data=vcard.serialize(),
		)
		if response.status_code not in (201, 204):
			raise ValueError(f"Unable to create contact: HTTP {response.status_code}")

		return {
			"uid": contact_uid,
			"full_name": full_name.strip(),
			"addressbook": addressbook["name"],
		}

	@tool
	@impulse(contact_radius)
	@destructive
	async def update_contact(
		name: str,
		full_name: str | None = None,
		email: str | None = None,
		phone: str | None = None,
		address: str | None = None,
	) -> dict[str, typing.Any]:
		"""
		Update a uniquely matching contact while preserving unspecified fields
		:param name: the current contact name to search for
		:param full_name: optional replacement full name
		:param email: optional replacement email address
		:param phone: optional replacement phone number
		:param address: optional replacement street address
		:return: the updated contact
		"""
		if all(value is None for value in (full_name, email, phone, address)):
			raise ValueError("At least one contact field must be provided")

		matches = await search_contacts(name)
		if not matches:
			raise ValueError(f"No contact found for name: {name}")
		if len(matches) > 1:
			raise ValueError(f"Multiple contacts found for name: {name}")

		contact = matches[0]
		vcard = contact["_vcard"]
		if not contact["uid"]:
			raise ValueError("The matching contact has no UID")
		if not contact["href"] or not contact["etag"]:
			raise ValueError("The matching contact has no CardDAV identity")

		if full_name is not None:
			if not full_name.strip():
				raise ValueError("The contact's full name cannot be empty")
			vcard.fn.value = full_name.strip()
			if hasattr(vcard, "n"):
				vcard.n.value = vobject.vcard.Name(given=full_name.strip())
		if email is not None:
			(vcard.email if hasattr(vcard, "email") else vcard.add("email")).value = email
		if phone is not None:
			(vcard.tel if hasattr(vcard, "tel") else vcard.add("tel")).value = phone
		if address is not None:
			(vcard.adr if hasattr(vcard, "adr") else vcard.add("adr")).value = vobject.vcard.Address(street=address)

		response = await nc._session._create_adapter(True).request(
			"PUT",
			f"{nc.app_cfg.endpoint}{contact['href']}",
			headers={
				"Content-Type": "text/vcard; charset=utf-8",
				"If-Match": contact["etag"],
			},
			data=vcard.serialize(),
		)
		if response.status_code == 412:
			raise ValueError("The contact changed since it was found; search again before updating it")
		if response.status_code not in (201, 204):
			raise ValueError(f"Unable to update contact: HTTP {response.status_code}")

		return {
			**_contact_fields(vcard),
			"addressbook": contact["addressbook"],
		}

	return [
		find_person_in_users,
		find_person_in_contacts,
		find_details_of_current_user,
		create_contact,
		update_contact,
	]

def get_category_name():
	return "Contacts"

async def is_available(nc: AsyncNextcloudApp):
	return True
