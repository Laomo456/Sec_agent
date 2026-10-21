"""Offline attack-surface ledger. All mutations commit one locked JSON document."""
import copy
import hashlib
import ipaddress
import json
import os
import re
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DELTA = ('NEW', 'CHANGED', 'UNCHANGED', 'STALE', 'INVALID')
TASK = ('PENDING', 'RUNNING', 'DONE', 'BLOCKED', 'TIMEOUT', 'SKIPPED')
CONFIRM = ('CONFIRMED', 'NEGATIVE', 'CANDIDATE', 'NEEDS_AUTH', 'INCOMPLETE')
FAMILIES = {'web_api': 'Web/API', 'management': '管理监控', 'files': '文件配置',
            'cloud': '云存储', 'middleware': '中间件', 'container': '容器 CI/CD',
            'remote': '远程访问/数据库', 'dns': 'DNS/证书/邮件', 'js': '前端 JS'}
TYPES = {'web': 'web_api', 'api': 'web_api', 'api-doc': 'web_api',
         'admin': 'management', 'file': 'files', 'cloud': 'cloud',
         'middleware': 'middleware', 'container': 'container', 'remote': 'remote',
         'dns': 'dns', 'js': 'js'}
PORTS = {'http': 80, 'https': 443, 'dns': 53, 'mqtt': 1883, 'mqtts': 8883,
         'ssh': 22, 'redis': 6379, 'mysql': 3306, 'postgres': 5432}
PHASES = ('ENUMERATE', 'CLUSTER', 'DISPATCH', 'WAIT', 'REVIEW', 'COMMIT', 'SUMMARIZE', 'COMPLETE')
NEXT = {'ENUMERATE': {'CLUSTER'}, 'CLUSTER': {'DISPATCH'}, 'DISPATCH': {'WAIT', 'REVIEW'},
        'WAIT': {'REVIEW'}, 'REVIEW': {'DISPATCH', 'COMMIT'}, 'COMMIT': {'SUMMARIZE', 'REVIEW'},
        'SUMMARIZE': {'COMPLETE', 'REVIEW'}, 'COMPLETE': set()}
ACTIVE = {'CLAIMED', 'SUBMITTED'}
READ_OPS = {'status', 'list'}
OPS = {'init', 'status', 'list', 'upsert', 'claim', 'bind', 'release', 'report',
       'review', 'mark', 'chain', 'advance', 'new_round', 'export'}
FACTS = ('ip', 'cname', 'asn', 'tech', 'auth_state', 'exposure', 'cdn', 'waf', 'evidence')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                          separators=(',', ':'), allow_nan=False)
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(text, encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def enum(value, values, name):
    if value not in values:
        raise ValueError(f'{name} 必须为 {", ".join(values)}')
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} 必须是非空文本')
    return value.strip()


def integer(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} 必须为 {low}..{high} 的整数')
    return value


def array(value, name):
    if not isinstance(value, list):
        raise ValueError(f'{name} 必须为数组')
    return value


def host_name(host):
    host = text(host, 'asset').rstrip('.').lower()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        host = host.encode('idna').decode('ascii')
        if len(host) > 253 or any(not re.fullmatch(r'[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?', x)
                                  for x in host.split('.')):
            raise ValueError('asset 不是合法主机名或 IP')
        return host


def identity(item):
    if not isinstance(item, dict):
        raise ValueError('surface 必须为对象')
    kind = enum(item.get('type'), TYPES, 'type')
    raw = item.get('entry')
    if not raw:
        proto = text(item.get('proto'), 'proto').lower()
        host = host_name(item.get('asset'))
        port = item.get('port', PORTS.get(proto))
        host = f'[{host}]' if ':' in host else host
        raw = f'{proto}://{host}:{port}'
    parsed = urlsplit(text(raw, 'entry'))
    if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('entry 必须是 scheme://host[:port]/path，不能含认证信息')
    host = host_name(parsed.hostname)
    proto = parsed.scheme.lower()
    port = integer(parsed.port if parsed.port is not None else PORTS.get(proto, item.get('port')),
                   1, 65535, 'port')
    if item.get('asset') and host_name(item['asset']) != host:
        raise ValueError('asset 与 entry 主机不一致')
    if item.get('port') is not None and item['port'] != port:
        raise ValueError('port 与 entry 不一致')
    if item.get('proto') and item['proto'].lower() != proto:
        raise ValueError('proto 与 entry 不一致')
    path = parsed.path or '/'
    resource = path + ('?' + parsed.query if parsed.query else '')
    authority = f'[{host}]' if ':' in host else host
    if PORTS.get(proto) != port:
        authority += f':{port}'
    entry = urlunsplit((proto, authority, path, parsed.query, ''))
    aid = 'A-' + digest(host)
    svc = f'SVC-{aid}-{port}-{proto}'
    eid = f'E-{svc}-{digest(resource)}'
    ids = {'asset_id': aid, 'service_id': svc, 'entry_id': eid, 'surface_id': f'SF-{eid}-{kind}'}
    for key, val in ids.items():
        if item.get(key) and item[key] != val:
            raise ValueError(f'{key} 与集中生成的 ID 不一致')
    family = enum(item.get('family', TYPES[kind]), FAMILIES, 'family')
    return {**ids, 'asset': host, 'entry': entry, 'proto': proto, 'port': port,
            'type': kind, 'family': family}


