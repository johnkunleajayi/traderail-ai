"""Three-way research evidence assessment. No simulations, optimization or authority."""
from dataclasses import asdict, replace
from decimal import Decimal
from app.strategy_lab.benchmarks import BenchmarkKind
from app.strategy_lab.execution.config import identity
from app.strategy_lab.canonical import decimal_token
from app.strategy_lab.numeric import difference, ratio
from app.strategy_lab.walk_forward.summary import return_sign
from .binding import bind_evidence, performance
from .models import Category, Status, Verdict, CriterionResult, ResearchValidationReport, ValidationEvidence
from .policy import DEFAULT_RESEARCH_POLICY, ValidationPolicy, BenchmarkTreatment

REPORT_ENGINE_VERSION = "1.0.0"


def verdict_from_criteria(criteria):
    rows = tuple(criteria)
    if not rows or any(not isinstance(row, CriterionResult) for row in rows) or len({row.code for row in rows}) != len(rows):
        raise ValueError("Distinct structured criteria required")
    if any(row.status is Status.INSUFFICIENT for row in rows):
        return Verdict.INSUFFICIENT_EVIDENCE
    if any(row.status is Status.FAIL for row in rows):
        return Verdict.FAILED_VALIDATION
    return Verdict.VALIDATION_CANDIDATE


