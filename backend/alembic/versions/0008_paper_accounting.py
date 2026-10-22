"""Atomic PAPER LONG positions/accounting, preserving initial capital and 0007 facts."""
from alembic import op
import sqlalchemy as sa
revision='0008_paper_accounting'
down_revision='0007_execution_artifacts'
branch_labels=None
depends_on=None
TABLE_SQL = (
    '''
CREATE TABLE paper_positions (
	position_id UUID NOT NULL, 
	position_identity VARCHAR(71) NOT NULL, 
	user_id UUID NOT NULL, 
	portfolio_id UUID NOT NULL, 
	instrument_id UUID NOT NULL, 
	canonical_symbol VARCHAR(100) NOT NULL, 
	environment VARCHAR(5) NOT NULL, 
	direction VARCHAR(5) NOT NULL, 
	currency VARCHAR(16) NOT NULL, 
	strategy_family VARCHAR(64) NOT NULL, 
	strategy_version VARCHAR(64) NOT NULL, 
	strategy_configuration_identity VARCHAR(71) NOT NULL, 
	origin_intent_identity VARCHAR(71) NOT NULL, 
	origin_decision_identity VARCHAR(71) NOT NULL, 
	opening_request_identity VARCHAR(71) NOT NULL, 
	opening_order_identity VARCHAR(71) NOT NULL, 
	opening_fill_identity VARCHAR(71) NOT NULL, 
	opening_quantity NUMERIC NOT NULL, 
	quantity NUMERIC NOT NULL, 
	average_entry_price NUMERIC NOT NULL, 
	entry_reference_notional NUMERIC NOT NULL, 
	entry_cost_basis NUMERIC NOT NULL, 
	entry_fee NUMERIC NOT NULL, 
	entry_spread_cost NUMERIC NOT NULL, 
	entry_slippage_cost NUMERIC NOT NULL, 
	status VARCHAR(6) NOT NULL, 
	opened_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	closed_at TIMESTAMP WITH TIME ZONE, 
	closing_request_identity VARCHAR(71), 
	PRIMARY KEY (position_id), 
	CONSTRAINT ck_paper_position_scope CHECK (environment='PAPER' AND direction='LONG' AND currency='USD'), 
	CONSTRAINT ck_paper_position_lifecycle CHECK ((status='OPEN' AND quantity>0 AND quantity=opening_quantity AND closed_at IS NULL AND closing_request_identity IS NULL) OR (status='CLOSED' AND quantity=0 AND closed_at IS NOT NULL AND closed_at>=opened_at AND closing_request_identity IS NOT NULL)), 
	CONSTRAINT ck_paper_pos_opening_quantity CHECK (opening_quantity NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND opening_quantity > 0), 
	CONSTRAINT ck_paper_pos_quantity CHECK (quantity NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND quantity >= 0), 
	CONSTRAINT ck_paper_pos_average_entry_price CHECK (average_entry_price NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND average_entry_price > 0), 
	CONSTRAINT ck_paper_pos_entry_reference_notional CHECK (entry_reference_notional NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND entry_reference_notional > 0), 
	CONSTRAINT ck_paper_pos_entry_cost_basis CHECK (entry_cost_basis NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND entry_cost_basis > 0), 
	CONSTRAINT ck_paper_pos_entry_fee CHECK (entry_fee NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND entry_fee >= 0), 
	CONSTRAINT ck_paper_pos_entry_spread_cost CHECK (entry_spread_cost NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND entry_spread_cost >= 0), 
	CONSTRAINT ck_paper_pos_entry_slippage_cost CHECK (entry_slippage_cost NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND entry_slippage_cost >= 0), 
	UNIQUE (position_identity), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	FOREIGN KEY(portfolio_id) REFERENCES paper_portfolios (id) ON DELETE RESTRICT, 
	FOREIGN KEY(instrument_id) REFERENCES instruments (id) ON DELETE RESTRICT, 
	UNIQUE (opening_request_identity), 
	UNIQUE (closing_request_identity), 
	CONSTRAINT fk_paper_pos_opening_request_identity FOREIGN KEY(opening_request_identity) REFERENCES execution_requests (request_identity) ON DELETE RESTRICT, 
	CONSTRAINT fk_paper_pos_opening_order_identity FOREIGN KEY(opening_order_identity) REFERENCES execution_orders (order_identity) ON DELETE RESTRICT, 
	CONSTRAINT fk_paper_pos_opening_fill_identity FOREIGN KEY(opening_fill_identity) REFERENCES execution_fills (fill_identity) ON DELETE RESTRICT, 
	CONSTRAINT fk_paper_pos_closing_request_identity FOREIGN KEY(closing_request_identity) REFERENCES execution_requests (request_identity) ON DELETE RESTRICT
)

''',
    '''
CREATE TABLE paper_accounting_entries (
	request_identity VARCHAR(71) NOT NULL, 
	result_identity VARCHAR(71) NOT NULL, 
	command_identity VARCHAR(71) NOT NULL, 
	before_state_identity VARCHAR(71) NOT NULL, 
	position_id UUID NOT NULL, 
	portfolio_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	order_identity VARCHAR(71) NOT NULL, 
	fill_identity VARCHAR(71) NOT NULL, 
	decision_identity VARCHAR(71) NOT NULL, 
	intent_identity VARCHAR(71) NOT NULL, 
	action VARCHAR(11) NOT NULL, 
	environment VARCHAR(5) NOT NULL, 
	currency VARCHAR(16) NOT NULL, 
	quantity NUMERIC NOT NULL, 
	cash_before NUMERIC NOT NULL, 
	cash_after NUMERIC NOT NULL, 
	gross_notional NUMERIC NOT NULL, 
	fee NUMERIC NOT NULL, 
	entry_cost_basis NUMERIC, 
	entry_fee_attributable NUMERIC, 
	gross_realized_pnl NUMERIC, 
	net_realized_pnl NUMERIC, 
	executed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	evidence JSONB NOT NULL, 
	canonical_payload TEXT NOT NULL, 
	PRIMARY KEY (request_identity), 
	CONSTRAINT ck_paper_accounting_scope CHECK (environment='PAPER' AND currency='USD' AND action IN ('ENTER_LONG','EXIT_LONG')), 
	CONSTRAINT ck_paper_accounting_realized CHECK ((action='ENTER_LONG' AND entry_cost_basis IS NULL AND entry_fee_attributable IS NULL AND gross_realized_pnl IS NULL AND net_realized_pnl IS NULL) OR (action='EXIT_LONG' AND entry_cost_basis IS NOT NULL AND entry_fee_attributable IS NOT NULL AND gross_realized_pnl IS NOT NULL AND net_realized_pnl IS NOT NULL)), 
	CONSTRAINT ck_paper_acct_quantity CHECK (quantity NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND quantity > 0), 
	CONSTRAINT ck_paper_acct_cash_before CHECK (cash_before NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND cash_before >= 0), 
	CONSTRAINT ck_paper_acct_cash_after CHECK (cash_after NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND cash_after >= 0), 
	CONSTRAINT ck_paper_acct_gross_notional CHECK (gross_notional NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND gross_notional > 0), 
	CONSTRAINT ck_paper_acct_fee CHECK (fee NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND fee >= 0), 
	CONSTRAINT ck_paper_acct_entry_cost_basis CHECK (entry_cost_basis NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND entry_cost_basis >= 0), 
	CONSTRAINT ck_paper_acct_entry_fee_attributable CHECK (entry_fee_attributable NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND entry_fee_attributable >= 0), 
	CONSTRAINT ck_paper_acct_gross_realized_pnl CHECK (gross_realized_pnl NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)), 
	CONSTRAINT ck_paper_acct_net_realized_pnl CHECK (net_realized_pnl NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)), 
	UNIQUE (result_identity), 
	FOREIGN KEY(position_id) REFERENCES paper_positions (position_id) ON DELETE RESTRICT, 
	FOREIGN KEY(portfolio_id) REFERENCES paper_portfolios (id) ON DELETE RESTRICT, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_paper_accounting_request_identity FOREIGN KEY(request_identity) REFERENCES execution_requests (request_identity) ON DELETE RESTRICT, 
	CONSTRAINT fk_paper_accounting_order_identity FOREIGN KEY(order_identity) REFERENCES execution_orders (order_identity) ON DELETE RESTRICT, 
	CONSTRAINT fk_paper_accounting_fill_identity FOREIGN KEY(fill_identity) REFERENCES execution_fills (fill_identity) ON DELETE RESTRICT
)

''',
    "CREATE UNIQUE INDEX uq_paper_position_open_instrument ON paper_positions (portfolio_id, instrument_id) WHERE status = 'OPEN'",
)
OLD_SIDE="(direction='LONG' AND side='BUY' AND action='ENTER_LONG') OR (direction='SHORT' AND side='SELL' AND action='ENTER_SHORT')"
NEW_SIDE="(direction='LONG' AND side='BUY' AND action='ENTER_LONG') OR (direction='LONG' AND side='SELL' AND action='EXIT_LONG') OR (direction='SHORT' AND side='SELL' AND action='ENTER_SHORT')"
POSITION_GUARD="CREATE FUNCTION traderail_paper_position_guard() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n IF TG_OP='DELETE' THEN RAISE EXCEPTION 'paper position audit cannot be deleted' USING ERRCODE='23514'; END IF;\n IF (to_jsonb(NEW)-ARRAY['quantity','status','closed_at','closing_request_identity']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['quantity','status','closed_at','closing_request_identity']) THEN\n  RAISE EXCEPTION 'immutable paper position origin' USING ERRCODE='23514'; END IF;\n IF OLD.status<>'OPEN' OR NEW.status<>'CLOSED' OR NEW.quantity<>0 THEN\n  RAISE EXCEPTION 'only full LONG close is enabled' USING ERRCODE='23514'; END IF;\n RETURN NEW;\nEND; $$"


