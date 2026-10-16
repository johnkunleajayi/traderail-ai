import { useEffect, useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { readPortfolio, formatVirtualUsd, SessionExpired, type PaperPortfolio } from '../portfolio/api'
import { Card, SectionLabel } from './ui'

export default function PaperPortfolioSummary({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    readPortfolio(controller.signal).then(setPortfolio).catch(reason => {
      if (controller.signal.aborted) return
      if (reason instanceof SessionExpired) { onSessionExpired(); return }
      setError(reason instanceof Error ? reason.message : 'Paper Portfolio is unavailable.')
    })
    return () => controller.abort()
  }, [attempt, onSessionExpired])
  return <Card className="paper-portfolio-card">
    <div className="paper-portfolio-heading"><div><SectionLabel>VIRTUAL CAPITAL</SectionLabel><h2>Paper Portfolio</h2></div><span className="paper-environment"><FlaskConical size={15}/>PAPER</span></div>
    {error ? <div className="paper-portfolio-error" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Retry</button></div>
      : !portfolio ? <p role="status">Loading your Paper Portfolio…</p>
      : <><div className="paper-balance"><span className="paper-balance-label">Cash balance</span><strong>{formatVirtualUsd(portfolio.cash_balance)}</strong><span>Virtual USD</span></div><div className="paper-portfolio-details"><p>Starting virtual capital <strong>{formatVirtualUsd(portfolio.starting_balance)}</strong></p><p>Status <strong>{portfolio.status === 'active' ? 'Active' : 'Disabled'}</strong></p></div></>}
    <p className="paper-money-notice">Simulated money only. This balance is not real money and cannot be withdrawn.</p>
    <p className="paper-boundary">Separate from any future LIVE account. Trading is not yet available.</p>
  </Card>
}