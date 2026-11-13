"""Fixed synthetic Analyzer rubrics and deterministic replay metrics.

This is an evaluation harness, not another reasoner. Gold judgments are kept out
of prompt-safe cases. Metrics do not establish arbitrary prose or kernel semantic
correctness: they measure explicit predicates, schema/bindings, experiment-control
choices and reproducibility. Empty denominators are null, never a perfect score.
"""
from pathlib import Path
import json
import math
import re

from ..contracts import canonical_json
from .outputs import detached
from .tools import AnalysisTools


ANSWER_SCHEMA = {
    'verdict': 'supported | contradicted | undetermined: truth of the proposition',
    'category': 'observed_fact | derived_measurement | compiler_estimate | mathematical_prediction | source_claim | historical_claim | hypothesis | unknown: strongest defensible classification of your conclusion',
    'refs': ['case:<case id>; other applicable manifest references'],
    'reason': 'Explain the judgment from the supplied evidence, including uncertainty; prose is preserved for human review',
    'controls': ['Select appropriate identifiers from CONTROL_TAGS'],
    'numeric_result': '{value: finite number, unit: string} only when asked for a calculation; otherwise null',
}
CONTROL_TAGS = (
    'critical_path_trace', 'identity_rebind', 'one_variable_ablation', 'correctness_tolerances',
    'condition_matched_retest', 'real_measurement_required', 'retain_both_strategies',
    'causal_ablation', 'preserve_revision_and_mark_stale', 'collect_missing_metric',
    'interval_union_by_clock',
)
_CATEGORIES = {'observed_fact', 'derived_measurement', 'compiler_estimate', 'mathematical_prediction',
               'source_claim', 'historical_claim', 'hypothesis', 'unknown'}


def load_cases(path=None, *, include_expected=True):
    """Load pinned cases; use include_expected=False for every model packet."""
    if path is None:
        path = Path(__file__).resolve().parents[3] / 'tests/fixtures/analyzer_cases.json'
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('Unsupported Analyzer evaluation dataset')
    cases = detached(data['cases'])
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate case ID')
    for case in cases:
        case['provenance'] = data['provenance']
        if not include_expected:
            case.pop('expected', None)
    return cases


def prepare_evaluation_manifest(base, cases):
    """Attach scenario facts as synthetic evaluation references, never experiments.

    This detached evaluation overlay is not a production AnalysisInputManifest
    and must not be published as one. Existing strategy source/history stays intact.
    """
    result = detached(base.to_dict() if hasattr(base, 'to_dict') else base)
    for case in cases:
        ref = 'case:' + case['id']
        if ref in result['references'] or case['strategy_id'] not in result['strategies']:
            raise ValueError('Duplicate case reference or unknown evaluation strategy')
        result['references'][ref] = dict(kind='EvaluationScenario', strategy_id=case['strategy_id'],
            availability='available', provenance='synthetic', value=detached(case['facts']))
    result['evaluation_only'] = True
    return result


def _ratio(numerator, denominator):
    return dict(value=numerator / denominator if denominator else None,
                numerator=numerator, denominator=denominator)


def _signature(value):
    return re.sub(r'\W+', '', value.casefold()) if isinstance(value, str) else ''


def _valid_ref(ref, strategy, catalog):
    return isinstance(ref, str) and ref in catalog and catalog[ref].get('strategy_id') in (None, strategy)


def _supported_predicate(obs, catalog):
    """Check a rendered exact fact, not the truth of free-form causal prose."""
    pred = obs.get('predicate')
    if not isinstance(pred, dict) or set(pred) != {'ref', 'path', 'value'}:
        return False
    ref = catalog.get(pred['ref'])
    if not isinstance(ref, dict) or ref.get('availability') not in (None, 'available'):
        return False
    if not isinstance(pred['path'], list) or pred['ref'] not in obs.get('refs', []):
        return False
    value = ref.get('value')
    try:
        for part in pred['path']:
            if isinstance(value, list) and (type(part) is not int or not 0 <= part < len(value)):
                return False
            value = value[part]
    except (KeyError, IndexError, TypeError):
        return False
    if value is None or type(value) is not type(pred['value']) or value != pred['value']:
        return False
    rendered = f"{pred['ref']} {canonical_json(pred['path'])} = {canonical_json(value)}"
    if obs.get('text') != rendered:
        return False
    kind = ref.get('kind')
    payload = ref.get('value')
    evidence_kind = payload.get('evidence_kind') if isinstance(payload, dict) else None
    expected = {'compiler_estimated':'compiler_estimate', 'model_predicted':'mathematical_prediction',
                'trace_derived':'derived_measurement', 'derived_measurement':'derived_measurement',
                'source_claim':'source_claim', 'historical_claim':'historical_claim'}.get(evidence_kind, 'observed_fact')
    expected = {'calculation':'mathematical_prediction', 'knowledge':'source_claim', 'Knowledge':'source_claim',
                'MethodRecord':'source_claim', 'history':'historical_claim', 'History':'historical_claim'}.get(kind, expected)
    return obs.get('category') == expected


