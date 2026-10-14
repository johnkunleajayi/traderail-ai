export default function BrandLogo({ className = '' }: { className?: string }) {
  return <span className={`brand-logo ${className}`}>
    <img className="logo-dark" src="/brand/dark.png" alt="TradeRail" width="2172" height="724" />
    <img className="logo-light" src="/brand/light.png" alt="TradeRail" width="2172" height="724" />
  </span>
}