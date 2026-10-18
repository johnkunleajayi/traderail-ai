"""Explicit Adaptive Trend perturbations, never search or profitability selection."""
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from pathlib import Path
import hashlib
import subprocess
import sys
import pytest
from app.strategies.adaptive_trend import AdaptiveTrendConfiguration, AdaptiveTrendStrategy
from app.strategies.adaptive_trend.config import DEFAULT_CONFIGURATION
from app.strategy_lab.sensitivity import SensitivityPlan, evaluate_sensitivity, changed_fields
from app.strategy_lab.walk_forward import WalkForwardPlan
from app.strategy_lab.out_of_sample import OutOfSamplePlan
from test_out_of_sample_validation import plan as paired_plan, AS_OF, D
from test_walk_forward_validation import flat_plan, join_folds
from test_integrated_9c_audit import frozen_dataset

BASE = DEFAULT_CONFIGURATION
# Explicit test hypotheses near defaults; production code never generates these.
PERTURBATIONS = (
    ("entry_min_ema_spread", D(".0016")),
    ("entry_fast_slope", D(".00021")),
    ("entry_slow_slope", D(".00011")),
    ("long_rsi_min", D("56")),
    ("long_rsi_max", D("69")),
    ("short_rsi_min", D("31")),
    ("short_rsi_max", D("44")),
    ("entry_min_normalized_histogram", D(".00011")),
    ("max_normalized_atr", D(".019")),
    ("max_realized_volatility", D(".019")),
    ("exit_min_ema_spread", D(".00051")),
    ("exit_fast_slope", D(".00011")),
    ("exit_slow_slope", D(".000051")),
    ("exit_long_rsi", D("49")),
    ("exit_short_rsi", D("51")),
    ("exit_min_normalized_histogram", D(".000051")),
    ("exit_confirmation_dimensions", 3),
)


def sensitivity(validation=None, configs=None, ofat=True):
    configs = configs if configs is not None else (BASE, replace(BASE, entry_min_ema_spread=D(".0016")))
    return SensitivityPlan(validation or paired_plan(), BASE, configs, ofat)


@pytest.mark.parametrize("field,value", PERTURBATIONS)
def test_explicit_representative_ofat_perturbations(field, value):
    variant = replace(BASE, **{field:value})
    result = evaluate_sensitivity(sensitivity(configs=(variant,BASE)), as_of=AS_OF)
    assert result.baseline.configuration == BASE
    assert result.baseline.changed_fields == ()
    assert len(result.variants) == 1 and result.variants[0].changed_fields == (field,)
    alternative = result.variants[0]
    assert alternative.configuration == variant
    assert alternative.configuration_identity != result.baseline.configuration_identity
    assert alternative.validation.validation_identity != result.baseline.validation.validation_identity
    assert result.baseline.validation.plan.dataset is alternative.validation.plan.dataset
    assert result.baseline.validation.plan.split == alternative.validation.plan.split
    assert result.baseline.validation.plan.execution_configuration == alternative.validation.plan.execution_configuration
    assert result.baseline.validation.plan.accounting_configuration == alternative.validation.plan.accounting_configuration
    assert result.baseline.validation.plan.quantity == alternative.validation.plan.quantity
    assert result.baseline.validation.plan.initial_capital == alternative.validation.plan.initial_capital
    metrics = {row.metric:row for row in alternative.differences[0].metrics}
    for name in ("net_return","maximum_drawdown","trade_count","expectancy","exposure_percentage"):
        assert name in metrics
    assert alternative.differences[0].return_sign_changed is False
    assert result.baseline.configuration == DEFAULT_CONFIGURATION


def test_walk_forward_sensitivity_preserves_exact_fold_data_and_assumptions():
    result = evaluate_sensitivity(sensitivity(flat_plan()), as_of=AS_OF+timedelta(days=200))
    assert isinstance(result.baseline.validation.plan, WalkForwardPlan)
    for variant in result.variants:
        assert len(variant.oos_metrics) == len(variant.differences) == 2
        assert variant.validation.plan.dataset is result.baseline.validation.plan.dataset
        for baseline, alternative in zip(result.baseline.validation.plan.folds, variant.validation.plan.folds):
            assert baseline.dataset is alternative.dataset
            assert baseline.warmup == alternative.warmup and baseline.split == alternative.split
            assert baseline.execution_configuration == alternative.execution_configuration
            assert baseline.accounting_configuration == alternative.accounting_configuration
            assert baseline.quantity == alternative.quantity and baseline.initial_capital == alternative.initial_capital
            assert baseline.initial_state == alternative.initial_state


