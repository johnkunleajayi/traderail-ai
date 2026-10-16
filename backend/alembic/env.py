from alembic import context
from sqlalchemy import create_engine, pool
from app.core.config import get_settings
from app.db.base import Base
from app.market_data.models import MarketCandle  # noqa: F401
from app.venues.models import Venue, VenueInstrumentMapping  # noqa: F401
from app.assets.models import Asset  # noqa: F401
from app.instruments.models import Instrument  # noqa: F401
from app.portfolio.models import PaperPortfolio  # noqa: F401
from app.users.models import User  # noqa: F401
from app.auth.models import AuthSession  # noqa: F401

target_metadata = Base.metadata
url = str(get_settings().database_url)

def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()