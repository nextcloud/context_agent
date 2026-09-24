# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from nc_py_api.ex_app.providers.task_processing import TaskProcessingProvider, ShapeDescriptor, ShapeType


_optional_output_shape = [
	ShapeDescriptor(
		name="sources",
		description="Used tools",
		shape_type=ShapeType.LIST_OF_TEXTS
	),
	# Both describe the actions in the `actions` output and are only set when
	# there are any, i.e. when the task is waiting for the user's confirmation.
	ShapeDescriptor(
		name="impulse_radius",
		description="How far the actions awaiting confirmation reach: "
		            "'self', 'individuals', 'group' or 'external'",
		shape_type=ShapeType.TEXT
	),
	ShapeDescriptor(
		name="destructive",
		description="Whether the actions awaiting confirmation delete or overwrite something (1) or not (0)",
		shape_type=ShapeType.NUMBER
	)
]

_optional_input_shape = [
	ShapeDescriptor(
		name="memories",
		description="Injected memories",
		shape_type=ShapeType.LIST_OF_TEXTS
	)
]

provider = TaskProcessingProvider(
	id='context_agent:agent',
	name='ContextAgent Provider',
	task_type='core:contextagent:interaction',
	expected_runtime=60,
	optional_output_shape=_optional_output_shape,
	optional_input_shape=_optional_input_shape,
)

multimodal_provider = TaskProcessingProvider(
	id='context_agent:agent_multimodal',
	name='ContextAgent Multimodal Provider',
	task_type='core:contextagent:multimodal-interaction',
	expected_runtime=60,
	optional_output_shape=_optional_output_shape,
	optional_input_shape=_optional_input_shape,
)
