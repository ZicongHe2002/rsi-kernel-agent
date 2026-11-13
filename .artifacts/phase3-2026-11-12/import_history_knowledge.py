"""Publish reviewed historical knowledge through the existing Knowledge MCP."""
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.knowledge import MethodRecord, SourceRef, KnowledgeError
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge


async def main():
    report = {'provenance': 'source_audit', 'methods': [], 'status': 'pending'}
    title = 'phase3-project2-2026-11-12'
    note_id = 'RSI/History/' + title + '.md'
    manifest_path = ROOT / 'knowledge-site/manifests/phase3-2026-11-12/history-experiments.json'
    inventory = json.loads(manifest_path.read_text('utf-8'))
    parts = ['# project2 历史证据恢复（2026-11-12）',
             '这是历史来源知识，不是当前 Task 的实测或 Best。当前 Decode 源码与历史已验证版本不同。',
             '原始目录：C:\\Users\\fengluo\\Desktop\\project2',
             f'完整恢复清单：{manifest_path}；原始字节 SHA256：{sha256(manifest_path.read_bytes()).hexdigest()}']
    for name in ('phase3-project2-history', 'mla-forward-history', 'mla-decode-history', 'optimization-methods', 'failed-experiments'):
        path = ROOT / 'docs/phase3' / (name + '.md')
        parts.append(f'\n## 审查来源：{name}\n\n原文路径：{path}\nSHA256：{sha256(path.read_bytes()).hexdigest()}\n\n' + path.read_text('utf-8'))
    content = '\n\n'.join(parts)
    async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
        try:
            current = await knowledge.fetch_document(note_id)
        except KnowledgeError:
            current = None
        if current is None:
            await knowledge._call('create_note', {'title': title, 'category': 'RSI/History', 'content': content})
        elif current.content != content:
            raise ValueError('Existing history note differs; preserve human edits')
        source = await knowledge.fetch_document(note_id)
        report['source'] = {'id': source.id, 'revision': source.revision}
        table = (ROOT / 'docs/phase3/optimization-methods.md').read_text('utf-8')
        for line in table.splitlines():
            if not line.startswith('| hist-'):
                continue
            label, requirements, risks, evidence = [cell.strip() for cell in line.strip('|').split('|')]
            method_id, mechanism = [text.strip() for text in label.split('/', 1)]
            captured = SourceRef(source.id, source.revision, 'optimization-methods / ' + method_id, line)
            method = MethodRecord(method_id, label, evidence,
                (captured,), {source.id: source.revision}, mechanism,
                ('TPU v6e 历史；其他硬件未验证',),
                ('MLA Decode' if 'decode' in method_id else 'MLA Forward' if 'fwd' in method_id else 'TPU Profile',),
                ('历史 shape、版本与计时边界必须先核对；当前 Task 尚未验证', requirements),
                (requirements,), (risks,), (risks,),
                ('未证明对当前源码和当前 Runtime 有收益', '历史精确源码父关系未确认的条目保留 unknown'),
                (), 'source_supported', mechanism)
            result = await knowledge.save_method(method)
            fetched = await knowledge.fetch_document(result['id'], expected_revision=result['revision'])
            report['methods'].append({'method_id': method_id, 'id': fetched.id, 'revision': fetched.revision})
        report['status'] = 'saved_and_refetched'
    return report


if __name__ == '__main__':
    try:
        result = asyncio.run(main())
    except Exception as error:
        result = {'status': 'unavailable', 'error_type': type(error).__name__,
                  'reason': 'Original MCP write/read did not complete; no credentials or auth settings changed'}
    (ROOT / 'knowledge-site/manifests/phase3-2026-11-12/history-knowledge-import.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
