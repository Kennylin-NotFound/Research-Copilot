"""Deny-by-default tool grants, checked at both dispatch and direct tool entry."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

MAIN_TOOLS = frozenset({'write_todos', 'task', 'read_file_content', 'generate_markdown', 'convert_md_to_pdf'})
NETWORK_TOOLS = frozenset({'write_todos', 'internet_search', 'fetch_public_page'})
KNOWLEDGE_TOOLS = frozenset({'write_todos', 'get_assistant_list', 'create_ask_delete'})
RESEARCH_TOOLS = frozenset({'list_research_sources', 'read_research_source', 'search_research_sources', 'acquire_research_source', 'record_research_finding', 'record_research_note', 'review_research_findings', 'finalize_research_findings'})
ALL_TOOLS = MAIN_TOOLS | NETWORK_TOOLS | KNOWLEDGE_TOOLS | RESEARCH_TOOLS
SUBAGENTS = frozenset({'网络搜索助手', 'RAGFlow助手', '维度研究员'})
EXCLUDED_BUILTINS = frozenset({'execute', 'ls', 'read_file', 'write_file', 'edit_file', 'glob', 'grep'})


@dataclass(frozen=True)
class ToolGrant:
    tools: frozenset[str]
    allow_network: bool = True


_grant = ContextVar('research_tool_grant', default=None)


@contextmanager
def tool_scope(tools=ALL_TOOLS, *, allow_network=True):
    requested = frozenset(tools)
    parent = _grant.get()
    # Nested scopes can narrow authority but cannot grant new rights.
    effective = requested if parent is None else requested & parent.tools
    token = _grant.set(ToolGrant(effective, allow_network and (parent.allow_network if parent else True)))
    try:
        yield
    finally:
        _grant.reset(token)


def require_tool(name, *, role=None, arguments=None):
    grant = _grant.get()
    if grant is None or name not in ALL_TOOLS or name not in grant.tools or (role is not None and name not in role):
        raise PermissionError('Tool is not authorized in this execution scope: ' + name)
    if name in ('internet_search', 'fetch_public_page', 'search_research_sources', 'acquire_research_source') and not grant.allow_network:
        raise PermissionError('Network access is disabled for this execution')
    if name == 'task' and (arguments or {}).get('subagent_type') not in SUBAGENTS:
        raise PermissionError('Subagent is not authorized')


def visible_tools(tools, allowed):
    return [tool for tool in tools if (getattr(tool, 'name', None) or (tool.get('name') if isinstance(tool, dict) else None)) in allowed]


def gateway_middleware(allowed):
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.messages import ToolMessage

    class ToolGateway(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            return handler(request.override(tools=visible_tools(request.tools, allowed)))

        async def awrap_model_call(self, request, handler):
            return await handler(request.override(tools=visible_tools(request.tools, allowed)))

        def wrap_tool_call(self, request, handler):
            try:
                require_tool(request.tool_call['name'], role=allowed, arguments=request.tool_call.get('args'))
            except PermissionError as exc:
                return ToolMessage(content=str(exc), tool_call_id=request.tool_call['id'], status='error')
            return handler(request)

        async def awrap_tool_call(self, request, handler):
            try:
                require_tool(request.tool_call['name'], role=allowed, arguments=request.tool_call.get('args'))
            except PermissionError as exc:
                return ToolMessage(content=str(exc), tool_call_id=request.tool_call['id'], status='error')
            return await handler(request)

    return ToolGateway()
