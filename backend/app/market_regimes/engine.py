from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from decimal import DecimalException
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_intelligence.numeric import calculation_context
from app.market_intelligence.types import FeatureRow
from app.market_regimes.classification import Confirmation, classify_evidence
from app.market_regimes.config import REGIME_CONFIG, RegimeConfiguration, candidate_first_index
from app.market_regimes.evidence import measure_evidence
from app.market_regimes.types import Evidence, Regime, RegimeResult
from app.market_regimes.validation import aware, validate_row


def classify_regimes(rows: Sequence[FeatureRow], *, as_of: datetime,
                     config: RegimeConfiguration = REGIME_CONFIG) -> list[RegimeResult]:
    """Classify prefixes only; invalid input quarantines that row and all later rows."""
    if not aware(as_of) or not isinstance(config, RegimeConfiguration):
        raise ValueError("An aware completion boundary and regime configuration are required")
    first = candidate_first_index(config)
    results, context, previous, invalid_prefix = [], None, None, False
    confirmation = Confirmation()
    with calculation_context():
        for index, row in enumerate(rows):
            evidence = Evidence(available_candles=index + 1, candidate_history_required=first + 1)
            try:
                problem = "INVALID_PREFIX" if invalid_prefix else validate_row(row, index, previous, context, as_of)
                if problem:
                    regime = Regime.UNSAFE
                    invalid_prefix = True
                    evidence = replace(evidence, candidate=Regime.UNSAFE, reason_codes=("DATA_UNSAFE", problem))
                elif index < first:
                    regime = Regime.UNKNOWN
                    evidence = replace(evidence, reason_codes=("WARMUP_REQUIRED", "INSUFFICIENT_EVIDENCE"))
                else:
                    measured = measure_evidence(rows, index, config)
                    candidate, measured = classify_evidence(measured, config)
                    regime, evidence = confirmation.apply(candidate, measured, config)
                    evidence = replace(evidence, available_candles=index + 1, candidate_history_required=first + 1)
            except (DecimalException, OverflowError):
                regime, invalid_prefix = Regime.UNSAFE, True
                evidence = replace(evidence, candidate=Regime.UNSAFE, reason_codes=("DATA_UNSAFE", "NUMERIC_RANGE_ERROR"))
            valid_type = isinstance(row, FeatureRow)
            results.append(RegimeResult(
                canonical_symbol=row.canonical_symbol if valid_type and isinstance(row.canonical_symbol, str) else None,
                provider=row.provider if valid_type and isinstance(row.provider, str) else None,
                timeframe=row.timeframe if valid_type and isinstance(row.timeframe, str) else None,
                open_time=row.open_time.astimezone(timezone.utc) if valid_type and aware(row.open_time) else None,
                close_time=row.close_time.astimezone(timezone.utc) if valid_type and aware(row.close_time) else None,
                regime=regime, evidence=evidence, configuration=config, feature_engine_version=FEATURE_CONFIG.engine_version))
            if not invalid_prefix:
                context = (row.canonical_symbol, row.provider, row.timeframe)
                previous = row
    return results
