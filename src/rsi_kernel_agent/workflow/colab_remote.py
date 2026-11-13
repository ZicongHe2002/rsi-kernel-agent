"""Install a pinned worker and invoke its durable spool through ColabBridge."""
import base64
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from threading import Lock

from .colab_bridge import extract_json_result


def _no_local_links(path):
    for entry in (path, *path.parents):
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            raise ValueError('Local transfer cache cannot contain links')


def _verified_chunk(path, digest, size):
    _no_local_links(path)
    if not path.is_file() or path.stat().st_size != size:
        return None
    raw = path.read_bytes()
    return raw if len(raw) == size and sha256(raw).hexdigest() == digest else None


def _copy_chunk(path, raw):
    """Publish independent bytes atomically, never a writable hard link."""
    _no_local_links(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.chunk-')
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        _no_local_links(path)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ColabRemote:
    def __init__(self, bridge, root, nonce, worker_path, runner_path, local_cache, *, remote_python=None):
        if (type(root) is not str or not root.startswith('/content/rsi-kernel-agent/')
                or '\\' in root or ':' in root or any(p in ('', '.', '..') for p in root[1:].split('/'))):
            raise ValueError('Remote spool must be a child of /content/rsi-kernel-agent')
        if type(nonce) is not str or not nonce:
            raise ValueError('A persistent runtime ledger nonce is required')
        self.bridge, self.root, self.nonce = bridge, root, nonce
        self.runtime_id = bridge.runtime_id
        if remote_python is not None and (type(remote_python) is not str
                or not remote_python.startswith('/content/rsi-kernel-agent/')
                or '\\' in remote_python or any(p in ('', '.', '..') for p in remote_python[1:].split('/'))):
            raise ValueError('Explicit remote interpreter must be in the isolated RSI directory')
        self.remote_python = remote_python
        self.chunk_bytes = 8 * 1024 * 1024
        self.transfer_workers = 4
        self.worker_path, self.runner_path = Path(worker_path), Path(runner_path)
        self.worker_digest = sha256(self.worker_path.read_bytes()).hexdigest()
        self.runner_digest = sha256(self.runner_path.read_bytes()).hexdigest()
        self.worker_remote_path = self.root + '/tools/worker-' + self.worker_digest + '.py'
        self.runner_remote_path = self.root + '/tools/runner-' + self.runner_digest + '.py'
        self.local_cache = Path(local_cache)
        _no_local_links(self.local_cache)
        self.local_cache.mkdir(parents=True, exist_ok=True)

    def _command(self, action, **extra):
        return {'action': action, 'root': self.root, 'runtime_id': self.runtime_id, 'nonce': self.nonce, **extra}

    def _code(self, command):
        path = self.worker_remote_path
        if self.remote_python is not None:
            return ('import subprocess, json, hashlib\nfrom pathlib import Path\n'
                    f'_rsi_path=Path({path!r})\n'
                    f'assert hashlib.sha256(_rsi_path.read_bytes()).hexdigest()=={self.worker_digest!r}, "worker identity changed"\n'
                    f'_rsi_reply=subprocess.run([{self.remote_python!r},"-I","-B",str(_rsi_path)],'
                    f'input={json.dumps(command)!r},capture_output=True,text=True,timeout=45)\n'
                    'assert _rsi_reply.returncode==0, "remote worker control failed"\n'
                    'print("RSI_RESULT:"+json.dumps(json.loads(_rsi_reply.stdout),allow_nan=False))\n')
        return ('import importlib.util, json, hashlib\nfrom pathlib import Path\n'
                f'_rsi_path=Path({path!r})\n'
                f'assert hashlib.sha256(_rsi_path.read_bytes()).hexdigest()=={self.worker_digest!r}, "worker identity changed"\n'
                '_rsi_spec=importlib.util.spec_from_file_location("rsi_worker_control",_rsi_path)\n'
                '_rsi_worker=importlib.util.module_from_spec(_rsi_spec)\n_rsi_spec.loader.exec_module(_rsi_worker)\n'
                f'_rsi_reply=_rsi_worker.run_command(json.loads({json.dumps(command)!r}))\n'
                'print("RSI_RESULT:"+json.dumps(_rsi_reply,allow_nan=False))\n')

    def bootstrap(self):
        """Explicit initialization only; lookup never calls this method."""
        content = {name: {'digest': digest, 'bytes': base64.b64encode(path.read_bytes()).decode('ascii')}
                   for name, path, digest in [(PurePosixPath(self.worker_remote_path).name, self.worker_path, self.worker_digest),
                                               (PurePosixPath(self.runner_remote_path).name, self.runner_path, self.runner_digest)]}
        code = ('import base64, hashlib, json, os\nfrom pathlib import Path\n'
                f'_rsi_root=Path({self.root!r})\n'
                'assert all(not p.is_symlink() for p in (_rsi_root,*_rsi_root.parents)), "linked root"\n'
                '_rsi_tools=_rsi_root/"tools"\n_rsi_tools.mkdir(parents=True,exist_ok=True)\n'
                f'for _rsi_name,_rsi_item in json.loads({json.dumps(content)!r}).items():\n'
                '    _rsi_raw=base64.b64decode(_rsi_item["bytes"],validate=True)\n'
                '    assert hashlib.sha256(_rsi_raw).hexdigest()==_rsi_item["digest"]\n'
                '    _rsi_target=_rsi_tools/_rsi_name\n'
                '    assert not _rsi_target.is_symlink(), "linked tool"\n'
                '    if _rsi_target.exists():\n'
                '        assert _rsi_target.read_bytes()==_rsi_raw, "installed tool changed"\n'
                '    else:\n'
                '        with _rsi_target.open("xb") as _rsi_f:\n'
                '            _rsi_f.write(_rsi_raw); _rsi_f.flush(); os.fsync(_rsi_f.fileno())\n')
        return extract_json_result(self.bridge.execute(code + self._code(self._command('bootstrap'))))

    def lookup(self, operation_id):
        return extract_json_result(self.bridge.execute(self._code(self._command('lookup', operation_id=operation_id))))

    def submit(self, request, payload):
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        remote = self.root + '/incoming/' + digest + '.json'
        # Upload only to an observed runtime and existing spool. Failed upload
        # cannot run a kernel; worker independently verifies every blob digest.
        code = ('from pathlib import Path\n'
                f'_rsi_incoming=Path({self.root + "/incoming"!r})\n'
                'assert all(not p.is_symlink() for p in (_rsi_incoming,*_rsi_incoming.parents))\n'
                '_rsi_incoming.mkdir(exist_ok=True)\n'
                "print('RSI_RESULT:{\"status\":\"ready\"}')\n")
        extract_json_result(self.bridge.execute(code))
        with tempfile.TemporaryDirectory(dir=self.local_cache) as directory:
            local = Path(directory) / 'payload.json'
            local.write_text(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False), encoding='utf-8')
            self.bridge.put(local, remote.lstrip('/'))
        return extract_json_result(self.bridge.execute(self._code(self._command(
            'submit', request=request, payload_path=remote, runner_path=self.runner_remote_path))))

    def read_artifact(self, artifact):
        path = artifact['path']
        if (type(path) is not str or not path.startswith(self.root + '/') or '\\' in path
                or any(p in ('', '.', '..') for p in path[1:].split('/'))):
            raise ValueError('Artifact path leaves the pinned remote spool')
        ref = artifact['ref']
        digest = ref['digest']
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid download digest')
        target = self.local_cache / digest
        _no_local_links(target)
        if ref['size'] > self.chunk_bytes:
            self._download_chunks(path, ref, target)
        else:
            self.bridge.get(path.lstrip('/'), target)
        raw = target.read_bytes()
        if len(raw) != ref['size'] or sha256(raw).hexdigest() != digest:
            raise ValueError('Downloaded artifact bytes differ from receipt')
        return raw

    def _download_chunks(self, path, ref, target):
        """Resume byte-verified staging chunks; no execution is resubmitted."""
        prefix = self.root + '/transfers/' + ref['digest'] + '/'
        code = ('import hashlib,json,os,tempfile\nfrom pathlib import Path\n'
                f'_rsi_src=Path({path!r}); _rsi_parts=Path({prefix!r})\n'
                'assert all(not p.is_symlink() for p in (_rsi_src,*_rsi_src.parents,_rsi_parts,*_rsi_parts.parents))\n'
                '_rsi_parts.mkdir(parents=True,exist_ok=True)\n_rsi_rows=[]; _rsi_hash=hashlib.sha256(); _rsi_offset=0\n'
                'with _rsi_src.open("rb") as _rsi_f:\n'
                '    while True:\n'
                f'        _rsi_data=_rsi_f.read({self.chunk_bytes})\n'
                '        if not _rsi_data: break\n'
                '        _rsi_hash.update(_rsi_data); _rsi_digest=hashlib.sha256(_rsi_data).hexdigest()\n'
                '        _rsi_part=_rsi_parts/str(len(_rsi_rows))\n'
                '        assert not _rsi_part.is_symlink()\n'
                '        if not _rsi_part.exists():\n'
                '            _rsi_fd,_rsi_temp=tempfile.mkstemp(dir=_rsi_parts,prefix=".pending-")\n'
                '            try:\n'
                '                with os.fdopen(_rsi_fd,"wb") as _rsi_out:\n'
                '                    _rsi_out.write(_rsi_data); _rsi_out.flush(); os.fsync(_rsi_out.fileno())\n'
                '                try: os.link(_rsi_temp,_rsi_part)\n'
                '                except FileExistsError: pass\n'
                '            finally: Path(_rsi_temp).unlink(missing_ok=True)\n'
                '        assert _rsi_part.stat().st_size==len(_rsi_data) and hashlib.sha256(_rsi_part.read_bytes()).hexdigest()==_rsi_digest\n'
                '        _rsi_rows.append({"path":str(_rsi_part),"digest":_rsi_digest,"size":len(_rsi_data),"offset":_rsi_offset})\n'
                '        _rsi_offset+=len(_rsi_data)\n'
                f'assert _rsi_offset=={ref["size"]!r} and _rsi_hash.hexdigest()=={ref["digest"]!r}\n'
                'print("RSI_RESULT:"+json.dumps({"digest":_rsi_hash.hexdigest(),"size":_rsi_offset,"chunks":_rsi_rows}))\n')
        manifest = extract_json_result(self.bridge.execute(code))
        if (manifest.get('digest') != ref['digest'] or manifest.get('size') != ref['size']
                or type(manifest.get('chunks')) is not list or not manifest['chunks']):
            raise ValueError('Chunk manifest differs from original artifact')
        offset = 0
        for row in manifest['chunks']:
            if (type(row) is not dict or set(row) != {'path','digest','size','offset'}
                    or type(row['path']) is not str or not row['path'].startswith(prefix)
                    or '\\' in row['path'] or any(p in ('', '.', '..') for p in row['path'][1:].split('/'))
                    or type(row['offset']) is not int or row['offset'] != offset
                    or type(row['size']) is not int or not 0 < row['size'] <= self.chunk_bytes
                    or type(row['digest']) is not str or len(row['digest']) != 64
                    or any(c not in '0123456789abcdef' for c in row['digest'])):
                raise ValueError('Invalid chunk path, coverage, or identity')
            offset += row['size']
        if offset != ref['size']:
            raise ValueError('Chunk coverage does not match original artifact size')
        if type(self.transfer_workers) is not int or not 1 <= self.transfer_workers <= 4:
            raise ValueError('Transfer concurrency must be 1..4')
        parts = self.local_cache / 'parts' / ref['digest']
        shared = self.local_cache / 'chunks'
        for local in (parts, shared, target):
            _no_local_links(local)
        parts.mkdir(parents=True, exist_ok=True)
        shared.mkdir(exist_ok=True)
        missing = {}
        for row in manifest['chunks']:
            identity = (row['digest'], row['size'])
            if _verified_chunk(shared / row['digest'], *identity) is None:
                missing[identity] = True
        # Migrate only currently needed chunks from pre-existing whole/parts
        # caches. Scan each legacy file at most once, not once per missing chunk.
        if missing:
            for directory in parts.parent.iterdir():
                _no_local_links(directory)
                if not directory.is_dir():
                    continue
                for local in directory.iterdir():
                    _no_local_links(local)
                    if not local.name.isdecimal() or not local.is_file():
                        continue
                    if local.stat().st_size not in {size for _, size in missing}:
                        continue
                    raw = local.read_bytes()
                    identity = (sha256(raw).hexdigest(), len(raw))
                    if identity in missing:
                        _copy_chunk(shared / identity[0], raw)
                        del missing[identity]
                    if not missing:
                        break
                if not missing:
                    break
        locks = {row['digest']: Lock() for row in manifest['chunks']}
        def fetch_chunk(item):
            index, row = item
            local = parts / str(index)
            cached = shared / row['digest']
            identity = (row['digest'], row['size'])
            with locks[row['digest']]:
                staged = _verified_chunk(local, *identity)
                raw = _verified_chunk(cached, *identity)
                if raw is None:
                    raw = staged
                    if raw is None:
                        descriptor, temporary = tempfile.mkstemp(dir=parts, prefix='.download-')
                        os.close(descriptor)
                        try:
                            self.bridge.get(row['path'].lstrip('/'), Path(temporary))
                            raw = _verified_chunk(Path(temporary), *identity)
                            if raw is None:
                                raise ValueError('Downloaded chunk differs from manifest')
                        finally:
                            Path(temporary).unlink(missing_ok=True)
                    _copy_chunk(cached, raw)
                if staged is None:
                    _copy_chunk(local, raw)
        if self.transfer_workers == 1:
            for item in enumerate(manifest['chunks']):
                fetch_chunk(item)
        else:
            with ThreadPoolExecutor(max_workers=self.transfer_workers) as pool:
                list(pool.map(fetch_chunk, enumerate(manifest['chunks'])))
        descriptor, temporary = tempfile.mkstemp(dir=self.local_cache, prefix='.assembled-')
        try:
            whole = sha256()
            with os.fdopen(descriptor, 'wb') as output:
                for index, row in enumerate(manifest['chunks']):
                    raw = _verified_chunk(parts / str(index), row['digest'], row['size'])
                    if raw is None:
                        raise ValueError('Chunk staging changed before assembly')
                    whole.update(raw); output.write(raw)
                output.flush(); os.fsync(output.fileno())
            if Path(temporary).stat().st_size != ref['size'] or whole.hexdigest() != ref['digest']:
                raise ValueError('Assembled artifact differs from original receipt')
            _no_local_links(target)
            Path(temporary).replace(target)
        finally:
            Path(temporary).unlink(missing_ok=True)
