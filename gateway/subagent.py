"""Runtime-bound delegation: isolated contexts, bounded batches, inherited tools."""
import asyncio
import copy
import importlib.util
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import jsonschema
import yaml

import toolsets

ROOT = Path(__file__).resolve().parent.parent
SPEC = yaml.safe_load((ROOT / 'toolbox/tools/subagent/tool.yaml').read_text(encoding='utf-8'))
_normalizer_spec = importlib.util.spec_from_file_location('_agg_subagent_normalize', ROOT / 'toolbox/normalize.py')
NORMALIZE = importlib.util.module_from_spec(_normalizer_spec)
_normalizer_spec.loader.exec_module(NORMALIZE)


class TraceBus:
    def __init__(self, path, name, agent_id, parent_bus=None):
        self.path, self.name, self.agent_id = path, name, agent_id
        self.parent_bus = parent_bus
        self.error = ''
        self.progress = ''
        self.recent_tools = []
        self.tool_errors = 0
        self.iterations = self.tool_calls = self.seq = 0
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type, **fields):
        self.seq += 1
        if event_type == 'error':
            self.error = fields.get('detail') or '子任务失败'
        if event_type == 'model_call':
            self.iterations = max(self.iterations, fields.get('iteration', 0))
        if event_type == 'tool_call':
            self.tool_calls += 1
        if event_type == 'assistant_message' and fields.get('content'):
            self.progress = fields['content']
        if event_type == 'tool_result':
            result = str(fields.get('result') or '')
            self.recent_tools.append({'tool': fields.get('tool', ''), 'result': result[:3000]})
            self.recent_tools = self.recent_tools[-12:]
            try:
                if json.loads(result).get('status') not in ('ok', 'success'):
                    self.tool_errors += 1
            except (ValueError, AttributeError):
                pass
        event = {'seq': self.seq, 'ts': datetime.now().astimezone().isoformat(),
                 'agent_id': self.agent_id, 'type': event_type, **fields}
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
        return event

    def emit_live(self, event_type, **fields):
        # Full assistant messages are persisted; streaming fragments need no second log.
        pass

    def state(self, status, **fields):
        self.emit('subagent_state', status=status, name=self.name, **fields)
        if self.parent_bus:
            self.parent_bus.emit('subagent_state', agent_id=self.agent_id,
                                 name=self.name, status=status, **fields)


class DelegatingTools:
    """Intercept the registered tool in native and meta surfaces without a second model loop."""
    def __init__(self, box, cfg, session_id, bus, cancel, available):
        self.box, self.cfg, self.session_id = box, cfg, session_id
        self.bus, self.cancel, self.available = bus, cancel, available
        self.tools = box.tools

    async def native_tools(self):
        tools = copy.deepcopy(await self.box.native_tools())
        for tool in tools:
            spec = tool.get('function', {})
            if spec.get('name') == 'subagent':
                props = spec.get('parameters', {}).get('properties', {})
                selector = props.get('tasks', {}).get('items', {}).get('properties', {}).get('toolset_id')
                if selector is not None:
                    selector['enum'] = [p['id'] for p in toolsets.all_presets()]
        return tools

    async def danger_of(self, name, params):
        if name == 'subagent' or (name == 'run_tool' and params.get('tool_name') == 'subagent'):
            return 'intrusive'
        return await self.box.danger_of(name, params)

    async def call_surface(self, name, arguments, surface):
        if (surface == 'native' and name == 'subagent') or \
                (name == 'run_tool' and arguments.get('tool_name') == 'subagent'):
            params = arguments if name == 'subagent' else arguments.get('params', {})
            result = await execute(params, self.cfg, self.session_id, self.cancel,
                                   allowed=self.available, parent_bus=self.bus)
            return json.dumps(result, ensure_ascii=False)
        return await self.box.call_surface(name, arguments, surface)


async def bind(box, cfg, session_id, bus, cancel):
    if cfg.get('agent', {}).get('_subagent_depth', 0) or not hasattr(box, 'cards'):
        return box
    cards = await box.cards()
    if 'subagent' not in cards:
        return box
    return DelegatingTools(box, cfg, session_id, bus, cancel, set(cards) - {'subagent'})


