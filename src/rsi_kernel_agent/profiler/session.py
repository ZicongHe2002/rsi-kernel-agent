"""Durable host/conversation-model exchange; no separate model service is used.

This is a single-writer offline session. A host reads packet.json, reasons in the
conversation, writes a response, then submits it. All accepted exchanges replay
against the pinned manifest before the next packet is published.
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from ..contracts import ArtifactRef, canonical_json
from ..store import ExperimentStore
from .analyzer import Analyzer
from .inputs import load_manifest
from .outputs import require


def _write(path, value):
    data = canonical_json(value).encode('utf-8')
    handle, name = tempfile.mkstemp(prefix='.analysis-', dir=path.parent)
    try:
        with os.fdopen(handle,'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def start_session(store, manifest, directory, *, model_label, configuration='review', skills=None,
                  max_turns=6, max_tool_calls=12, max_response_chars=60000):
    require(type(model_label) is str and bool(model_label.strip()), 'Session model label required')
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    require(not (directory/'session.json').exists(), 'Session already exists')
    pinned = load_manifest(store,manifest.artifact_ref)
    options = dict(configuration=configuration,skills=skills or {},max_turns=max_turns,
                   max_tool_calls=max_tool_calls,max_response_chars=max_response_chars)
    analyzer = Analyzer(pinned,artifacts=store.artifacts,**options)
    state = {'schema_version':1,'manifest':asdict(manifest.artifact_ref),'model_label':model_label,
             'options':options,'responses':[],'result_digest':None}
    _write(directory/'session.json',state)
    _write(directory/'packet.json',analyzer.packet())
    return directory/'packet.json'


def submit_response(store, directory, response_path):
    directory = Path(directory)
    state_path = directory/'session.json'
    before = state_path.read_bytes()
    state = json.loads(before)
    require(state['schema_version']==1 and state['result_digest'] is None, 'Session is complete or unsupported')
    manifest = load_manifest(store,ArtifactRef(**state['manifest']))
    analyzer = Analyzer(manifest,artifacts=store.artifacts,**state['options'])
    for old in state['responses']:
        analyzer.advance(old)
    response = json.loads(Path(response_path).read_text(encoding='utf-8-sig'))
    result = analyzer.advance(response)
    require(state_path.read_bytes()==before, 'Concurrent session update; re-read before submitting')
    state['responses'].append(response)
    if result is not None:
        result['model_label'] = state['model_label']
        result['model_transport'] = 'conversation_session'
        ref = store.artifacts.put(canonical_json(result).encode('utf-8'))
        state['result_digest'] = ref.digest
        _write(directory/'result.json',result)
    _write(state_path,state)
    if result is None: _write(directory/'packet.json',analyzer.packet())
    else: _write(directory/'packet.json',{'status':'complete','result':'result.json','digest':state['result_digest']})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True); parser.add_argument('--artifacts',required=True)
    parser.add_argument('--session',required=True); parser.add_argument('--response',required=True)
    args = parser.parse_args()
    result = submit_response(ExperimentStore(args.db,args.artifacts),args.session,args.response)
    print('complete' if result else 'awaiting conversation-model response')


if __name__=='__main__': main()
