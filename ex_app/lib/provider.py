from nc_py_api.ex_app.providers.task_processing import TaskProcessingProvider, TaskType, ShapeDescriptor, ShapeType

provider = TaskProcessingProvider(
	id='context_agent:agent',
	name='ContextAgent Provider',
	task_type='context_agent:interaction',
	expected_runtime=60,
)