def _answer_schema(answer):
    if type(answer) is not dict or set(answer) != set(ANSWER_SCHEMA):
        return False
    if answer['verdict'] not in ('supported', 'contradicted', 'undetermined') or answer['category'] not in _CATEGORIES:
        return False
    if not isinstance(answer['reason'], str) or not answer['reason'].strip():
        return False
    for name in ('refs', 'controls'):
        if type(answer[name]) is not list or not all(type(x) is str and x for x in answer[name]):
            return False
        if len(set(answer[name])) != len(answer[name]):
            return False
    numeric = answer['numeric_result']
    return numeric is None or (type(numeric) is dict and set(numeric) == {'value','unit'} and
        type(numeric['value']) in (int, float) and math.isfinite(numeric['value']) and
        type(numeric['unit']) is str and bool(numeric['unit']))


def evaluate_result(case, result, manifest, *, case_answer=None, artifacts=None, skills=None):
    """Score one case against actual Analyzer output and a separate case answer.

    case_answer is evaluation-only and is not part of the production Plan schema.
    An existing reference proves location/ownership, not arbitrary prose entailment.
    """
    m = manifest.to_dict() if hasattr(manifest, 'to_dict') else manifest
    catalog = detached(m['references'])
    calculations = result.get('calculations', {})
    for cid, value in calculations.items():
        catalog['calculation:' + cid] = dict(kind='calculation', availability='available',
                                           strategy_id=value.get('strategy_id'), value=value)
    analysis = result.get('analysis', {})
    plans = result.get('plan_set', {}).get('plans', [])
    answer = case_answer if case_answer is not None else result.get('case_answers', {}).get(case['id'])
    expected = case['expected']
    failures = []
    if not _answer_schema(answer):
        failures.append('answer_schema')
        answer = dict(verdict=None, category=None, refs=[], reason='', controls=[], numeric_result=None)
    if answer['verdict'] != expected['verdict']:
        failures.append('verdict')
    if answer['category'] not in expected['categories']:
        failures.append('category')
    if not set(expected['controls']) <= set(answer['controls']):
        failures.append('controls')
    if answer['numeric_result'] != expected['numeric_result']:
        failures.append('numeric_result')
    if ('case:' + case['id'] not in answer['refs'] or
            not all(_valid_ref(ref, case['strategy_id'], catalog) for ref in answer['refs'])):
        failures.append('case_references')

    cited = []
    for key in ('code_understanding', 'observations', 'hypotheses'):
        for item in analysis.get(key, []):
            cited.extend((ref, item.get('strategy_id')) for ref in item.get('refs', []))
    for plan in plans:
        cited.extend((ref, plan.get('strategy_id')) for ref in plan.get('refs', []))
    cited.extend((ref, case['strategy_id']) for ref in answer['refs'])
    validity = _ratio(sum(_valid_ref(ref, sid, catalog) for ref, sid in cited), len(cited))
    asserted = [obs for obs in analysis.get('observations', []) if obs.get('category') not in ('hypothesis', 'unknown')]
    unsupported = sum(not (_supported_predicate(obs, catalog) and
                          all(_valid_ref(ref, obs.get('strategy_id'), catalog) for ref in obs.get('refs', [])))
                      for obs in asserted)
    binding = sum(p.get('strategy_id') in m['strategies'] and
                  p.get('parent_id') == m['strategies'][p['strategy_id']]['parent_id'] for p in plans)
    seen = {sid: {'mechanisms':set(), 'changes':set(), 'unique':0} for sid in m['strategies']}
    duplicate = legal = 0
    for plan in plans:
        sid = plan.get('strategy_id')
        if sid not in seen:
            continue
        mechanism, change = _signature(plan.get('mechanism')), _signature(plan.get('modification'))
        row = seen[sid]
        if mechanism in row['mechanisms'] or change in row['changes']:
            duplicate += 1
        elif mechanism and change:
            row['unique'] += 1
        row['mechanisms'].add(mechanism); row['changes'].add(change)
        validation = plan.get('validation', {})
        legal += bool(mechanism and change and plan.get('parent_id') == m['strategies'][sid]['parent_id'] and
                      plan.get('refs') and all(_valid_ref(r, sid, catalog) for r in plan['refs']) and
                      all(isinstance(plan.get(key), list) and bool(plan[key]) for key in ('tradeoffs','risks','unknowns','variants')) and
                      validation.get('correctness') and validation.get('performance'))
    minimum = expected.get('minimum_plans_per_strategy')
    if minimum is not None and not all(row['unique'] >= minimum for row in seen.values()):
        failures.append('minimum_plans_per_strategy')

    tools = AnalysisTools(m, artifacts, skills)
    replayed = attempted = 0
    calculation_calls = set()
    unreplayable, tool_errors = [], []
    for call in result.get('tool_calls', []):
        if 'error' in call:
            tool_errors.append(call.get('id')); continue
        if call.get('name') == 'profile' and artifacts is None:
            unreplayable.append(call.get('id')); continue
        if call.get('name') == 'skill' and skills is None:
            unreplayable.append(call.get('id')); continue
        attempted += 1
        try:
            actual = tools.call(call['name'], call['arguments'])
            matches = canonical_json(actual) == canonical_json(call.get('result'))
            if call['name'] == 'calculate':
                calculation_calls.add(call['id'])
                matches = matches and canonical_json(actual) == canonical_json(calculations.get(call['id']))
            replayed += matches
        except (ValueError, KeyError, TypeError, OSError):
            pass
    # A registry entry without its successful tool exchange is an unproven
    # numerical result even if a caller supplied plausible-looking metadata.
    attempted += len(set(calculations) - calculation_calls)
    uncertain = expected['verdict'] == 'undetermined'
    uncertainty = _ratio(int(answer['verdict']=='undetermined' and answer['category'] in expected['categories']), 1) if uncertain else _ratio(0,0)
    return dict(case_id=case['id'], provenance='synthetic', case_passed=not failures, case_failures=failures,
                reference_validity=validity, unsupported_claim_rate=_ratio(unsupported,len(asserted)),
                uncertainty=uncertainty, semantic_legality=_ratio(legal,len(plans)),
                diversity=_ratio(sum(row['unique'] >= 2 for row in seen.values()),len(seen)),
                duplicate_rate=_ratio(duplicate,len(plans)), binding=_ratio(binding,len(plans)),
                tool_reproducibility=_ratio(replayed,attempted), unreplayable_tool_ids=unreplayable,
                failed_tool_ids=tool_errors,
                coverage={'semantic_legality':'Plan fields, validation requirements, references and exact parents only',
                          'diversity':'Distinct normalized mechanisms and modifications per strategy; semantic synonyms need human review',
                          'unsupported_claim_rate':'Only exact rendered assertions can be mechanically supported',
                          'case_reason':'Preserved for independent human review, not scored by length or self-rating'},
                limitations=['prose_semantics_unscored','kernel_transformation_legality_not_proven',
                             'synthetic_cases_not_hardware_validation','single_sample_not_statistical_quality_estimate'])


