import { Bell, BookOpen, ChartNoAxesColumnIncreasing, CircleUserRound, FlaskConical, Layers3, Search, Settings, ShieldCheck, Zap } from 'lucide-react'
import BrandLogo from './BrandLogo'

const concepts = [
  { title: 'Research', icon: ChartNoAxesColumnIncreasing, copy: 'Turn ideas into testable hypotheses.' },
  { title: 'Paper Trading', icon: Layers3, copy: 'Explore in a simulated environment.' },
  { title: 'Risk Control', icon: ShieldCheck, copy: 'Define boundaries and safeguards.' },
  { title: 'Live Execution', icon: Zap, copy: 'Execute with intention and control.' },
]
export function ConceptChart({ compact = false }: { compact?: boolean }) {
  return <div className={`concept-chart ${compact ? 'compact-chart' : ''}`}>
    <div className="chart-grid" />
    <svg viewBox="0 0 600 145" preserveAspectRatio="none" role="img" aria-label="Decorative concept curve, not trading data">
      <defs>
        <linearGradient id={compact ? 'line-small' : 'line-main'} x1="0" x2="1"><stop stopColor="var(--color-primary)"/><stop offset="1" stopColor="var(--color-accent)"/></linearGradient>
        <linearGradient id={compact ? 'fill-small' : 'fill-main'} x2="0" y2="1"><stop stopColor="var(--color-accent)" stopOpacity=".13"/><stop offset="1" stopColor="var(--color-accent)" stopOpacity="0"/></linearGradient>
      </defs>
      <path d="M0 128 C20 105 30 135 52 116 S80 108 100 116 S126 113 145 94 S180 87 203 98 S240 65 270 74 S305 38 330 59 S364 103 397 74 S430 48 454 48 S480 19 510 32 S555 63 600 25 L600 145 L0 145Z" fill={`url(#${compact ? 'fill-small' : 'fill-main'})`}/>
      <path d="M0 128 C20 105 30 135 52 116 S80 108 100 116 S126 113 145 94 S180 87 203 98 S240 65 270 74 S305 38 330 59 S364 103 397 74 S430 48 454 48 S480 19 510 32 S555 63 600 25" fill="none" stroke={`url(#${compact ? 'line-small' : 'line-main'})`} strokeWidth="2"/>
    </svg>
  </div>
}
export default function ProductPreview() {
  return <div className="product-preview" aria-label="TradeRail product concept. All trading capabilities are planned.">
    <aside className="preview-sidebar">
      <BrandLogo />
      <div className="preview-nav"><span className="selected"><BookOpen/>Overview</span>{concepts.map(({title,icon:Icon})=><span key={title}><Icon/>{title}</span>)}</div>
      <span className="preview-system">SYSTEM</span><div className="preview-nav"><span><Settings/>Settings</span><span><BookOpen/>Documentation</span></div>
      <span className="sidebar-foot">FOUNDATION / V0.1</span>
    </aside>
    <div className="preview-content">
      <div className="preview-toolbar"><span><FlaskConical size={13}/> Overview</span><span className="preview-search"><Search size={12}/> Product concept</span><span className="toolbar-icons"><Bell size={15}/><CircleUserRound size={16}/></span></div>
      <div className="preview-stats">{[{label:'Portfolio',value:'Not connected',state:'No exchange linked'},{label:'Paper Trading',value:'Planned',state:'Coming in a future release'},{label:'Strategies',value:'Not available',state:'Foundation preview'},{label:'Risk Status',value:'Not configured',state:'No active execution'}].map(({label,value,state})=><div className="preview-stat" key={label}><span>{label}</span><strong>{value}</strong><small><i/>{state}</small></div>)}</div>
      <div className="preview-chart-panel"><div className="panel-heading"><strong>Research workspace <span>(Concept)</span></strong><span>ILLUSTRATIVE ONLY</span></div><ConceptChart/><div className="chart-caption"><span>Hypothesis</span><span>Evaluation</span><span>Validation</span></div></div>
      <p className="preview-section-title">Planned Capabilities</p>
      <div className="preview-capabilities">{concepts.map(({title,icon:Icon,copy})=><div key={title}><Icon size={21}/><h3>{title}</h3><p>{copy}</p><span className="planned-pill">PLANNED</span></div>)}</div>
    </div>
  </div>
}