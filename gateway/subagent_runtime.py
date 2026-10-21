"""Gateway-owned jobs. Durable receipts and reports, independent of parent lifetimes."""
import asyncio
import copy
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import subagent

LOG = logging.getLogger(__name__)
ACTIVE = {'queued', 'running', 'writing'}
TERMINAL = {'ok', 'error', 'incomplete', 'timeout', 'cancelled', 'interrupted'}
_RUNTIMES = {}


def now():
    return datetime.now().astimezone().isoformat()


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(text, encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def runtime_for(cfg):
    workspace = Path(cfg.get('tools', {}).get('workspace') or subagent.ROOT / 'toolbox/workspace').resolve()
    key = str(workspace)
    if key not in _RUNTIMES:
        _RUNTIMES[key] = SubagentRuntime(workspace)
    return _RUNTIMES[key]


class JobBus(subagent.TraceBus):
    def __init__(self, runtime, sid, record, parent):
        super().__init__(runtime.files(sid) / record['trace_path'], record['name'], record['agent_id'])
        self.runtime, self.sid, self.record, self.parent = runtime, sid, record, parent

    def emit(self, event_type, **fields):
        event = super().emit(event_type, **fields)
        if event_type in ('model_call', 'tool_call', 'tool_result', 'assistant_message'):
            self.record.update(phase=event_type, iterations=self.iterations,
                               tool_calls=self.tool_calls, updated_at=now())
            self.runtime.save(self.sid, self.record)
            self.publish()
        return event

    def publish(self):
        if self.parent:
            self.parent.emit('subagent_state', **self.record)

    def state(self, status, **fields):
        if status in TERMINAL:
            # finish() already committed the report and terminal state.
            super().emit('subagent_state', status=status, **fields)
            self.publish()
            self.runtime.notify(self.sid, self.record)
            return
        if self.record['status'] in TERMINAL:
            return
        self.record.update(status=status, updated_at=now())
        if status == 'running':
            self.record.update(started_at=now(), phase='starting')
        self.runtime.save(self.sid, self.record)
        super().emit('subagent_state', status=status, **fields)
        self.publish()


class SubagentRuntime:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.manifests = {}
        self.batches = {}
        self.limits = {}
        self.notifier = None
        self.notified_ids = set()
        self.closed = False

    def files(self, sid):
        return self.workspace / sid / 'files'

    def manifest(self, sid):
        if sid not in self.manifests:
            path = self.files(sid) / 'subagents/manifest.json'
            self.manifests[sid] = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
                'version': 1, 'session_id': sid, 'manifest_path': 'subagents/manifest.json', 'jobs': []}
        return self.manifests[sid]

    def save(self, sid, record=None):
        manifest = self.manifest(sid)
        if record is not None:
            atomic_write(self.files(sid) / record['state_path'], json.dumps(record, ensure_ascii=False, indent=2))
        manifest['updated_at'] = now()
        atomic_write(self.files(sid) / manifest['manifest_path'], json.dumps(manifest, ensure_ascii=False, indent=2))

    def submit(self, params, cfg, sid, allowed, parent_bus=None):
        if self.closed:
            raise ValueError('后台运行时正在关闭')
        subagent._prepare(params, cfg, allowed)
        if sum(j['status'] in ACTIVE for j in self.manifest(sid)['jobs']) + len(params['tasks']) > 32:
            raise ValueError('会话最多允许 32 个在途子任务')
        batch_id = 'batch_' + uuid.uuid4().hex[:16]
        records = []
        for index, item in enumerate(params['tasks']):
            agent_id = 'sa_' + uuid.uuid4().hex[:16]
            base = f'subagents/{batch_id}/{agent_id}'
            records.append({'agent_id': agent_id, 'batch_id': batch_id, 'session_id': sid,
                'name': item.get('name') or f'子任务 {index + 1}', 'task': item['task'],
                'status': 'queued', 'phase': 'queued', 'created_at': now(), 'updated_at': now(),
                'started_at': None, 'finished_at': None, 'duration_ms': 0,
                'iterations': 0, 'tool_calls': 0, 'detail': '',
                'timeout_s': item.get('timeout_s', 1800),
                'manifest_path': 'subagents/manifest.json', 'state_path': base + '/state.json',
                'report_path': base + '/report.md', 'trace_path': base + '/events.jsonl',
                'notification_ack': False})
        self.manifest(sid)['jobs'].extend(records)
        for rec in records:
            self.save(sid, rec)
        cancel = asyncio.Event()
        task = asyncio.create_task(self._run(copy.deepcopy(params), copy.deepcopy(cfg), sid,
                                          set(allowed), parent_bus, records, cancel))
        self.batches[batch_id] = {'sid': sid, 'task': task, 'cancel': cancel, 'records': records,
                                 'children': {}, 'cancelled_ids': set(), 'parent': parent_bus}
        task.add_done_callback(lambda done: self._batch_done(batch_id, done))
        return {'status': 'ok', 'submitted': True, 'batch_id': batch_id,
                'total': len(records), 'mode': params.get('mode', 'parallel'),
                'manifest_path': 'subagents/manifest.json',
                'jobs': [{k: r[k] for k in ('agent_id', 'name', 'status', 'report_path')} for r in records]}

    def _batch_done(self, batch_id, task):
        self.batches.pop(batch_id, None)
        if not task.cancelled() and task.exception():
            LOG.error('Subagent persistence failed', exc_info=task.exception())

    async def _run(self, params, cfg, sid, allowed, parent, records, cancel):
        buses = [JobBus(self, sid, record, parent) for record in records]
        batch = self.batches[records[0]['batch_id']]
        try:
            await subagent.run_batch(params, cfg, sid, cancel, allowed,
                trace_factory=lambda i: buses[i], on_result=lambda i, result: self.finish(sid, records[i], result),
                shared_limit=self.limits.setdefault(sid, asyncio.Semaphore(4)),
                on_worker=lambda i, task: batch['children'].__setitem__(records[i]['agent_id'], task),
                should_cancel=lambda i: records[i]['agent_id'] in batch['cancelled_ids'])
        except asyncio.CancelledError:
            pass
        except Exception:
            LOG.exception('Background subagent batch failed: %s', records[0]['batch_id'])
        finally:
            for bus in buses:
                if bus.record['status'] in ACTIVE:
                    self.finish(sid, bus.record, {'status': 'cancelled' if cancel.is_set() else 'error',
                                                 'error': '后台批次已退出', 'text': ''})
                    bus.state(bus.record['status'])

    def finish(self, sid, record, result):
        if record['status'] in TERMINAL:
            return
        record.update(status='writing', phase='writing', updated_at=now())
        self.save(sid, record)
        report = (f"# {record['name']}\n\nStatus: {result['status']}\n\n"
                  f"Task: {record['task']}\n\n{result.get('text', '')}\n")
        if result.get('error'):
            report += '\nError: ' + result['error'] + '\n'
        if not result.get('text') and result.get('progress'):
            report += '\n## Last Progress (unfinished)\n\n' + result['progress'] + '\n'
        if result.get('recent_tools'):
            report += '\n## Recent Tool Evidence\n\n' + json.dumps(result['recent_tools'], ensure_ascii=False, indent=2) + '\n'
        report += '\nTrace: ' + record['trace_path'] + '\n'
        atomic_write(self.files(sid) / record['report_path'], report)
        record.update(status=result['status'], phase='finished', finished_at=now(), updated_at=now(),
                      detail=result.get('error', ''), duration_ms=result.get('duration_ms', 0),
                      iterations=result.get('iterations', record['iterations']),
                      tool_calls=result.get('tool_calls', record['tool_calls']))
        record['tool_errors'] = result.get('tool_errors', 0)
        self.save(sid, record)

    def notify(self, sid, record):
        if record['agent_id'] in self.notified_ids:
            return
        if self.notifier and not self.closed:
            self.notified_ids.add(record['agent_id'])
            self.notifier(sid, self.notice(record))

    @staticmethod
    def notice(record):
        payload = {k: record[k] for k in ('agent_id', 'batch_id', 'status', 'manifest_path', 'report_path')}
        return {'id': record['agent_id'], 'kind': 'subagent',
                'content': '[子智能体任务已结束] ' + json.dumps(payload, ensure_ascii=False)
                           + '。请自行读取清单和报告，再根据原任务继续处理。'}

    def pending(self, sid):
        return [self.notice(j) for j in self.manifest(sid)['jobs']
                if j['status'] in TERMINAL and not j['notification_ack']]

    def ack(self, sid, ids):
        for record in self.manifest(sid)['jobs']:
            if record['agent_id'] in ids:
                record['notification_ack'] = True
                self.save(sid, record)

    async def cancel_session(self, sid):
        batches = [b for b in self.batches.values() if b['sid'] == sid]
        for batch in batches:
            batch['cancel'].set()
            batch['task'].cancel()
        await asyncio.gather(*(b['task'] for b in batches), return_exceptions=True)
        for batch in batches:
            self._cancel_unstarted(sid, batch)

    def _cancel_unstarted(self, sid, batch):
        for record in batch['records']:
            if record['status'] in ACTIVE:
                self.finish(sid, record, {'status': 'cancelled', 'error': '任务启动前已取消', 'text': ''})
                self.notify(sid, record)

    async def cancel_batch(self, sid, batch_id):
        batch = self.batches.get(batch_id)
        if batch and batch['sid'] == sid:
            batch['cancel'].set()
            batch['task'].cancel()
            await asyncio.gather(batch['task'], return_exceptions=True)
            self._cancel_unstarted(sid, batch)

    async def cancel_job(self, sid, agent_id):
        for batch in list(self.batches.values()):
            if batch['sid'] != sid:
                continue
            record = next((r for r in batch['records'] if r['agent_id'] == agent_id), None)
            if record is None or record['status'] in TERMINAL:
                continue
            batch['cancelled_ids'].add(agent_id)
            task = batch['children'].get(agent_id)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if record['status'] in ACTIVE:
                self.finish(sid, record, {'status': 'cancelled', 'error': '子任务已取消', 'text': ''})
                JobBus(self, sid, record, batch['parent']).state('cancelled')

    def recover(self):
        self.import_legacy()
        for path in self.workspace.glob('*/files/subagents/manifest.json'):
            sid = path.parents[2].name
            if not subagent.re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', sid):
                continue
            for record in self.manifest(sid)['jobs']:
                if record['status'] in ACTIVE:
                    self.finish(sid, record, {'status': 'interrupted', 'error': '网关重启，任务执行已中断', 'text': ''})

    def import_legacy(self):
        """Keep existing conversation traces visible without rerunning or notifying them."""
        for folder in self.workspace.glob('*/subagents'):
            sid = folder.parent.name
            if not subagent.re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', sid):
                continue
            manifest = self.manifest(sid)
            seen = {j['agent_id'] for j in manifest['jobs']}
            results = {}
            for output in (folder.parent / 'outputs').glob('sa_*.txt'):
                try:
                    payload = json.loads(output.read_text(encoding='utf-8'))
                    results.update({r['agent_id']: r for r in payload.get('results', [])})
                except (OSError, ValueError, KeyError):
                    continue
            for trace in folder.glob('sa_*/events.jsonl'):
                agent_id = trace.parent.name
                if agent_id in seen:
                    continue
                try:
                    raw = trace.read_text(encoding='utf-8')
                    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
                    states = [e for e in events if e['type'] == 'subagent_state']
                    if not states:
                        continue
                    last = states[-1]
                    result = results.get(agent_id, {})
                    status = result.get('status') or last['status']
                    if status in ACTIVE:
                        status = 'interrupted'
                    started = next((e['ts'] for e in states if e['status'] == 'running'), None)
                    base = f'subagents/legacy/{agent_id}'
                    record = {'agent_id': agent_id, 'batch_id': 'legacy', 'session_id': sid,
                        'name': last.get('name', agent_id), 'task': '历史子任务', 'status': 'queued',
                        'phase': 'queued', 'created_at': states[0]['ts'], 'updated_at': last['ts'],
                        'started_at': started, 'finished_at': None, 'duration_ms': 0,
                        'iterations': last.get('iterations', 0), 'tool_calls': last.get('tool_calls', 0),
                        'detail': '', 'manifest_path': 'subagents/manifest.json',
                        'state_path': base + '/state.json', 'report_path': base + '/report.md',
                        'trace_path': base + '/events.jsonl', 'notification_ack': True, 'legacy': True}
                    manifest['jobs'].append(record)
                    atomic_write(self.files(sid) / record['trace_path'], raw)
                    progress = next((e.get('content', '') for e in reversed(events)
                                     if e['type'] == 'assistant_message'), '')
                    duration = int((datetime.fromisoformat(last['ts']) - datetime.fromisoformat(started)).total_seconds() * 1000) if started else 0
                    self.finish(sid, record, {**result, 'status': status, 'text': result.get('text', ''),
                        'progress': progress, 'error': result.get('error') or last.get('detail', ''),
                        'duration_ms': duration, 'iterations': record['iterations'],
                        'tool_calls': record['tool_calls']})
                    record.update(updated_at=last['ts'], finished_at=last['ts'])
                    self.save(sid, record)
                except (OSError, ValueError, KeyError):
                    LOG.warning('Could not import legacy trace: %s', trace, exc_info=True)

    async def close(self):
        self.closed = True
        for sid in {b['sid'] for b in self.batches.values()}:
            await self.cancel_session(sid)
