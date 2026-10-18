"""Bind trusted immutable research artifacts without executing any research engine."""
from dataclasses import asdict
from decimal import Decimal
from app.strategy_lab.accounting.types import PerformanceResults
from app.strategy_lab.dataset import create_dataset
from app.strategy_lab.out_of_sample import OutOfSampleResult, OutOfSamplePlan
from app.strategy_lab.walk_forward import WalkForwardResult
from app.strategy_lab.sensitivity.plan import changed_fields
from app.strategies.configuration import StrategyDefinition
from app.strategy_lab.execution.config import identity
from app.strategy_lab.numeric import require_decimal, difference
from .models import ValidationEvidence


def periods(result):
    if isinstance(result, OutOfSampleResult):
        return (result.out_of_sample,)
    if isinstance(result, WalkForwardResult):
        rows = tuple(sorted(result.folds, key=lambda row: row.index))
        if len({row.index for row in rows}) != len(rows):
            raise ValueError("Duplicate fold evidence")
        return tuple(row.validation.out_of_sample for row in rows)
    raise ValueError("Existing validation result required")


def performance(result):
    rows = tuple(row.performance for row in periods(result))
    for row in rows:
        if not isinstance(row, PerformanceResults) or type(row.trade_count) is not int or row.trade_count < 0:
            raise ValueError("Invalid performance evidence")
        for name, value in asdict(row).items():
            if name != "trade_count" and value is not None:
                require_decimal(value, name)
                if name not in ("total_return", "gross_return", "net_return", "expectancy") and value < 0:
                    raise ValueError("Invalid negative performance measurement")
        if row.maximum_drawdown is not None and row.maximum_drawdown > 1:
            raise ValueError("Invalid drawdown evidence")
    return rows


def plan_signature(plan, *, strategy=True, execution=True):
    def fold_payload(fold):
        payload = fold.configuration()
        if not strategy:
            payload.pop("strategy_configuration_identity")
        if not execution:
            payload.pop("execution")
        return payload
    payload = fold_payload(plan) if isinstance(plan, OutOfSamplePlan) else {
        "dataset": plan.dataset.dataset_fingerprint, "folds": tuple(fold_payload(fold) for fold in plan.folds)}
    return identity("strategy-lab.validation-evidence-assumptions/v1", payload)


def _paired(result):
    plan = result.plan
    for research, window in ((result.in_sample, plan.split.in_sample), (result.out_of_sample, plan.split.out_of_sample)):
        data = plan.dataset
        expected = create_dataset(data.context, tuple(row for row in data.candles if row.open_time < window.end),
            warmup_start=plan.warmup.start, evaluation_start=window.start, evaluation_end=window.end,
            as_of=max(data.evaluation_end, *(row.retrieved_at for row in data.candles)))
        if (research.dataset_fingerprint != expected.dataset_fingerprint
            or research.replay.dataset_fingerprint != expected.dataset_fingerprint
            or research.execution.dataset_fingerprint != expected.dataset_fingerprint
            or research.context != plan.dataset.context or research.initial_capital != plan.initial_capital
            or research.execution.quantity != plan.quantity or research.execution.configuration != plan.execution_configuration
            or research.accounting_configuration != plan.accounting_configuration
            or research.replay.strategy_definition != plan.strategy_definition
            or research.replay.evaluation_start != window.start or research.replay.evaluation_end != window.end):
            raise ValueError("Research result does not match its frozen validation assumptions")
    performance(result)


def _validation(result):
    if isinstance(result, OutOfSampleResult):
        _paired(result)
    elif isinstance(result, WalkForwardResult):
        seen = set()
        for fold in result.folds:
            if type(fold.index) is not int or fold.index in seen or not 0 <= fold.index < len(result.plan.folds):
                raise ValueError("Invalid fold identity/index")
            seen.add(fold.index)
            if fold.validation.plan != result.plan.folds[fold.index]:
                raise ValueError("Fold evidence differs from its declared window/assumptions")
            _paired(fold.validation)
        performance(result)
    else:
        raise ValueError("Typed validation evidence required")


