# Paper Portfolio foundation

Each authenticated user has one system-created PAPER portfolio with USD 10000.00 starting virtual capital and USD 10000.00 initial cash. This is simulated money only and cannot be withdrawn. No trades, positions, holdings, market data, execution, P&L or LIVE accounts are implemented.

## Persistence
The paper_portfolios table has a PostgreSQL UUID key and a unique user_id foreign key to users with cascade deletion. Money uses Numeric(20,2)/Decimal, with timezone-aware created_at/updated_at. Database checks enforce PAPER, USD, fixed starting capital, nonnegative cash and active/disabled status.

Migration 0002_paper_portfolios follows 0001_users_and_sessions and backfills all existing users without modifying their user profiles or sessions.

## Creation
Google callback initializes the portfolio in the same transaction as user/session creation. GET /api/portfolio also safely initializes a missing portfolio for an already authenticated user.

Both paths share an INSERT ... ON CONFLICT (user_id) DO NOTHING followed by a user-scoped SELECT. There is no balance-update clause. Uniqueness is enforced by PostgreSQL, so concurrent initialization cannot create duplicates. Subsequent logins and requests preserve existing cash and status. Future LIVE accounts must use a separate domain, not this PAPER-only table.

## API
GET /api/portfolio requires the existing authenticated session cookie. Anonymous requests return 401. The successful response contains only:
~~~json
{
  "environment": "PAPER",
  "base_currency": "USD",
  "starting_balance": "10000.00",
  "cash_balance": "10000.00",
  "status": "active"
}
~~~
Money is serialized as fixed two-place decimal strings. The frontend formats those strings directly without converting balances to binary floats. Internal IDs/timestamps are omitted. Successful responses use Cache-Control: no-store.

## Interface
Only the authenticated placeholder is replaced. The public landing page and brand assets are unchanged. Loading/error states do not invent fallback balances. The summary labels the environment PAPER and the amount Virtual USD, and explicitly says that the money is simulated and non-withdrawable.

## Verification
Unit tests use mocked SQLAlchemy boundaries, including real Google OIDC cryptography in the existing auth suite. Separate real-Neon verification uses synthetic users and balance changes inside a transaction that is rolled back, and checks defaults, cents, uniqueness, PAPER-only enforcement and existing-user backfill.

After restarting the backend/frontend, manually sign in, confirm Paper Portfolio / $10,000.00 / Virtual USD / PAPER for a new untouched account, refresh, sign out and sign back in. The same portfolio and existing balance should remain. Confirm logout still returns to the public landing page and inspect both themes on mobile/desktop. No funding, reset or trading action is included.
## Implementation results
- Complete backend suite: 47 passed, with the two existing Starlette/Authlib httpx deprecation warnings.
- pip check: passed.
- Frontend production build: passed.
- Configured Neon database: migration applied; current revision 0002_paper_portfolios (head).
- Real PostgreSQL rollback-only checks passed for defaults, Decimal values, timestamps, balance preservation, uniqueness, PAPER-only enforcement and existing-user backfill.
- Google callback regression test now also asserts portfolio initialization for the correct user.
- Secret scan passed; public landing, theme component, favicon and all four approved brand assets match the approved baseline.
- Browser rendering checked using an ignored mock-user/database fixture, including light/dark mode and narrow/mobile/desktop layout. Real Google browser sign-in after restarting remains a manual check.
- No dependencies added, and no files staged, committed or pushed.
