"""Fee-inclusive commitment oracles at the canonical Risk sizing boundary."""
from copy import deepcopy
from dataclasses import fields,replace
from decimal import Decimal
import pytest
from app.risk.trade_risk import calculate_trade_size, evaluate_trade_risk, TradeRiskStatus
from app.risk.trade_risk.arithmetic import floor_ratio
from app.risk.paper_source.policies import COSTS
from app.risk.canonical import values
from app.strategy_lab.canonical import canonical_json,fingerprint
from app.strategy_lab.numeric import exact_sum,exact_product
from app.execution.registry.codec import encode,decode
import test_trade_risk_sizing as f

D=Decimal

def hypotheses(cap=D('500'),**changes):
    intent=f.intent(invalidation_price=D('98'))
    assumptions=f.assumptions(costs=COSTS,constraints=f.constraints(max_entry_cash_commitment=cap),**changes)
    return intent,f.config(),f.snapshot(),assumptions


def test_all_in_500_oracle_uses_existing_risk_sizing_and_real_cash():
    i,c,s,a=hypotheses()
    result=calculate_trade_size(i,c,s,a)
    # 100 + 0.05 half-spread + 0.05 slippage = 100.1; fee = 0.1001.
    assert result.loss_model.modeled_adverse_entry_price==D('100.1')
    assert result.loss_model.entry_fee==D('.1001')
    assert result.constrained_max_quantity==floor_ratio(D('500'),D('100.2001'))
    assert result.maximum_projection.required_cash<=D('500')
    assert exact_sum((result.maximum_projection.notional,exact_product(result.constrained_max_quantity,D('.1001'))))==result.maximum_projection.required_cash
    assert result.available_cash_limit==D('500')
    assert s.available_cash==s.equity==D('10000')
    assert evaluate_trade_risk(i,c,s,a,f.AT).status is TradeRiskStatus.TRADE_RISK_PASS


def test_500_notional_plus_fee_cannot_pass():
    i,c,s,a=hypotheses()
    nominal_quantity=floor_ratio(D('500'),D('100.1'))
    rejected=evaluate_trade_risk(i,c,s,replace(a,proposed_quantity=nominal_quantity),f.AT)
    assert rejected.status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert rejected.evaluated_projection.required_cash>D('500')
    assert f.RiskReason.CASH_LIMIT_EXCEEDED in f.codes(rejected)
    smaller=evaluate_trade_risk(i,c,s,a,f.AT)
    assert smaller.evaluated_projection.quantity<nominal_quantity


def test_entry_cash_does_not_reserve_exit_fees():
    i,c,s,a=hypotheses()
    low=calculate_trade_size(i,c,s,a)
    high=calculate_trade_size(i,c,s,replace(a,costs=replace(COSTS,exit_fee_rate=D('.01'))))
    assert low.constrained_max_quantity==high.constrained_max_quantity
    assert low.maximum_projection.required_cash==high.maximum_projection.required_cash
    assert high.maximum_projection.modeled_loss>low.maximum_projection.modeled_loss


@pytest.mark.parametrize('change',[{'available_cash':D('200')},{'equity':D('200')}])
def test_cash_and_existing_limits_can_be_stricter_than_mandate(change):
    i,c,s,a=hypotheses()
    result=calculate_trade_size(i,c,replace(s,**change),a)
    assert result.maximum_projection.required_cash<=D('200')
    assert result.maximum_projection.modeled_loss<=result.permitted_monetary_risk


@pytest.mark.parametrize('bad',[D('-1'),D('NaN'),D('Infinity'),500.0,'500'])
def test_invalid_commitment_is_rejected(bad):
    with pytest.raises(ValueError):f.constraints(max_entry_cash_commitment=bad)


def test_zero_cap_fails_closed_and_decimal_normalizes():
    i,c,s,a=hypotheses(D('0'))
    assert evaluate_trade_risk(i,c,s,a,f.AT).status is TradeRiskStatus.TRADE_RISK_REJECTED
    assert f.constraints(max_entry_cash_commitment=D('500')).constraints_identity==f.constraints(max_entry_cash_commitment=D('500.00')).constraints_identity
    assert f.constraints(max_entry_cash_commitment=D('501')).constraints_identity!=f.constraints(max_entry_cash_commitment=D('500')).constraints_identity


def test_non_mandate_legacy_identity_and_codec_are_unchanged():
    constraints=f.constraints()
    original_fields={field.name:values(getattr(constraints,field.name)) for field in fields(constraints) if field.init and field.name!='max_entry_cash_commitment'}
    expected=fingerprint('risk.trade-size-constraints/v1',canonical_json(original_fields))
    assert expected==constraints.constraints_identity
    assert 'max_entry_cash_commitment' not in values(constraints)
    assert 'max_entry_cash_commitment' not in encode(constraints)[2]
    assert decode(encode(constraints))==constraints
    i,c,s,a=hypotheses(None)
    assert calculate_trade_size(i,c,s,a).maximum_projection.required_cash>D('500')
    from test_authorization_registry import sample
    package,_=sample()
    assert decode(encode(package))==package


def test_capped_codec_reconstructs_losslessly_and_cannot_strip_cap():
    constraints=f.constraints(max_entry_cash_commitment=D('500.00'))
    encoded=encode(constraints)
    assert decode(encoded)==constraints
    tampered=deepcopy(encoded);tampered[2].pop('max_entry_cash_commitment')
    with pytest.raises(ValueError):decode(tampered)