def upgrade():
    op.alter_column('paper_portfolios','cash_balance',existing_type=sa.Numeric(20,2),type_=sa.Numeric(asdecimal=True),existing_nullable=False)
    op.create_check_constraint('ck_paper_cash_finite','paper_portfolios',"cash_balance NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)")
    op.drop_constraint('ck_exec_request_side','execution_requests',type_='check')
    op.create_check_constraint('ck_exec_request_side','execution_requests',NEW_SIDE)
    for sql in TABLE_SQL:op.execute(sql)
    op.execute(POSITION_GUARD)
    op.execute('CREATE TRIGGER paper_position_guard BEFORE UPDATE OR DELETE ON paper_positions FOR EACH ROW EXECUTE FUNCTION traderail_paper_position_guard()')
    op.execute('CREATE TRIGGER paper_accounting_immutable BEFORE UPDATE OR DELETE ON paper_accounting_entries FOR EACH ROW EXECUTE FUNCTION traderail_execution_immutable()')


def downgrade():
    # Never round economic balances or discard close authorization facts to fit an older schema.
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM paper_positions) OR EXISTS(SELECT 1 FROM paper_accounting_entries) OR EXISTS(SELECT 1 FROM paper_portfolios WHERE cash_balance<>round(cash_balance,2)) OR EXISTS(SELECT 1 FROM execution_requests WHERE action='EXIT_LONG') THEN RAISE EXCEPTION 'incompatible Paper accounting data; explicit operational reconciliation required'; END IF; END $$")
    op.drop_table('paper_accounting_entries');op.drop_table('paper_positions')
    op.execute('DROP FUNCTION traderail_paper_position_guard()')
    op.drop_constraint('ck_exec_request_side','execution_requests',type_='check')
    op.create_check_constraint('ck_exec_request_side','execution_requests',OLD_SIDE)
    op.drop_constraint('ck_paper_cash_finite','paper_portfolios',type_='check')
    op.alter_column('paper_portfolios','cash_balance',existing_type=sa.Numeric(),type_=sa.Numeric(20,2),existing_nullable=False)
