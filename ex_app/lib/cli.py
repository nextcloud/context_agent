from agent import react
from nc_py_api import Nextcloud

nc = Nextcloud(nextcloud_url="https://cloud.nextcloud.com", nc_auth_user="marcelk", nc_auth_pass="RcSNc-zixF6-FrW5L-x4xEr-2RDPP")

task = {
	'message': input('User: '),
	'confirmation': 0,
	'conversation_token': '',
}

print(f"conf:{task['confirmation']}; message: {task['message']}")

response = react(task, nc)

while True:
	print(f"actions:{response['actions']}; message: {response['message']}")

	if response['actions'] != '':
		confirmation = input('Confirm? (y for yes; Or say what is wrong)')
		if confirmation != 'y':
			confirmation_int = 0
			message = confirmation
		else:
			confirmation_int = 1
			message = ''

		task = {
			'message': message,
			'confirmation': confirmation_int,
			'conversation_token': response['conversation_token'],
		}
	else:
		task = {
			'message': input('User: '),
			'confirmation': 0,
			'conversation_token': response['conversation_token'],
		}

	print(f"conf:{task['confirmation']}; message: {task['message']}")
	response = react({'input': task}, nc)
