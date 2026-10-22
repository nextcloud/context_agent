# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
from nc_py_api.ex_app.providers.task_processing import TaskProcessingProvider, ShapeDescriptor, ShapeType


_optional_output_shape = [
	ShapeDescriptor(
		name="sources",
		description="Used tools",
		shape_type=ShapeType.LIST_OF_TEXTS
	)
]

_optional_input_shape = [
	ShapeDescriptor(
		name="memories",
		description="Injected memories",
		shape_type=ShapeType.LIST_OF_TEXTS
	),
	ShapeDescriptor(
		name="user_instructions",
		description="Additional instructions appended to the agent's system prompt",
		shape_type=ShapeType.TEXT
	),
	ShapeDescriptor(
		name="output_mode",
		description="How the reply is presented to the user: \"speech\" if it is read out loud (no formatting)",
		shape_type=ShapeType.TEXT
	),
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
