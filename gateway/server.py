"""Gateway HTTP/WS 服务。

  POST /api/sessions                    创建会话
  GET  /api/sessions                    会话列表
  WS   /ws/sessions/{sid}               聊天通道：连上先回放历史，再接实时事件
       发送 {"type":"user_message","content":...,"skills":[技能名...]} / {"type":"cancel"}
       skills=对话框下方勾选的技能，注入为 system 通道【skill·name】块（仅当次任务）
  GET  /api/outputs/{sid}/{call_id}     分页查看工具全量输出 ?offset=&limit=&grep=
  POST /api/sessions/{sid}/files       上传文件进会话工作区 files/（multipart，字段 file）
  GET  /api/knowledge/*                知识库(RAG)管理面:状态/配置/文档 CRUD/上传/试搜
"""
import asyncio
import copy
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import config
import prompts as prompt_store
import skill_store
import toolsets
import workflow as wf_engine
from agent import run_agent
from events import SESSIONS_DIR, EventBus, list_sessions
from subagent_runtime import atomic_write, runtime_for


@asynccontextmanager
async def lifespan(app):
    runtime = runtime_for(CFG)
    runtime.closed = False
    runtime.notifier = _deliver_completion
    runtime.recover()
    for sid in list(runtime.manifests):
        if not sid.startswith('wf-') and sid != 'tool-playground' and runtime.pending(sid):
            sess = _get_session(sid, create=True)
            _queue_pending(sess, sid)
            _resume_if_needed(sess, sid)
    yield
    for sess in SESSIONS.values():
        sess['suppress_resume'] = True
        if sess['task'] and not sess['task'].done():
            sess['cancel'].set()
            sess['task'].cancel()
    await asyncio.gather(*(s['task'] for s in SESSIONS.values() if s['task']), return_exceptions=True)
    await runtime.close()

app = FastAPI(title="agent-gateway", lifespan=lifespan)
# 只放行本机前端（生产同源 + vite dev）。监听 0.0.0.0 且无鉴权时，
# 通配 CORS 等于让任意网页驱动本机 agent 执行命令
app.add_middleware(CORSMiddleware,
                   allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
                   allow_methods=["*"], allow_headers=["*"])

CFG = config.load()
SESSIONS: dict = {}   # sid -> {bus, task, cancel, inbox}
DELETING_SESSIONS = set()
RUNS: dict = {}       # run_id -> {bus, task, cancel}
UNPACK_RUN_DIR = Path(__file__).resolve().parent.parent / "toolbox" / "workspace" / "unpack-runs"

# 会话 id 白名单：挡住 ".." 之类的路径穿越（DELETE 会 rmtree 拼出来的路径）
_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_UPLOAD_CHUNK = 1024 * 1024   # 上传流式落盘的块大小
# 上传文件名净化：控制字符 + Windows 保留字符 <>:|?*/\ 一律去掉（CJK/空格/括号保留）
_ILLEGAL_FN = re.compile(r"[\x00-\x1f\x7f<>:|?*\\/]")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


def _check_id(value: str) -> str:
    if not _SID_RE.match(value or ""):
        raise HTTPException(400, "非法 id")
    return value


def _safe_name(raw) -> str:
    """上传文件名净化：取末段、去非法字符、限长保扩展名、避开 Windows 保留名。"""
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1]   # 双分隔符防御
    name = _ILLEGAL_FN.sub("", name).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if dot and 0 < len(ext) <= 10:       # 有像样的扩展名：截 stem 保 ext
        stem = stem[:80 - len(ext) - 1]
    else:
        stem, ext = name[:80], ""
    if stem.upper() in _WIN_RESERVED:
        stem = "_" + stem
    name = f"{stem}.{ext}" if ext else stem
    return name or f"upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _get_session(sid: str, create: bool = False) -> dict | None:
    if sid not in SESSIONS and create:
        SESSIONS[sid] = {"bus": EventBus(sid), "task": None,
                         "cancel": asyncio.Event(), "inbox": [], 'suppress_resume': False,
                         'last_options': {}, 'context_control': {}}
    return SESSIONS.get(sid)


@app.post("/api/sessions")
def create_session():
    sid = "g" + datetime.now().strftime("%Y%m%d-%H%M%S")
    n = 1
    while (SESSIONS_DIR / sid).exists():     # 同一秒连点两次新建不撞 id
        sid = "g" + datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{n}"
        n += 1
    _get_session(sid, create=True)
    return {"session_id": sid}


@app.get("/api/sessions")
def get_sessions():
    return list_sessions()