def test_input_order_cannot_change_outputs_or_identities():
    alternatives = (BASE, replace(BASE, entry_min_ema_spread=D(".0016")), replace(BASE, long_rsi_min=D(56)))
    forward = sensitivity(configs=alternatives)
    reverse = sensitivity(configs=tuple(reversed(alternatives)))
    assert forward.plan_identity == reverse.plan_identity
    left = evaluate_sensitivity(forward, as_of=AS_OF)
    right = evaluate_sensitivity(reverse, as_of=AS_OF+timedelta(days=1))
    assert left == right and left.sensitivity_identity == right.sensitivity_identity
    ids = tuple(row.configuration_identity for row in left.variants)
    assert ids == tuple(sorted(ids))


def test_equivalent_decimal_representations_preserve_identity_and_meaningful_fields():
    equivalent = replace(BASE, entry_min_ema_spread=D(".0015000"))
    first = SensitivityPlan(paired_plan(), BASE, (BASE, replace(BASE, entry_min_ema_spread=D(".0016"))))
    second = SensitivityPlan(paired_plan(), equivalent,
        (equivalent, replace(equivalent, entry_min_ema_spread=D(".0016000"))))
    assert changed_fields(BASE,equivalent) == ()
    assert first.plan_identity == second.plan_identity
    assert evaluate_sensitivity(first,as_of=AS_OF) == evaluate_sensitivity(second,as_of=AS_OF)


@pytest.mark.parametrize("configs", [(), (replace(BASE, entry_min_ema_spread=D(".0016")),)])
def test_baseline_must_be_explicitly_included(configs):
    with pytest.raises(ValueError, match="baseline|Baseline"): sensitivity(configs=configs)


@pytest.mark.parametrize("duplicate", [BASE, replace(BASE, entry_min_ema_spread=D(".0015000"))])
def test_duplicate_equivalent_configurations_rejected(duplicate):
    with pytest.raises(ValueError, match="Duplicate"): sensitivity(configs=(BASE,duplicate))


def test_ofat_rejects_multiple_changed_fields_and_explicit_multifactor_mode_accepts():
    variant = replace(BASE, entry_min_ema_spread=D(".0016"), long_rsi_min=D(56))
    with pytest.raises(ValueError, match="exactly one"): sensitivity(configs=(BASE,variant))
    result = evaluate_sensitivity(sensitivity(configs=(BASE,variant),ofat=False),as_of=AS_OF)
    assert result.variants[0].changed_fields == ("entry_min_ema_spread","long_rsi_min")
    assert result.baseline.configuration == BASE


def test_frozen_validation_baseline_must_match_exact_definition():
    other = replace(BASE, entry_min_ema_spread=D(".0016"))
    with pytest.raises(ValueError, match="match"): SensitivityPlan(paired_plan(),other,(other,))
    with pytest.raises(ValueError): SensitivityPlan(paired_plan(),BASE,(BASE,),ofat="yes")
    with pytest.raises(ValueError): SensitivityPlan(paired_plan(),BASE,(BASE,{}))


def test_meaningful_variant_and_mode_changes_change_identity():
    original = sensitivity()
    changed = sensitivity(configs=(BASE,replace(BASE,entry_min_ema_spread=D(".0017"))))
    assert original.plan_identity != changed.plan_identity
    assert evaluate_sensitivity(original,as_of=AS_OF).sensitivity_identity != evaluate_sensitivity(changed,as_of=AS_OF).sensitivity_identity
    assert sensitivity(ofat=False).plan_identity != original.plan_identity


def test_baseline_only_run_still_retains_explicit_baseline_without_selection():
    result = evaluate_sensitivity(sensitivity(configs=(BASE,)),as_of=AS_OF)
    assert result.baseline.configuration == BASE and result.variants == ()
    assert all(row.variant_minus_baseline == 0 or row.variant_minus_baseline is None for row in result.baseline.differences[0].metrics)


