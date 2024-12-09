from nc_py_api.ex_app.providers.task_processing import TaskProcessingProvider, TaskType, ShapeDescriptor, ShapeType

provider = TaskProcessingProvider(
	id='context_agent:agent',
	name='ContextAgent Provider',
	task_type='context_agent:interaction',
	expected_runtime=60,
)

task_type = TaskType(
	id='context_agent:interaction',
	name='ContextAgent Interaction',
	description='Interaction with the ContextAgent',
	input_shape = [
		ShapeDescriptor(
			name = 'message',
			description = 'The new message for the conversation',
			shape_type = ShapeType.TEXT,
			type = ShapeType.TEXT,
		),
		ShapeDescriptor(
			name = 'confirmation',
			description = 'Whether to confirm actions suggested by the agent. 1 for yes, 0 for no',
			shape_type = ShapeType.NUMBER,
			type = ShapeType.NUMBER,
		),
		ShapeDescriptor(
			name = 'conversation_token',
			description = 'The token representing the conversation to invoke this interaction with',
			shape_type = ShapeType.TEXT,
			type = ShapeType.TEXT,
		),
	],
	output_shape = [
		ShapeDescriptor(
			name = 'response',
			description = 'The response from the agent',
			shape_type = ShapeType.TEXT,
			type = ShapeType.TEXT,
		),
		ShapeDescriptor(
			name = 'conversation_token',
			description = 'The token representing the conversation after this interaction',
			shape_type = ShapeType.TEXT,
			type = ShapeType.TEXT,
		),
		ShapeDescriptor(
			name = 'actions',
			description = 'A JSON array of actions the agent would like to perform',
			shape_type = ShapeType.TEXT,
			type = ShapeType.TEXT,
		),
	]
)