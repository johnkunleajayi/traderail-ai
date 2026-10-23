"""SQLAlchemy statement-level ledger double. NOT PostgreSQL lock/trigger proof.

Runs the real repositories and economic service with rollback-capable in-memory
rows. Physical constraints, concurrent connections and crash recovery remain
explicitly deferred to PostgreSQL integration verification.
"""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from sqlalchemy.sql.dml import Insert,Update
from sqlalchemy.sql.elements import BooleanClauseList,BindParameter
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError
from app.db.base import Base


class Result:
    def __init__(self,rows): self.rows=rows;self.mapping=False;self.scalar=False
    def mappings(self): self.mapping=True;return self
    def scalars(self): self.scalar=True;return self
    def _value(self,row):
        return next(iter(row.values())) if self.scalar else deepcopy(row) if self.mapping else tuple(row.values())
    def all(self): return [self._value(row) for row in self.rows]
    def __iter__(self): return iter(self.all())
    def first(self): return self._value(self.rows[0]) if self.rows else None
    def one_or_none(self):
        assert len(self.rows)<=1
        return self.first()
    def one(self):
        assert len(self.rows)==1
        return self.first()
    def scalar_one_or_none(self):
        self.scalar=True;return self.one_or_none()
    def scalar_one(self): self.scalar=True;return self.one()


def matches(clause,row):
    if isinstance(clause,BooleanClauseList):
        checks=[matches(c,row) for c in clause.clauses]
        return any(checks) if clause.operator.__name__=='or_' else all(checks)
    left=row[clause.left.name];right=clause.right.value
    name=clause.operator.__name__
    return {'eq':lambda:left==right,'ne':lambda:left!=right,'lt':lambda:left<right,
        'le':lambda:left<=right,'gt':lambda:left>right,'ge':lambda:left>=right,
        'in_op':lambda:left in right}[name]()


class Ledger:
    dialect=SimpleNamespace(name='postgresql')
    def __init__(self):
        self.rows={name:[] for name in Base.metadata.tables};self.commits=0;self.rollbacks=0
        self.connection=SimpleNamespace(dbapi_connection=SimpleNamespace(autocommit=False))
        self.fail_table=None;self.statements=[]
    def in_transaction(self):return True
    def get_isolation_level(self):return 'READ COMMITTED'
    @contextmanager
    def begin_nested(self):
        saved=deepcopy(self.rows)
        try:yield self
        except BaseException:
            self.rows=saved;raise
    @contextmanager
    def begin(self):
        try:
            with self.begin_nested():yield self
        except BaseException:self.rollbacks+=1;raise
        else:self.commits+=1
    def execute(self,statement):
        self.statements.append(statement)
        table=statement.table if isinstance(statement,(Insert,Update)) else statement.get_final_froms()[0]
        rows=self.rows.setdefault(table.name,[])
        if isinstance(statement,Insert):
            if table.name==self.fail_table:raise IntegrityError('isolated failure',{},Exception())
            row={getattr(key,'name',key):value.value for key,value in statement._values.items()}
            groups=[[col.name for col in table.primary_key]]+[[col.name for col in constraint.columns]
                for constraint in table.constraints if isinstance(constraint,UniqueConstraint)]
            if any(all(row.get(k)==old.get(k) for k in group) for old in rows for group in groups):
                if getattr(statement,'_post_values_clause',None) is not None:return Result([])
                raise IntegrityError('isolated uniqueness conflict',{},Exception())
            rows.append(deepcopy(row))
            returning=list(statement._returning)
            return Result([{col.name:row[col.name] for col in returning}] if returning else [])
        found=[r for r in rows if all(matches(clause,r) for clause in statement._where_criteria)]
        if isinstance(statement,Update):
            for r in found:r.update({getattr(k,'name',k):v.value for k,v in statement._values.items()})
            return Result([])
        for order in reversed(statement._order_by_clauses):
            col=getattr(order,'element',order)
            found.sort(key=lambda r:r[col.name],reverse=getattr(getattr(order,'modifier',None),'__name__','')=='desc_op')
        if statement._limit_clause is not None:found=found[:statement._limit_clause.value]
        columns=list(statement.selected_columns)
        return Result([{col.name:r[col.name] for col in columns} for r in found])