def test_return_sign_change_is_descriptive_and_does_not_select_a_variant():
    validation = join_folds((frozen_dataset("long_loss"),))
    # Explicit adverse gating scenario, not a search: suppress entries to challenge reporting.
    blocked = replace(BASE,max_normalized_atr=D(".000001"))
    result = evaluate_sensitivity(sensitivity(validation,configs=(blocked,BASE)),as_of=AS_OF+timedelta(days=200))
    baseline,variant = result.baseline,result.variants[0]
    assert baseline.oos_metrics[0].net_return == D("-.0003")
    assert variant.oos_metrics[0].net_return == 0
    assert variant.differences[0].return_sign_changed is True
    differences = {row.metric:row for row in variant.differences[0].metrics}
    assert differences["net_return"].variant_minus_baseline == D(".0003")
    assert differences["net_return"].baseline == D("-.0003") and differences["net_return"].variant == 0
    assert {item.name for item in fields(type(differences["net_return"]))} == {
        "metric","baseline","variant","variant_minus_baseline"}
    assert differences["trade_count"].variant_minus_baseline == -1
    assert baseline.configuration == BASE
    names = {item.name for item in fields(type(result))}
    assert not names.intersection({"best","winner","selected_configuration","rankings","promoted","verdict"})


def test_future_data_cannot_change_earlier_fold_for_any_variant():
    first = evaluate_sensitivity(sensitivity(flat_plan()),as_of=AS_OF+timedelta(days=200))
    second = evaluate_sensitivity(sensitivity(flat_plan(changes={135:D(101)})),as_of=AS_OF+timedelta(days=200))
    assert first.baseline.validation.folds[0] == second.baseline.validation.folds[0]
    assert first.variants[0].validation.folds[0] == second.variants[0].validation.folds[0]
    assert first.sensitivity_identity != second.sensitivity_identity


def test_defaults_remain_byte_for_byte_unchanged_and_results_cannot_mutate_configuration():
    path = Path(__file__).resolve().parents[1]/"app/strategies/adaptive_trend/config.py"
    before = hashlib.sha256(path.read_bytes()).digest()
    result = evaluate_sensitivity(sensitivity(),as_of=AS_OF)
    with pytest.raises(FrozenInstanceError): result.baseline.configuration.long_rsi_min = D(60)
    with pytest.raises(FrozenInstanceError): result.variants[0].configuration = BASE
    with pytest.raises(FrozenInstanceError): result.plan.configurations = ()
    with pytest.raises(TypeError): result.baseline.validation.plan.strategy_definition.parameters["long_rsi_min"] = D(60)
    assert hashlib.sha256(path.read_bytes()).digest() == before
    assert DEFAULT_CONFIGURATION == AdaptiveTrendConfiguration() == BASE


def test_repeated_run_is_identical_and_keeps_all_explicit_alternatives():
    p = sensitivity(configs=(BASE,replace(BASE,long_rsi_min=D(56)),replace(BASE,entry_fast_slope=D(".00021"))))
    first,second = evaluate_sensitivity(p,as_of=AS_OF),evaluate_sensitivity(p,as_of=AS_OF)
    assert first == second and first.sensitivity_identity == second.sensitivity_identity
    assert len(first.variants) == 2
    assert {row.configuration_identity for row in first.variants} == {
        AdaptiveTrendStrategy(configuration).definition.configuration_identity for configuration in p.configurations
        if configuration != BASE}


def test_no_external_provider_database_api_or_lifecycle_dependencies():
    script = """import sys
from app.strategy_lab.sensitivity import SensitivityPlan,evaluate_sensitivity
for prefix in ('sqlalchemy','psycopg','httpx','fastapi','app.db','app.market_data','app.strategy_lab.lifecycle'):
    assert not any(name==prefix or name.startswith(prefix+'.') for name in sys.modules),prefix
"""
    result = subprocess.run([sys.executable,"-c",script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode == 0,result.stderr


def test_forged_underlying_validation_identity_rejected():
    from copy import copy
    p = copy(paired_plan())
    object.__setattr__(p,"plan_identity","forged")
    with pytest.raises(ValueError,match="identity"): sensitivity(p)