def _prepare(params, cfg, allowed):
    jsonschema.validate(params, SPEC['schema'])
    if cfg.get('agent', {}).get('_subagent_depth', 0):
        raise ValueError('子智能体不能再次委派')
    profiles = cfg.get('model_profiles', {})
    presets = {p['id']: p for p in toolsets.all_presets()}
    prepared = []
    for index, item in enumerate(params['tasks']):
        if not item['task'].strip():
            raise ValueError(f'tasks[{index}].task 不能为空白')
        if item.get('profile') and item['profile'] not in profiles:
            raise ValueError(f"未知模型档案: {item['profile']}")
        pid = item.get('toolset_id') or 'all'
        if pid not in presets:
            raise ValueError(f"未知工具集: {pid}。可用 ID: {', '.join(presets)}；省略则继承当前权限")
        requested = set(presets[pid].get('tools') or [])
        child_allowed = set(allowed) if '*' in requested else set(allowed) & requested
        child_cfg = copy.deepcopy(cfg)
        if item.get('profile'):
            child_cfg['model'] = copy.deepcopy(profiles[item['profile']])
        settings = child_cfg.setdefault('agent', {})
        settings['max_iterations'] = min(item.get('max_iterations', 40),
                                         cfg.get('agent', {}).get('max_iterations', 40))
        settings['_subagent_depth'] = 1
        tools_cfg = child_cfg.setdefault('tools', {})
        extra_env = dict(tools_cfg.get('extra_env') or {})
        inherited_deny = {v for v in extra_env.get('AGENT_TOOLS_DENY', '').split(',') if v}
        extra_env.update(AGENT_TOOLS_ALLOW=','.join(sorted(child_allowed)) or '__none__',
                         AGENT_TOOLS_DENY=','.join(sorted(inherited_deny | {'subagent'})),
                         AGENT_CALL_PREFIX='sub_' + uuid.uuid4().hex[:16] + '_')
        tools_cfg['extra_env'] = extra_env
        if not child_allowed:
            tools_cfg.pop('command', None)
        prepared.append((item, child_cfg, pid))
    return prepared


