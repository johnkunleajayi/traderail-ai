// Presentation only: fixed-point API strings stay intact. No Number conversion.
// Prices/ATR/MACD: at most 2 decimals above 1; 4 significant digits below 1.
// RSI: at most 2 decimals. Volatility: percentage, at most 4 decimals,
// extending precision for tiny nonzero values. Display rounding is half-up.
function parts(value: string) {
  if (!/^-?\d+(?:\.\d+)?$/.test(value)) return null
  const negative = value.startsWith('-')
  const [whole, fraction = ''] = value.replace(/^-/, '').split('.')
  return { negative, whole: whole.replace(/^0+(?=\d)/, ''), fraction }
}

function display(value: string, decimals: number, preserveSmall: boolean): string {
  const parsed = parts(value)
  if (!parsed) return 'Unavailable'
  const { negative, whole, fraction } = parsed
  const first = fraction.search(/[1-9]/)
  const places = preserveSmall && whole === '0' && first >= 0 ? Math.max(decimals, first + 4) : decimals
  let kept = whole + fraction.slice(0, places).padEnd(places, '0')
  if (fraction.length > places && fraction[places] >= '5') kept = (BigInt(kept) + 1n).toString()
  kept = kept.padStart(places + 1, '0')
  const integer = (places ? kept.slice(0, -places) : kept).replace(/^0+(?=\d)/, '')
  const tail = places ? kept.slice(-places).replace(/0+$/, '') : ''
  const sign = negative && /[1-9]/.test(integer + tail) ? '-' : ''
  return sign + integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (tail ? '.' + tail : '')
}

export function formatMarketValue(value: string): string { return display(value, 2, true) }
export function formatIndicator(value: string, kind: 'rsi' | 'market' = 'market'): string {
  return kind === 'rsi' ? display(value, 2, false) : formatMarketValue(value)
}
export function formatVolatility(value: string): string {
  const parsed = parts(value)
  if (!parsed) return 'Unavailable'
  // Decimal-point shift for percent display only, without binary floating point.
  const digits = parsed.whole + parsed.fraction.padEnd(2, '0')
  const point = parsed.whole.length + 2
  const percentage = (parsed.negative ? '-' : '') + digits.slice(0, point) + '.' + (digits.slice(point) || '0')
  return display(percentage, 4, true) + '%'
}
