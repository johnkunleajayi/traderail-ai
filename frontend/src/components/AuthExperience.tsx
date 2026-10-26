import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from 'react'
import { LogOut, ShieldCheck, PanelsTopLeft, ChartNoAxesCombined, FlaskConical, ChevronRight } from 'lucide-react'
import { logout, readSession, type SessionUser } from '../auth/api'
import BrandLogo from './BrandLogo'
import ThemeToggle from './ThemeToggle'
import PaperPortfolioSummary from './PaperPortfolioSummary'
import MarketsSection from './MarketsSection'
const MarketHistory = lazy(() => import('./MarketHistory'))
const StrategyLab = lazy(() => import('../strategy-lab/StrategyLab'))
const MandateWorkspace = lazy(() => import('../mandates/MandateWorkspace'))
import '../workspace.css'

export default function AuthExperience({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [signingOut, setSigningOut] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [view, setView] = useState<'workspace' | 'lab' | 'mandates'>('mandates')
  const [labOpened, setLabOpened] = useState(false)
  const [mandatesOpened, setMandatesOpened] = useState(true)
  const [marketsOpened, setMarketsOpened] = useState(false)
  const clearUser = useCallback(() => { setUser(null); setView('workspace'); setLabOpened(false); setMandatesOpened(false); setMarketsOpened(false) }, [])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    readSession(controller.signal).then(value => {
      setUser(value)
      setError('')
      const url = new URL(window.location.href)
      if (url.searchParams.has('auth')) {
        url.searchParams.delete('auth')
        window.history.replaceState({}, '', url.pathname + url.search + url.hash)
      }
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Sign-in check failed.')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [attempt])
  async function signOut() {
    setSigningOut(true)
    setError('')
    try { await logout(); clearUser() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Sign-out failed.') }
    finally { setSigningOut(false) }
  }
  if (loading) return <div className="auth-loading" role="status"><BrandLogo/><p>Checking your session…</p></div>
  if (!user) return <>{error && <div className="auth-notice" role="status">{error}<button onClick={() => setAttempt(value => value + 1)}>Retry</button></div>}{children}</>
  const title = view === 'mandates' ? 'Mandates' : view === 'lab' ? 'Strategy Lab' : 'Market Intelligence'
  return <div className="authenticated-shell enterprise-workspace">
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <aside className="ws-rail">
      <a href="/" className="brand-link ws-brand" aria-label="TradeRail home"><BrandLogo/></a>
      <div className="ws-rail-label">Paper workspace</div>
      <nav className="ws-primary-nav" aria-label="Workspace navigation">
        <button aria-current={view === 'mandates' ? 'page' : undefined} onClick={() => { setMandatesOpened(true); setView('mandates') }}><PanelsTopLeft size={18}/><span>Trading Mandates</span></button>
        <button aria-current={view === 'workspace' ? 'page' : undefined} onClick={() => { setMarketsOpened(true); setView('workspace') }}><ChartNoAxesCombined size={18}/><span>Market Intelligence</span></button>
        <button aria-current={view === 'lab' ? 'page' : undefined} onClick={() => { setLabOpened(true); setView('lab') }}><FlaskConical size={18}/><span>Strategy Lab</span></button>
      </nav>
      <div className="ws-rail-boundary"><ShieldCheck size={19}/><strong>Control before exposure.</strong><p>Strategy proposes.<br/>Risk authorizes.<br/>Execution executes.</p><span>Paper only. No Live orders.</span></div>
      <details className="ws-account"><summary><span className="ws-avatar" aria-hidden="true">{user.name.slice(0,1).toUpperCase()}</span><span><strong>{user.name}</strong><small>Google account</small></span></summary><p>{user.email}</p><p>Signed in with Google</p></details>
    </aside>
    <div className="ws-main-frame"><header className="ws-topbar"><div className="ws-breadcrumb"><span>Workspace</span><ChevronRight size={13}/><strong>{title}</strong></div><div className="ws-topbar-actions"><span className="ws-paper-tag"><FlaskConical size={13}/> Paper</span><ThemeToggle/><button className="ws-logout" onClick={signOut} disabled={signingOut} aria-label={signingOut ? 'Signing out…' : 'Log out'}><LogOut size={17}/><span>{signingOut ? 'Signing out…' : 'Log out'}</span></button></div></header>
      <main className="ws-main" id="workspace">
        {error && <div className="ws-inline-warning" role="alert">{error}</div>}
        {mandatesOpened && <div hidden={view !== 'mandates'}><Suspense fallback={<div className="ws-shell-loading" role="status">Loading your mandate workspace…</div>}><MandateWorkspace onSessionExpired={clearUser}/></Suspense></div>}
        {marketsOpened && <div hidden={view !== 'workspace'}><header className="ws-page-heading"><div><p className="ws-eyebrow">Observe before acting</p><h1>Market Intelligence</h1><p>Canonical markets, completed history and analytical context.</p></div><span className="ws-state">Read & research</span></header><div className="ws-market-context"><PaperPortfolioSummary onSessionExpired={clearUser}/><MarketsSection/></div><Suspense fallback={<div className="ws-shell-loading" role="status">Loading Market History…</div>}><MarketHistory/></Suspense></div>}
        {labOpened && <div hidden={view !== 'lab'}><header className="ws-page-heading"><div><p className="ws-eyebrow">Evidence before eligibility</p><h1>Strategy Lab</h1><p>Inspect experiments and validation. Research is not trading permission.</p></div><span className="ws-state">Research environment</span></header><Suspense fallback={<div className="ws-shell-loading" role="status">Loading Strategy Lab…</div>}><StrategyLab onSessionExpired={clearUser}/></Suspense></div>}
      </main><footer className="ws-app-footer"><span>TradeRail · Systematic trading. Engineered for control.</span><span>Paper simulation · No Live trading</span></footer>
    </div>
  </div>
}
