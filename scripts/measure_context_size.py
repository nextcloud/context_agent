#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Measure the context size (chars and estimated tokens) of the system prompt,
tool categories, and individual tool schemas sent to the LLM.

Usage:
	python scripts/measure_context_size.py

Estimated tokens use the rough heuristic of 1 token ≈ 4 chars.
"""
import ast
import json
import os
import sys

TOOL_DIR = os.path.join(os.path.dirname(__file__), '..', 'ex_app', 'lib', 'all_tools')
AGENT_FILE = os.path.join(os.path.dirname(__file__), '..', 'ex_app', 'lib', 'agent.py')

# Rough token estimate
def estimate_tokens(text: str) -> int:
	return len(text) // 4


def extract_system_prompt(agent_file: str) -> str:
	"""Extract the system prompt string literal from agent.py."""
	with open(agent_file) as f:
		source = f.read()

	tree = ast.parse(source)

	# Find the system_prompt_text assignment inside call_model
	for node in ast.walk(tree):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'call_model':
			for child in ast.walk(node):
				if isinstance(child, ast.Assign):
					for target in child.targets:
						if isinstance(target, ast.Name) and target.id == 'system_prompt_text':
							if isinstance(child.value, ast.Constant) and isinstance(child.value.value, str):
								return child.value.value
	return ''


def extract_conditional_prompt_lines(agent_file: str) -> list[str]:
	"""Extract the conditional tool hint lines appended to the system prompt."""
	lines = []
	with open(agent_file) as f:
		source = f.read()

	# Simple text-based extraction of the += lines
	in_conditionals = False
	for line in source.splitlines():
		stripped = line.strip()
		if 'system_prompt_text +=' in stripped:
			in_conditionals = True
			# Extract the string literal
			start = stripped.find('"')
			end = stripped.rfind('"')
			if start != -1 and end != -1 and start != end:
				lines.append(stripped[start + 1:end])
		elif in_conditionals and stripped.startswith('if task['):
			break
	return lines


def extract_tools_from_file(filepath: str) -> list[dict]:
	"""Extract tool functions and their docstrings/parameters from a tool file."""
	with open(filepath) as f:
		source = f.read()

	tree = ast.parse(source)
	tools = []

	for node in ast.walk(tree):
		if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
			continue

		# Check if it has @tool decorator
		has_tool_decorator = False
		for dec in node.decorator_list:
			if isinstance(dec, ast.Name) and dec.id == 'tool':
				has_tool_decorator = True
			elif isinstance(dec, ast.Attribute) and dec.attr == 'tool':
				has_tool_decorator = True
		if not has_tool_decorator:
			continue

		docstring = ast.get_docstring(node) or ''

		# Extract parameters from function signature
		params = []
		for arg in node.args.args:
			if arg.arg == 'self':
				continue
			annotation = ''
			if arg.annotation:
				annotation = ast.unparse(arg.annotation)
			params.append({'name': arg.arg, 'type': annotation})

		# Build a rough schema representation (similar to what gets sent to the LLM)
		schema_parts = [f'Tool: {node.name}']
		if docstring:
			schema_parts.append(f'Description: {docstring}')
		if params:
			schema_parts.append('Parameters:')
			for p in params:
				schema_parts.append(f'  - {p["name"]}: {p["type"]}')

		schema_text = '\n'.join(schema_parts)

		tools.append({
			'name': node.name,
			'docstring': docstring,
			'params': params,
			'schema_text': schema_text,
			'docstring_chars': len(docstring),
			'schema_chars': len(schema_text),
		})

	return tools


def main():
	# --- System Prompt ---
	print('=' * 80)
	print('SYSTEM PROMPT')
	print('=' * 80)

	base_prompt = extract_system_prompt(AGENT_FILE)
	conditional_lines = extract_conditional_prompt_lines(AGENT_FILE)
	conditional_text = '\n'.join(conditional_lines)
	full_prompt = base_prompt + '\n' + conditional_text

	print(f'\n  Base prompt:        {len(base_prompt):6d} chars  ~{estimate_tokens(base_prompt):5d} tokens')
	print(f'  Conditional hints:  {len(conditional_text):6d} chars  ~{estimate_tokens(conditional_text):5d} tokens')
	print(f'  Total system prompt:{len(full_prompt):6d} chars  ~{estimate_tokens(full_prompt):5d} tokens')

	if conditional_lines:
		print(f'\n  Conditional hint lines ({len(conditional_lines)}):')
		for line in conditional_lines:
			print(f'    [{len(line):3d} chars] {line[:100]}')

	# --- Tools by Category ---
	print('\n' + '=' * 80)
	print('TOOLS BY CATEGORY (module)')
	print('=' * 80)

	tool_dir = os.path.normpath(TOOL_DIR)
	py_files = sorted(f for f in os.listdir(tool_dir)
					  if f.endswith('.py') and f != '__init__.py')

	all_tools = []
	category_stats = []

	for filename in py_files:
		filepath = os.path.join(tool_dir, filename)
		module_name = filename[:-3]  # strip .py

		tools = extract_tools_from_file(filepath)
		if not tools:
			continue

		cat_docstring_chars = sum(t['docstring_chars'] for t in tools)
		cat_schema_chars = sum(t['schema_chars'] for t in tools)

		category_stats.append({
			'module': module_name,
			'tool_count': len(tools),
			'docstring_chars': cat_docstring_chars,
			'schema_chars': cat_schema_chars,
			'tools': tools,
		})

		all_tools.extend(tools)

	# Sort categories by schema size descending
	category_stats.sort(key=lambda c: c['schema_chars'], reverse=True)

	print(f'\n  {"Module":<20s} {"Tools":>5s}  {"Docstr Chars":>12s}  {"Schema Chars":>12s}  {"~Tokens":>8s}')
	print(f'  {"-" * 20} {"-" * 5}  {"-" * 12}  {"-" * 12}  {"-" * 8}')

	total_tools = 0
	total_docstring = 0
	total_schema = 0

	for cat in category_stats:
		print(f'  {cat["module"]:<20s} {cat["tool_count"]:5d}  {cat["docstring_chars"]:12d}  {cat["schema_chars"]:12d}  {estimate_tokens(chr(0) * cat["schema_chars"]):8d}')
		total_tools += cat['tool_count']
		total_docstring += cat['docstring_chars']
		total_schema += cat['schema_chars']

	print(f'  {"-" * 20} {"-" * 5}  {"-" * 12}  {"-" * 12}  {"-" * 8}')
	print(f'  {"TOTAL":<20s} {total_tools:5d}  {total_docstring:12d}  {total_schema:12d}  {estimate_tokens(chr(0) * total_schema):8d}')

	# --- Individual Tools (top 30 by schema size) ---
	print('\n' + '=' * 80)
	print('TOP 30 TOOLS BY SCHEMA SIZE')
	print('=' * 80)

	all_tools.sort(key=lambda t: t['schema_chars'], reverse=True)

	print(f'\n  {"Tool Name":<45s} {"Docstr":>7s} {"Schema":>7s} {"~Tokens":>8s}  {"Params":>6s}')
	print(f'  {"-" * 45} {"-" * 7} {"-" * 7} {"-" * 8}  {"-" * 6}')

	for tool in all_tools[:30]:
		print(f'  {tool["name"]:<45s} {tool["docstring_chars"]:7d} {tool["schema_chars"]:7d} {estimate_tokens(chr(0) * tool["schema_chars"]):8d}  {len(tool["params"]):6d}')

	# --- Summary ---
	print('\n' + '=' * 80)
	print('OVERALL CONTEXT SIZE ESTIMATE')
	print('=' * 80)

	total_fixed = len(full_prompt) + total_schema
	print(f'\n  System prompt:           {len(full_prompt):8d} chars  ~{estimate_tokens(full_prompt):6d} tokens')
	print(f'  All tool schemas:        {total_schema:8d} chars  ~{estimate_tokens(chr(0) * total_schema):6d} tokens')
	print(f'  Memories:                {"unbounded":>8s}        (injected at runtime)')
	print(f'  MCP tools:               {"unbounded":>8s}        (loaded at runtime)')
	print(f'  Conversation history:    {"up to 42":>8s}        non-tool msgs + tool pairs')
	print(f'  ─────────────────────────────────────────────────────')
	print(f'  Fixed overhead per call: {total_fixed:8d} chars  ~{estimate_tokens(chr(0) * total_fixed):6d} tokens')
	print(f'                           (when all tools are enabled)')
	print()


if __name__ == '__main__':
	main()
