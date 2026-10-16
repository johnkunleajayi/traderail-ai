import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'
const source=await readFile(new URL('../src/markets/format.ts',import.meta.url),'utf8')
const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const {formatMarketValue,formatIndicator,formatVolatility}=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`)
for(const [raw,expected] of [['83415.32000000','83,415.32'],['2357.83000000','2,357.83'],['82218.82000000','82,218.82'],['100.00000000','100'],['12.5000','12.5'],['0.0000123456789','0.00001235'],['0.123456789','0.1235'],['-83415.3200','-83,415.32'],['-0.0000123456789','-0.00001235'],['0.00000000','0'],['-0.0000','0'],['999.999','1,000'],['999999999999999999999999.995','1,000,000,000,000,000,000,000,000']]) {
 test(`market display ${raw}`,()=>assert.equal(formatMarketValue(raw),expected))
}
test('RSI, ATR and signed MACD have readable precision',()=>{
 assert.equal(formatIndicator('52.9313303232323232','rsi'),'52.93')
 assert.equal(formatIndicator('100.0000','rsi'),'100')
 assert.equal(formatIndicator('423.3573360608840962018023'),'423.36')
 assert.equal(formatIndicator('-4.82815113000000'),'-4.83')
 assert.equal(formatIndicator('0.000002345678'),'0.000002346')
})
for(const [raw,expected] of [['0.00259252','0.2593%'],['0.01','1%'],['0','0%'],['-0.00259252','-0.2593%'],['0.00000000123456789','0.0000001235%'],['1.23456789','123.4568%']]) {
 test(`volatility percent ${raw}`,()=>assert.equal(formatVolatility(raw),expected))
}
test('tiny and very large decimals never pass through Number',()=>{
 const tiny='0.'+'0'.repeat(350)+'123456789'
 assert.equal(formatMarketValue(tiny),'0.'+'0'.repeat(350)+'1235')
 const huge='1'+'0'.repeat(350)+'.32000000'
 assert.equal(formatMarketValue(huge),huge.split('.')[0].replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.32')
})
test('invalid strings are unavailable rather than fabricated zero',()=>{
 for(const value of ['NaN','Infinity','1e-4','','1,000']) assert.equal(formatMarketValue(value),'Unavailable')
})
