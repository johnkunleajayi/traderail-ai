"""Deterministic experiment identity separated from informational creation time."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import re
from app.market_intelligence.config import FEATURE_CONFIG
from app.market_regimes.config import REGIME_CONFIG
from .canonical import canonical_json, fingerprint, utc_timestamp
from .dataset import ResearchDataset, DATASET_FORMAT

CONFIGURATION_FORMAT = "strategy-lab.configuration/v1"
EXPERIMENT_FORMAT = "strategy-lab.experiment/v1"


@dataclass(frozen=True)
class ConfigurationSnapshot:
    canonical_payload: str
    configuration_fingerprint: str


def snapshot_configuration(configuration: Mapping) -> ConfigurationSnapshot:
    if not isinstance(configuration, Mapping):
        raise ValueError("Strategy configuration must be a mapping")
    payload = canonical_json(configuration)
    return ConfigurationSnapshot(payload, fingerprint(CONFIGURATION_FORMAT, payload))


@dataclass(frozen=True)
class ExperimentIdentity:
    experiment_id: str
    dataset_fingerprint: str
    market_intelligence_version: str
    regime_engine_version: str
    strategy_family: str | None
    strategy_version: str | None
    strategy_configuration: ConfigurationSnapshot


@dataclass(frozen=True)
class ExperimentSpecification:
    identity: ExperimentIdentity
    created_at: datetime

    @property
    def experiment_id(self) -> str:
        return self.identity.experiment_id


@dataclass(frozen=True)
class ReproducibilityManifest:
    identity: ExperimentIdentity
    dataset_format: str
    experiment_format: str
    configuration_format: str
    canonical_symbol: str
    provider: str
    timeframe: str
    warmup_start: str
    evaluation_start: str
    evaluation_end: str
    candle_count: int
    warmup_candle_count: int
    evaluation_candle_count: int
    created_at: str  # Informational only; excluded from identity.


def create_experiment(dataset: ResearchDataset, *, strategy_configuration: Mapping,
                      created_at: datetime, strategy_family: str | None = None,
                      strategy_version: str | None = None,
                      market_intelligence_version: str = FEATURE_CONFIG.engine_version,
                      regime_engine_version: str = REGIME_CONFIG.engine_version) -> ExperimentSpecification:
    utc_timestamp(created_at)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", dataset.dataset_fingerprint):
        raise ValueError("Invalid dataset fingerprint")
    for value in (market_intelligence_version, regime_engine_version):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Engine versions must be non-empty")
    if (strategy_family is None) != (strategy_version is None):
        raise ValueError("Strategy family and version must both be specified or both placeholders")
    for value in (strategy_family, strategy_version):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Invalid strategy placeholder metadata")
    snapshot = snapshot_configuration(strategy_configuration)
    payload = canonical_json({"dataset_fingerprint": dataset.dataset_fingerprint,
        "market_intelligence_version": market_intelligence_version,
        "regime_engine_version": regime_engine_version, "strategy_family": strategy_family,
        "strategy_version": strategy_version,
        "configuration_fingerprint": snapshot.configuration_fingerprint})
    identity = ExperimentIdentity(fingerprint(EXPERIMENT_FORMAT, payload), dataset.dataset_fingerprint,
        market_intelligence_version, regime_engine_version, strategy_family, strategy_version, snapshot)
    return ExperimentSpecification(identity, created_at)


def create_manifest(dataset: ResearchDataset, experiment: ExperimentSpecification) -> ReproducibilityManifest:
    if experiment.identity.dataset_fingerprint != dataset.dataset_fingerprint:
        raise ValueError("Experiment does not describe this dataset")
    return ReproducibilityManifest(experiment.identity, DATASET_FORMAT, EXPERIMENT_FORMAT,
        CONFIGURATION_FORMAT, dataset.context.canonical_symbol, dataset.context.provider,
        dataset.context.timeframe, utc_timestamp(dataset.warmup_start),
        utc_timestamp(dataset.evaluation_start), utc_timestamp(dataset.evaluation_end),
        dataset.candle_count, len(dataset.warmup_candles), len(dataset.evaluation_candles),
        utc_timestamp(experiment.created_at))
