# TradeRail
Systematic trading. Engineered for control.

Foundation only: React/TypeScript/Vite/Tailwind frontend with reusable primitives, design tokens and persistent themes; modular FastAPI backend with PostgreSQL configuration, SQLAlchemy, Alembic, CORS and application liveness.

Research, Paper Trading, Risk Control and Live Execution are planned concepts. Google sign-in and the PostgreSQL User/session foundation are implemented. Exchange integration, market data, strategies, trading and billing remain deferred.

## Location
C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail

## Frontend (PowerShell)
~~~powershell
Set-Location 'C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail\frontend'
npm install
npm run dev
~~~
Open http://127.0.0.1:5173. Production check: npm run build.
Theme follows the system on first visit and remembers explicit choices when storage is available.

## Backend (PowerShell)
~~~powershell
Set-Location 'C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail\backend'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
~~~
Edit .env with your PostgreSQL password, then:
~~~powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~
GET http://127.0.0.1:8000/api/health returns {"status":"healthy"}.
This is liveness only, not a database check. API docs: /docs.
Tests: .\.venv\Scripts\python.exe -m pytest -q.

## PostgreSQL
Requires PostgreSQL installed/running and command-line tools on PATH. For a new database:
~~~powershell
psql -U postgres -d postgres -c "CREATE ROLE traderail LOGIN;"
psql -U postgres -d postgres -c "\password traderail"
psql -U postgres -d postgres -c "CREATE DATABASE traderail OWNER traderail;"
~~~
The second command prompts for a password. Put the same password, URL-encoded where necessary, in backend/.env:
TRADERAIL_DATABASE_URL=postgresql+psycopg://traderail:YOUR_URL_ENCODED_PASSWORD@localhost:5432/traderail

~~~powershell
Set-Location 'C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail\backend'
.\.venv\Scripts\python.exe -m alembic upgrade head
~~~
Migration 0001_users_and_sessions creates users and revocable auth sessions. Migrations are explicit operational steps.

## Deployment and architecture
See docs/deployment.md and docs/foundation.md. Frontend and backend use separate Vercel deployment projects from this same repository. The backend is one modular monolith. No deployment has been performed. Credentials remain server-side.
## Google sign-in
See docs/authentication.md for exact environment variables, Google Cloud setup, local URLs, session security and verification limits.
