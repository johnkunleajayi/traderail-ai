import type { ExecutionAssumptions } from '../types'
import type { Timeframe } from '../../history/api'
export interface Boundaries { warmup_start:string; in_sample_start:string; out_of_sample_start:string; end:string }
export interface ValidationRequest {
  canonical_symbol:string; provider:'BINANCE'; timeframe:Timeframe; requested_candle_count:number
  primary:Boundaries; walk_forward_folds:Boundaries[]; initial_capital:string; quantity:string
  execution:ExecutionAssumptions; strategy_family:'ADAPTIVE_TREND'; strategy_version:'1.0.0'
}
export type Scalar=string|number|boolean|null
export type Wire=Scalar|Wire[]|{[key:string]:Wire}
export type WireObject={ [key:string]:Wire }
export const verdicts=['INSUFFICIENT_EVIDENCE','FAILED_VALIDATION','VALIDATION_CANDIDATE'] as const
export type Verdict=typeof verdicts[number]
export const moneyMetrics=['total_return','gross_return','net_return','maximum_drawdown','win_rate','loss_rate','profit_factor','average_win','average_loss','expectancy','exposure_percentage','turnover','total_fees','total_spread_cost','total_slippage_cost'] as const
export type Metrics=Record<typeof moneyMetrics[number],string|null>&{trade_count:number}
export interface Assumptions extends ExecutionAssumptions {timing:string;cost_model_identity:string;execution_configuration_identity:string}
export interface Metadata {
  validation_identity:string;report_identity:string;evidence_identity:string;api_version:string;strategy_family:string;strategy_version:string;strategy_configuration_identity:string
  dataset_identity:string;canonical_symbol:string;provider:string;timeframe:Timeframe;candle_count:number;primary_boundaries:Boundaries
  initial_capital:string;quantity:string;baseline_execution:Assumptions;policy_identity:string;policy_version:string;policy_configuration:WireObject
  verdict:Verdict;source_evidence_identities:Record<string,string>
}
export interface Criterion {code:string;category:string;observed:Scalar;required:Scalar;status:'SUFFICIENT'|'INSUFFICIENT'|'PASS'|'FAIL'|'NOT_APPLICABLE';reason_code:string;explanation:string}
export interface Fold {index:number;boundaries:Boundaries;fold_identity:string;validation_identity:string;backtest_identity:string;metrics:Metrics}
export interface ProbeDifference {fold_index:number;return_sign_changed:boolean|null;metrics:{metric:string;baseline:string|number|null;variant:string|number|null;variant_minus_baseline:string|number|null}[]}
export interface Probe {configuration_identity:string;validation_identity:string;changed_fields:string[];changed_parameters:Record<string,string>;metrics:Metrics;differences:ProbeDifference[]}
export interface Benchmark {kind:'CASH'|'BUY_AND_HOLD';dataset_identity:string;window_identity:string;canonical_symbol:string;quote_currency:string;initial_capital:string;ending_equity:string;total_return:string|null;maximum_drawdown:string|null;exposure_percentage:string|null;turnover:string|null;fees:string;spread_cost:string;slippage_cost:string;comparison_identity:string;result_identity:string;strategy_minus_benchmark:Record<string,string|null>}
export interface Stress {code:string;scenario_identity:string;assumptions:Assumptions;validation_identity:string|null;result_identity:string|null;metrics:Metrics|null;differences:{fold_index:number;metric_changes:[string,string|number|null][];total_cost_change:string;return_sign_changed:boolean|null}[]|null;execution_failure:string|null;failure_source_identity:string|null}
export interface ValidationResult {
  validation:Metadata;primary_oos:{validation_identity:string;backtest_identity:string;metrics:Metrics}
  walk_forward:{validation_identity:string;active_folds:number;summary:WireObject;folds:Fold[]}
  sensitivity:Probe[];sensitivity_baseline_configuration_identity:string;sensitivity_identity:string
  benchmarks:Benchmark[];stress:Stress[];stress_evidence_identity:string;criteria:Criterion[]
  verdict_explanation:{insufficiency_reasons:string[];failure_reasons:string[];passed_criteria:string[];benchmark_context:Benchmark[]}
}
