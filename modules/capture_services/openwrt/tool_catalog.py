"""Shared tool registry; common mandatory selections remain independently scoped."""
from .common_tool_dependencies import TOOLS as COMMON_TOOLS, check_tools as _check
from .other_tool_dependencies import OTHER_TOOLS, OTHER_KEYS

TOOLS = COMMON_TOOLS + OTHER_TOOLS
TOOL_BY_KEY = {tool.key: tool for tool in TOOLS}
TOOL_KEYS = frozenset(TOOL_BY_KEY)

def check_tools(runner, keys, cancel_event=None):
    return _check(runner, keys, cancel_event, catalog=TOOLS)
