# TradeRail working boundaries
Work only in C:\Users\JOHN-KUNLE\OneDrive\Desktop\traderail. Do not create another copy.
This is a foundation-only modular monolith. Do not implement authentication, exchange integrations, market data, strategies, risk engines, trading, or billing without explicit authorization.
Use the shared CSS/Tailwind tokens and reusable UI primitives. Preserve responsive behavior, keyboard focus, reduced-motion handling, and persistent themes. Label future features honestly.
Keep credentials server-side and out of Git. Keep backend modules small. Database migrations are explicit operational steps, never application startup actions.
Verify frontend with npm run build from frontend. Verify backend with .\.venv\Scripts\python.exe -m pytest from backend.
Do not commit, push, or deploy without explicit authorization.