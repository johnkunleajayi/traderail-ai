import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Pause, Play } from 'lucide-react'
import { LandingDemoProvider } from './LandingDemo'

/** Landing motion controls only. Never consumes market or strategy state. */
export default function LandingMotion({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null)
  const [paused, setPaused] = useState(false)
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(media.matches)
    update(); media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  useEffect(() => {
    const element = root.current
    if (!element || !('IntersectionObserver' in window)) return
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add('lp-visible'); observer.unobserve(entry.target) } })
    }, { threshold: 0.12 })
    element.querySelectorAll('.lp-reveal').forEach(node => observer.observe(node))
    element.classList.add('lp-motion-ready')
    return () => observer.disconnect()
  }, [])
  return <div className={`landing${paused || reduced ? ' lp-motion-paused' : ''}`} id="top" ref={root}>
    <LandingDemoProvider paused={paused} reduced={reduced} surfaceRef={root}>{children}</LandingDemoProvider>
    {!reduced && <button className="lp-motion-toggle" type="button" aria-pressed={paused} onClick={() => setPaused(value => !value)}>{paused ? <Play size={12}/> : <Pause size={12}/>} {paused ? 'Resume motion' : 'Pause motion'}</button>}
  </div>
}
