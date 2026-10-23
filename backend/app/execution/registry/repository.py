"""PostgreSQL transaction participant; never approves, consumes or executes."""
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from app.portfolio.models import PaperPortfolio
from app.risk.canonical import require,timestamp,values
from app.strategy_lab.canonical import canonical_json
from app.execution.paper import PaperCommand
from app.execution.paper_fill import calculate_paper_fill
from app.risk.trade_risk.assumptions import TradeRiskCosts
from .contracts import AuthorizationPackage
from .codec import encode,decode
from .models import authorizations,commands


class RegistryConflict(ValueError):pass
class RegistryUnavailable(RuntimeError):pass
class AuthorizationUnavailable(LookupError):pass


def _transaction(c):
    if (c.dialect.name!='postgresql' or not c.in_transaction()
            or c.connection.dbapi_connection.autocommit is not False
            or c.get_isolation_level()!='READ COMMITTED'):
        raise ValueError('Explicit non-autocommit READ COMMITTED PostgreSQL transaction required')


def _auth_row(package):
    a=package.authorization;d=a.decision;i=a.intent
    return dict(registry_identity=package.registry_identity,authorization_identity=a.authorization_identity,
        intent_identity=i.intent_identity,decision_identity=d.decision_identity,user_id=i.scope.user_id,
        portfolio_id=i.scope.portfolio_id,instrument_id=i.instrument.instrument_id,environment='PAPER',outcome='APPROVED',
        quantity=a.quantity,created_at=d.evaluated_at,evaluated_at=d.evaluated_at,
        approval_expires_at=d.approval_expires_at,payload=encode(package),canonical_payload=canonical_json(values(package)))


def _read_package(row):
    try:
        package=decode(row['payload']);require(package,AuthorizationPackage,'stored authorization')
        expected=_auth_row(package)
        if any(row[k]!=v for k,v in expected.items()):raise ValueError('Stored metadata mismatch')
        return package
    except Exception:
        raise RegistryUnavailable('Stored authorization cannot be verified') from None


def load_authorization(c,registry_identity,*,user_id):
    require(user_id,UUID,'authenticated user')
    row=c.execute(select(authorizations).where(authorizations.c.registry_identity==registry_identity,
        authorizations.c.user_id==user_id)).mappings().one_or_none()
    if row is None:raise AuthorizationUnavailable('Authorization is unavailable')
    # Reading an expired immutable fact does not renew permission to execute it.
    return _read_package(row)


def persist_authorization(c,package,*,user_id,as_of):
    _transaction(c);require(package,AuthorizationPackage,'authorization package');require(user_id,UUID,'authenticated user')
    at=timestamp(as_of);row=_auth_row(package)
    if row['user_id']!=user_id:raise AuthorizationUnavailable('Authorization is unavailable')
    try:
        with c.begin_nested():
            owner=c.execute(select(PaperPortfolio.id).where(PaperPortfolio.id==row['portfolio_id'],
                PaperPortfolio.user_id==user_id,PaperPortfolio.environment=='PAPER').with_for_update(read=True)).scalar_one_or_none()
            if owner is None:raise AuthorizationUnavailable('Portfolio is unavailable')
            existing=c.execute(select(authorizations).where(authorizations.c.decision_identity==row['decision_identity'])).mappings().one_or_none()
            if existing is not None:
                if existing['canonical_payload']!=row['canonical_payload']:raise RegistryConflict('Authorization payload conflict')
                return _read_package(existing)
            package.authorization.validate_at(at)
            c.execute(insert(authorizations).values(**row).on_conflict_do_nothing())
            stored=c.execute(select(authorizations).where(authorizations.c.registry_identity==package.registry_identity)).mappings().one_or_none()
            if stored is None or stored['canonical_payload']!=row['canonical_payload']:
                raise RegistryConflict('Authorization identity conflict')
            return _read_package(stored)
    except DBAPIError:
        raise RegistryUnavailable('Authorization persistence failed') from None


def _command_row(package,command):
    r=command.request;i=r.authorization.intent
    return dict(request_identity=r.request_identity,command_identity=command.command_identity,
        action_identity=r.action_identity,registry_identity=package.registry_identity,
        user_id=i.scope.user_id,portfolio_id=i.scope.portfolio_id,executed_at=command.executed_at,
        payload=encode(command),canonical_payload=canonical_json(values(command)))


def load_command(c,request_identity,*,user_id):
    require(user_id,UUID,'authenticated user')
    row=c.execute(select(commands).where(commands.c.request_identity==request_identity,commands.c.user_id==user_id)).mappings().one_or_none()
    if row is None:raise AuthorizationUnavailable('Command is unavailable')
    package=load_authorization(c,row['registry_identity'],user_id=user_id)
    try:
        command=decode(row['payload']);require(command,PaperCommand,'stored command')
        if canonical_json(values(command.request.authorization))!=canonical_json(values(package.authorization)):
            raise ValueError('Command authorization differs')
        if any(row[k]!=v for k,v in _command_row(package,command).items()):raise ValueError('Command metadata differs')
        return command
    except Exception:
        raise RegistryUnavailable('Stored command cannot be verified') from None


def persist_command(c,registry_identity,command,*,user_id,as_of):
    _transaction(c);require(command,PaperCommand,'command');at=timestamp(as_of)
    package=load_authorization(c,registry_identity,user_id=user_id)
    if canonical_json(values(command.request.authorization))!=canonical_json(values(package.authorization)):
        raise RegistryConflict('Command authorization mismatch')
    row=_command_row(package,command)
    try:
        with c.begin_nested():
            existing=c.execute(select(commands.c.canonical_payload).where(commands.c.request_identity==row['request_identity'])).scalar_one_or_none()
            if existing is not None:
                if existing!=row['canonical_payload']:raise RegistryConflict('Command payload conflict')
                return load_command(c,row['request_identity'],user_id=user_id)
            if at!=command.executed_at:raise ValueError('Initial command binding requires its explicit execution instant')
            package.authorization.validate_at(at)
            calculate_paper_fill(command.request,command.market,command.assumptions,command.request.action,
                executed_at=at,evaluated_at=at)
            cm=command.assumptions.cost_model
            costs=TradeRiskCosts(cm.trading_fee_rate,cm.trading_fee_rate,cm.spread_rate,cm.spread_rate,cm.slippage_rate,cm.slippage_rate)
            if (costs.costs_identity!=package.evidence_references.costs_identity
                    or command.market.source_identity!=package.evidence_references.market_source_identity
                    or command.market.reference_price!=package.inputs.sizing_assumptions.entry_reference_price):
                raise RegistryConflict('Command execution evidence differs from approved references')
            c.execute(insert(commands).values(**row).on_conflict_do_nothing())
            saved=load_command(c,row['request_identity'],user_id=user_id)
            if canonical_json(values(saved))!=row['canonical_payload']:raise RegistryConflict('Command identity conflict')
            return saved
    except DBAPIError:
        raise RegistryUnavailable('Command persistence failed') from None