def fingerprint(item):
    facts = {k: item[k] for k in FACTS if k in item}
    if isinstance(facts.get('evidence'), dict):
        facts['evidence'] = {k: v for k, v in facts['evidence'].items()
                             if k not in ('timestamp', 'observed_at', 'evidence_id')}
    if isinstance(facts.get('tech'), list):
        facts['tech'] = sorted(facts['tech'])
    return digest(facts)


class Ledger:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'recon/state.json'

    def local(self, value):
        path = Path(text(value, 'path'))
        path = (self.root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('recon_state 的文件路径必须在当前会话 files 内')
        return path

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a+b') as lock:
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    lock.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError('状态文件忙，请稍后重试')
                    time.sleep(.025)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def call(self, op, params=None):
        enum(op, OPS, 'op')
        p = copy.deepcopy(params or {})
        if not isinstance(p, dict):
            raise ValueError('params 必须为对象')
        with self.locked():
            s = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else None
            if s is not None and s.get('schema_version') != 1:
                raise ValueError('未知状态版本，不修改文件')
            if op == 'init':
                target = text(p.get('target'), 'target')
                if s:
                    if s['target'] != target:
                        raise ValueError('本会话已初始化其他 target，请使用新会话')
                    return self.summary(s)
                scope = p.get('scope', {})
                if not isinstance(scope, dict):
                    raise ValueError('scope 必须为对象（仅记录背景，不执行授权控制）')
                s = {'schema_version': 1, 'id_version': 1, 'target': target, 'scope': scope,
                     'round': 1, 'phase': 'ENUMERATE', 'version': 0, 'surfaces': {}, 'evidence': {},
                     'tasks': {}, 'reports': {}, 'findings': {}, 'chains': {}, 'negatives': [],
                     'next_directions': [], 'blocked_by': [], 'conflicts': [], 'history': [],
                     'next_task': 1, 'created_at': now()}
                result = {'initialized': True}
            else:
                if s is None:
                    if op == 'status':
                        return {'initialized': False, 'next_actions': ['init(target, scope?)']}
                    raise ValueError('请先 init(target)')
                if s['phase'] == 'COMPLETE' and op not in ('status', 'list', 'export', 'new_round', 'report'):
                    raise ValueError('本轮已收口，请 new_round 后继续修改')
                result = getattr(self, 'op_' + op)(s, p)
            if op not in READ_OPS:
                if result.get('duplicate') or result.get('unchanged') or result.get('claimed') is False:
                    return {**result, 'version': s['version'], 'phase': s['phase'], 'round': s['round']}
                s['version'] += 1
                s['updated_at'] = now()
                s['history'].append({'op': op, 'version': s['version'], 'round': s['round'], 'at': now()})
                atomic(self.path, json.dumps(s, ensure_ascii=False, indent=2, allow_nan=False))
                # Derived artifacts are recoverable. The committed ledger is authoritative.
                if op in ('report', 'export') or (op == 'advance' and s['phase'] == 'COMPLETE'):
                    self.materialize(s)
            return {**result, 'version': s['version'], 'phase': s['phase'], 'round': s['round']}

    def payload(self, p, inline, file_key):
        if inline in p and file_key in p:
            raise ValueError(f'{inline} 与 {file_key} 二选一')
        if file_key in p:
            path = self.local(p[file_key])
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('输入文件超过 16MB')
            return json.loads(path.read_text(encoding='utf-8-sig'))
        if inline not in p:
            raise ValueError(f'需要 {inline} 或 {file_key}')
        return p[inline]

    def summary(self, s):
        surfaces = list(s['surfaces'].values())
        retry = [x['surface_id'] for x in surfaces if self.retryable(s, x)]
        pending = [x['surface_id'] for x in surfaces if self.pending(s, x)]
        review = [x['surface_id'] for x in surfaces if self.needs_review(x)]
        live = {}
        manifest = self.root / 'subagents/manifest.json'
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding='utf-8'))
            live = {j['agent_id']: j for j in data.get('jobs', [])}
        tasks = []
        for task in s['tasks'].values():
            item = {k: task[k] for k in ('task_id', 'status', 'surface_ids', 'report_json', 'report_path')}
            if task.get('agent_id'):
                item['agent_id'] = task['agent_id']
                item['job_status'] = live.get(task['agent_id'], {}).get('status', 'unknown')
                item['runtime_report_path'] = live.get(task['agent_id'], {}).get('report_path')
            tasks.append(item)
        actions = []
        if pending:
            actions.append('claim(queue=new)，再调用 subagent；提交成功后 bind')
        if any(t['status'] in ACTIVE for t in tasks):
            actions.append('等待现有子代理完成通知；完成后 report(path=机器报告路径)，缺产物用 release')
        if review:
            actions.append('主智能体复核 P0/P1 或冲突项后 review（附独立证据）')
        if retry:
            actions.append('检查已有报告/脚本后 claim(queue=retry)，受 max_attempts 限制')
        if not actions:
            actions.append('检查待授权/阻塞项；advance 推进阶段，export 导出汇总，再用 wpbox 入库')
        return {'initialized': True, 'target': s['target'], 'scope': s['scope'],
                'phase': s['phase'], 'round': s['round'], 'version': s['version'],
                'counts': {k: dict(Counter(x[k] for x in surfaces))
                           for k in ('delta_state', 'task_state', 'confirm_state')},
                'queues': {'new': pending, 'retry': retry, 'review': review,
                           'needs_auth': [x['surface_id'] for x in surfaces if x['confirm_state'] == 'NEEDS_AUTH'],
                           'blocked': [x['surface_id'] for x in surfaces if x['task_state'] == 'BLOCKED']},
                'tasks': tasks, 'next_actions': actions, 'state_path': 'recon/state.json'}

    def assigned(self, s, sid):
        return any(t['status'] in ACTIVE and sid in t['surface_ids'] for t in s['tasks'].values())

    def pending(self, s, item):
        return item['delta_state'] in ('NEW', 'CHANGED') and item['task_state'] == 'PENDING' \
            and item['confirm_state'] != 'NEEDS_AUTH' and not self.assigned(s, item['surface_id'])

    def retryable(self, s, item):
        return (item['task_state'] == 'TIMEOUT' or item['confirm_state'] == 'INCOMPLETE'
                or (item['task_state'] == 'PENDING' and item['delta_state'] == 'UNCHANGED')) \
            and item['delta_state'] not in ('INVALID', 'STALE') \
            and item['task_state'] != 'SKIPPED' and item['confirm_state'] != 'NEEDS_AUTH' \
            and not self.assigned(s, item['surface_id'])

    @staticmethod
    def needs_review(item):
        return (item.get('priority') in ('P0', 'P1') or item.get('has_conflict')) \
            and item['task_state'] == 'DONE' and item['confirm_state'] in ('CONFIRMED', 'NEGATIVE') \
            and not item.get('review')

    def op_status(self, s, p):
        return self.summary(s)

    def op_list(self, s, p):
        rows = list(s['surfaces'].values())
        for field, values in (('delta_state', DELTA), ('task_state', TASK), ('confirm_state', CONFIRM),
                              ('family', FAMILIES), ('priority', ('P0', 'P1', 'P2', 'P3'))):
            if field in p:
                wanted = enum(p[field], values, field)
                rows = [x for x in rows if x[field] == wanted]
        if p.get('surface_ids'):
            ids = self.ids(s, p['surface_ids'])
            rows = [x for x in rows if x['surface_id'] in ids]
        rows.sort(key=lambda x: (x['priority'], x['surface_id']))
        offset = integer(p.get('offset', 0), 0, 10000000, 'offset')
        limit = integer(p.get('limit', 50), 1, 500, 'limit')
        return {'total': len(rows), 'items': rows[offset:offset + limit]}

    @staticmethod
    def ids(s, values):
        values = array(values, 'surface_ids')
        if not values or len(set(values)) != len(values):
            raise ValueError('surface_ids 需非空且不能重复')
        for value in values:
            if value not in s['surfaces']:
                raise ValueError(f'未知 surface_id: {value}')
        return values

    @staticmethod
    def evidence(s, payload, source):
        if not isinstance(payload, dict) or not payload:
            raise ValueError('evidence 必须为非空对象')
        clean = {k: v for k, v in payload.items() if k not in ('timestamp', 'observed_at', 'evidence_id')}
        if not clean:
            raise ValueError('evidence 不能只有时间戳')
        eid = 'EV-' + digest(clean)
        if payload.get('evidence_id') and payload['evidence_id'] != eid:
            raise ValueError('evidence_id 不匹配')
        s['evidence'].setdefault(eid, {'evidence_id': eid, 'payload': clean, 'first_seen': now(), 'sources': []})
        if source not in s['evidence'][eid]['sources']:
            s['evidence'][eid]['sources'].append(source)
        return eid

    def upsert(self, s, raw):
        base = identity(raw)
        sid = base['surface_id']
        old = s['surfaces'].get(sid)
        item = copy.deepcopy(old) if old else {**base, 'revision': 1, 'delta_state': 'NEW',
            'task_state': 'PENDING', 'confirm_state': 'CANDIDATE', 'auth_state': 'unknown',
            'exposure': 'public', 'tech': [], 'priority': 'P3', 'evidence_ids': [], 'attempts': 0,
            'first_seen': now(), 'next_direction': ''}
        for field in (*FACTS, 'priority', 'next_direction', 'family'):
            if field in raw:
                item[field] = raw[field]
        enum(item['priority'], ('P0', 'P1', 'P2', 'P3'), 'priority')
        enum(item['auth_state'], ('anonymous', 'authenticated', 'unknown'), 'auth_state')
        enum(item['exposure'], ('public', 'internal', 'cdn', 'waf'), 'exposure')
        enum(item['family'], FAMILIES, 'family')
        if not isinstance(item['tech'], list) or any(not isinstance(v, str) for v in item['tech']):
            raise ValueError('tech 必须为字符串数组')
        if 'evidence' in raw and not isinstance(raw['evidence'], dict):
            raise ValueError('evidence 必须为对象')
        if raw.get('evidence'):
            eid = self.evidence(s, raw['evidence'], 'discovery')
            item['evidence_ids'] = sorted(set(item['evidence_ids']) | {eid})
        fp = fingerprint(item)
        if old and fingerprint(old) != fp:
            if self.assigned(s, sid):
                raise ValueError('条目仍在已认领任务中，先接收报告或 release 后再修改事实')
            item.update(revision=old['revision'] + 1, task_state='PENDING', confirm_state='CANDIDATE')
            item.pop('review', None)
            for finding in s['findings'].values():
                if finding['surface_id'] == sid:
                    finding.update(reviewed=False, confirm_state='CANDIDATE')
        baseline = item.get('baseline_fingerprint')
        if baseline is not None:
            item['delta_state'] = 'UNCHANGED' if fp == baseline else 'CHANGED'
        elif old is None:
            item['delta_state'] = 'NEW'
        item['last_seen_round'] = s['round']
        item['last_seen'] = now()
        s['surfaces'][sid] = item
        return item

    def op_upsert(self, s, p):
        items = array(self.payload(p, 'items', 'path'), 'items')
        if not items:
            raise ValueError('items 不能为空')
        result = [self.upsert(s, item) for item in items]
        return {'items': [{k: x[k] for k in ('surface_id', 'entry_id', 'entry', 'delta_state', 'task_state')}
                          for x in result]}

    def op_claim(self, s, p):
        queue = enum(p.get('queue', 'new'), ('new', 'retry'), 'queue')
        size = integer(p.get('limit', 6), 1, 6, 'limit')
        cap = integer(p.get('max_attempts', 3), 1, 20, 'max_attempts')
        candidates = [x for x in s['surfaces'].values() if (self.pending(s, x) if queue == 'new'
                       else self.retryable(s, x)) and x['attempts'] < cap]
        if p.get('family'):
            family = enum(p['family'], FAMILIES, 'family')
            candidates = [x for x in candidates if x['family'] == family]
        candidates.sort(key=lambda x: (x['priority'], x['family'], x['surface_id']))
        selected = candidates[:size]
        if not selected:
            return {'claimed': False, 'reason': '没有满足条件的条目；检查 max_attempts 或待授权/阻塞项'}
        # Keep a task in one family, so probes can be reused.
        selected = [x for x in selected if x['family'] == selected[0]['family']]
        tid = 'E' + str(s['next_task'])
        s['next_task'] += 1
        task = {'task_id': tid, 'round': s['round'], 'status': 'CLAIMED', 'queue': queue,
                'surface_ids': [x['surface_id'] for x in selected], 'created_at': now(),
                'report_json': f'recon/batch/{tid}.json', 'report_path': f'recon/batch/{tid}.md'}
        s['tasks'][tid] = task
        for x in selected:
            x['task_state'] = 'PENDING'
            x['attempts'] += 1
            x['owner_task_id'] = tid
        return {'claimed': True, **task, 'surface_items': selected, 'scope': s['scope'],
                'system': '你是攻击面探测子代理。仅按任务范围做 L0/L1，禁止 wpbox、修改汇总或他人文件。'
                          '不调用 recon_state；结果写入指定独立 JSON/MD 报告，不自行拼接 ID。',
                'next': '自行调用 subagent 派发此任务；拿到回执后 bind。未派发成功时 release。'}

    @staticmethod
    def task(s, p):
        tid = text(p.get('task_id'), 'task_id')
        if tid not in s['tasks']:
            raise ValueError('未知 task_id')
        return s['tasks'][tid]

    def op_bind(self, s, p):
        task = self.task(s, p)
        if task['status'] not in ACTIVE:
            raise ValueError('任务已结束，不能绑定')
        aid = text(p.get('agent_id'), 'agent_id')
        bid = text(p.get('batch_id'), 'batch_id')
        if task.get('agent_id') and (task['agent_id'], task['batch_id']) != (aid, bid):
            raise ValueError('任务已有其他子代理绑定')
        task.update(agent_id=aid, batch_id=bid, status='SUBMITTED')
        for sid in task['surface_ids']:
            s['surfaces'][sid]['task_state'] = 'RUNNING'
        return {'task': task}

    def op_release(self, s, p):
        task = self.task(s, p)
        if task['status'] not in ACTIVE:
            raise ValueError('任务已结束，不能 release；补测请重新 claim')
        reason = text(p.get('reason'), 'reason')
        state = enum(p.get('task_state', 'BLOCKED'), ('PENDING', 'BLOCKED', 'TIMEOUT', 'SKIPPED'), 'task_state')
        task.update(status='RELEASED', reason=reason, finished_at=now())
        for sid in task['surface_ids']:
            item = s['surfaces'][sid]
            item.update(task_state=state, blocked_reason=reason)
            if state in ('TIMEOUT', 'BLOCKED'):
                item['confirm_state'] = 'INCOMPLETE'
        return {'task': task}

    def op_report(self, s, p):
        task = self.task(s, p)
        report = self.payload(p, 'data', 'path')
        if not isinstance(report, dict) or report.get('task_id') != task['task_id']:
            raise ValueError('报告 task_id 不匹配')
        for key in ('surfaces', 'findings', 'negatives', 'next_directions', 'blocked_by'):
            if key not in report:
                raise ValueError(f'报告缺少 {key} 数组')
            array(report[key], key)
        if not isinstance(report.get('timeout'), bool):
            raise ValueError('报告 timeout 必须为布尔值')
        h = digest(report)
        if task.get('report_digest') == h or (task['task_id'] in s['reports'] and digest(s['reports'][task['task_id']]) == h):
            self.materialize(s)
            return {'duplicate': True, 'task_id': task['task_id']}
        if s['phase'] == 'COMPLETE':
            raise ValueError('本轮已收口，只允许重复提交原报告；修改请 new_round')
        if task['status'] == 'REPORTED':
            raise ValueError('此任务已提交不同报告；保留历史，补测请新建任务')
        if task['round'] != s['round']:
            raise ValueError('旧轮次的迟到报告不能覆盖当前轮次')
        if any(s['surfaces'][sid].get('owner_task_id') != task['task_id'] for sid in task['surface_ids']):
            raise ValueError('条目已分配给更新的任务，迟到报告不能覆盖')
        seen = set()
        for raw in report['surfaces']:
            if not isinstance(raw, dict):
                raise ValueError('surfaces 项必须为对象')
            sid = raw.get('surface_id')
            if sid not in task['surface_ids'] or sid in seen:
                raise ValueError('报告含未分配或重复的 surface_id；新发现请另用 upsert')
            seen.add(sid)
            item = s['surfaces'][sid]
            previous_fingerprint = fingerprint(item)
            # Identity is assigned centrally; observations cannot retarget it.
            for field in ('asset_id', 'service_id', 'entry_id', 'entry', 'type'):
                if field in raw and raw[field] != item[field]:
                    raise ValueError(f'报告 {field} 与分配项不一致')
            state = enum(raw.get('confirm_state'), CONFIRM, 'confirm_state')
            work = enum(raw.get('task_state', 'DONE'), TASK, 'task_state')
            if work not in ('DONE', 'BLOCKED', 'TIMEOUT', 'SKIPPED'):
                raise ValueError('报告条目必须是已结束的任务状态')
            if state in ('CONFIRMED', 'NEGATIVE') and work != 'DONE':
                raise ValueError('确认/负结论需要 task_state=DONE')
            if state in ('CONFIRMED', 'NEGATIVE') and not raw.get('evidence'):
                raise ValueError('确认/负结论必须有 evidence')
            if 'evidence' in raw and not isinstance(raw['evidence'], dict):
                raise ValueError('evidence 必须为对象')
            prior = item['confirm_state']
            if prior in ('CONFIRMED', 'NEGATIVE') and state in ('CONFIRMED', 'NEGATIVE') and prior != state:
                s['conflicts'].append({'surface_id': sid, 'task_id': task['task_id'],
                                       'previous': prior, 'incoming': state, 'at': now()})
                item['has_conflict'] = True
            item.pop('review', None)
            if raw.get('evidence'):
                eid = self.evidence(s, raw['evidence'], task['task_id'])
                item['evidence_ids'] = sorted(set(item['evidence_ids']) | {eid})
            for field in (*FACTS, 'priority', 'next_direction'):
                if field in raw:
                    item[field] = raw[field]
            enum(item['priority'], ('P0', 'P1', 'P2', 'P3'), 'priority')
            enum(item['auth_state'], ('anonymous', 'authenticated', 'unknown'), 'auth_state')
            enum(item['exposure'], ('public', 'internal', 'cdn', 'waf'), 'exposure')
            if not isinstance(item['tech'], list) or any(not isinstance(v, str) for v in item['tech']):
                raise ValueError('tech 必须为字符串数组')
            if fingerprint(item) != previous_fingerprint:
                item['revision'] += 1
                if 'baseline_fingerprint' in item:
                    item['delta_state'] = 'UNCHANGED' if fingerprint(item) == item['baseline_fingerprint'] else 'CHANGED'
            item.update(task_state=work, confirm_state=state, last_task_id=task['task_id'])
            if state == 'NEGATIVE':
                s['negatives'].append({'surface_id': sid, 'family': item['family'], 'entry': item['entry'],
                    'evidence_ids': item['evidence_ids'][:], 'task_id': task['task_id'], 'round': s['round']})
            for finding in s['findings'].values():
                if finding['surface_id'] == sid:
                    finding.update(reviewed=False, confirm_state='CANDIDATE')
        missing = set(task['surface_ids']) - seen
        for sid in missing:
            s['surfaces'][sid].update(task_state='TIMEOUT' if report['timeout'] else 'BLOCKED',
                                     confirm_state='INCOMPLETE', last_task_id=task['task_id'])
        for raw in report['findings']:
            if not isinstance(raw, dict) or raw.get('surface_id') not in seen:
                raise ValueError('finding 必须引用本报告中的 surface_id')
            title = text(raw.get('title'), 'finding.title')
            severity = enum(raw.get('severity'), ('info', 'low', 'medium', 'high', 'critical'), 'severity')
            fid = 'F-' + digest(raw['surface_id'] + title)
            if raw.get('finding_id') and raw['finding_id'] != fid:
                raise ValueError('finding_id 不匹配')
            finding = {**raw, 'finding_id': fid, 'severity': severity, 'reviewed': False,
                       'confirm_state': 'CANDIDATE', 'task_id': task['task_id']}
            s['findings'][fid] = finding
        for key in ('negatives', 'next_directions', 'blocked_by'):
            for value in report[key]:
                if value not in s[key]:
                    s[key].append(value)
        task.update(status='REPORTED', report_digest=h, finished_at=now(), missing=sorted(missing),
                    timeout=report['timeout'])
        canonical = copy.deepcopy(report)
        canonical['surfaces'] = [copy.deepcopy(s['surfaces'][sid]) for sid in sorted(seen)]
        canonical['findings'] = [copy.deepcopy(x) for x in s['findings'].values() if x['task_id'] == task['task_id']]
        canonical.setdefault('timestamp', now())
        canonical.setdefault('tool_versions', {})
        canonical.setdefault('scope', s['scope'])
        s['reports'][task['task_id']] = canonical
        return {'task_id': task['task_id'], 'accepted': len(seen), 'missing': sorted(missing),
                'report_json': task['report_json'], 'report_path': task['report_path']}

    def op_review(self, s, p):
        ids = self.ids(s, p.get('surface_ids'))
        note = text(p.get('note'), 'note')
        reviewer = text(p.get('reviewer', 'main'), 'reviewer')
        replay = text(p.get('replay_call_id'), 'replay_call_id（主代理重放的调用记录）')
        state = enum(p.get('confirm_state', 'CONFIRMED'), CONFIRM, 'confirm_state')
        eid = self.evidence(s, p.get('evidence'), f'review:{reviewer}')
        for sid in ids:
            item = s['surfaces'][sid]
            if self.assigned(s, sid):
                raise ValueError('条目仍在子任务中，先接收报告或 release')
            if eid not in item['evidence_ids']:
                item['evidence_ids'].append(eid)
            item.update(confirm_state=state, task_state='DONE' if state != 'INCOMPLETE' else 'BLOCKED',
                        review={'reviewer': reviewer, 'evidence_id': eid, 'replay_call_id': replay,
                                'note': note, 'at': now()},
                        has_conflict=False)
        for fid in p.get('finding_ids', []):
            if fid not in s['findings'] or s['findings'][fid]['surface_id'] not in ids:
                raise ValueError('finding_ids 必须引用本次复核的攻击面')
            s['findings'][fid].update(reviewed=True, confirm_state=state, review_evidence_id=eid)
        return {'reviewed': ids, 'evidence_id': eid}

    def op_mark(self, s, p):
        ids = self.ids(s, p.get('surface_ids'))
        reason = text(p.get('note'), 'note')
        if not any(k in p for k in ('delta_state', 'task_state', 'confirm_state')):
            raise ValueError('至少提供一个状态轴')
        for sid in ids:
            item = s['surfaces'][sid]
            if self.assigned(s, sid):
                raise ValueError('条目仍在已领取任务中，请先 report/release')
            for key, values in (('delta_state', ('STALE', 'INVALID')),
                                 ('task_state', ('PENDING', 'BLOCKED', 'TIMEOUT', 'SKIPPED')),
                                 ('confirm_state', ('CANDIDATE', 'NEEDS_AUTH', 'INCOMPLETE'))):
                if key in p:
                    item[key] = enum(p[key], values, key)
            item['state_note'] = reason
            if 'confirm_state' in p:
                item.pop('review', None)
        return {'marked': ids}

    def op_chain(self, s, p):
        data = self.payload(p, 'data', 'path')
        if not isinstance(data, dict):
            raise ValueError('chain data 必须为对象')
        entries = {x['entry_id'] for x in s['surfaces'].values()}
        for key in ('entry_id', 'next_entry_id'):
            if data.get(key) not in entries:
                raise ValueError(f'{key} 必须引用已有入口')
        if data.get('evidence_id') not in s['evidence']:
            raise ValueError('chain evidence_id 不存在')
        for key in ('precondition', 'verify_action', 'impact'):
            text(data.get(key), key)
        enum(data.get('confidence'), ('high', 'medium', 'low'), 'confidence')
        enum(data.get('status', 'proposed'), ('proposed', 'reviewed', 'approved', 'blocked'), 'status')
        data.setdefault('status', 'proposed')
        if not isinstance(data.get('authorization_required'), bool):
            raise ValueError('authorization_required 必须为布尔值')
        cid = 'C-' + digest(data['entry_id'] + data['next_entry_id'])
        if data.get('chain_id') and data['chain_id'] != cid:
            raise ValueError('chain_id 不匹配')
        s['chains'][cid] = {**data, 'chain_id': cid, 'updated_at': now()}
        return {'chain_id': cid, 'note': '只记录关系和建议，不执行链式验证'}

    def op_advance(self, s, p):
        phase = enum(p.get('phase'), PHASES, 'phase')
        if phase == s['phase']:
            return {'unchanged': True}
        if phase not in NEXT[s['phase']]:
            raise ValueError(f'当前 {s["phase"]} 可推进到 {", ".join(sorted(NEXT[s["phase"]])) or "new_round"}')
        if phase in ('COMMIT', 'SUMMARIZE', 'COMPLETE') and any(t['status'] in ACTIVE for t in s['tasks'].values()):
            raise ValueError('仍有已认领/已派发任务，先 report 或 release')
        if phase in ('COMMIT', 'COMPLETE') and any(self.needs_review(x) for x in s['surfaces'].values()):
            raise ValueError('仍有 P0/P1 或冲突项待主智能体复核')
        if phase == 'COMPLETE':
            if s.get('export_version') != s['version']:
                raise ValueError('先 export 当前汇总，再推进 COMPLETE')
            unresolved = [x for x in s['surfaces'].values() if x['task_state'] in ('PENDING', 'RUNNING', 'TIMEOUT', 'BLOCKED')
                          or x['confirm_state'] in ('INCOMPLETE', 'NEEDS_AUTH', 'CANDIDATE')]
            if unresolved and not p.get('note'):
                raise ValueError('存在未完成/待授权项，请以 note 明确部分完成原因')
            s['completion_note'] = p.get('note', '')
            s['export_version'] = s['version'] + 1
        s['phase'] = phase
        return {'phase': phase}

    def op_new_round(self, s, p):
        if any(t['status'] in ACTIVE for t in s['tasks'].values()):
            raise ValueError('仍有已认领/派发任务，先 report 或 release')
        for sid in p.get('stale_ids', []):
            self.ids(s, [sid])
        s['round'] += 1
        s['phase'] = 'ENUMERATE'
        for item in s['surfaces'].values():
            item['baseline_fingerprint'] = fingerprint(item)
            if item['delta_state'] not in ('INVALID', 'STALE'):
                item['delta_state'] = 'UNCHANGED'
            item['attempts'] = 0
        for sid in p.get('stale_ids', []):
            s['surfaces'][sid]['delta_state'] = 'STALE'
        return {'round': s['round'], 'note': '未观测对象不自动 STALE；旧超时/不完整仍可进入补测队列'}

    def aggregate(self, s):
        items = sorted(s['surfaces'].values(), key=lambda x: (x['priority'], x['surface_id']))
        return {'schema_version': 1, 'version': s['version'], 'target': s['target'],
                'scope': s['scope'], 'round': s['round'], 'phase': s['phase'],
                'surfaces': items, 'findings': sorted(s['findings'].values(), key=lambda x:
                    ('critical', 'high', 'medium', 'low', 'info').index(x['severity'])),
                'evidence': list(s['evidence'].values()), 'negatives': s['negatives'],
                'next_directions': s['next_directions'], 'blocked_by': s['blocked_by'],
                'conflicts': s['conflicts'], 'chains': list(s['chains'].values()),
                'queues': self.summary(s)['queues'], 'tasks': list(s['tasks'].values()),
                'completion_note': s.get('completion_note', ''),
                'wpbox': '由主智能体调用现有 wpbox 工具提交，本卡不自动入库'}

    @staticmethod
    def markdown(data):
        def cell(v):
            return str(v).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
        lines = [f'# {data["target"]} 攻击面汇总', '',
                 f'版本：{data["version"]} · 轮次：{data["round"]} · 阶段：{data["phase"]}', '',
                 '| 优先级 | 族 | 入口 | 增量 | 任务 | 确认 |', '| --- | --- | --- | --- | --- | --- |']
        for x in data['surfaces']:
            lines.append('| ' + ' | '.join(cell(v) for v in (x['priority'], FAMILIES[x['family']], x['entry'],
                         x['delta_state'], x['task_state'], x['confirm_state'])) + ' |')
        for key, title in (('findings', '漏洞与候选（仅 reviewed=true 为已复核）'), ('negatives', '负结论'),
                           ('next_directions', '深入方向'), ('blocked_by', '阻塞项'), ('conflicts', '冲突'),
                           ('queues', '队列'), ('chains', '攻击链假设（不自动执行）')):
            lines.extend(['', '## ' + title, '', '```json', json.dumps(data[key], ensure_ascii=False, indent=2), '```'])
        if data.get('completion_note'):
            lines.extend(['', data['completion_note']])
        return '\n'.join(lines) + '\n'

    def materialize(self, s):
        for tid, report in s['reports'].items():
            task = s['tasks'][tid]
            atomic(self.local(task['report_json']), json.dumps(report, ensure_ascii=False, indent=2))
            atomic(self.local(task['report_path']), f'# {tid}\n\n```json\n' +
                   json.dumps(report, ensure_ascii=False, indent=2) + '\n```\n')
        if s.get('export_version') is not None:
            data = self.aggregate(s)
            atomic(self.root / 'AGGREGATE.json', json.dumps(data, ensure_ascii=False, indent=2))
            atomic(self.root / 'AGGREGATE.md', self.markdown(data))

    def op_export(self, s, p):
        # call() increments version after this operation, before materialization.
        s['export_version'] = s['version'] + 1
        return {'json_path': 'AGGREGATE.json', 'md_path': 'AGGREGATE.md',
                'state_path': 'recon/state.json', 'queues': self.summary(s)['queues']}
