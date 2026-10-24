export type RiskLevel = 'CONSERVATIVE' | 'BALANCED' | 'GROWTH'
export type TradingStyle = 'SHORT_TERM' | 'MEDIUM_TERM' | 'LONGER_TERM'
export type MandateStatus = 'DRAFT' | 'ACTIVE' | 'PAUSED' | 'RETIRED'
export type MandateAction = 'activate' | 'pause' | 'retire'
export interface MandateIntent {
  environment: 'PAPER'
  capital_allocation: string
  allocation_currency: 'USDT'
  instrument: 'BTC/USDT'
  risk_level: RiskLevel
  trading_style: TradingStyle
  direction_capability: 'LONG_ONLY'
}
export interface TradingMandate extends MandateIntent {
  id: string
  user_id: string
  status: MandateStatus
  created_at: string
  updated_at: string
}
export interface IntentForm { capital: string; risk: RiskLevel; style: TradingStyle }
