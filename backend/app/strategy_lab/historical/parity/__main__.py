"""Explicit local CLI. Only download accesses public archive URLs."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
from app.strategy_lab.historical.input import decimal_string
from .data import download,normalize,DEFAULT_MAX_CANDLES
from .engine import Settings
from .report import evaluate


def utc_time(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def main(argv=None):
    parser=argparse.ArgumentParser(description='HISTORICAL SIMULATION — NOT LIVE OR FORWARD PERFORMANCE')
    subs=parser.add_subparsers(dest='command',required=True)
    d=subs.add_parser('download');d.add_argument('--month',action='append',required=True);d.add_argument('--output',required=True,type=Path)
    n=subs.add_parser('import');n.add_argument('--manifest',required=True,type=Path);n.add_argument('--output',required=True,type=Path);n.add_argument('--max-candles',type=int,default=DEFAULT_MAX_CANDLES)
    e=subs.add_parser('evaluate');e.add_argument('--dataset',type=Path,required=True);e.add_argument('--output',type=Path,required=True)
    for key in ('start','split','end'):e.add_argument('--'+key,type=utc_time,required=True)
    for key in ('capital','quantity','commitment-ceiling'):e.add_argument('--'+key,type=decimal_string,required=True)
    e.add_argument('--max-candles',type=int,default=DEFAULT_MAX_CANDLES)
    e.add_argument('--repeated-research-cycles',action='store_true',help='NON-PRODUCTION repeated cycles; default first-close terminal barrier')
    args=parser.parse_args(argv)
    try:
        if args.command=='download':download(args.month,args.output)
        elif args.command=='import':
            result=normalize(args.manifest,args.output,max_candles=args.max_candles)
            if not result['usable']:raise ValueError('Rejected/missing rows require inspection')
        else:evaluate(args.dataset,args.output,start=args.start,split=args.split,end=args.end,
            settings=Settings(args.capital,args.quantity,args.commitment_ceiling,args.max_candles,args.repeated_research_cycles))
        print('PASS: '+args.command.upper()+'; HISTORICAL SIMULATION ONLY',flush=True);return 0
    except Exception as error:
        print('STOP: HISTORICAL_PARITY_'+args.command.upper()+'_FAILED; category='+type(error).__name__,flush=True);return 1

if __name__=='__main__':raise SystemExit(main())
