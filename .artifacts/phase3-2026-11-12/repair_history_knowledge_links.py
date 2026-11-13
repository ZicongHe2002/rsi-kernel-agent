"""Repair only this import's 11 notes through original MCP CAS operations."""
import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.knowledge import KnowledgeError, RevisionConflict, _parse_method
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge

MANIFEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12/history-knowledge-import.json'
SOURCE_ID = 'RSI/History/phase3-project2-2026-11-12.md'
LINK = re.compile(r'\[([^\]\n]+)\]\(([^)\n]+)\)')
NOTICE = ('\n\n> Knowledge 链接维护：以上原文中的相对链接已改为明确的本地绝对路径文本；'
          '原文路径、原始 SHA256、历史数值和方法引文保持不变。SHA256 标识首次导入时的原始文档字节，'
          '不标识这份调整链接展示后的笔记。当前 1 篇历史来源与 10 篇方法已通过原 Knowledge MCP 保存；'
          '各方法的 SourceRef/source_revision 绑定本笔记的新 revision。\n')


def rewrite_links(content):
    changed = []
    def update(match):
        label, target = match.groups()
        if '://' in target or target.startswith(('#', '/', 'C:')):
            return match.group(0)
        resolved = (ROOT / 'docs/phase3' / target).resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.is_file():
            raise ValueError('Unexpected or missing local link target')
        changed.append({'label': label, 'relative': target, 'local_path': str(resolved)})
        return f'{label}（本地路径：`{resolved}`）'
    rewritten = LINK.sub(update, content)
    if NOTICE not in rewritten:
        rewritten += NOTICE
    return rewritten, changed


def checkpoint(report):
    temporary = MANIFEST.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(MANIFEST)


async def main(apply):
    report = json.loads(MANIFEST.read_text('utf-8'))
    assert report['source']['id'] == SOURCE_ID and len(report['methods']) == 10
    assert len({item['id'] for item in report['methods']}) == 10
    report.setdefault('original_import', deepcopy({k: report[k] for k in ('source', 'methods', 'status')}))
    fix = report.setdefault('link_maintenance', {'status': 'pending', 'conflicts': [], 'methods': []})
    async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
        source = await knowledge.fetch_document(SOURCE_ID)
        if source.revision != report['source']['revision']:
            # Re-read before a merge; only known link tokens are modified.
            source = await knowledge.fetch_document(SOURCE_ID)
            fix['conflicts'].append({'id': SOURCE_ID, 'observed_revision': source.revision})
        rewritten, changes = rewrite_links(source.content)
        original_sha_lines = re.findall(r'^.*SHA256.*$', source.content, re.M)
        assert all(line in rewritten for line in original_sha_lines)
        methods = []
        for item in report['methods']:
            current = await knowledge.fetch_document(item['id'])
            if current.revision != item['revision']:
                current = await knowledge.fetch_document(item['id'])
                fix['conflicts'].append({'id': current.id, 'observed_revision': current.revision})
            method, _ = _parse_method(current.content)
            assert method.method_id == item['method_id']
            assert all(ref.document_id == SOURCE_ID and ref.excerpt in source.content
                       and ref.excerpt in rewritten for ref in method.source_refs)
            methods.append((item, current))
        if not apply:
            if fix['status'] == 'saved_and_refetched':
                assert not changes
                for _, current in methods:
                    method, _ = _parse_method(current.content)
                    assert method.source_revision == {SOURCE_ID: source.revision}
                    assert all(ref.revision == source.revision for ref in method.source_refs)
            return {'status': 'dry_run', 'notes_checked': 11, 'relative_links': changes,
                    'original_sha_lines_preserved': len(original_sha_lines),
                    'method_excerpts_preserved': 10, 'source_revision': source.revision,
                    'maintenance_status': fix['status'], 'conflicts': fix['conflicts']}
        fix['status'] = 'in_progress'
        checkpoint(report)
        for _ in range(3):
            try:
                if rewritten != source.content:
                    await knowledge._call('update_note', {'id': SOURCE_ID,
                        'revision': source.revision, 'content': rewritten})
                break
            except RevisionConflict:
                source = await knowledge.fetch_document(SOURCE_ID)
                fix['conflicts'].append({'id': SOURCE_ID, 'observed_revision': source.revision})
                rewritten, changes = rewrite_links(source.content)
            except KnowledgeError:
                observed = await knowledge.fetch_document(SOURCE_ID)
                if observed.content != rewritten:
                    raise
                break
        else:
            raise RevisionConflict('Repeated source edits; no blind overwrite')
        source = await knowledge.fetch_document(SOURCE_ID)
        assert source.content == rewritten
        fix.setdefault('relative_links', changes)
        fix['source_revision'] = source.revision
        report['source']['revision'] = source.revision
        checkpoint(report)
        for item, current in methods:
            for _ in range(3):
                old, block = _parse_method(current.content)
                assert old.method_id == item['method_id']
                assert all(ref.document_id == SOURCE_ID and ref.excerpt in source.content
                           for ref in old.source_refs)
                updated = replace(old,
                    source_refs=tuple(replace(ref, revision=source.revision) for ref in old.source_refs),
                    source_revision={SOURCE_ID: source.revision})
                try:
                    saved = await knowledge.save_method(updated, expected_revision=current.revision)
                    fetched = await knowledge.fetch_document(saved['id'], expected_revision=saved['revision'])
                    break
                except RevisionConflict:
                    current = await knowledge.fetch_document(item['id'])
                    fix['conflicts'].append({'id': current.id, 'observed_revision': current.revision})
                except KnowledgeError:
                    fetched = await knowledge.fetch_document(item['id'])
                    if _parse_method(fetched.content)[0] != updated:
                        raise
                    break
            else:
                raise RevisionConflict('Repeated method edits; no blind overwrite')
            actual, actual_block = _parse_method(fetched.content)
            assert actual == updated
            assert current.content[:block.start()] == fetched.content[:actual_block.start()]
            assert current.content[block.end():] == fetched.content[actual_block.end():]
            assert all(status['availability'] == 'available' for status in await knowledge.source_status(actual))
            item['revision'] = fetched.revision
            fix['methods'] = [entry for entry in fix['methods'] if entry['id'] != item['id']]
            fix['methods'].append({'id': item['id'], 'revision': fetched.revision,
                                   'source_status': 'available', 'outside_block_preserved': True,
                                   'excerpt_preserved': True})
            checkpoint(report)
        fix['status'] = 'saved_and_refetched'
        fix['verified_notes'] = 11
        report['status'] = 'saved_and_refetched'
        checkpoint(report)
        return fix


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.apply)), ensure_ascii=False, indent=2))
