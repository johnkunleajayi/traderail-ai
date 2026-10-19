import {useEffect,useRef,useState} from 'react'
import {Card,SectionLabel} from '../../components/ui'
import {buildValidationRequest,emptyWindow,initialValidationForm,suggestValidationWindows,type ValidationFormValues} from './model'
import {readValidationHistory,runValidation,ValidationApiError} from './api'
import type {ValidationResult} from './types'
import ValidationForm from './ValidationForm'
import ValidationEvidence from './ValidationEvidence'
export default function ValidationWorkspace({onSessionExpired}:{onSessionExpired:()=>void}){
  const [values,setValues]=useState(initialValidationForm),[result,setResult]=useState<ValidationResult|null>(null)
  const [busy,setBusy]=useState(false),[suggesting,setSuggesting]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState(''),[expired,setExpired]=useState(false)
  const runController=useRef<AbortController|null>(null),suggestController=useRef<AbortController|null>(null),output=useRef<HTMLDivElement>(null)
  useEffect(()=>()=>{runController.current?.abort();suggestController.current?.abort()},[])
  function change(next:ValidationFormValues){
    if(suggesting){suggestController.current?.abort();setSuggesting(false);setNotice('Inputs changed. Request a fresh suggestion or enter known stored windows.')}
    if(next.canonical_symbol!==values.canonical_symbol||next.timeframe!==values.timeframe){suggestController.current?.abort();setSuggesting(false);setNotice('Selection changed. Enter or suggest matching stored windows.');next={...next,primary:emptyWindow(),walk_forward_folds:next.walk_forward_folds.map(()=>emptyWindow())}}
    setValues(next)
  }
  async function suggest(){
    if(busy||suggesting)return
    const controller=new AbortController();suggestController.current=controller;setSuggesting(true);setNotice('Reading stored TradeRail history; no ingestion is performed.')
    try{const candles=await readValidationHistory(values.canonical_symbol,values.timeframe,controller.signal)
      const windows=suggestValidationWindows(candles,values)
      if(!controller.signal.aborted){setValues(current=>({...current,...windows}));setNotice('Suggested from existing candles only: 52 warm-up candles per window, then chronological IS/OOS observations. Review every boundary; this is not a sufficiency verdict.')}}
    catch(reason){if(!controller.signal.aborted)setNotice(reason instanceof Error?reason.message:'Stored boundaries unavailable.')}
    finally{if(!controller.signal.aborted)setSuggesting(false)}
  }
  async function run(){
    if(runController.current||suggesting)return
    let payload;try{payload=buildValidationRequest(values)}catch(reason){setError(reason instanceof Error?reason.message:'Review the validation inputs.');return}
    const controller=new AbortController();runController.current=controller;setBusy(true);setError('');setExpired(false);setResult(null)
    try{const evidence=await runValidation(payload,controller.signal);if(!controller.signal.aborted){setResult(evidence);requestAnimationFrame(()=>output.current?.focus())}}
    catch(reason){if(!controller.signal.aborted){setError(reason instanceof Error?reason.message:'Validation unavailable.');setExpired(reason instanceof ValidationApiError&&reason.status===401)}}
    finally{if(!controller.signal.aborted){setBusy(false);runController.current=null}}
  }
  return <section className="lab-validation-workspace" aria-labelledby="validation-title"><SectionLabel>FORMAL RESEARCH VALIDATION</SectionLabel><h2 id="validation-title">Challenge the evidence.</h2><p className="lab-caption">Experiments inspect one historical run. Formal validation combines separated OOS windows, independent folds, fixed probes, benchmarks and cost stress under the backend research policy.</p>
    <Card className="lab-validation-setup"><ValidationForm values={values} onChange={change} onRun={run} onSuggest={suggest} busy={busy} suggesting={suggesting} notice={notice}/></Card>
    <div ref={output} tabIndex={-1} className="lab-validation-output">
      {error&&<div className="lab-error" role="alert"><h3>Validation not completed</h3><p>{error}</p>{expired&&<button type="button" className="button button-secondary" onClick={onSessionExpired}>Return to sign in</button>}</div>}
      {busy?<Card className="lab-empty"><h3>Evaluating declared research evidence</h3><p role="status">Running the existing validation framework on stored history. No ingestion, parameter optimization or strategy promotion occurs.</p></Card>:result?<ValidationEvidence result={result}/>:!error&&<Card className="lab-empty"><h3>No formal validation run yet.</h3><p>Declare stored research windows and assumptions. Backend evidence will explain whether coverage is insufficient, policy checks fail, or the research merits consideration for another controlled validation stage.</p></Card>}
    </div>
  </section>
}
