import functools

def safe_tool(tool):
	setattr(tool, 'safe', True)
	return tool

def dangerous_tool(tool):
	setattr(tool, 'safe', False)
	return tool