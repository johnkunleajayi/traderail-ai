"""Run only with python -m app.mandate_runner. Importing never starts a loop."""
from .runtime import main

if __name__=='__main__':
    raise SystemExit(main())
