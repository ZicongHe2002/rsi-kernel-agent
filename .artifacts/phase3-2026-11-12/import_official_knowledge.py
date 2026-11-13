"""Save deduplicated official source knowledge through the existing MCP."""
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

SOURCES = ROOT / 'knowledge-site/manifests/phase3-2026-11-12/sources/official'
REPORT_PATH = ROOT / 'knowledge-site/manifests/phase3-2026-11-12/official-knowledge-import.json'
REPORT = {'classification': 'Source audit; no runtime capability or performance claim', 'status': 'pending', 'methods': []}


def excerpt(path, start, end):
    text = (SOURCES / path).read_text('utf-8')
    return text[text.index(start):text.index(end, text.index(start))].strip()


async def main():
    manifest = json.loads((SOURCES / 'manifest.json').read_bytes())
    tagged = json.loads((SOURCES / 'jax-0.8.1-source-manifest.json').read_bytes())
    async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
        existing = await knowledge.find_methods()
        if not existing['complete']:
            raise KnowledgeError('Cannot complete method deduplication while existing records are unavailable')
        REPORT['deduplication'] = {
            'method_count_before': len(existing['methods']),
            'existing_ids': [method.method_id for method in existing['methods']],
            'existing_mechanisms': sorted({method.mechanism for method in existing['methods']}),
            'retained_without_duplicate': ['hist-decode-dma2', 'hist-profile-accounting'],
            'decision': 'Add only synchronization-boundary and version-capability-gate methods; do not recreate historical pipeline or profile accounting methods',
        }
        if any(method.mechanism in ('synchronized-benchmark-boundary', 'profile-mode-version-gate')
               and not method.method_id.startswith('official-') for method in existing['methods']):
            raise KnowledgeError('An existing non-official method already has the requested mechanism; preserve it for review')
        bench = excerpt('jax-benchmarking.txt', 'JAX code is Just-In-Time', 'Here’s an example')
        trace = excerpt('jax-0.8.1-profiling.md', '1.  `tpu_trace_mode`', '2.  `tpu_num_sparse_cores_to_trace`')
        gates = excerpt('xprof-custom-call-profiling.txt', 'Requires xprof >= 2.24.0', 'Flag Availability Diagnostic')
        counter = excerpt('xprof-kernel-profiling.txt', 'Note: This advanced feature', 'Enabling Fine-Grained Performance Counters')
        title = 'phase3-official-2026-11-12'
        note_id = 'RSI/References/' + title + '.md'
        content = '\n\n'.join([
            '# Phase 3 官方JAX/XProf来源审计（2026-11-12）',
            '这是官方来源知识，validation_status=source_supported，不代表本轮Runtime已支持全部功能。固定运行环境为JAX0.8.1/libtpu0.0.30/XProf2.22.1；不执行文档安装命令、不升级环境。',
            '完整审计文件：' + str(ROOT / 'docs/phase3/phase3-official-reference-audit.md'),
            '## 抓取网页清单\n```json\n' + json.dumps(manifest, ensure_ascii=False, indent=2) + '\n```',
            '## JAX0.8.1官方tag来源\n```json\n' + json.dumps(tagged, ensure_ascii=False, indent=2) + '\n```',
            '## 官方同步Benchmark原文\nURL: https://docs.jax.dev/en/latest/benchmarking.html\n\n' + bench,
            '## 官方JAX0.8.1 TRACE模式原文\nURL: https://raw.githubusercontent.com/jax-ml/jax/jax-v0.8.1/docs/profiling.md\n\n' + trace,
            '## 官方LLO与硬件能力门槛原文\nURL: https://openxla.org/xprof/custom_call_profiling\n\n' + gates,
            '## 官方周期计数器硬件限制原文\nURL: https://openxla.org/xprof/kernel-profiling\n\n' + counter,
            '## 使用边界\nTRACE字符串通过ProfileOptions.advanced_configuration中的tpu_trace_mode传递；LLO初始化flag通过LIBTPU_INIT_ARGS且须早于初始化。这两者不同。当前只实测默认ProfileOptions的program/custom-call；四模式、LLO和高级counter均不能据文档先标本地可用。原始HTML和正文SHA各自保存，不混用。'
        ])
        listing = await knowledge.search(title)
        found = next((row for row in listing['results'] if row['id'] == note_id), None)
        if found:
            current = await knowledge.fetch_document(note_id, expected_revision=found['revision'])
            if current.content != content:
                raise KnowledgeError('Existing official source note differs; preserving human edits')
        else:
            await knowledge._call('create_note', {'title': title, 'category': 'RSI/References', 'content': content})
        source = await knowledge.fetch_document(note_id)
        REPORT['source'] = {'id': source.id, 'revision': source.revision}
        specs = [
            dict(method_id='official-jax-synchronized-benchmark', method_name='JAX同步Benchmark边界',
                 description='按官方要求分开初次编译、设备放置与重复执行，并等待全部输出计算完成；host同步计时与device trace分别报告。',
                 quote=bench, locator='官方同步Benchmark原文', mechanism='synchronized-benchmark-boundary',
                 requirements=('编译/预热在样本之外按固定协议执行', '同输入、同输出语义，使用jax.block_until_ready等待完整pytree', '必要prepare与cache输出按原契约计时'),
                 risks=('只计dispatch会低估执行时间', '移除必要工作会破坏可比性', 'host同步time不能直接称为纯device时间'),
                 unknowns=('当前Kernel变体是否改善固定协议延迟仍需真实实验',)),
            dict(method_id='official-tpu-profile-mode-version-gates', method_name='TPU Profile模式与版本能力门槛',
                 description='区分ProfileOptions的tpu_trace_mode、初始化LLO flag和TPU7x+周期硬件counter；在精确软件/硬件环境中单独验证可用事件。',
                 quote=trace + '\n\n' + gates + '\n\n' + counter, locator='官方JAX0.8.1 TRACE模式原文 / 官方LLO与硬件能力门槛原文',
                 mechanism='profile-mode-version-gate',
                 requirements=('tpu_trace_mode通过ProfileOptions.advanced_configuration配置', '固定JAX/libtpu/XProf版本并保存原始trace', 'LLO flag须满足官方软件门槛且在初始化前设置', '当前v6e不能套用TPU7x+周期counter能力'),
                 risks=('未知初始化flag可能使backend中止', '细粒度trace可能丢弃外层HLO事件', '文档定义不证明本地采集成功', 'LLO插值span不是独立实测counter'),
                 unknowns=('JAX0.8.1/libtpu0.0.30上四模式的实际事件覆盖尚未分别验证', '不通过升级环境来声称原context支持新能力')),
        ]
        for spec in specs:
            method = MethodRecord(spec['method_id'], spec['method_name'], spec['description'],
                (SourceRef(source.id, source.revision, spec['locator'], spec['quote']),), {source.id: source.revision},
                'measurement-validity', ('TPU v6e; documented capability gates apply; other hardware requires its own audit',),
                ('JAX/Pallas workload measurement and profiling',),
                ('Exact software versions and workload semantics must be fixed',), tuple(spec['requirements']),
                ('More explicit measurement boundaries and retained raw evidence; no promised performance gain',),
                tuple(spec['risks']), tuple(spec['unknowns']), (), 'source_supported', spec['mechanism'])
            result = await knowledge.save_method(method)
            document = await knowledge.fetch_document(result['id'], expected_revision=result['revision'])
            statuses = await knowledge.source_status(method)
            REPORT['methods'].append({'method_id': method.method_id, 'id': document.id,
                                      'revision': document.revision, 'source_status': statuses,
                                      'validation_status': method.validation_status, 'local_experiment_refs': []})
        REPORT['status'] = 'saved_and_refetched'


try:
    asyncio.run(main())
except Exception as error:
    REPORT.update(status='incomplete', error_type=type(error).__name__, reason=str(error))
finally:
    REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(REPORT, ensure_ascii=False))