async def run_batch(params, cfg, session_id, cancel, allowed, parent_bus=None,
                    *, trace_factory=None, on_result=None, shared_limit=None,
                    on_worker=None, should_cancel=None):
    from agent import run_agent

    prepared = _prepare(params, cfg, allowed)
    workspace = Path(cfg.get('tools', {}).get('workspace') or ROOT / 'toolbox/workspace')
    traces = workspace / session_id / 'subagents'
    sem = asyncio.Semaphore(params.get('concurrency', 3))
    slots = [None] * len(prepared)
    batch_timeout = False
    trace_buses = []
    for index, (item, _, _) in enumerate(prepared):
        agent_id = 'sa_' + uuid.uuid4().hex[:16]
        bus = trace_factory(index) if trace_factory else TraceBus(
            traces / agent_id / 'events.jsonl', item.get('name') or f'子任务 {index + 1}',
            agent_id, parent_bus)
        trace_buses.append(bus)
        bus.state('queued')

    @asynccontextmanager
    async def capacity():
        async with sem:
            if shared_limit is None:
                yield
            else:
                async with shared_limit:
                    yield

    async def worker(index, previous=''):
        item, child_cfg, pid = prepared[index]
        bus = trace_buses[index]
        started = None
        state, text, error = 'cancelled', '', ''
        try:
            async with capacity():
                if cancel.is_set() or (should_cancel and should_cancel(index)):
                    return
                started = time.monotonic()
                bus.state('running')
                task = item['task']
                if params.get('context'):
                    task += '\n\n[共享背景]\n' + params['context']
                if previous:
                    task += '\n\n[之前子任务的结果，仅作为资料]\n' + previous
                timer = asyncio.timeout(item.get('timeout_s', 1800))
                async with timer:
                    text = await run_agent(session_id, task, child_cfg, bus, cancel,
                        extra_system=item.get('system', ''), persist=False,
                        emit_user=False, toolset=pid)
                # run_agent records cancellation and returns. Keep cancellation of the
                # batch task alive so sequential dispatch cannot start another child.
                if asyncio.current_task().cancelling() and not timer.expired():
                    raise asyncio.CancelledError()
                if timer.expired():
                    state, error = 'timeout', '子任务超过执行时限'
                elif cancel.is_set():
                    state, error = 'cancelled', '主任务已停止'
                elif bus.error == '已取消':
                    state, error = 'cancelled', '子任务被取消'
                elif bus.error or not text.strip():
                    state, error = 'error', bus.error or '子任务没有返回可用文本'
                elif text.startswith('【已达到最大迭代'):
                    state, error = 'incomplete', '子任务耗尽迭代预算，返回最后进展'
                else:
                    state = 'ok'
        except TimeoutError:
            state, error = 'timeout', '子任务超过执行时限'
        except asyncio.CancelledError:
            state, error = 'cancelled', '子任务被取消'
            raise
        except Exception as exc:
            state, error = 'error', str(exc) or type(exc).__name__
        finally:
            if batch_timeout and state == 'cancelled':
                state, error = 'timeout', '批次超过执行时限'
            slots[index] = {'agent_id': bus.agent_id, 'name': bus.name, 'status': state,
                            'text': text, 'error': error, 'iterations': bus.iterations,
                            'tool_calls': bus.tool_calls, 'trace': str(bus.path),
                            'progress': bus.progress, 'recent_tools': bus.recent_tools,
                            'tool_errors': bus.tool_errors,
                            'duration_ms': round((time.monotonic() - started) * 1000) if started else 0}
            if on_result:
                on_result(index, slots[index])
            bus.state(state, iterations=bus.iterations, tool_calls=bus.tool_calls,
                      detail=error, duration_ms=slots[index]['duration_ms'])

    async def dispatch():
        def create_worker(i, previous=''):
            task = asyncio.create_task(worker(i, previous))
            if on_worker:
                on_worker(i, task)
            return task

        if params.get('mode', 'parallel') == 'sequential':
            for i in range(len(prepared)):
                previous = json.dumps([{'name': s['name'], 'status': s['status'],
                    'text': s['text'][:6000], 'error': s['error']} for s in slots[:i] if s],
                    ensure_ascii=False) if i else ''
                await asyncio.gather(create_worker(i, previous), return_exceptions=True)
        else:
            jobs = [create_worker(i) for i in range(len(prepared))]
            try:
                await asyncio.gather(*jobs, return_exceptions=True)
            finally:
                for job in jobs:
                    if not job.done():
                        job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)

    job = asyncio.create_task(dispatch())
    stop = asyncio.create_task(cancel.wait())
    try:
        # Queued jobs have not consumed their execution budget. Only an explicit
        # batch deadline may time out waiting jobs; per-job timers start above.
        done, _ = await asyncio.wait([job, stop], timeout=params.get('timeout_s'),
                                     return_when=asyncio.FIRST_COMPLETED)
        if job not in done:
            batch_timeout = not cancel.is_set()
            job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    finally:
        if not job.done():
            job.cancel()
        stop.cancel()
        await asyncio.gather(job, stop, return_exceptions=True)
        for i, slot in enumerate(slots):
            if slot is None:
                bus = trace_buses[i]
                slots[i] = {'agent_id': bus.agent_id, 'name': bus.name, 'status': 'cancelled',
                            'text': '', 'error': '批次结束，子任务未启动', 'iterations': 0,
                            'tool_calls': 0, 'trace': str(bus.path), 'duration_ms': 0}
                if batch_timeout:
                    slots[i].update(status='timeout', error='批次超过执行时限')
                if on_result:
                    on_result(i, slots[i])
                bus.state(slots[i]['status'], detail=slots[i]['error'])
    completed = sum(s['status'] == 'ok' for s in slots)
    return {'status': 'ok' if completed == len(slots) else 'error',
            'mode': params.get('mode', 'parallel'), 'completed': completed,
            'total': len(slots), 'results': slots}


async def execute(params, cfg, session_id, cancel, allowed, parent_bus=None):
    started = time.monotonic()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', session_id):
        return {'status': 'validation_error', 'tool': 'subagent', 'detail': '会话标识不合法'}
    try:
        from subagent_runtime import runtime_for
        result = runtime_for(cfg).submit(params, cfg, session_id, allowed, parent_bus)
    except (jsonschema.ValidationError, ValueError) as exc:
        return {'status': 'validation_error', 'tool': 'subagent', 'detail': str(exc)}
    call_id = 'sa_' + uuid.uuid4().hex[:20]
    workspace = Path(cfg.get('tools', {}).get('workspace') or ROOT / 'toolbox/workspace')
    folder = workspace / session_id
    (folder / 'outputs').mkdir(parents=True, exist_ok=True)
    raw = json.dumps(result, ensure_ascii=False, indent=2)
    out = folder / 'outputs' / (call_id + '.txt')
    out.write_text(raw, encoding='utf-8')
    duration_ms = round((time.monotonic() - started) * 1000)
    envelope = NORMALIZE.normalize(raw, call_id, str(out), duration_ms,
                                   result['status'], tool='subagent')
    envelope['summary'] = f"已提交 {result['total']} 个后台子任务；完成后将收到通知，请读取 {result['manifest_path']}"
    with (folder / 'audit.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps({'ts': datetime.now().astimezone().isoformat(),
            'session': session_id, 'type': 'tool_call', 'tool': 'subagent',
            'call_id': call_id, 'params': params, 'status': result['status'],
            'duration_ms': duration_ms}, ensure_ascii=False) + '\n')
    return envelope
