import re

def get_file_id_from_file_url(file_url) -> int:
	# Define the regex pattern to capture only the digits
	pattern = r"https?://[a-zA-Z-_.:0-9]+/(index\.php/)?f/(\d+)"
	match = re.search(pattern, file_url)

	if match:
		return int(match.group(2))
	else:
		raise Exception("Not a valid nextcloud file URL")