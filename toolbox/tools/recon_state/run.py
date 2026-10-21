"""Standalone, registry-loaded state card; no gateway lifecycle hooks or networking."""
import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
from state import Ledger


def main(params):
    session = os.environ.get('AGENT_SESSION', 'default')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', session):
        raise ValueError('会话标识不合法')
    root = Path(__file__).resolve().parents[2] / 'workspace' / session / 'files'
    return Ledger(root).call(params['op'], params.get('params', {}))


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.loads(sys.argv[1])), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        sys.exit(2)
