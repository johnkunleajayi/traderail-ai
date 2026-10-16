import { useEffect, useState, type ReactNode } from 'react'
import { ArrowRight, LogOut, ShieldCheck, UserRound } from 'lucide-react'
import { logout, readSession, type SessionUser } from '../auth/api'
import BrandLogo from './BrandLogo'
import ThemeToggle from './ThemeToggle'
import { Card, SectionLabel } from './ui'

export default function AuthExperience({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [signingOut, setSigningOut] = useState(false)
  const [attempt, setAttempt] = useState(0)
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
    try { await logout(); setUser(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Sign-out failed.') }
    finally { setSigningOut(false) }
  }
  if (loading) return <div className="auth-loading" role="status"><BrandLogo/><p>Checking your session…</p></div>
  if (!user) return <>{error && <div className="auth-notice" role="status">{error}<button onClick={() => setAttempt(value => value + 1)}>Retry</button></div>}{children}</>
  return <div className="authenticated-shell">
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <header className="header container"><a href="/" className="brand-link" aria-label="TradeRail home"><BrandLogo/></a><div className="header-end"><ThemeToggle/><button className="button button-secondary" onClick={signOut} disabled={signingOut}><LogOut size={15}/>{signingOut ? 'Signing out…' : 'Log out'}</button></div></header>
    <main className="container workspace" id="workspace">
      <SectionLabel>YOUR TRADERAIL WORKSPACE</SectionLabel>
      <div className="workspace-heading"><div><h1>Welcome, {user.name}.</h1><p>Systematic trading. Engineered for control.</p></div><span className="workspace-badge"><ShieldCheck size={15}/> Signed in with Google</span></div>
      {error && <p className="auth-notice" role="alert">{error}</p>}
      <Card className="profile-card"><div className="user-avatar">{user.avatar_url ? <img src={user.avatar_url} alt="" referrerPolicy="no-referrer" onError={event => { event.currentTarget.style.display = 'none' }}/>: <UserRound size={26}/>}</div><div><h2>{user.name}</h2><p>{user.email}</p></div></Card>
      <Card className="workspace-placeholder"><div><SectionLabel>NEXT BUILD STAGE</SectionLabel><h2>Paper Trading &amp; portfolio</h2><p>Your workspace starts here. Portfolio tools and paper trading are planned for a future release.</p><p className="workspace-boundary">No exchange is connected. No balances, trades or market data are available.</p></div><span className="placeholder-arrow" aria-hidden="true"><ArrowRight size={25}/></span></Card>
    </main>
  </div>
}