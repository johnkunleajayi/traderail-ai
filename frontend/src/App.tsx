import { ArrowRight, ArrowDown, Check, ShieldCheck, LockKeyhole, Workflow, FlaskConical } from 'lucide-react'
import { LinkButton, SectionLabel } from './components/ui'
import ThemeToggle from './components/ThemeToggle'
import AuthExperience from './components/AuthExperience'
import { googleLoginUrl } from './auth/api'
import BrandLogo from './components/BrandLogo'
import LandingMotion from './components/LandingMotion'
import { DemoHero, DemoEngine, DemoHeartbeat, MarketTrace } from './components/LandingDemo'
import './landing.css'

const safeguards = [
  ['Strategy proposes. It cannot execute.', 'A signal is a proposal, never permission to spend.'],
  ['Risk controls the exposure.', 'Deterministic checks set the boundaries before capital is exposed.'],
  ['Authorization comes before execution.', 'Missing, stale or unsafe evidence means no trade.'],
  ['Paper and Live stay separate.', 'Simulated capital never becomes Live capital automatically.'],
]
export default function App() {
  return <AuthExperience><LandingMotion>
    <a className="skip-link" href="#main">Skip to content</a>
    <header className="lp-nav lp-wrap">
      <a href="#top" className="brand-link" aria-label="TradeRail home"><BrandLogo/></a>
      <nav aria-label="Main navigation"><a href="#how-it-works">How it works</a><a href="#risk-first">Built for control</a><a href="#paper-to-live">Paper to Live</a></nav>
      <div className="header-end"><ThemeToggle/><LinkButton href={googleLoginUrl}>Start Paper Trading <ArrowRight size={15}/></LinkButton></div>
    </header>
    <main id="main">
      <section className="lp-hero lp-wrap">
        <div className="lp-hero-copy"><SectionLabel>TRADING INFRASTRUCTURE. HUMAN CONTROL.</SectionLabel>
          <h1>Systematic trading.<br/><span>Engineered<br/>for control.</span></h1>
          <p>Turn your trading mandate into systematic market monitoring, risk-controlled decisions and execution. A connected process. Clear boundaries. You stay in control.</p>
          <div className="lp-actions"><LinkButton href={googleLoginUrl}>Start Paper Trading <ArrowRight size={17}/></LinkButton><a className="lp-text-link" href="#how-it-works">See How TradeRail Works <ArrowDown size={16}/></a></div>
          <span className="lp-small"><FlaskConical size={14}/> Begin with simulated capital. No Live authorization implied.</span>
        </div>
        <div className="lp-hero-scene" data-demo-surface aria-label="Illustrative Paper control plane, not a live session">
          <div className="lp-photo-aura" aria-hidden="true"/>
          <img className="lp-hero-photo" src="/landing/hero-trader-laptop.png" width="1672" height="941" alt="A trader reviewing a laptop at a desk" fetchPriority="high"/>
          <div className="lp-scene-grid" aria-hidden="true"/>
          <div className="lp-scene-caption">HUMAN INTENT. SYSTEMATIC INFRASTRUCTURE.</div>
          <DemoHero/>
          <p className="lp-illustration">Illustrative workflow · no live signal or performance data</p>
        </div>
      </section>
      <section className="lp-trust lp-wrap" aria-label="Capital and permission boundaries">
        <div><LockKeyhole size={22}/><p><strong>Your funds stay at your exchange.</strong><span>For future Live connections: trading permission, not withdrawal permission. Exchange trading is not enabled here.</span></p></div>
        <ul><li>Paper-first validation</li><li>Deterministic Risk controls</li><li>Explicit Live authorization</li></ul>
      </section>
      <section id="how-it-works" className="lp-section lp-wrap lp-reveal">
        <div className="lp-section-head"><div><SectionLabel>01 / ONE CONNECTED PROCESS</SectionLabel><h2>A system behind<br/>every decision.</h2></div><p>Strategy proposes. Risk authorizes. Execution executes. Each stage has a role—and a boundary it cannot cross.</p></div>
        <DemoEngine/>
        <div className="lp-cash"><span className="lp-status-dot"/><strong>Doing nothing is a decision.</strong><p>NO_TRADE / CASH is a valid outcome when the evidence or risk conditions do not support exposure.</p></div>
      </section>
      <section className="lp-analysis lp-reveal" aria-labelledby="analysis-title">
        <img src="/landing/analysis-trader-desk.png" width="1672" height="941" alt="A trader considering market information at an analysis desk" loading="lazy"/>
        <div className="lp-analysis-shade"/>
        <div className="lp-wrap lp-analysis-content"><SectionLabel>MARKET INTELLIGENCE / NOT MARKET NOISE</SectionLabel><h2 id="analysis-title">More context.<br/>Less reaction.</h2><p>Price is an observation. Intelligence gives it context. Strategy decides whether that context calls for action.</p><div className="lp-analysis-measures"><span>BTC / USDT<b>Canonical observations</b></span><span>REGIME<b>Context before conviction</b></span><span>RISK<b>Permission before exposure</b></span></div><small>Conceptual analysis view · no current market readings</small></div>
        <div className="lp-analysis-heartbeat"><DemoHeartbeat/></div>
        <div className="lp-analysis-trace" aria-hidden="true"><MarketTrace/></div>
      </section>
      <section className="lp-mandate-section"><div className="lp-wrap lp-split lp-section lp-reveal">
        <div><SectionLabel>02 / YOUR MANDATE. A SYSTEMATIC PROCESS.</SectionLabel><h2>Set the direction.<br/>Keep the boundaries.</h2><p className="lp-body">The future mandate experience puts your objectives first. You define the capital, market and risk profile. TradeRail handles the technical configuration within explicit controls.</p><p className="lp-small">Product concept · not an available configuration form</p></div>
        <div className="lp-mandate"><div className="lp-mandate-heading"><Workflow size={20}/><span>EXAMPLE TRADING MANDATE</span></div><dl>{[['Capital','2,000 USDT'],['Market','BTC/USDT'],['Risk','Balanced'],['Style','Medium-term'],['Environment','Paper']].map(([label,value])=><div key={label} tabIndex={0}><dt>{label}</dt><dd>{value}<Check size={13} aria-hidden="true"/></dd></div>)}</dl><p><ShieldCheck size={16}/> A mandate never bypasses Risk authorization.</p><div className="lp-mandate-progress" aria-hidden="true"><span/>Mandate → Strategy → Risk</div></div>
      </div></section>
      <section className="lp-human lp-wrap lp-section lp-reveal">
        <div className="lp-human-visual"><img src="/landing/hero-trader-mobile.png" width="1672" height="941" alt="A person checking their phone away from the trading desk" loading="lazy"/><div className="lp-human-note"><Workflow size={19}/><span>Your mandate.<strong>A connected process behind it.</strong></span></div></div>
        <div className="lp-human-copy"><SectionLabel>LESS MACHINERY. MORE CLARITY.</SectionLabel><h2>Your intent.<br/>The system's discipline.</h2><p className="lp-body">Define what you want. TradeRail handles the systematic machinery within your boundaries—without asking you to watch every chart.</p><a href="#how-it-works" className="lp-text-link">See the process behind the decision <ArrowRight size={16}/></a></div>
      </section>
      <section id="risk-first" className="lp-wrap lp-section lp-split lp-reveal">
        <div><SectionLabel>03 / CONTROL IS THE ARCHITECTURE</SectionLabel><h2>Opportunity moves fast.<br/><span className="lp-gradient-text">Your limits hold.</span></h2><p className="lp-body">The system can say no. Independent controls protect the process even when a strategy sees an opportunity.</p><div className="lp-boundary"><LockKeyhole size={32}/><span>NO VALID AUTHORIZATION<strong>NO EXECUTION</strong></span></div></div>
        <div className="lp-safeguards">{safeguards.map(([title,copy])=><div key={title}><Check size={19}/><div><h3>{title}</h3><p>{copy}</p></div></div>)}</div>
      </section>
      <section id="paper-to-live" className="lp-journey lp-wrap lp-section lp-reveal">
        <SectionLabel>04 / PROVE THE PROCESS FIRST</SectionLabel><h2>Start in Paper.<br/>Move forward deliberately.</h2>
        <ol>{['Paper Trading','Validation','Explicit Live Authorization','Controlled Live Trading'].map((title,i)=><li key={title}><span>0{i+1}</span><h3>{title}</h3>{i>1&&<small>Future stage</small>}</li>)}</ol>
        <p>Paper results do not guarantee Live performance. Live trading requires separate, explicit authorization and is not enabled in the current Paper experience.</p>
      </section>
      <section className="lp-final"><div className="lp-wrap"><SectionLabel>BUILD A PROCESS YOU CAN INSPECT.</SectionLabel><h2>Systematic trading.<br/><span>Engineered for control.</span></h2><div className="lp-actions"><LinkButton href={googleLoginUrl}>Start Paper Trading <ArrowRight size={17}/></LinkButton><LinkButton href={googleLoginUrl} className="button-secondary">Explore the Workspace</LinkButton></div><p>Sign in with Google to enter your workspace.</p></div></section>
    </main>
    <footer className="lp-footer lp-wrap"><a href="#top" className="brand-link" aria-label="TradeRail home"><BrandLogo/></a><p>Systematic trading. Engineered for control.</p><a href="#risk-first">Built for control <ArrowRight size={14}/></a></footer>
  </LandingMotion></AuthExperience>
}
