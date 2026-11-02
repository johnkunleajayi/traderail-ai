"""Feasibility specification only. No Risk service, decision or authorization creation."""
FAITHFUL='FAITHFUL_FROM_AVAILABLE_EVIDENCE'
MODELED='MODELED_ASSUMPTIONS_REQUIRED'
UNAVAILABLE='UNAVAILABLE_OPERATIONAL_EVIDENCE'

CONTROLS=(
    {'control':'allocation_ceiling','classification':MODELED,'required':'Predeclared allocation, denomination and exact modeled entry commitment including fees; historical mandate ownership/allocation changes remain unavailable','source':'app/risk/trade_risk/calculation.py'},
    {'control':'position_sizing','classification':MODELED,'required':'Canonical Risk configuration, exact cash/equity/exposures, invalidation and loss costs; reuse existing sizing with explicit simulated state, never fake authoritative snapshots','source':'app/risk/trade_risk/calculation.py'},
    {'control':'completed_market_candles','classification':FAITHFUL,'required':'Checksummed archive, exact UTC interval/context, contiguous validated OHLCV and causal 100-candle window','source':'app/risk/paper_source/market.py'},
    {'control':'operational_market_freshness','classification':UNAVAILABLE,'required':'Contemporaneous provider retrieval/quality/health evidence; late archive retrieval cannot prove fresh operational availability','source':'app/risk/paper_source/market.py'},
    {'control':'authorization_freshness','classification':MODELED,'required':'Explicit simulated evaluation/execution times and canonical expiry policy can test counterfactual age only; no historical approval is fabricated','source':'app/execution/paper/production.py'},
    {'control':'six_kill_switch_scopes','classification':UNAVAILABLE,'required':'Contemporaneous USER/PORTFOLIO/STRATEGY/MARKET/DATA/SYSTEM states and immutable subject evidence; missing never means inactive','source':'app/risk/paper_switches.py'},
    {'control':'strategy_lifecycle_health','classification':UNAVAILABLE,'required':'Historical activation, lifecycle and health attestations; ACTIVE mandate or historical signal does not grant permission','source':'app/risk/paper_source/service.py'},
    {'control':'portfolio_state_and_drawdown','classification':MODELED,'required':'Explicit initial research capital/genesis, fees, positions, valuations and full entry-to-current history; repeat-cycle mode is not production first-cycle history','source':'app/risk/paper_source/close.py'},
    {'control':'position_lifecycle','classification':MODELED,'required':'Genuine decisions with exact simulated fills/quantity and terminal first-close semantics; existing production provenance is never invented','source':'app/mandate_paper/positions.py'},
    {'control':'fixed_fill_accounting_reconciliation','classification':FAITHFUL,'required':'Canonical Decimal prices/fees/costs and accounting receipts for simulated fills; proves arithmetic, not actual execution or authorization','source':'app/strategy_lab/accounting/ledger.py'},
)


def feasibility():
    return {'version':'historical-risk-feasibility-v1','controls':CONTROLS,
        'mandatory_gates':['TRADE_RISK','PORTFOLIO_RISK','RISK_STATE','SAFETY_GATE'],
        'approval_creation':False,'production_permission':False,
        'future_contract':['Freeze a versioned modeled/observed evidence package with independent source labels',
            'Supply every canonical gate input; missing operational evidence means unavailable or blocked, never assumed PASS',
            'Use the existing Risk engine and sizing, with no threshold changes or alternate authorization registry',
            'Keep hypothetical reports separate from executable approvals and production execution',
            'Require exact quantity, allocation-plus-entry-fee ceiling, costs, state transitions, freshness and reconciliation tests'],
        'status':'SPECIFICATION_ONLY; complete faithful historical Risk replay remains unavailable'}
