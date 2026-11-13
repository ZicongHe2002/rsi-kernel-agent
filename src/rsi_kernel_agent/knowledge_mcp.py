"""Use the installed MCP SDK and existing knowledge-site stdio server unchanged."""
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from pathlib import Path
import sys

from .knowledge import KnowledgeClient, KnowledgeError, RevisionConflict


class StdioKnowledge:
    """Trusted local connection configuration, never supplied by a model tool call.

    The default entrypoint is the existing website's mcp_server.py. Its current
    authentication and write policy are unchanged. Optional entrypoint is for a
    trusted deployment/test configuration, not dynamically retrieved content.
    """
    def __init__(self, website_dir, python_executable=None, entrypoint=None):
        self.website_dir = Path(website_dir).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.entrypoint = Path(entrypoint).resolve() if entrypoint else self.website_dir / 'mcp_server.py'
        self._manager = None

    async def __aenter__(self):
        self._manager = self._open()
        return await self._manager.__aenter__()

    async def __aexit__(self, *args):
        return await self._manager.__aexit__(*args)

    @asynccontextmanager
    async def _open(self):
        # Optional boundary: core contracts/storage import without MCP installed.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        parameters = StdioServerParameters(command=self.python_executable,
            args=['-B', '-u', str(self.entrypoint)], cwd=str(self.website_dir),
            env={'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                initialized = await session.initialize()
                if initialized.serverInfo.name != 'Zhixu Knowledge':
                    raise KnowledgeError('Unexpected MCP server identity')
                listing = await session.list_tools()
                tools = {tool.name: tool for tool in listing.tools}
                expected = {'search': {'query', 'offset', 'limit'}, 'fetch': {'id', 'offset', 'limit'},
                            'create_note': {'title', 'content', 'category'},
                            'update_note': {'id', 'revision', 'content', 'title', 'category'}}
                for name, properties in expected.items():
                    if name not in tools or not properties <= set(tools[name].inputSchema.get('properties', {})):
                        raise KnowledgeError(f'MCP {name} schema is incompatible')

                async def call(name, arguments):
                    if name not in expected:
                        raise KnowledgeError('Unsupported knowledge operation')
                    result = await session.call_tool(name, arguments)
                    text = '\n'.join(item.text for item in result.content if getattr(item, 'type', None) == 'text')
                    if result.isError:
                        cls = RevisionConflict if 'HTTP 409:' in text else KnowledgeError
                        raise cls(text or 'Knowledge MCP call failed')
                    if isinstance(result.structuredContent, dict):
                        return result.structuredContent
                    try:
                        value = json.loads(text)
                    except ValueError as error:
                        raise KnowledgeError('Knowledge MCP response is not JSON') from error
                    if not isinstance(value, dict):
                        raise KnowledgeError('Knowledge MCP response is not an object')
                    return value

                yield KnowledgeClient(call)