@app.get('/api/sessions/{sid}/context')
async def session_context(sid: str):
    import context_store
    _check_id(sid)
    if not (SESSIONS_DIR / sid).exists() and sid not in SESSIONS:
        raise HTTPException(404, '对话不存在')
    sess = _get_session(sid, create=True)
    control = sess.setdefault('context_control', {})
    active = sess['task'] and not sess['task'].done()
    stats = control.get('stats') if active else None
    if stats is None:
        workspace = CFG.get('tools', {}).get('workspace')
        stats = context_store.usage(context_store.load(sid, Path(workspace) if workspace else None),
                                    CFG['agent'].get('context_budget_est', context_store.DEFAULT_BUDGET_EST))
    return {**stats, 'pending': bool(sess.get('compacting'))}


@app.post('/api/sessions/{sid}/context/compact')
async def compact_session(sid: str):
    import context_store
    await session_context(sid)  # Validate identity/existence without creating phantom sessions.
    sess = _get_session(sid)
    control = sess.setdefault('context_control', {})
    if sess.get('compacting'):
        return {'pending': True}
    sess['compacting'] = True
    sess['suppress_resume'] = True
    sess['bus'].emit('context_compaction_started')
    try:
        task = sess['task']
        if task and not task.done():
            sess['cancel'].set()
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=15)
            if task not in done:
                raise HTTPException(409, '当前请求仍在退出，尚未压缩；请稍候重试')
            if not task.cancelled() and task.exception():
                raise HTTPException(409, '当前进展保存失败，尚未压缩；请检查后重试')
        workspace = Path(CFG.get('tools', {}).get('workspace') or Path(__file__).resolve().parents[1] / 'toolbox/workspace')
        history = context_store.load(sid, workspace)
        context_store.finalize_dangling(history)
        handoff = await context_store.summarize_history(history, CFG)
        result = context_store.compact_history(sid, history, workspace,
            CFG['agent'].get('context_budget_est', context_store.DEFAULT_BUDGET_EST), handoff=handoff)
        sess['bus'].emit('context_compacted', **result)
        context_store.publish_usage(sess['bus'], history, CFG, control)
        return {**control.get('stats', {}), 'pending': False}
    except Exception as exc:
        sess['bus'].emit('context_compacted', compacted=False,
                         reason=f'压缩未完成：{getattr(exc, "detail", None) or exc}')
        raise
    finally:
        sess['compacting'] = False


@app.get("/api/prompts")
def get_prompts():
    return prompt_store.load()


# ═══════════════ 技能库（Skill 栏） ═══════════════
# 技能 = skills/<name>/SKILL.md(+参考文件)；目录现扫即热更新。
# 会话里勾选的技能经 WS 消息 skills 字段注入（见 _start_task）。

@app.get("/api/skills")
def skills_list():
    return skill_store.list_skills()


@app.get("/api/skills/{name}")
def skills_get(name: str):
    skill = skill_store.get_skill(name)
    if not skill:
        raise HTTPException(404, f"技能 {name!r} 不存在（列表见 GET /api/skills）")
    return skill


