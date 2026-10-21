"""Reuse unchanged local file ranges without suppressing legitimate fresh reads.

Snapshots live on tool messages, so they survive turns and process restarts.
Only known local file_read calls are eligible; other tools retain their existing
execution semantics. A stat version before/after the read prevents stale reuse.
"""
import json
from pathlib import Path


def _version(path: Path) -> list | None:
    try:
        stat = path.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except OSError:
        return None


class FileReadCache:
    def __init__(self, workspace: Path | None, sid: str, history: list):
        self.root = workspace / sid / 'files' if workspace is not None else None
        self.entries = []
        for message in history:
            snapshot = message.get('_file_read')
            if message.get('role') == 'tool' and isinstance(snapshot, dict):
                self._add(snapshot, message.get('content', ''))

    def request(self, name: str, params: dict) -> dict | None:
        if name == 'run_tool':
            name, params = params.get('tool_name'), params.get('params') or {}
        if name != 'file_read' or self.root is None or not isinstance(params, dict):
            return None
        # Pattern mode returns matching lines, not a contiguous offset window.
        if params.get('pattern'):
            return None
        try:
            path = Path(params['path'])
            path = path if path.drive else self.root / path
            path = path.resolve()
            version = _version(path)
            if version is None or not path.is_file():
                return None
            return {'path': str(path), 'version': version,
                    'offset': max(0, int(params.get('offset', 0))),
                    'limit': min(2000, max(1, int(params.get('limit', 200))))}
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _add(self, snapshot: dict, result: str) -> None:
        try:
            envelope = json.loads(result)
            output = envelope.get('output')
            if envelope.get('status') != 'ok' or not isinstance(output, str):
                return
            if envelope.get('stats', {}).get('truncated') or envelope.get('preview_truncated'):
                return
            lines = output.splitlines()
            if not lines:
                return
            if lines[0].startswith('[二进制文件]') or ' …[行过长，截断 ' in output:
                return
            # A real file_read output has its header followed by numbered lines.
            start = snapshot['offset']
            body = lines[1:]
            if any(not line.startswith(f'{start + i + 1}: ') for i, line in enumerate(body)):
                return
            if len(body) > snapshot['limit']:
                return
            self.entries.append((snapshot, envelope, body))
        except (ValueError, TypeError, KeyError, AttributeError):
            return

    def lookup(self, request: dict) -> str | None:
        for saved, envelope, body in reversed(self.entries):
            if saved['path'] != request['path'] or saved['version'] != request['version']:
                continue
            first, last = saved['offset'], saved['offset'] + len(body)
            start, end = request['offset'], request['offset'] + request['limit']
            # A short range reaching EOF covers later requests up to the same EOF.
            at_eof = len(body) < saved['limit']
            if start < first or start > last or (end > last and not at_eof):
                continue
            lines = body[start - first:min(end, last) - first]
            cached = dict(envelope)
            cached['output'] = (f"{request['path']} 已读内容（复用未变化文件），返回 {start}-{end}\n"
                                + '\n'.join(lines))
            cached['cached'] = True
            cached['hint'] = '文件版本未变，已复用此前完整读取结果；无需再从头读取。'
            cached['stats'] = {**cached.get('stats', {}), 'lines': len(lines) + 1,
                               'duration_ms': 0, 'truncated': False}
            # Preserve the numbered output format for subsequent cache hydration.
            cached.pop('preview_truncated', None)
            return json.dumps(cached, ensure_ascii=False)
        return None

    def remember(self, request: dict | None, result: str) -> dict | None:
        if request is None or _version(Path(request['path'])) != request['version']:
            return None
        count = len(self.entries)
        self._add(request, result)
        return request if len(self.entries) > count else None
