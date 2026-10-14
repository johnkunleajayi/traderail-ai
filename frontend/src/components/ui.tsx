import type { ComponentProps, ReactNode } from 'react'
export function Badge({ children }: { children: ReactNode }) { return <span className="badge">{children}</span> }
export function Card({ className = '', ...props }: ComponentProps<'article'>) { return <article className={`card ${className}`} {...props} /> }
export function LinkButton({ className = '', ...props }: ComponentProps<'a'>) { return <a className={`button ${className}`} {...props} /> }
export function SectionLabel({ children }: { children: ReactNode }) { return <p className="section-label">{children}</p> }