def evaluate_suite(cases, result, manifest, answers, *, artifacts=None, skills=None):
    rows = [evaluate_result(case, result, manifest, case_answer=answers.get(case['id']),
                            artifacts=artifacts, skills=skills) for case in cases]
    acceptance = []
    if not rows or not all(row['case_passed'] for row in rows): acceptance.append('case_answers')
    for row in rows:
        for name in ('reference_validity','semantic_legality','diversity','binding'):
            if row[name]['value'] != 1: acceptance.append(name)
        for name in ('unsupported_claim_rate','duplicate_rate'):
            if row[name]['numerator'] != 0: acceptance.append(name)
        if row['tool_reproducibility']['denominator'] and row['tool_reproducibility']['value'] != 1:
            acceptance.append('tool_reproducibility')
        if row['unreplayable_tool_ids'] or row['failed_tool_ids']: acceptance.append('tool_execution')
    return dict(schema_version=1, provenance='synthetic', total_cases=len(rows),
                passed_cases=sum(row['case_passed'] for row in rows), cases=rows,
                overall_accepted=not acceptance,acceptance_failures=sorted(set(acceptance)),
                metrics_note='passed_cases counts answer rubrics only; overall_accepted also gates output validity. Shared PlanSet metrics repeat per case; do not sum them as independent samples.')
