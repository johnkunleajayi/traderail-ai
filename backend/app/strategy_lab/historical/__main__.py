"""python -m app.strategy_lab.historical --input LOCAL.json --output NEW.json."""
import argparse
import json
from pathlib import Path
from .input import HistoricalInput, decimal_string
from .report import evaluate_local, LABEL


def main(argv=None):
    parser=argparse.ArgumentParser(description=LABEL)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--initial-capital',required=True,type=decimal_string)
    parser.add_argument('--quantity',required=True,type=decimal_string)
    args=parser.parse_args(argv)
    try:
        if args.output.exists():raise ValueError('Output exists')
        if args.input.stat().st_size>2_000_000:raise ValueError('Input too large')
        source=HistoricalInput.model_validate_json(args.input.read_text(encoding='utf-8'))
        report=evaluate_local(source,initial_capital=args.initial_capital,quantity=args.quantity)
        encoded=json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)
        with args.output.open('x',encoding='utf-8') as stream:stream.write(encoded+'\n')
        print('PASS: HISTORICAL SIMULATION REPORT WRITTEN; NOT LIVE OR FORWARD PERFORMANCE')
        return 0
    except Exception as error:
        print('STOP: HISTORICAL_EVALUATION_FAILED; category='+type(error).__name__)
        return 1

if __name__=='__main__':raise SystemExit(main())
