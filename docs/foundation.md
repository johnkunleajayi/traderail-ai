# Foundation boundaries
app/main.py composes FastAPI. app/api/health.py owns the liveness route. app/core/config.py owns validated settings. app/db/base.py supplies SQLAlchemy metadata; app/db/session.py supplies lazy connections and scoped sessions. Alembic uses the same metadata and settings. No domain tables exist. Tests do not require PostgreSQL.

src/styles.css exposes the requested palette as Tailwind v4 @theme tokens and semantic light/dark variables. Reusable Card, Badge, LinkButton and SectionLabel live in src/components/ui.tsx. ThemeToggle persists preferences when storage is available.

The frontend uses constrained grids, restrained borders and gradients, system typography, keyboard focus, a skip link and reduced-motion support. Future concepts are marked planned; there are no simulated trading statistics.

Deferred: Google authentication, Quidax, market data, strategies, risk engine, paper trading, live trading and billing.