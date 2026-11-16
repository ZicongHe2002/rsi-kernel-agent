"""Per-invocation snapshot paging cache; all live Store APIs remain uncached.

Use a new VerificationView for each independent verification invocation. The
first read calls the original snapshot reader twice (once when total <= 1), so
its digest/version/count checks remain authoritative. Later pages describe the
same detached snapshot captured then, not another disk-integrity observation.
Restarting with a new view performs the original checks again. No cache is
persisted and no artifact reads, live projections, or Controller state is cached.

This adapter is not a permission boundary: non-snapshot APIs are forwarded to
the supplied store. Supply the existing ReadOnlyStore for read-only workflows.
"""
from copy import deepcopy

from rsi_kernel_agent.contracts import canonical_json, record_to_dict
from rsi_kernel_agent.store import StoreError


def _require(condition, message):
    if not condition:
        raise StoreError(message)


def _check_page(page, snapshot_id, limit):
    """Reject inconsistent coverage before retaining an original-reader page."""
    try:
        total = page['total']
        _require(type(total) is int and total >= 0, 'Invalid snapshot total')
        end = min(limit, total)
        _require(page['snapshot']['id'] == snapshot_id
                 and page['snapshot']['record_count'] == total
                 and page['offset'] == 0
                 and type(page['records']) is list and len(page['records']) == end
                 and page['next_offset'] == (end if end < total else None)
                 and page['complete'] is (end == total)
                 and type(page['projections']) is dict,
                 'Snapshot page coverage or identity differs')
        return total
    except (KeyError, TypeError) as error:
        raise StoreError('Malformed snapshot page') from error


class VerificationView:
    """Cache only complete verified snapshots owned by this adapter instance."""
    def __init__(self, store):
        self._store = store
        self._snapshots = {}

    def __getattr__(self, name):
        return getattr(self._store, name)

    def snapshot_page(self, snapshot_id, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or limit <= 0:
            raise StoreError('Snapshot pagination requires nonnegative offset and positive limit')
        if snapshot_id not in self._snapshots:
            first = self._store.snapshot_page(snapshot_id, offset=0, limit=1)
            total = _check_page(first, snapshot_id, 1)
            full = first if total <= 1 else self._store.snapshot_page(snapshot_id, offset=0, limit=total)
            _check_page(full, snapshot_id, max(1, total))
            _require(canonical_json(full['snapshot']) == canonical_json(first['snapshot'])
                     and full['total'] == total
                     and canonical_json(full['projections']) == canonical_json(first['projections'])
                     and [(type(record), canonical_json(record_to_dict(record)))
                          for record in full['records'][:len(first['records'])]] ==
                         [(type(record), canonical_json(record_to_dict(record)))
                          for record in first['records']],
                     'Snapshot changed between coverage probe and complete read')
            # Records are frozen dataclasses but their nested JSON is mutable.
            # Keep a private detached copy and never return its mutable objects.
            self._snapshots[snapshot_id] = deepcopy(full)
        full = self._snapshots[snapshot_id]
        total = full['total']
        if offset > total:
            raise StoreError('Offset exceeds snapshot coverage')
        end = min(offset + limit, total)
        return deepcopy({'snapshot': full['snapshot'], 'records': full['records'][offset:end],
                         'offset': offset, 'next_offset': end if end < total else None,
                         'complete': offset == 0 and end == total, 'total': total,
                         'projections': full['projections']})
