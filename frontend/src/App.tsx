import { ArrowRight, ChartNoAxesColumnIncreasing, Check, Circle, Clock3, Code2, FlaskConical, Layers3, Link2, LockKeyhole, Monitor, Palette, ShieldCheck, Workflow, Zap } from 'lucide-react'
import { Card, LinkButton, SectionLabel } from './components/ui'
import ThemeToggle from './components/ThemeToggle'
import BrandLogo from './components/BrandLogo'
import ProductPreview, { ConceptChart } from './components/ProductPreview'

const stages = [
  { number: '01', title: 'Research', icon: ChartNoAxesColumnIncreasing, copy: 'Turn an idea into a testable hypothesis. Build conviction through evidence.' },
  { number: '02', title: 'Paper Trading', icon: Layers3, copy: 'Observe your approach in a simulated environment before committing capital.' },
  { number: '03', title: 'Risk Control', icon: ShieldCheck, copy: 'Define the boundaries. Make exposure, limits and safeguards explicit.' },
  { number: '04', title: 'Live Execution', icon: Zap, copy: 'Move from validated intent to deliberate execution, with control at every step.' },
]
const foundations = [
  {icon: Palette, text:'A consistent, accessible design language'},
  {icon: Monitor, text:'Responsive layouts. Light and dark modes.'},
  {icon: Layers3, text:'A modular API foundation'},
  {icon: Link2, text:'A foundation for future integrations'},
  {icon: LockKeyhole, text:'No exchange connections. No orders. No funds at risk.'},
  {icon: Clock3, text:'Trading capabilities will arrive in future releases.'},
]
export default function App() {
  return <div className="app-shell" id="top">
    <a className="skip-link" href="#main">Skip to content</a>
    <div className="hero-background">
      <div className="ambient-ribbon ribbon-one" aria-hidden="true"/><div className="ambient-ribbon ribbon-two" aria-hidden="true"/>
      <header className="header container">
        <a href="#top" className="brand-link" aria-label="TradeRail home"><BrandLogo/></a>
        <nav aria-label="Main navigation"><a href="#top" className="active">Home</a><a href="#approach">The Approach</a><a href="#roadmap">Roadmap</a><a href="#foundation">Foundation</a></nav>
        <div className="header-end"><ThemeToggle/><LinkButton href="#foundation" className="header-cta">Explore TradeRail</LinkButton></div>
      </header>
      <main id="main">
        <section className="hero container">
          <div className="hero-copy">
            <SectionLabel>A DELIBERATE BEGINNING</SectionLabel>
            <h1>Systematic <br/>trading.<br/><span>Engineered <br/>for control.</span></h1>
            <p className="hero-description">A considered foundation for the entire trading lifecycle. From the first hypothesis to execution, every step should have a purpose. And a boundary.</p>
            <div className="hero-actions"><LinkButton href="#approach">Explore the approach <ArrowRight size={17}/></LinkButton><LinkButton className="button-secondary" href="#principles">Learn more</LinkButton></div>
            <div className="hero-principles"><span><ShieldCheck/><span>Disciplined<br/>by design</span></span><span><ChartNoAxesColumnIncreasing/><span>Evidence<br/>over emotion</span></span><span><Layers3/><span>Built for the<br/>long term</span></span></div>
          </div>
          <div className="product-stage"><ProductPreview/></div>
        </section>
        <div className="container"><div className="principle-strip">{[{icon:Workflow,text:'A MODULAR FOUNDATION'},{icon:Code2,text:'BUILT FOR SCALABILITY'},{icon:ShieldCheck,text:'SECURITY BY DESIGN'},{icon:FlaskConical,text:'A CONSIDERED APPROACH'}].map(({icon:Icon,text})=><span key={text}><Icon size={21}/>{text}</span>)}</div></div>
        <section id="approach" className="section container">
          <div className="framework-label"><span>THE TRADERAIL FRAMEWORK</span><span>CONCEPT / 001</span></div>
          <div className="section-heading"><h2>One lifecycle. Four deliberate steps.</h2><p>A connected vision for systematic trading. These capabilities are planned, not yet available.</p></div>
          <div className="stage-grid">{stages.map(({number,title,icon:Icon,copy})=><Card key={title}><div className="stage-icon"><Icon size={26}/></div><div className="stage-body"><SectionLabel>{number} / PLANNED</SectionLabel><h3>{title}</h3><p>{copy}</p></div><span className="stage-arrow" aria-hidden="true"><ArrowRight size={18}/></span></Card>)}</div>
        </section>
        <section id="principles" className="container discipline-section">
          <div className="discipline-copy"><SectionLabel>DISCIPLINE BY DESIGN</SectionLabel><h2>Evidence before conviction.<br/>Boundaries before exposure.<br/>Intent before execution.</h2><p>TradeRail is being built for traders who value a systematic, thoughtful approach. Good trading starts with a process.</p></div>
          <div className="process-preview"><div className="process-list"><h3>Strategy development <span>(Concept)</span></h3>{['Define hypothesis','Backtest idea','Validate in paper trading','Set risk parameters','Deploy to live execution'].map((text,i)=><p key={text}>{i<2?<span className="process-check"><Check size={12}/></span>:<Circle size={16}/>}<span>{text}</span></p>)}<span className="process-disclaimer">PLANNED WORKFLOW · NOT FUNCTIONAL</span></div><div className="process-chart"><div className="concept-label">Illustrative concept · no market data</div><ConceptChart compact/><div className="process-metrics">{['Evaluation','Risk limits','Execution'].map(text=><div key={text}><span>{text}</span><strong>Planned</strong></div>)}</div></div></div>
        </section>
        <section id="foundation" className="foundation-section">
          <div className="container foundation-grid"><div className="foundation-copy"><SectionLabel>FOUNDATION FIRST</SectionLabel><h2>Good systems start<br/>with clear boundaries.</h2><p>This is the beginning of TradeRail. A visual system and technical foundation, designed to grow with care.</p><LinkButton href="#roadmap">The road ahead <ArrowRight size={16}/></LinkButton></div><div className="foundation-features">{foundations.map(({icon:Icon,text})=><div key={text}><span className="foundation-icon"><Icon size={23}/></span><p>{text}</p></div>)}</div></div>
          <div className="container"><div id="roadmap" className="journey-panel"><img src="/brand/icon.png" width="84" height="84" alt="" loading="lazy"/><div><SectionLabel>THE ROAD AHEAD</SectionLabel><h2>Built with intention. Ready for what’s next.</h2><p>TradeRail is in its early stages. The foundation is here, and more capabilities are planned.</p></div><LinkButton href="#approach">Explore the roadmap <ArrowRight size={17}/></LinkButton></div></div>
        </section>
      </main>
    </div>
    <footer className="container footer"><div><a href="#top" className="brand-link" aria-label="TradeRail home"><BrandLogo/></a><p>Systematic trading. Engineered for control.</p></div><nav aria-label="Footer navigation"><strong>Product</strong><a href="#approach">The Approach</a><a href="#roadmap">Roadmap</a><a href="#foundation">Foundation</a></nav><div className="footer-status"><span>Foundation / V0.1</span><p>A deliberate beginning.</p><span className="footer-note">Trading capabilities are not yet available.</span></div></footer>
  </div>
}