"""Recoverable workflow states in existing append-only Decision records.

An OS-held lock serializes each Task on one host. The lock file is never deleted:
removing an unlocked pathname could let another process lock a different inode.
The journal is an application protocol, not a new database or Store schema.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
import threading

from ..contracts import Decision, Task, canonical_json
from ..store import ConflictError


def _detach(value):
    return json.loads(canonical_json(value))


def _digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


class WorkflowJournal:
    def __init__(self, store, task_id):
        if type(store.get(task_id)) is not Task:
            raise ValueError('Workflow requires an existing Task')
        self.store, self.task_id = store, task_id
        self.prefix = 'wf-state:' + sha256(task_id.encode('utf-8')).hexdigest()[:32] + ':'
        self.lock_path = store.db_path.resolve().with_name(
            store.db_path.name + '.workflow-' + sha256(task_id.encode('utf-8')).hexdigest()[:32] + '.lock')
        self._lock_owner = None

    @contextmanager
    def lock(self):
        """Nonblocking exclusive lock; process death releases the OS handle."""
        if self._lock_owner is not None:
            raise ConflictError('Workflow is already locked')
        with self.lock_path.open('a+b') as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ConflictError('Another Controller holds the Task lock') from exc
            self._lock_owner = threading.get_ident()
            try:
                yield self
            finally:
                self._lock_owner = None
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _events(self):
        events, previous = [], None
        for record in self.store.history(self.task_id):
            if not isinstance(record, Decision):
                continue
            marker = record.details.get('type')
            if not (record.id.startswith(self.prefix) or
                    (isinstance(marker, str) and marker.startswith('workflow_state.'))):
                continue
            data = record.details
            revision = len(events) + 1
            valid = (record.kind == 'note' and marker == 'workflow_state.v1'
                     and set(data) == {'type','revision','previous_id','state','state_digest'}
                     and type(data.get('revision')) is int and data['revision'] == revision
                     and record.id == self.prefix + str(revision)
                     and data.get('previous_id') == previous
                     and type(data.get('state')) is dict
                     and data['state'].get('task_id') == self.task_id
                     and type(data['state'].get('revision')) is int
                     and data['state']['revision'] == revision
                     and data.get('state_digest') == _digest(data['state']))
            if not valid:
                raise ValueError('Malformed workflow state or broken journal chain')
            events.append(record)
            previous = record.id
        return events

    def load(self):
        events = self._events()
        return _detach(events[-1].details['state']) if events else None

    def save(self, state, reason, target_id=None):
        """Append one state; identical lost-response retries reuse its revision.

        Controllers hold lock across read/modify/effects/save. Direct saves also
        obtain the lock, so two callers cannot silently overwrite a newer state.
        """
        if self._lock_owner != threading.get_ident():
            with self.lock():
                return self.save(state, reason, target_id)
        if type(state) is not dict or state.get('task_id') != self.task_id:
            raise ValueError('Workflow state Task does not match journal')
        prior_revision = state.get('revision', 0)
        if type(prior_revision) is not int or prior_revision < 0:
            raise ValueError('Invalid workflow revision')
        payload = _detach(state)
        payload['revision'] = prior_revision + 1
        identity = self.prefix + str(payload['revision'])
        previous_id = self.prefix + str(prior_revision) if prior_revision else None
        record = Decision(identity,self.task_id,'note',target_id or self.task_id,reason,
            {'type':'workflow_state.v1','revision':payload['revision'],'previous_id':previous_id,
             'state':payload,'state_digest':_digest(payload)})
        events = self._events()
        if prior_revision != len(events):
            if prior_revision + 1 == len(events) and events[-1] == record:
                return _detach(payload)
            raise ConflictError('Workflow state changed; reload before saving')
        self.store.append(record, identity)
        return _detach(payload)
