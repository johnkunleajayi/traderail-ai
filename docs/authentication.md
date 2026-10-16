# Google authentication and PostgreSQL User foundation

All source remains in C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail.
No password system, exchange connection or trading implementation is included.

## Local environment
Copy backend/.env.example to backend/.env. Never commit .env. Use:
- TRADERAIL_DATABASE_URL=postgresql+psycopg://traderail:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:5432/traderail
- TRADERAIL_GOOGLE_CLIENT_ID: your Web application OAuth client ID
- TRADERAIL_GOOGLE_CLIENT_SECRET: your backend-only Google client secret
- TRADERAIL_SESSION_SECRET: a stable random secret of at least 32 characters
- TRADERAIL_GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/auth/google/callback
- TRADERAIL_FRONTEND_URL=http://127.0.0.1:5173
- TRADERAIL_CORS_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173"]
- TRADERAIL_SESSION_COOKIE_SECURE=false
- TRADERAIL_SESSION_COOKIE_SAMESITE=lax
- TRADERAIL_SESSION_MAX_AGE=604800

Generate the session secret locally using:
~~~powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
~~~
Place the generated value only in the ignored backend/.env or deployment secrets.
Leave all of client ID, client secret and session secret blank to disable Google auth. Partial credential configuration fails validation. Health and anonymous /me still work with auth disabled.

The optional frontend VITE_API_BASE_URL is a public backend origin, never a secret. Leave blank locally so Vite proxies /api to http://127.0.0.1:8000.

## Google Cloud Console
Create/select a project. Configure Google Auth Platform branding, support contact and audience. If in testing, add your own Google account as a test user. Create an OAuth client of type Web application.
Register this exact authorized redirect URI:
http://127.0.0.1:8000/api/auth/google/callback

This server-side flow does not need a browser Google SDK or JavaScript origin registration. It requests only openid, email and profile. Do not request exchange or other Google API scopes.

## Run
Requires the configured PostgreSQL database and role to exist. No SQLite fallback:
~~~powershell
Set-Location 'C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail\backend'
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
~~~
In another terminal:
~~~powershell
Set-Location 'C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail\frontend'
npm run dev
~~~

Use http://127.0.0.1:5173 consistently, not a mixture of localhost and 127.0.0.1.
Local flow: frontend /api/auth/google (Vite proxy) -> backend -> Google -> http://127.0.0.1:8000/api/auth/google/callback -> http://127.0.0.1:5173/?auth=success.
Direct backend initiation: http://127.0.0.1:8000/api/auth/google.
Session endpoint: http://127.0.0.1:8000/api/auth/me.
Logout: POST http://127.0.0.1:8000/api/auth/logout with Origin http://127.0.0.1:5173 and the session cookie. The frontend sends these automatically.

## Design
Authlib performs authorization-code OIDC with state, nonce, S256 PKCE, and signed ID-token verification against Google's discovery/JWKS. Tokens are never placed in localStorage or persisted to the database/cookies. A 10-minute signed HttpOnly OAuth cookie holds temporary state only; it is cleared after callback.

TradeRail sessions use random opaque tokens in a host-only HttpOnly cookie, with SHA-256 token hashes and expiry in PostgreSQL auth_sessions. Seven-day fixed expiry by default. Login rotates the current session; logout deletes its database row, preventing replay. Expired rows are cleaned opportunistically on login. Database lookups enforce active user status. Logout requires the exact configured frontend Origin. Authentication responses are no-store and no-referrer.

Users have UUID primary keys, unique Google subject and normalized email, optional country/avatar, USD base currency and active status defaults, and timezone-aware timestamps. No country is inferred from Google or the user's environment. Google refreshes profile fields without changing country/base currency. Conflicting emails are rejected rather than auto-linked.

Migration 0001_users_and_sessions creates users plus the small authentication-session table. Migrations run explicitly, never at application startup.

## Production
Set frontend, callback and API origins to your actual HTTPS domains, and register the production callback with Google. Set TRADERAIL_SESSION_COOKIE_SECURE=true.
Use exact CORS origins and a stable session secret across instances. Prefer app/API custom domains on the same site, using SameSite=lax. Separate cross-site domains require SameSite=none plus Secure, and browsers may still block third-party cookies. No cookie Domain is configured.
Set frontend VITE_API_BASE_URL to the public backend HTTPS origin before building. Never place Google client secret or session secret in frontend settings.

## Verification boundary
Tests mock Google's HTTP endpoints and the database session boundary; they exercise actual Authlib ID-token cryptography, state, nonce, audience, issuer, expiry, and PKCE. They do not prove live Google credentials or a real database round trip. Perform the online migration and a real browser sign-in/logout after configuring PostgreSQL and Google credentials.
## Results of this implementation
- Frontend production build passed.
- Complete backend suite: 38 passed, two dependency deprecation warnings (Starlette/Authlib legacy httpx integration).
- pip check passed.
- Online Alembic upgrade attempted: PostgreSQL OperationalError; local port 5432 refused connections. No database fallback was used.
- PostgreSQL migration SQL generation passed offline. Actual table creation still requires a reachable PostgreSQL instance.
- Live anonymous /api/auth/me returned 401; health returned 200; unconfigured Google login returned 503.
- Landing sections and original stylesheet prefix were compared with baseline commit 8c7bab9062c2f18cb074e06239da920a9b5cd5a6. Only the auth wrapper/action and appended auth styles differ.
- All four approved brand PNG hashes match the baseline.
- Browser layout checks covered 320, 390, 768 and 1440px. The shell used an isolated ignored mock-user fixture; no real Google login was claimed.
- Normal port 8000 was occupied during verification. QA used backend 46080/frontend 45173, and an isolated mock-user shell on backend 46081/frontend 45174. These are not the configured Google callback URLs.
- No new commits or pushes. Real Google credentials, consent-screen setup and browser login/logout remain manual verification steps.
