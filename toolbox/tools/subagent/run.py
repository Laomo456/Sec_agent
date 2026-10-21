"""Delegation requires the gateway's persistent background runtime."""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')


async def main(params):
    print(json.dumps({'status': 'validation_error', 'tool': 'subagent',
        'detail': 'subagent 需要网关后台运行时。请通过对话、工作流或网页工具试运行提交。'}, ensure_ascii=False))
    return 1


if __name__ == '__main__':
    try:
        sys.exit(asyncio.run(main(json.loads(sys.argv[1]))))
    except (ValueError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
