import copy
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.profiler.analyzer import Analyzer
from test_analyzer import manifest, proposal


class ContextEncodingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.profiler.context'),
                             'Lossless shared-node context encoding is missing')
        self.api = importlib.import_module('rsi_kernel_agent.profiler.context')

    def repeated(self):
        contract = {'source': 'load and compute\n' * 600, 'config': {'tile': 128, 'dtype': 'bf16'},
                    'oracle': {'atol': 0.0001, 'rtol': 0.001, 'full_cache': True}}
        return {'history': [{'id': 'state-' + str(i), 'jobs': [
            {'id': 'job-' + str(j), 'contract': copy.deepcopy(contract),
             'operation': {'stage': 'benchmark', 'contract': copy.deepcopy(contract)}} for j in range(4)]}
            for i in range(20)], 'source': contract['source'], 'counts': [0, 1, 1.0, True, False, None]}

    def test_repeated_contracts_and_strings_are_lossless_and_substantially_smaller(self):
        value = self.repeated()
        encoded = self.api.encode_context(value)
        self.assertEqual(encoded['codec'], 'rsi-shared-json.v1')
        self.assertIs(encoded['lossless'], True)
        self.assertLess(len(canonical_json(encoded)), len(canonical_json(value)) // 10)
        self.assertEqual(canonical_json(self.api.decode_context(encoded)), canonical_json(value))
        self.assertEqual(encoded['original_sha256'], sha256(canonical_json(value).encode()).hexdigest())
        self.assertEqual(encoded['original_bytes'], len(canonical_json(value).encode()))
        self.assertEqual(len(encoded['nodes']), len(set(encoded['nodes'])))
        self.assertEqual(self.api.encode_context(copy.deepcopy(value)), encoded)

    def test_business_tags_and_all_json_scalar_types_roundtrip_without_collision(self):
        value = {'type': 'ref', 'digest': '0' * 64, 'nested': [
            {'type': 'object', 'value': {'type': 'array', 'value': []}},
            {'codec': 'rsi-shared-json.v1', 'root': {'type': 'ref'}, 'nodes': {}},
            1, 1.0, -0.0, True, False, None, '汉字\n"quoted"', [], {}]}
        self.assertEqual(canonical_json(self.api.decode_context(self.api.encode_context(value))), canonical_json(value))
        for value in (None, True, 1, 1.0, '', [], {}, 'large' * 1000):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(canonical_json(self.api.decode_context(self.api.encode_context(value))), canonical_json(value))

    def test_changed_node_payload_or_digest_is_rejected(self):
        original = self.api.encode_context(self.repeated())
        variants = []
        forged = copy.deepcopy(original)
        key = next(iter(forged['nodes']))
        forged['nodes'][key] = {'type': 'string', 'value': 'tampered'}
        variants.append(forged)
        forged = copy.deepcopy(original)
        key = next(iter(forged['nodes']))
        forged['nodes']['0' * 64] = forged['nodes'].pop(key)
        variants.append(forged)
        forged = copy.deepcopy(original)
        forged['root']['digest'] = 'f' * 64
        variants.append(forged)
        forged = copy.deepcopy(original)
        forged['original_sha256'] = '0' * 64
        variants.append(forged)
        for value in variants:
            with self.subTest(value=value['root']), self.assertRaises(ValueError):
                self.api.decode_context(value)

    def test_missing_or_unreachable_nodes_and_invalid_tags_are_rejected(self):
        original = self.api.encode_context(self.repeated())
        forged = copy.deepcopy(original)
        forged['nodes'].pop(forged['root']['digest'])
        with self.assertRaises(ValueError):
            self.api.decode_context(forged)
        forged = copy.deepcopy(original)
        extra = {'type': 'string', 'value': 'unreachable'}
        forged['nodes'][sha256(canonical_json(extra).encode()).hexdigest()] = extra
        with self.assertRaises(ValueError):
            self.api.decode_context(forged)
        forged = copy.deepcopy(original)
        node = {'type': 'unrecognized', 'value': []}
        key = sha256(canonical_json(node).encode()).hexdigest()
        forged['nodes'] = {key: node}
        forged['root'] = {'type': 'ref', 'digest': key}
        with self.assertRaises(ValueError):
            self.api.decode_context(forged)

    def test_v2_packet_has_only_encoded_manifest_and_reconstructs_every_value(self):
        value = manifest()
        value['schema_version'] = 2
        value['strategies'] = {'A': value['strategies']['A']}
        value['references'].pop('source:B')
        value['strategies']['A']['history'] = self.repeated()
        packet = Analyzer(value).packet()
        self.assertEqual(packet['manifest']['codec'], 'rsi-shared-json.v1')
        self.assertEqual(self.api.decode_context(packet['manifest']), value)
        self.assertEqual(packet['manifest_digest'], sha256(canonical_json(value).encode()).hexdigest())
        self.assertNotIn('manifest_uncompressed', packet)
        self.assertNotIn('raw_manifest', packet)
        self.assertLess(len(canonical_json(packet)), len(canonical_json(value)) // 5)
        self.assertIn('pool', packet['instructions'])

    def test_node_pages_and_chunked_reads_cover_complete_reference_closure(self):
        encoded = self.api.encode_context(self.repeated())
        reader = self.api.ContextReader(encoded)
        initial = reader.coverage()
        self.assertFalse(initial['complete'])
        self.assertEqual(initial['read_nodes'], 0)
        index, offset = [], 0
        while True:
            page = reader.index(offset=offset, limit=2)
            index.extend(page['nodes'])
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertEqual({row['digest'] for row in index}, set(encoded['nodes']))
        first = index[0]
        response = reader.read(first['digest'], offset=0, limit=17)
        reader.read(first['digest'], offset=0, limit=17)
        self.assertEqual(reader.coverage()['read_chars'], len(response['content']))
        for node in index:
            content, offset = '', 0
            while True:
                page = reader.read(node['digest'], offset=offset, limit=29)
                self.assertEqual(page['page_sha256'], sha256(page['content'].encode()).hexdigest())
                content += page['content']
                if page['next_offset'] is None:
                    break
                offset = page['next_offset']
            self.assertEqual(content, canonical_json(encoded['nodes'][node['digest']]))
        coverage = reader.coverage()
        self.assertTrue(coverage['complete'])
        self.assertEqual(coverage['expected_nodes'], coverage['read_nodes'])
        self.assertEqual(coverage['expected_chars'], coverage['read_chars'])
        self.assertEqual(coverage['missing_nodes'], [])
        self.assertTrue(coverage['read_log'])
        self.assertFalse(coverage['semantic_comprehension_verified'])

    def test_node_reader_rejects_unknown_nodes_gaps_and_invalid_page_ranges(self):
        reader = self.api.ContextReader(self.api.encode_context(self.repeated()))
        digest = next(iter(reader.index()['nodes']))['digest']
        reader.read(digest, offset=8, limit=4)
        self.assertFalse(reader.coverage()['complete'])
        self.assertEqual(reader.coverage()['read_nodes'], 0)
        for arguments in ({'digest': '0' * 64}, {'digest': digest, 'offset': -1},
                          {'digest': digest, 'offset': True}, {'digest': digest, 'limit': 0},
                          {'digest': digest, 'limit': 24001}, {'digest': digest, 'offset': 10**9}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                reader.read(**arguments)
        with self.assertRaises(ValueError):
            reader.index(offset=-1)

    def test_v2_context_tools_replay_bounded_reads_without_claiming_comprehension(self):
        value = manifest()
        value['schema_version'] = 2
        value['strategies'] = {'A': value['strategies']['A']}
        value['references'].pop('source:B')
        analyzer = Analyzer(value)
        packet = analyzer.packet()
        self.assertIn('context_index', packet['tools'])
        analyzer.advance({'tool_calls': [{'id': 'index', 'name': 'context_index', 'arguments': {'offset': 0, 'limit': 100}}]})
        indexed = analyzer.packet()['tool_results'][0]['result']
        digest = indexed['nodes'][0]['digest']
        response = {'tool_calls': [{'id': 'read', 'name': 'context_read',
                                  'arguments': {'digest': digest, 'offset': 0, 'limit': 19}},
                                 {'id': 'coverage', 'name': 'context_coverage', 'arguments': {}}]}
        analyzer.advance(response)
        results = analyzer.packet()['tool_results']
        self.assertEqual(len(results[0]['result']['content']), 19)
        self.assertEqual(results[1]['result']['read_chars'], 19)
        self.assertFalse(results[1]['result']['complete'])
        self.assertFalse(results[1]['result']['semantic_comprehension_verified'])
        answer = proposal()
        for key in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[key] = [row for row in answer[key] if row['strategy_id'] == 'A']
        answer['plans'] = answer['plans'][:1]
        result = analyzer.advance({'final': answer})
        replay = Analyzer(value)
        for exchange in result['exchanges']:
            replay.advance(exchange)
        self.assertEqual(replay.result, result)
        self.assertNotIn('context_read', Analyzer(manifest()).packet()['tools'])

    def test_v1_packet_and_tool_result_remain_plain_and_replayable(self):
        value = manifest()
        analyzer = Analyzer(value)
        self.assertEqual(analyzer.packet()['manifest'], value)
        analyzer.advance({'tool_calls': [{'id': 'source-call', 'name': 'source', 'arguments': {'strategy_id': 'A'}}]})
        self.assertEqual(analyzer.packet()['tool_results'][0]['result']['source'], value['strategies']['A']['source'])
        result = analyzer.advance({'final': proposal()})
        self.assertEqual(len(result['plan_set']['plans']), 4)


if __name__ == '__main__':
    unittest.main()
