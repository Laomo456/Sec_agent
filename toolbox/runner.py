"""子进程执行器：schema 校验 → 依赖检查 → 执行 → 归一化 → 落盘审计。

子进程隔离：工具崩溃/卡死不拖垮 server；超时由 tool.yaml 的 timeout 控制。
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import jsonschema

import audit
import normalize
from pathlib import Path

from registry import Tool
from process_tree import WindowsJob

KINDS_RUNNER = Path(__file__).resolve().parent / "kinds_runner.py"


def _stop_tree(proc, job=None) -> None:
    """只清理本次工具进程树，等待有上限。"""
    if job is not None:
        job.terminate()
    elif os.name == "nt":
        taskkill = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe")
        try:
            subprocess.run([taskkill, "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def _run_process(argv, timeout):
    # 文件代替 PIPE：Windows 上子孙继承 PIPE 会让 subprocess.run 超时后
    # 的 communicate() 再次无限等 EOF。文件读取不依赖子孙关闭句柄。
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        job = WindowsJob() if os.name == "nt" else None
        proc = None
        timed_out = False
        try:
            proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr,
                                    start_new_session=os.name != "nt",
                                    creationflags=0x00000004 if job else 0,  # CREATE_SUSPENDED
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            if job:
                try:
                    job.attach_and_resume(proc)
                except BaseException:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_tree(proc, job)
        finally:
            try:
                if proc is not None and proc.poll() is None:
                    _stop_tree(proc, job)
                elif job and not timed_out:
                    # Preserve explicit background tools after their completed invocation.
                    job.detach()
            finally:
                if job:
                    job.close()
        stdout.seek(0)
        stderr.seek(0)
        raw = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        if err.strip():
            raw += "\n[stderr]\n" + err
        if timed_out:
            raw += f"\n[timeout] 工具超过 {timeout:g}s，已终止本次进程树"
        return raw, "timeout" if timed_out else ("ok" if proc.returncode == 0 else "error"), \
            None if timed_out else proc.returncode


def validate(tool: Tool, params: dict) -> dict | None:
    """schema 校验，通过返回 None，否则返回错误 envelope（指明字段，模型自纠错）。"""
    try:
        jsonschema.validate(instance=params, schema=tool.schema)
        return None
    except jsonschema.ValidationError as e:
        field_path = ".".join(str(p) for p in e.absolute_path) or "(root)"
        audit.log_event("validation_error", tool=tool.name, params=params,
                        field=field_path, detail=e.message)
        return {"status": "validation_error", "tool": tool.name,
                "field": field_path, "detail": e.message}


def execute(tool: Tool, params: dict, call_id: str) -> dict:
    # 1) schema 校验：报错指明字段，模型自纠错
    if (err := validate(tool, params)):
        return err

    # 2) 依赖可用性
    missing = [b for b in tool.requires if shutil.which(b) is None]
    if missing:
        audit.log_event("validation_error", tool=tool.name,
                        reason="missing_binaries", missing=missing)
        return {"status": "unavailable", "tool": tool.name,
                "missing_binaries": missing,
                "hint": f"本机未安装 {missing}，换用其他工具或先安装"}

    # 3) 子进程执行：script 跑自己的 run.py；http/command 跑声明式执行体
    started = time.time()
    if tool.kind == "script":
        argv = [sys.executable, str(tool.path / "run.py")]
    else:
        argv = [sys.executable, str(KINDS_RUNNER), str(tool.path / "tool.yaml")]
    raw, status, exit_code = _run_process(
        [*argv, json.dumps(params, ensure_ascii=False)], tool.timeout)
    duration_ms = int((time.time() - started) * 1000)

    # 4) 先落盘（归一化结果要引用路径），再归一化，再审计
    out_path = audit.output_path(call_id)
    out_path.write_text(raw, encoding="utf-8")
    result = normalize.normalize(raw, call_id, str(out_path), duration_ms, status,
                                 tool=tool.name, exit_code=exit_code)
    audit.log_event("tool_call", tool=tool.name, call_id=call_id, params=params,
                    status=status, duration_ms=duration_ms,
                    tokens_est=result["stats"]["tokens_est"],
                    normalized={"summary": result["summary"],
                                "truncated": result["stats"]["truncated"]})
    return result
