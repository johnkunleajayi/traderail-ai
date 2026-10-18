"""Pure deterministic paired IS/OOS validation; no optimization or promotion."""
from .plan import OutOfSamplePlan, VALIDATION_ENGINE_VERSION
from .engine import OutOfSampleResult, validate_out_of_sample
from .comparison import MetricComparison, compare_performance

__all__ = ["OutOfSamplePlan", "OutOfSampleResult", "validate_out_of_sample",
           "MetricComparison", "compare_performance", "VALIDATION_ENGINE_VERSION"]