def evaluate_validation_evidence(evidence: ValidationEvidence, policy: ValidationPolicy = DEFAULT_RESEARCH_POLICY):
    if not isinstance(policy, ValidationPolicy) or replace(policy).policy_identity != policy.policy_identity:
        raise ValueError("An immutable valid research policy is required")
    source_ids, evidence_id = bind_evidence(evidence)
    criteria = []
    def check(code, category, observed, required, *, minimum=True, coverage=False, mandatory=True):
        relation = ">=" if minimum else "<="
        if not mandatory:
            status, reason = Status.NOT_APPLICABLE, code+"_INFORMATIONAL"
        elif observed is None:
            status, reason = Status.INSUFFICIENT, code+"_UNDEFINED_OR_MISSING"
        else:
            passed = observed >= required if minimum else observed <= required
            status = (Status.SUFFICIENT if passed else Status.INSUFFICIENT) if coverage else (Status.PASS if passed else Status.FAIL)
            reason = code+("_MET" if passed else "_INSUFFICIENT" if coverage else "_BREACHED")
        def text(value):
            return decimal_token(value) if isinstance(value, Decimal) else str(value)
        explanation = (f"{code}: observed {text(observed)}; declared requirement {relation} {text(required)}. "
            + ("Coverage requirement." if coverage else "Research-policy hypothesis.")
            + (" Informational only; does not gate this verdict." if not mandatory else ""))
        criteria.append(CriterionResult(code, category, observed, required, status, reason, explanation))
    C = Category
    oos = evidence.oos.out_of_sample.performance
    check("OOS_COMPLETED_TRADES", C.EVIDENCE_COVERAGE, oos.trade_count, policy.minimum_oos_completed_trades, coverage=True)
    check("OOS_EXPECTANCY", C.RETURN_QUALITY, oos.expectancy, policy.minimum_oos_expectancy)
    check("OOS_PROFIT_FACTOR", C.RETURN_QUALITY, oos.profit_factor, policy.minimum_oos_profit_factor)
    check("OOS_DRAWDOWN", C.DRAWDOWN_CONTROL, oos.maximum_drawdown, policy.maximum_oos_drawdown, minimum=False)
    folds = () if evidence.walk_forward is None else performance(evidence.walk_forward)
    check("WALK_FORWARD_FOLDS", C.EVIDENCE_COVERAGE, len(folds), policy.minimum_walk_forward_folds, coverage=True)
    if evidence.walk_forward is not None:
        check("WALK_FORWARD_DECLARED_COVERAGE", C.EVIDENCE_COVERAGE, len(folds), len(evidence.walk_forward.plan.folds), coverage=True)
    active = tuple(row for row in folds if row.trade_count > 0)
    check("ACTIVE_FOLDS", C.EVIDENCE_COVERAGE, len(active), policy.minimum_active_folds, coverage=True)
    defined = sum(row.net_return is not None and row.maximum_drawdown is not None for row in folds)
    check("FOLD_METRIC_COVERAGE", C.EVIDENCE_COVERAGE, defined if folds else None, len(folds), coverage=True)
    proportion = ratio(Decimal(sum(row.net_return is not None and row.net_return >= 0 for row in active)), Decimal(len(active)))
    if any(row.net_return is None for row in active):
        proportion = None
    check("NON_LOSING_ACTIVE_FOLDS", C.WALK_FORWARD_CONSISTENCY, proportion, policy.minimum_non_losing_fold_proportion)
    worst = max(row.maximum_drawdown for row in folds) if folds and all(row.maximum_drawdown is not None for row in folds) else None
    check("WORST_FOLD_DRAWDOWN", C.DRAWDOWN_CONTROL, worst, policy.maximum_worst_fold_drawdown, minimum=False)
    sensitivity = evidence.sensitivity
    variants = () if sensitivity is None else tuple(sorted(sensitivity.variants, key=lambda row: row.configuration_identity))
    required = policy.require_sensitivity_stability
    check("SENSITIVITY_VARIANTS", C.EVIDENCE_COVERAGE, len(variants), policy.minimum_sensitivity_variants,
        coverage=True, mandatory=required)
    if sensitivity is not None:
        check("SENSITIVITY_DECLARED_COVERAGE", C.EVIDENCE_COVERAGE, len(variants), len(sensitivity.plan.configurations)-1,
            coverage=True, mandatory=required)
        baseline = performance(sensitivity.baseline.validation)
        for variant in variants:
            check("SENSITIVITY_FOLD_COVERAGE_"+variant.configuration_identity, C.EVIDENCE_COVERAGE,
                len(variant.oos_metrics), len(baseline), coverage=True, mandatory=required)
            for index, (base, value) in enumerate(zip(baseline, variant.oos_metrics)):
                prefix = f"SENSITIVITY_{variant.configuration_identity}_{index}_"
                _robustness_checks(check, prefix, C.PARAMETER_STABILITY, base, value,
                    policy.minimum_variant_completed_trades, policy.minimum_variant_trade_retention,
                    policy.maximum_sensitivity_return_degradation, policy.allow_sensitivity_return_sign_flip, required)
    stress = evidence.stress
    completed = () if stress is None else tuple(sorted(stress.completed, key=lambda row: row.scenario.code))
    failures = () if stress is None else tuple(sorted(stress.failures, key=lambda row: row.scenario_code))
    check("STRESS_SCENARIOS", C.EVIDENCE_COVERAGE, len(completed)+len(failures), policy.minimum_stress_scenarios, coverage=True)
    if stress is not None:
        check("STRESS_DECLARED_COVERAGE", C.EVIDENCE_COVERAGE, len(completed)+len(failures), len(stress.plan.scenarios), coverage=True)
        base_rows = tuple(row for row in completed if row.scenario.code == "BASELINE")
        check("STRESS_BASELINE_COVERAGE", C.EVIDENCE_COVERAGE, len(base_rows), 1, coverage=True)
        base = evidence.oos if stress.plan.validation == evidence.oos.plan else evidence.walk_forward
        baseline = performance(base)
        for scenario in completed:
            if scenario.scenario.code == "BASELINE":
                continue
            check("STRESS_FOLD_COVERAGE_"+scenario.scenario.code, C.EVIDENCE_COVERAGE,
                len(scenario.oos_metrics), len(baseline), coverage=True)
            for index, (base_value, value) in enumerate(zip(baseline, scenario.oos_metrics)):
                prefix = f"STRESS_{scenario.scenario.code}_{index}_"
                _robustness_checks(check, prefix, C.COST_RESILIENCE, base_value, value,
                    policy.minimum_stress_completed_trades, policy.minimum_stress_trade_retention,
                    policy.maximum_stress_return_degradation, policy.allow_stress_return_sign_flip, True)
        for failure in failures:
            criteria.append(CriterionResult("STRESS_"+failure.scenario_code+"_EXECUTION", C.COST_RESILIENCE,
                failure.reason.value, "COMPLETED_WITHOUT_CAPITAL_FAILURE", Status.FAIL,
                "STRESSED_EXECUTION_UNAFFORDABLE", "Declared stress attempt could not execute with fixed unleveraged capital; no quantity resizing or invented metrics."))
    primary_id = evidence.oos.out_of_sample.result_identity
    benchmarks = tuple(replace(row, differences=tuple(sorted(row.differences))) for row in
        sorted(evidence.benchmarks, key=lambda row: (row.strategy_result_identity, row.benchmark.kind.value)))
    for kind, required, treatment in ((BenchmarkKind.CASH, policy.require_cash_comparison, policy.cash_treatment),
        (BenchmarkKind.BUY_AND_HOLD, policy.require_buy_and_hold_comparison, policy.buy_and_hold_treatment)):
        rows = tuple(row for row in benchmarks if row.strategy_result_identity == primary_id and row.benchmark.kind is kind)
        mandatory = treatment is BenchmarkTreatment.MANDATORY
        check("BENCHMARK_"+kind.value+"_COVERAGE", C.EVIDENCE_COVERAGE, len(rows), 1,
            coverage=True, mandatory=required or mandatory)
        observed = dict(rows[0].differences).get("total_return") if rows else None
        check("BENCHMARK_"+kind.value+"_EXCESS_RETURN", C.BENCHMARK_CONTEXT, observed,
            policy.minimum_benchmark_excess_return, mandatory=mandatory)
        if rows and (required or mandatory):
            check("BENCHMARK_"+kind.value+"_METRIC", C.EVIDENCE_COVERAGE, int(observed is not None), 1, coverage=True)
    criteria = tuple(sorted(criteria, key=lambda row: row.code))
    verdict = verdict_from_criteria(criteria)
    insufficiency = tuple(row.reason_code for row in criteria if row.status is Status.INSUFFICIENT)
    failure_reasons = tuple(row.reason_code for row in criteria if row.status is Status.FAIL)
    passed = tuple(row.code for row in criteria if row.status is Status.PASS)
    definition = evidence.oos.plan.strategy_definition
    result_id = identity("strategy-lab.research-verdict/v1", {"engine": REPORT_ENGINE_VERSION,
        "evidence": evidence_id, "policy": policy.policy_identity, "verdict": verdict,
        "criteria": tuple(asdict(row) for row in criteria)})
    return ResearchValidationReport(definition.family, definition.version, definition.configuration_identity,
        evidence.oos.plan.dataset_identity, policy, source_ids, verdict, criteria, insufficiency,
        failure_reasons, passed, benchmarks, evidence_id, result_id)


def _robustness_checks(check, prefix, category, baseline, value, minimum_trades, minimum_retention,
                       maximum_degradation, allow_sign_flip, mandatory):
    check(prefix+"TRADES", Category.EVIDENCE_COVERAGE, value.trade_count, minimum_trades, coverage=True, mandatory=mandatory)
    retention = ratio(Decimal(value.trade_count), Decimal(baseline.trade_count))
    check(prefix+"TRADE_RETENTION", category, retention, minimum_retention, mandatory=mandatory)
    degradation = None if baseline.net_return is None or value.net_return is None else max(Decimal(0), difference(baseline.net_return, value.net_return))
    check(prefix+"RETURN_DEGRADATION", category, degradation, maximum_degradation, minimum=False, mandatory=mandatory)
    # Any sign transition is recorded; policy may explicitly permit it.
    flip = None if baseline.net_return is None or value.net_return is None else return_sign(baseline.net_return) != return_sign(value.net_return)
    check(prefix+"RETURN_SIGN_CHANGE", category, flip, allow_sign_flip, minimum=False, mandatory=mandatory)