@app.post("/api/skills")
def skills_save(data: dict = Body(...)):
    try:
        return skill_store.save_skill(data.get("name"), data.get("description"),
                                      data.get("content"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/skills/{name}")
def skills_delete(name: str):
    if not skill_store.delete_skill(name):
        raise HTTPException(404, f"技能 {name!r} 不存在")
    return {"ok": True}

@app.get("/api/settings")
def get_settings():
    return config.public_settings(CFG)

@app.put("/api/settings")
def put_settings(data: dict):
    global CFG
    CFG = config.update_settings(data)
    return config.public_settings(CFG)


@app.get("/api/model-profiles")
def get_model_profiles():
    """轻量档案列表（不含密钥），供工作流节点下拉选用。"""
    return [{"id": name, "model": p.get("model", ""), "base_url": p.get("base_url", "")}
            for name, p in (CFG.get("model_profiles") or {}).items()]


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    """删除会话：取消在跑任务，清理 gateway 会话目录与工具工作区目录。"""
    _check_id(sid)
    if SESSIONS.get(sid, {}).get('compacting'):
        raise HTTPException(409, '对话正在压缩，请完成后删除')
    DELETING_SESSIONS.add(sid)
    sess = SESSIONS.get(sid)
    if sess:
        sess['suppress_resume'] = True
        sess['deleted'] = True
    if sess and sess["task"] and not sess["task"].done():
        sess["cancel"].set()
        sess["task"].cancel()   # 只 set event 等不到检查点：阻塞中的任务要硬取消
        await asyncio.gather(sess['task'], return_exceptions=True)
    await runtime_for(CFG).cancel_session(sid)
    SESSIONS.pop(sid, None)
    runtime_for(CFG).manifests.pop(sid, None)
    shutil.rmtree(SESSIONS_DIR / sid, ignore_errors=True)
    workspace = (CFG.get("tools", {}) or {}).get("workspace")
    if workspace:
        shutil.rmtree(Path(workspace) / sid, ignore_errors=True)
    DELETING_SESSIONS.discard(sid)
    return {"ok": True}


@app.post("/api/prompts")
def save_prompts(prompts: list[dict] = Body(...)):
    # 裸 `list` 注解在新版 FastAPI 下会被当成内嵌字段 {"prompts": [...]}，
    # 前端发的是顶层数组 → 一律 422，预设永远存不上
    return {"ok": True, "prompts": prompt_store.save(prompts)}


@app.get("/api/outputs/{sid}/{call_id}")
def read_output(sid: str, call_id: str, offset: int = 0, limit: int = 50, grep: str = ""):
    _check_id(sid)
    _check_id(call_id)
    offset = max(0, offset)
    workspace = (CFG.get("tools", {}) or {}).get("workspace")
    if not workspace:
        return {"status": "not_found"}
    path = Path(workspace) / sid / "outputs" / f"{call_id}.txt"
    if not path.exists():
        return {"status": "not_found"}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if grep:
        try:
            rx = re.compile(grep, re.IGNORECASE)
            lines = [l for l in lines if rx.search(l)]
        except re.error as e:
            return {"status": "validation_error", "detail": str(e)}
    limit = max(1, min(limit, 200))
    page = [f"{offset + i + 1}: {l}" for i, l in enumerate(lines[offset:offset + limit])]
    resp = {"status": "ok", "total_lines": len(lines), "offset": offset,
            "returned": len(page), "lines": page}
    if offset + limit < len(lines):
        resp["next_offset"] = offset + limit
    return resp


@app.post("/api/sessions/{sid}/files")
async def upload_file(sid: str, file: UploadFile = File(...)):
    """上传文件进会话工作区 files/。三条前端触发（粘贴/拖拽/附件按钮）共用本通道。

    落盘成功后往会话 inbox 注入一条 kind=upload 的通知：任务运行中下一轮
    模型调用前注入，空闲则留队等下个任务第 1 轮——所以 _start_task 清
    插话残留时要放行这类项。文件落 workspace/<sid>/files/ 即被 file_read/
    file_list 覆盖，toolbox 侧零改动。
    """
    _check_id(sid)
    workspace = (CFG.get("tools", {}) or {}).get("workspace")
    if not workspace:
        raise HTTPException(400, "未配置工具工作区")
    sess = _get_session(sid, create=True)   # 与 WS 首连同语义：惰性建会话
    target_dir = Path(workspace) / sid / "files"
    target_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_name(file.filename)
    final = target_dir / name
    n = 2
    while final.exists():                   # 同名不改写，加序号
        stem, dot, ext = name.rpartition(".")
        final = target_dir / (f"{stem}-{n}{dot}{ext}" if dot else f"{name}-{n}")
        n += 1
    limit = int((CFG.get("tools", {}) or {}).get("upload_max_mb", 64)) * 1024 * 1024
    total = 0
    try:
        with final.open("wb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"文件超过 {limit // 1048576} MB 上限")
                out.write(chunk)
    except Exception:
        final.unlink(missing_ok=True)       # 超限/异常不留半截文件
        raise
    note = (f"[已上传文件 {final.name}（{total} 字节）到会话工作区 files/ 目录，"
            f"可用 file_list / file_read 读取]")
    # 不 emit interject：那会让前端把上传当可撤回插话显示
    sess["inbox"].append({"id": uuid.uuid4().hex[:8], "content": note, "kind": "upload"})
    sess["bus"].emit("file_upload", name=final.name, size=total)
    return {"name": final.name, "size": total, "path": f"files/{final.name}"}


def _queue_pending(sess, sid):
    if not sess.get('last_options'):
        owner = runtime_for(CFG).files(sid) / 'subagents/owner.json'
        if owner.exists():
            sess['last_options'] = json.loads(owner.read_text(encoding='utf-8'))
    import context_store
    saved = context_store.load(sid)
    saved_ids = {m['_notice_id'] for m in saved if m.get('_notice_id')}
    saved_ids.update(n for m in saved for n in m.get('_notice_ids', []))
    runtime = runtime_for(CFG)
    acknowledged = {n['id'] for n in runtime.pending(sid)} & saved_ids
    if acknowledged:
        runtime.ack(sid, acknowledged)
    ids = {x['id'] for x in sess['inbox']}
    sess['inbox'].extend(x for x in runtime_for(CFG).pending(sid) if x['id'] not in ids)


def _deliver_completion(sid, notice):
    if sid in DELETING_SESSIONS or sid.startswith('wf-') or sid == 'tool-playground':
        return
    sess = _get_session(sid, create=True)
    if sess['suppress_resume']:
        return
    if not any(x['id'] == notice['id'] for x in sess['inbox']):
        sess['inbox'].append(notice)
    _resume_if_needed(sess, sid)


def _resume_if_needed(sess, sid):
    if sess['suppress_resume'] or sess.get('compacting') or (sess['task'] and not sess['task'].done()):
        return
    if any(x.get('kind') == 'subagent' for x in sess['inbox']):
        _start_task(sess, sid, {**sess['last_options'], 'content':
            '收到子智能体完成通知。读取通知中的清单和报告，继续处理原任务。'}, resume=True)


def _parent_done(sess, sid, done):
    if not done.cancelled():
        error = done.exception()
        if error:
            sess['suppress_resume'] = True
            sess['bus'].emit('error', detail=str(error))
        elif done.result() == '':
            # A failed initialization may leave notices undrained. Do not spin
            # automatic continuations until a user explicitly retries.
            sess['suppress_resume'] = True
    if sess['task'] is done:
        _resume_if_needed(sess, sid)


def _start_task(sess: dict, sid: str, msg: dict, resume=False) -> None:
    if sid in DELETING_SESSIONS or sess.get('deleted') or sess.get('compacting'):
        return
    preset = prompt_store.get(msg.get("prompt_id", ""))
    sess["cancel"] = asyncio.Event()
    # 清掉上一任务的插话残留；文件上传通知是会话级事实，留给新任务第 1 轮注入
    sess['suppress_resume'] = False
    sess["inbox"][:] = [x for x in sess["inbox"] if x.get("kind") in ('upload', 'subagent')]
    _queue_pending(sess, sid)
    if not resume:
        sess['last_options'] = {k: msg[k] for k in ('prompt_id', 'toolset_id') if k in msg}
        sess['last_options']['toolset_id'] = str(msg.get('toolset_id') or '') or toolsets.load()['default']
        atomic_write(runtime_for(CFG).files(sid) / 'subagents/owner.json',
                     json.dumps(sess['last_options'], ensure_ascii=False))
    else:
        sess['bus'].emit('agent_resumed')
    # 勾选技能注入：渲染【skill·name】块进 system 通道，并逐个发 skill_injected
    # 事件（前端在用户消息上方渲染同款块；事件落盘，回放/删技能后仍可见）
    skill_text = ""
    names = [s for s in (msg.get("skills") or []) if isinstance(s, str)]
    if not resume and names:
        blocks, emitted = skill_store.render(names)
        for nm, content in emitted:
            sess['bus'].emit('skill_injected', name=nm, content=content)
        skill_text = blocks
    cfg = copy.deepcopy(CFG)
    cfg.setdefault('tools', {}).setdefault('extra_env', {})['AGENT_CALL_PREFIX'] = 'parent_' + uuid.uuid4().hex[:12] + '_'
    extra_system = "\n\n".join(x for x in (skill_text,
                                           preset["content"] if preset else "") if x)
    sess["task"] = asyncio.create_task(
        run_agent(sid, msg["content"], cfg, sess["bus"], sess["cancel"],
                  extra_system=extra_system,
                  prompt_name=preset["name"] if preset else "",
                  inbox=sess["inbox"],
                  emit_user=not resume,
                  on_notifications_saved=lambda ids: runtime_for(CFG).ack(sid, ids),
                  context_control=sess.setdefault('context_control', {}),
                  toolset=sess['last_options'].get('toolset_id') or toolsets.load()['default']))
    sess['task'].add_done_callback(lambda done: _parent_done(sess, sid, done))


@app.get('/api/sessions/{sid}/subagents')
async def subagent_jobs(sid: str):
    _check_id(sid)
    return runtime_for(CFG).manifest(sid)


@app.post('/api/sessions/{sid}/subagents/{batch_id}/cancel')
async def subagent_cancel(sid: str, batch_id: str):
    _check_id(sid)
    _check_id(batch_id)
    await runtime_for(CFG).cancel_batch(sid, batch_id)
    return {'ok': True}


@app.get('/api/sessions/{sid}/subagents/{agent_id}/report')
async def subagent_report(sid: str, agent_id: str):
    _check_id(sid)
    _check_id(agent_id)
    runtime = runtime_for(CFG)
    rec = next((r for r in runtime.manifest(sid)['jobs'] if r['agent_id'] == agent_id), None)
    if not rec or rec['status'] not in ('ok', 'error', 'incomplete', 'timeout', 'cancelled', 'interrupted'):
        raise HTTPException(404, '报告尚未生成')
    return FileResponse(runtime.files(sid) / rec['report_path'], media_type='text/plain; charset=utf-8')


@app.post('/api/sessions/{sid}/subagents/{agent_id}/stop')
async def subagent_stop(sid: str, agent_id: str):
    _check_id(sid)
    _check_id(agent_id)
    await runtime_for(CFG).cancel_job(sid, agent_id)
    return {'ok': True}


@app.websocket("/ws/sessions/{sid}")
async def ws_session(ws: WebSocket, sid: str):
    if not _SID_RE.match(sid):
        await ws.close(code=4400)
        return
    await ws.accept()
    sess = _get_session(sid, create=True)
    bus: EventBus = sess["bus"]
    # Subscribe before replay so events produced while sending history are kept.
    q = bus.subscribe()

    async def sender():
        while True:
            evt = await q.get()
            await ws.send_text(json.dumps(evt, ensure_ascii=False))

    send_task = None
    try:
        for evt in bus.history():
            await ws.send_text(json.dumps(evt, ensure_ascii=False))
        # Final usage is live-only; replayed usage can therefore be outdated.
        # Queue the current snapshot after events collected during replay.
        q.put_nowait({'seq': 0, 'live': True, 'session': sid, 'type': 'context_usage',
                      'ts': datetime.now().astimezone().isoformat(timespec='seconds'),
                      **await session_context(sid)})
        send_task = asyncio.create_task(sender())
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            if sess.get('deleted'):
                await ws.close(code=4404)
                return
            if sess.get('compacting') and msg.get('type') in ('user_message', 'interject'):
                # Direct WebSocket clients must respect the same user-initiated
                # pause as the composer. Never race context.json replacement.
                bus.emit('context_compaction_started')
                continue
            content = str(msg.get("content") or "").strip()
            if msg.get("type") in ("user_message", "interject") and not content:
                continue
            running = sess["task"] and not sess["task"].done()
            if msg.get("type") in ("user_message", "interject") and running \
                    and not sess["cancel"].is_set():
                # Claude Code 式插话：任务在跑时的新消息不拒绝，排队到下一轮模型调用前注入
                item = {"id": uuid.uuid4().hex[:8], "content": content}
                sess["inbox"].append(item)
                bus.emit("interject", **item)
                continue
            if msg.get("type") in ("user_message", "interject"):
                if running:
                    # 用户已取消但旧任务还在退出中（落盘记忆、收尾子进程）：等它退出再接管
                    try:
                        await asyncio.wait_for(asyncio.shield(sess["task"]), timeout=3)
                    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                        pass
                    if not sess["task"].done():
                        bus.emit("error", detail="旧任务正在退出，请稍候重发")
                        continue
                _start_task(sess, sid, {**msg, "content": content})
            elif msg.get("type") == "withdraw":
                # 撤回还没被注入的插话
                wid = msg.get("id")
                before = len(sess["inbox"])
                sess["inbox"][:] = [x for x in sess["inbox"] if x["id"] != wid]
                if len(sess["inbox"]) < before:
                    bus.emit("interject_withdrawn", id=wid)
            elif msg.get("type") == "cancel":
                sess['suppress_resume'] = True
                sess["cancel"].set()
                # 协作式取消只在迭代/工具调用间隙生效；阻塞在模型或
                # MCP 调用中时要硬取消，任务才能立刻让位给下一条消息
                if sess["task"] and not sess["task"].done():
                    sess["task"].cancel()
                await runtime_for(CFG).cancel_session(sid)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q)
        if send_task:
            send_task.cancel()


# ═══════════════ 工作流 ═══════════════

@app.get("/api/workflows")
def wf_list():
    return wf_engine.list_workflows()


@app.post("/api/workflows")
def wf_create(data: dict | None = None):
    data = data or {}
    if data.get("nodes"):                    # 导入 / 另存为
        data.pop("id", None)
        return wf_engine.save_workflow(data)
    return wf_engine.new_workflow(data.get("name") or "未命名工作流")


@app.get("/api/workflows/{wid}")
def wf_get(wid: str):
    _check_id(wid)
    wf = wf_engine.get_workflow(wid)
    if not wf:
        raise HTTPException(404, "工作流不存在")
    return wf


@app.post("/api/workflows/{wid}/files")
async def wf_upload_file(wid: str, file: UploadFile = File(...)):
    wf_get(wid)
    workspace = (CFG.get("tools", {}) or {}).get("workspace")
    if not workspace:
        raise HTTPException(400, "未配置工具工作区")
    target_dir = Path(workspace) / "workflow-inputs" / wid
    target_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_name(file.filename)
    final = target_dir / f"{uuid.uuid4().hex[:12]}-{name}"
    limit = min(64, int((CFG.get("tools", {}) or {}).get("upload_max_mb", 64))) * 1048576
    total = 0
    try:
        with final.open("xb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"文件超过 {limit // 1048576} MB 上限")
                out.write(chunk)
        if not total:
            raise HTTPException(400, "文件为空")
    except BaseException:
        final.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {"name": name, "size": total, "path": str(final.resolve())}


@app.get("/api/unpack-runs/{run_id}/artifacts/{name}")
def unpack_artifact(run_id: str, name: str):
    if not re.fullmatch(r"[a-f0-9]{16}", run_id) or Path(name).name != name:
        raise HTTPException(400, "非法产物路径")
    folder = (UNPACK_RUN_DIR / run_id).resolve()
    path = (folder / name).resolve()
    if path.parent != folder:
        raise HTTPException(400, "非法产物路径")
    try:
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "运行产物不存在")
    record = next((a for a in state.get("artifacts", []) if a.get("name") == name), None)
    if not record or not path.is_file():
        raise HTTPException(404, "运行产物不存在")
    import hashlib
    if hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
        raise HTTPException(409, "产物内容已变更")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@app.put("/api/workflows/{wid}")
def wf_save(wid: str, data: dict):
    _check_id(wid)
    saved = wf_engine.save_workflow({**data, "id": wid})
    return {**saved, "problems": wf_engine.validate(saved)}


@app.delete("/api/workflows/{wid}")
def wf_delete(wid: str):
    _check_id(wid)
    wf_engine.delete_workflow(wid)
    return {"ok": True}


@app.get("/api/workflows/{wid}/runs")
def wf_runs(wid: str):
    _check_id(wid)
    return wf_engine.list_runs(wid)


@app.get("/api/workflow-runs/{run_id}")
def wf_run_detail(run_id: str):
    _check_id(run_id)
    run = wf_engine.get_run(run_id)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    return run


async def _catalog(admin: bool = True) -> list:
    """拉起一次 toolbox 取全量工具卡（admin=True 不受工具集过滤、带管理字段）。"""
    tcfg = CFG.get("tools", {}) or {}
    if not tcfg.get("command"):
        return []
    import mcp_client
    async with mcp_client.open_tools(tcfg, "tool-catalog") as box:
        raw = await box.call("dump_tools", {"admin": admin})
    return json.loads(raw).get("tools", [])


@app.get("/api/tool-cards")
async def tool_cards():
    """工具节点选择器用：全量工具卡（去掉全局禁用的）。"""
    disabled = set(toolsets.load()["disabled"])
    return [c for c in await _catalog() if c["name"] not in disabled]


# ═══════════════ 工具配置 / 工具工坊 ═══════════════

@app.get("/api/tools")
async def tools_list():
    data = toolsets.load()
    disabled = set(data["disabled"])
    tools = [{**c, "enabled": c["name"] not in disabled} for c in await _catalog()]
    return {"tools": tools, "disabled": data["disabled"], "default": data["default"],
            "presets": toolsets.all_presets(data)}


@app.put("/api/toolsets")
def toolsets_save(data: dict):
    saved = toolsets.save(data)
    return {**saved, "presets": toolsets.all_presets(saved)}


@app.get("/api/tools/{name}/source")
def tool_source(name: str):
    try:
        meta = toolsets.read_user_tool(name)
    except toolsets.ToolSpecError as e:
        raise HTTPException(400, str(e))
    if not meta:
        raise HTTPException(404, "工具不存在")
    return meta


@app.post("/api/tools")
def tool_create(spec: dict):
    try:
        return toolsets.write_user_tool(spec)
    except toolsets.ToolSpecError as e:
        raise HTTPException(400, str(e))


@app.put("/api/tools/{name}")
def tool_update(name: str, spec: dict):
    try:
        return toolsets.write_user_tool(spec, original=name)
    except toolsets.ToolSpecError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/tools/{name}")
def tool_delete(name: str):
    try:
        toolsets.delete_user_tool(name)
    except toolsets.ToolSpecError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/tools/{name}/try")
async def tool_try(name: str, body: dict):
    """试运行：在独立的 tool-playground 工作区里真实执行一次，返回和模型看到的完全一样的信封。"""
    tcfg = CFG.get("tools", {}) or {}
    if not tcfg.get("command"):
        raise HTTPException(400, "未配置工具服务器")
    import mcp_client
    async with mcp_client.open_tools(tcfg, "tool-playground") as box:
        if name == 'subagent':
            import subagent
            return await subagent.execute(body.get('params') or {}, CFG, 'tool-playground',
                asyncio.Event(), set(await box.cards()) - {'subagent'})
        raw = await box.call("run_tool", {"tool_name": name, "params": body.get("params") or {}})
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "error", "detail": raw[:2000]}


@app.post("/api/tools/search")
async def tool_search_preview(body: dict):
    """检索预览：看模型用某个关键词 search_tools 会拿到什么（调试关键词用）。"""
    tcfg = CFG.get("tools", {}) or {}
    if not tcfg.get("command"):
        return {"results": []}
    import mcp_client
    async with mcp_client.open_tools(tcfg, "tool-catalog",
                                     toolset=body.get("toolset") or "all") as box:
        raw = await box.call("search_tools", {"query": str(body.get("query") or ""), "limit": 10})
    return json.loads(raw)


# ═══════════════ 知识库(RAG)管理面 ═══════════════
# 知识库是全局资源(不属于某个会话):gateway 直接 import toolbox/rag_store.py
# (纯 stdlib;SQLite WAL 支持 gateway/toolbox 双进程并发;配置同源 rag_config.yaml)。
# 模型侧不受影响,仍走 MCP rag 工具卡(审计/LoopGuard 原样生效)。

_RAG = None


def _rag():
    """懒加载 toolbox/rag_store;toolbox 目录从 tools.args 的 server.py 路径推。"""
    global _RAG
    if _RAG is None:
        import importlib
        import sys
        args = (CFG.get("tools", {}) or {}).get("args") or []
        toolbox_dir = None
        if args:
            toolbox_dir = (Path(__file__).resolve().parent / str(args[0])).resolve().parent
        if not toolbox_dir or not (toolbox_dir / "rag_store.py").exists():
            toolbox_dir = Path(__file__).resolve().parent.parent / "toolbox"
        if str(toolbox_dir) not in sys.path:
            sys.path.insert(0, str(toolbox_dir))
        _RAG = importlib.import_module("rag_store")
    return _RAG


@app.get("/api/knowledge/status")
def knowledge_status():
    return _rag().status()


@app.get("/api/knowledge/config")
def knowledge_config_get():
    return _rag().config()


@app.put("/api/knowledge/config")
def knowledge_config_put(body: dict = Body(...)):
    return _rag().save_config(body)


@app.get("/api/knowledge/documents")
def knowledge_documents(collection: str = "", q: str = "", limit: int = 50, offset: int = 0):
    return _rag().list_documents(collection=collection, q=q, limit=limit, offset=offset)


@app.get("/api/knowledge/documents/{doc_id}")
def knowledge_document(doc_id: int):
    try:
        return _rag().get_document(doc_id)
    except KeyError:
        raise HTTPException(404, "文档不存在")


@app.delete("/api/knowledge/documents/{doc_id}")
def knowledge_document_delete(doc_id: int):
    try:
        return _rag().delete_document(doc_id)
    except KeyError:
        raise HTTPException(404, "文档不存在")


@app.post("/api/knowledge/search")
def knowledge_search(body: dict = Body(...)):
    """试搜:和模型看到的同一检索路径,max_chars 放宽给 UI 展示。"""
    try:
        return _rag().search(
            str(body.get("query") or ""),
            collection=str(body.get("collection") or ""),
            mode=str(body.get("mode") or ""),
            top_k=int(body.get("top_k") or 0),
            max_chars=int(body.get("max_chars") or 2000))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _ingest_checks(cfg: dict, name: str, size: int) -> None:
    limit = int(cfg["ingest"].get("max_file_bytes", 10 * 1024 * 1024))
    if size > limit:
        raise HTTPException(413, f"文件超过 {limit // 1048576} MB 上限")


def _ingest_ext_check(cfg: dict, name: str) -> None:
    allowed = {str(x).lower() for x in cfg["ingest"].get("allowed_extensions", [])}
    ext = Path(name).suffix.lower()
    if allowed and ext not in allowed:
        raise HTTPException(400, f"不支持的扩展名: {ext or '(无)'}")


@app.post("/api/knowledge/upload")
async def knowledge_upload(file: UploadFile = File(...),
                           collection: str = Form("general"), title: str = Form("")):
    """上传文档入库:内容直接进 rag_store.ingest,天然绕开模型侧 workspace 路径限制。"""
    cfg = _rag().config()
    name = _safe_name(file.filename)
    _ingest_ext_check(cfg, name)
    buf = bytearray()
    while chunk := await file.read(_UPLOAD_CHUNK):
        buf += chunk
        _ingest_checks(cfg, name, len(buf))      # 流式超限即断,不留半截
    text = bytes(buf).decode("utf-8", errors="replace")
    try:
        return _rag().ingest(content=text, title=title or name,
                             collection=collection or "general",
                             source=f"upload:{name}", path=name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/knowledge/ingest-path")
def knowledge_ingest_path(body: dict = Body(...)):
    """本机绝对路径导入——仅本管理面(服务绑定 127.0.0.1),不给模型暴露此能力。"""
    raw = str(body.get("path") or "").strip()
    path = Path(raw)
    if not path.is_absolute() or not path.is_file():
        raise HTTPException(400, "需要已存在的本机绝对路径")
    cfg = _rag().config()
    _ingest_ext_check(cfg, path.name)
    _ingest_checks(cfg, path.name, path.stat().st_size)
    try:
        return _rag().ingest(
            content=path.read_text(encoding="utf-8", errors="replace"),
            title=str(body.get("title") or "") or path.name,
            collection=str(body.get("collection") or "general"),
            source=str(path), path=raw,
            overwrite=bool(body.get("overwrite")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.websocket("/ws/workflows/{wid}/run")
async def wf_run_ws(ws: WebSocket, wid: str):
    """运行通道：客户端发 {type:run, inputs, workflow?(未保存的草稿)} /
    {type:run_node, node_id, inputs} / {type:stop}；服务端推 workflow_* 事件。"""
    if not _SID_RE.match(wid):
        await ws.close(code=4400)
        return
    await ws.accept()
    bus = wf_engine.WorkflowBus()
    q = bus.subscribe()
    state = {"task": None, "cancel": asyncio.Event(), "runtime": None}

    def set_runtime(rt):
        state["runtime"] = rt

    def reject(detail):
        bus.emit("workflow_command_error", command=kind,
                 node_key=msg.get("node_key"), detail=detail)

    async def sender():
        while True:
            evt = await q.get()
            await ws.send_text(json.dumps(evt, ensure_ascii=False, default=str))

    send_task = asyncio.create_task(sender())
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind in ("run", "run_node"):
                if state["task"] and not state["task"].done():
                    reject("已有运行在进行中")
                    continue
                wf = msg.get("workflow") or wf_engine.get_workflow(wid)
                if not wf:
                    bus.emit("workflow_error", detail="工作流不存在")
                    continue
                wf = {**wf, "id": wid}
                state["cancel"] = asyncio.Event()
                state["runtime"] = None
                if kind == "run":
                    coro = wf_engine.execute(wf, msg.get("inputs") or {}, CFG, bus,
                                             state["cancel"], on_runtime=set_runtime)
                else:
                    coro = _run_node_and_report(wf, msg, bus, state["cancel"])
                state["task"] = asyncio.create_task(coro)
            elif kind in ("retry", "skip"):
                key = str(msg.get("node_key") or "")
                if kind == "skip" and state["task"] and not state["task"].done():
                    rt = state.get("runtime")
                    try:
                        if not rt or rt.run["id"] != msg.get("run_id"):
                            raise wf_engine.NodeError("运行记录已变化，请重新选择节点")
                        if "outputs" not in msg:
                            raise wf_engine.NodeError("请填写跳过结果（允许空结果）")
                        rt.request_skip(key, msg["outputs"], msg.get("handle"))
                    except Exception as exc:
                        reject(str(exc))
                    continue
                # 断点续跑：按旧 run 记录新建一次运行，succeeded 节点复用输出。
                # 图用记录里的快照（重试"当时那一次"，改过画布也不会错位缓存）。
                if state["task"] and not state["task"].done():
                    reject("已有运行在进行中")
                    continue
                old = wf_engine.get_run(str(msg.get("run_id") or ""))
                if not old or old.get("workflow_id") != wid:
                    reject("找不到原运行记录")
                    continue
                if old.get("status") not in ("failed", "stopped"):
                    reject("只能继续失败或已停止的运行")
                    continue
                wf = ({"id": wid, "name": old.get("workflow_name"), **old.get("graph")}
                      if old.get("graph", {}).get("nodes")
                      else wf_engine.get_workflow(wid))
                if not wf or not wf.get("nodes"):
                    bus.emit("workflow_error", detail="工作流不存在")
                    continue
                wf = {**wf, "id": wid}
                overrides = None
                if kind == "skip":
                    try:
                        rec = (old.get("nodes") or {}).get(key)
                        if not rec or rec.get("status") not in ("failed", "stopped", "exception", "running"):
                            raise wf_engine.NodeError("请选择失败或已停止的节点")
                        if "outputs" not in msg:
                            raise wf_engine.NodeError("请填写跳过结果（允许空结果）")
                        overrides = {key: wf_engine.manual_override(wf, key, msg["outputs"], msg.get("handle"))}
                    except Exception as exc:
                        reject(str(exc))
                        continue
                state["cancel"] = asyncio.Event()
                state["task"] = asyncio.create_task(
                    wf_engine.execute(wf, old.get("inputs") or {}, CFG, bus,
                                      state["cancel"], resume_from=old,
                                      node_overrides=overrides, on_runtime=set_runtime))
            elif kind == "stop":
                state["cancel"].set()
                if state.get('runtime'):
                    await runtime_for(CFG).cancel_session(state['runtime'].session_id)
                if state["task"] and not state["task"].done():
                    bus.emit("workflow_stopping")
                    state["task"].cancel()
    except WebSocketDisconnect:
        pass
    finally:
        if state["task"] and not state["task"].done():
            state["cancel"].set()
            state["task"].cancel()
        bus.unsubscribe(q)
        send_task.cancel()


async def _run_node_and_report(wf, msg, bus, cancel):
    run = await wf_engine.run_single_node(wf, msg.get("node_id", ""), msg.get("inputs") or {},
                                          CFG, bus, cancel)
    bus.emit("workflow_node_debug", node_id=msg.get("node_id"), status=run["status"],
             outputs=run.get("outputs"), error=run.get("error"))


# 静态托管前端产物（frontend/dist）：一个进程同时服务页面与 API。
# 必须在所有路由注册之后 mount，否则会吞掉 /api 与 /ws。
from fastapi.staticfiles import StaticFiles

_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")


@app.middleware("http")
async def _cache_headers(request, call_next):
    """index.html 每次回源校验（否则浏览器启发式缓存旧页面，引用旧 hash 的 js，
    出现"改了前端但页面行为不变"）；/assets/ 文件名带内容 hash，可放心长缓存。"""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


if __name__ == "__main__":
    uvicorn.run(app, host=CFG["server"]["host"], port=CFG["server"]["port"])
