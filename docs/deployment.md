# Eventual Vercel deployment
Link two Vercel projects to this same source repository.

## Frontend
Root Directory: frontend. Framework: Vite. Install: npm ci. Build: npm run build. Output: dist.
frontend/vercel.json configures the framework, build and SPA fallback.
The current static foundation needs no API credentials.

## Backend
Root Directory: backend. Framework: FastAPI.
index.py exports app; pyproject.toml explicitly selects index:app.
Production dependencies come from requirements.txt.
Set TRADERAIL_DATABASE_URL to a hosted PostgreSQL URL using postgresql+psycopg. Apply the provider's SSL requirements and use a pooled endpoint where available.
Set TRADERAIL_CORS_ORIGINS to a JSON array of exact frontend origins, e.g. ["https://your-traderail.vercel.app"]. No wildcard origins.
SQLAlchemy NullPool avoids retaining local connections between invocations.
Run Alembic manually from a trusted environment before future schema-dependent releases. Never run migrations during requests or application startup.

Deployment and hosted PostgreSQL connectivity have not been tested.

References:
https://vercel.com/docs/frameworks/frontend/vite
https://vercel.com/docs/frameworks/backend/fastapi
Google authentication deployment: see authentication.md for backend-only credentials, Secure cookie settings, exact origins and the frontend VITE_API_BASE_URL setting. The previous no-credentials statement applies to the static foundation; Google sign-in now requires these backend environment variables.
