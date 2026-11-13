"""Real website Handler + MCP in a temporary library; no authentication files."""
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile
import threading
import mcp  # Import before IsolatedAsyncioTestCase starts its debug event loop.

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'knowledge-site'))
sys.path.insert(0, str(ROOT / 'src'))
from server import Handler, KnowledgeServer, NoteStore


class NoSessions:
    def get_session(self, token):
        return None

    def close(self):
        pass


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class TemporaryKnowledgeSite:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.root = self.workspace / 'content'
        self.root.mkdir()
        self.server = KnowledgeServer(('127.0.0.1', 0), QuietHandler)
        self.server.store = NoteStore(self.root)
        self.server.web_root = self.workspace / 'web'
        self.server.shutdown_token = None
        self.server.auth = NoSessions()
        self.server.local_token = 'synthetic-phase2b-test-token'
        self.server.public_origin = 'https://fixture.invalid'
        self.server.static_cache = {}
        self.server.static_lock = threading.Lock()
        self.server.mermaid_assets = {}
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        try:
            assert not (self.workspace / '.auth').exists(), 'Fixture must never create auth state'
        finally:
            self.temp.cleanup()

    @asynccontextmanager
    async def connect(self, authorized=True):
        from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
        token = self.server.local_token if authorized else 'invalid-synthetic-token'
        entry = self.workspace / ('stdio-authorized.py' if authorized else 'stdio-denied.py')
        entry.write_text(
            'import sys\n'
            'import logging\n'
            'logging.disable(logging.INFO)\n'
            f'sys.path.insert(0, {str(ROOT / "knowledge-site")!r})\n'
            'import mcp_server\n'
            f'mcp_server.local_headers = lambda root: {{"Authorization": "Bearer " + {token!r}}}\n'
            f'mcp_server.build_server({str(self.root)!r}, site_port={self.port}).run(transport="stdio")\n',
            encoding='utf-8')
        async with StdioKnowledge(ROOT / 'knowledge-site', entrypoint=entry) as client:
            yield client