def bind_evidence(evidence: ValidationEvidence):
    if not isinstance(evidence, ValidationEvidence):
        raise ValueError("Immutable validation evidence required")
    primary = evidence.oos
    _validation(primary)
    definition = primary.plan.strategy_definition
    sources = {"IS_OOS": primary.validation_identity, "IS_OOS_PLAN": primary.plan.plan_identity,
        "IS_BACKTEST": primary.in_sample.result_identity, "OOS_BACKTEST": primary.out_of_sample.result_identity}
    observations = {"IS": asdict(primary.in_sample.performance), "OOS": asdict(primary.out_of_sample.performance)}
    anchors = {primary.validation_identity: primary}
    benchmarks_allowed = {primary.out_of_sample.result_identity: (primary.plan, primary.out_of_sample)}
    if evidence.walk_forward is not None:
        wf = evidence.walk_forward
        _validation(wf)
        if wf.plan.dataset.dataset_fingerprint != primary.plan.dataset.dataset_fingerprint:
            raise ValueError("Walk-forward evidence uses a different frozen parent dataset")
        for fold in wf.plan.folds:
            if (fold.strategy_definition != definition or fold.execution_configuration != primary.plan.execution_configuration
                or fold.accounting_configuration != primary.plan.accounting_configuration
                or fold.quantity != primary.plan.quantity or fold.initial_capital != primary.plan.initial_capital):
                raise ValueError("Walk-forward evidence changes the declared strategy/research assumptions")
        anchors[wf.validation_identity] = wf
        sources["WALK_FORWARD"] = wf.validation_identity
        sources["WALK_FORWARD_PLAN"] = wf.plan.plan_identity
        observations["WALK_FORWARD"] = tuple(asdict(row) for row in performance(wf))
        for fold in wf.folds:
            sources[f"FOLD:{fold.index}"] = fold.fold_identity
            sources[f"FOLD:{fold.index}:VALIDATION"] = fold.validation.validation_identity
            sources[f"FOLD:{fold.index}:IS"] = fold.validation.in_sample.result_identity
            sources[f"FOLD:{fold.index}:OOS"] = fold.validation.out_of_sample.result_identity
            benchmarks_allowed[fold.validation.out_of_sample.result_identity] = (fold.validation.plan, fold.validation.out_of_sample)
    def anchor(result):
        found = anchors.get(result.validation_identity)
        if (found is None or result.plan != found.plan or performance(result) != performance(found)
            or tuple(row.result_identity for row in periods(result)) != tuple(row.result_identity for row in periods(found))):
            raise ValueError("Robustness baseline is not the supplied unchanged validation evidence")
        return found
    if evidence.sensitivity is not None:
        sensitivity = evidence.sensitivity
        base = anchor(sensitivity.baseline.validation)
        baseline_config_id = StrategyDefinition(definition.family, definition.version, asdict(sensitivity.baseline.configuration)).configuration_identity
        if (sensitivity.plan.validation != base.plan or sensitivity.baseline.configuration_identity != definition.configuration_identity
            or baseline_config_id != definition.configuration_identity or sensitivity.plan.baseline != sensitivity.baseline.configuration):
            raise ValueError("Sensitivity baseline/configuration mismatch")
        if sensitivity.baseline.oos_metrics != performance(base):
            raise ValueError("Sensitivity baseline metric projection mismatch")
        expected = {StrategyDefinition(definition.family, definition.version, asdict(config)).configuration_identity
            for config in sensitivity.plan.configurations}
        seen = {definition.configuration_identity}
        sources["SENSITIVITY"] = sensitivity.sensitivity_identity
        sources["SENSITIVITY_PLAN"] = sensitivity.plan.plan_identity
        sources["SENSITIVITY_BASELINE"] = base.validation_identity
        for variant in sorted(sensitivity.variants, key=lambda row: row.configuration_identity):
            config_id = StrategyDefinition(definition.family, definition.version, asdict(variant.configuration)).configuration_identity
            if config_id != variant.configuration_identity or config_id not in expected or config_id in seen:
                raise ValueError("Undeclared/duplicate sensitivity alternative")
            seen.add(config_id)
            _validation(variant.validation)
            if (plan_signature(base.plan, strategy=False) != plan_signature(variant.validation.plan, strategy=False)
                or variant.oos_metrics != performance(variant.validation)
                or variant.changed_fields != changed_fields(sensitivity.plan.baseline, variant.configuration)):
                raise ValueError("Sensitivity evidence changes data/windows/assumptions or metric projection")
            plans = (variant.validation.plan,) if isinstance(variant.validation, OutOfSampleResult) else variant.validation.plan.folds
            if any(fold.strategy_definition.configuration_identity != config_id for fold in plans):
                raise ValueError("Sensitivity configuration is not the evaluated configuration")
            sources[f"SENSITIVITY:{config_id}"] = variant.validation.validation_identity
            observations[f"SENSITIVITY:{config_id}"] = tuple(asdict(row) for row in variant.oos_metrics)
    if evidence.stress is not None:
        stress = evidence.stress
        base_rows = tuple(row for row in stress.completed if row.scenario.code == "BASELINE")
        if len(base_rows) > 1:
            raise ValueError("Duplicate stress baseline evidence")
        matching = tuple(row for row in anchors.values() if row.plan == stress.plan.validation)
        if len(matching) != 1:
            raise ValueError("Stress baseline plan mismatch")
        base = matching[0]
        if base_rows:
            anchor(base_rows[0].validation)
        declared = {row.code: row for row in stress.plan.scenarios}
        for code, scenario in declared.items():
            sources["STRESS_ASSUMPTIONS:"+code] = scenario.scenario_identity
        seen = set()
        sources["STRESS"] = stress.source_identity
        sources["STRESS_PLAN"] = stress.plan.plan_identity
        for scenario in sorted(stress.completed, key=lambda row: row.scenario.code):
            code = scenario.scenario.code
            if code in seen or declared.get(code) != scenario.scenario:
                raise ValueError("Undeclared/duplicate stress evidence")
            seen.add(code)
            _validation(scenario.validation)
            if (plan_signature(base.plan, execution=False) != plan_signature(scenario.validation.plan, execution=False)
                or scenario.oos_metrics != performance(scenario.validation)):
                raise ValueError("Stress evidence changes strategy/data/windows/quantity/capital")
            plans = (scenario.validation.plan,) if isinstance(scenario.validation, OutOfSampleResult) else scenario.validation.plan.folds
            if any(fold.execution_configuration.cost_model != scenario.scenario.costs for fold in plans):
                raise ValueError("Stress costs are not the evaluated costs")
            sources[f"STRESS:{code}"] = scenario.result_identity
            sources[f"STRESS:{code}:VALIDATION"] = scenario.validation.validation_identity
            observations[f"STRESS:{code}"] = tuple(asdict(row) for row in scenario.oos_metrics)
        for failure in sorted(stress.failures, key=lambda row: row.scenario_code):
            if failure.scenario_code == "BASELINE" or failure.scenario_code in seen or failure.scenario_code not in declared:
                raise ValueError("Undeclared/duplicate stress failure")
            seen.add(failure.scenario_code)
            sources[f"STRESS_FAILURE:{failure.scenario_code}"] = failure.source_identity
            observations[f"STRESS_FAILURE:{failure.scenario_code}"] = failure.reason.value
    seen_benchmarks = set()
    for comparison in evidence.benchmarks:
        if comparison.strategy_result_identity not in benchmarks_allowed:
            raise ValueError("Benchmark belongs to a different strategy evaluation")
        plan, research = benchmarks_allowed[comparison.strategy_result_identity]
        row = comparison.benchmark
        key = (comparison.strategy_result_identity, row.kind)
        if key in seen_benchmarks:
            raise ValueError("Duplicate benchmark evidence")
        seen_benchmarks.add(key)
        if (row.dataset_identity != plan.dataset.dataset_fingerprint or row.context != plan.dataset.context
            or row.window != plan.split.out_of_sample or row.initial_capital != plan.initial_capital
            or row.execution_configuration != plan.execution_configuration
            or row.canonical_symbol != plan.dataset.context.canonical_symbol
            or row.quote_currency != plan.dataset.context.canonical_symbol.split("/")[1]):
            raise ValueError("Benchmark data/window/capital/currency/assumptions mismatch")
        pairs = (("total_return", research.performance.total_return, row.total_return),
            ("maximum_drawdown", research.performance.maximum_drawdown, row.maximum_drawdown),
            ("exposure_percentage", research.performance.exposure_percentage, row.exposure_percentage),
            ("turnover", research.performance.turnover, row.turnover),
            ("total_cost", research.incurred_costs.total, row.costs.total))
        expected = tuple((name, None if left is None or right is None else difference(left,right)) for name,left,right in pairs)
        if dict(comparison.differences) != dict(expected):
            raise ValueError("Benchmark difference projection mismatch")
        label = f"BENCHMARK:{comparison.strategy_result_identity}:{row.kind.value}"
        sources[label] = comparison.comparison_identity
        sources[label+":RESULT"] = row.result_identity
        observations[label] = {"differences": tuple(sorted(expected)), "ending_equity": row.ending_equity,
            "return": row.total_return, "drawdown": row.maximum_drawdown, "costs": asdict(row.costs)}
    source_ids = tuple(sorted(sources.items()))
    evidence_id = identity("strategy-lab.bound-validation-evidence/v1", {"dataset": primary.plan.dataset_identity, "strategy": definition.configuration_identity, "sources": source_ids, "observations": observations})
    return source_ids, evidence_id
