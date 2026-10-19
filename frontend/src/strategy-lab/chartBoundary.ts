// Presentation boundary only. These coordinates MUST NOT feed financial calculations.
import type { StoredCandle } from '../history/api'
import type { ResearchData, ResearchEvent } from './research'
import type { SeriesMarker, UTCTimestamp } from 'lightweight-charts'
export function chartNumber(exact: string): number {
  const value = Number(exact)
  if (!Number.isFinite(value) || (value === 0 && /[1-9]/.test(exact))) throw new Error('Exact values exceed chart-rendering limits. Use the exact inspection values instead.')
  return value
}
export const chartTime = (timestamp: string) => (Date.parse(timestamp)/1000) as UTCTimestamp
export function candleCoordinates(candles: StoredCandle[]) {
  return candles.map(c => {
    const row = {time:chartTime(c.open_time),open:chartNumber(c.open),high:chartNumber(c.high),low:chartNumber(c.low),close:chartNumber(c.close)}
    if ([row.open,row.high,row.low,row.close].some(n => n <= 0)) throw new Error('Stored candle prices cannot be rendered.')
    return row
  })
}
export function performanceCoordinates(data: ResearchData) {
  return { equity:data.equity_curve.map(r => ({time:chartTime(r.timestamp),value:chartNumber(r.total_equity)})),
    // Backend drawdown is a nonnegative magnitude; mirror vertically only for display.
    drawdown:data.equity_curve.map(r => r.drawdown === null ? {time:chartTime(r.timestamp)} : {time:chartTime(r.timestamp),value:drawdownCoordinate(r.drawdown)}) }
}
export function markerCoordinates(events: ResearchEvent[], token: (name: string) => string): SeriesMarker<UTCTimestamp>[] {
  return events.map(event => {
    const enter = event.action.startsWith('ENTER'), long = event.action.endsWith('LONG')
    const label = `${enter ? '+' : '−'}${long ? 'L' : 'S'}`
    if (event.kind === 'fill') return {time:chartTime(event.candle_open_time),position:'atPriceMiddle' as const,price:chartNumber(event.price!),shape:'circle' as const,
      color:token('--color-accent'),text:`F ${label}${event.status === 'Open position' ? ' · open' : ''}`,id:event.id}
    return {time:chartTime(event.candle_open_time),position:long === enter ? 'belowBar' as const : 'aboveBar' as const,
      shape:event.status === 'Unfilled' ? 'square' as const : long === enter ? 'arrowUp' as const : 'arrowDown' as const,
      color:token(event.status === 'Unfilled' ? '--color-warning' : '--color-primary'),
      text:`D ${label}${event.status === 'Unfilled' ? ' · unfilled' : ''}`,id:event.id}
  }).sort((a,b)=>(a.time as number)-(b.time as number))
}

export function drawdownAxisLabel(value: number): string {
  return `${value === 0 ? '0.00' : (value * 100).toFixed(2)}%` // Axis label only, never an accounting value.
}

function drawdownCoordinate(exact: string): number {
  const value = chartNumber(exact)
  return value === 0 ? 0 : -value
}
