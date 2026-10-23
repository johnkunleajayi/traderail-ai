"""Immutable PAPER pre-execution approvals and original command bindings."""
from alembic import op
revision = '0010_paper_authorizations'
down_revision = '0009_paper_position_lifecycle'
branch_labels = None
depends_on = None
TABLE_SQL = ("\nCREATE TABLE paper_authorizations (\n\tregistry_identity VARCHAR(71) NOT NULL, \n\tauthorization_identity VARCHAR(71) NOT NULL, \n\tintent_identity VARCHAR(71) NOT NULL, \n\tdecision_identity VARCHAR(71) NOT NULL, \n\tuser_id UUID NOT NULL, \n\tportfolio_id UUID NOT NULL, \n\tinstrument_id UUID NOT NULL, \n\tenvironment VARCHAR(5) NOT NULL, \n\toutcome VARCHAR(8) NOT NULL, \n\tquantity NUMERIC NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tevaluated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tapproval_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tpayload JSONB NOT NULL, \n\tcanonical_payload TEXT NOT NULL, \n\tPRIMARY KEY (registry_identity), \n\tCONSTRAINT ck_paper_auth_scope CHECK (environment='PAPER' AND outcome='APPROVED'), \n\tCONSTRAINT ck_paper_auth_quantity CHECK (quantity NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AND quantity>0), \n\tCONSTRAINT ck_paper_auth_times CHECK (created_at=evaluated_at AND evaluated_at<approval_expires_at), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_paper_auth_owner FOREIGN KEY(portfolio_id, user_id) REFERENCES paper_portfolios (id, user_id) ON DELETE RESTRICT, \n\tFOREIGN KEY(instrument_id) REFERENCES instruments (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_paper_auth_owner UNIQUE (registry_identity, user_id, portfolio_id), \n\tUNIQUE (authorization_identity), \n\tUNIQUE (decision_identity)\n)\n\n", '\nCREATE TABLE paper_authorized_commands (\n\trequest_identity VARCHAR(71) NOT NULL, \n\tcommand_identity VARCHAR(71) NOT NULL, \n\taction_identity VARCHAR(71) NOT NULL, \n\tregistry_identity VARCHAR(71) NOT NULL, \n\tuser_id UUID NOT NULL, \n\tportfolio_id UUID NOT NULL, \n\texecuted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tpayload JSONB NOT NULL, \n\tcanonical_payload TEXT NOT NULL, \n\tPRIMARY KEY (request_identity), \n\tCONSTRAINT fk_paper_command_auth FOREIGN KEY(registry_identity, user_id, portfolio_id) REFERENCES paper_authorizations (registry_identity, user_id, portfolio_id) ON DELETE RESTRICT, \n\tUNIQUE (command_identity), \n\tUNIQUE (action_identity)\n)\n\n')

def upgrade():
    op.create_unique_constraint('uq_paper_portfolio_owner','paper_portfolios',['id','user_id'])
    for sql in TABLE_SQL:op.execute(sql)
    for table in ('paper_authorizations','paper_authorized_commands'):
        op.execute('CREATE TRIGGER immutable_authorization BEFORE UPDATE OR DELETE ON '+table+' FOR EACH ROW EXECUTE FUNCTION traderail_execution_immutable()')

def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM paper_authorizations) OR EXISTS(SELECT 1 FROM paper_authorized_commands) THEN RAISE EXCEPTION 'persisted authorization history requires explicit operational reconciliation'; END IF; END $$")
    op.drop_table('paper_authorized_commands')
    op.drop_table('paper_authorizations')
    op.drop_constraint('uq_paper_portfolio_owner','paper_portfolios',type_='unique')
