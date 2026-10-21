"""Shared validation/process utilities for the five mobile reverse-engineering cards."""
import difflib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from process_tree import WindowsJob

ROOT = Path(__file__).resolve().parent.parent
EXTERNAL = ROOT / "external"

class ArgError(Exception):
    pass

class OpError(Exception):
    pass

def files_root():
    session = os.environ.get("AGENT_SESSION", "default")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", session) or session in (".", ".."):
        raise ArgError("AGENT_SESSION must be a session name, not a path")
    # Same directory as file_read/file_write and the tool runner.
    return ROOT / "toolbox" / "workspace" / session / "files"

def resolve(value, label="path"):
    if not isinstance(value, str) or not value.strip() or chr(0) in value:
        raise ArgError(f"{label} must be a nonempty path string")
    path = Path(value.strip()).expanduser()
    if path.is_absolute():
        return path
    if path.drive or path.root:
        raise ArgError(f"{label} must be fully absolute or relative to session files")
    return files_root() / path

def text(params, key, default=None, required=False):
    value = params.get(key, default)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ArgError(f"params.{key} must be a nonempty string")
    return value.strip()

def integer(value, label, low=1, high=1000000):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ArgError(f"{label} must be an integer between {low} and {high}")
    number = int(value)
    if not low <= number <= high:
        raise ArgError(f"{label} must be between {low} and {high}")
    return number

def boolean(value, label):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ArgError(f"{label} must be true/false")

def package(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:[.][A-Za-z_][A-Za-z0-9_]*)+", value):
        raise ArgError("pkg must be an Android package name, e.g. com.example.app")
    return value

def output_dir(params, key, category, name):
    if params.get(key) is not None:
        return resolve(params[key], key)
    safe = re.sub(r"[^\w.-]", "_", name)[:80] or "output"
    return files_root() / category / (safe + "_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))

def run_process(argv, timeout=60, cwd=None, env=None):
    # Files, not inherited PIPEs: a grandchild cannot stall communicate() forever.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        job = WindowsJob() if os.name == "nt" else None
        proc = None
        timed_out = False
        try:
            proc = subprocess.Popen(argv if isinstance(argv, str) else [str(a) for a in argv], stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, cwd=cwd, env=env,
                start_new_session=os.name != "nt", creationflags=0x00000004 if job else 0)
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
                if job:
                    job.terminate()
                elif os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait(timeout=5)
                raise OpError(f"Command timed out after {timeout}s; this invocation's process tree was stopped: {argv[0]}")
            stdout.seek(0); stderr.seek(0)
            raw = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            return proc.returncode, (raw + ("\n[stderr]\n" + err if err.strip() else "")).strip()
        except OSError as error:
            raise OpError(f"Cannot execute {argv[0]}: {error}") from error
        finally:
            if proc is not None and proc.poll() is None:
                if job: job.terminate()
                else: proc.kill()
                proc.wait(timeout=5)
            if job:
                try:
                    # adb and MuMuManager intentionally start services. Keep them after a
                    # completed call, but contain every descendant when the call times out.
                    if not timed_out and proc is not None and proc.returncode is not None: job.detach()
                finally: job.close()

def dispatch(ops, startup_error=None):
    try:
        if startup_error: raise ArgError(startup_error)
        if len(sys.argv) != 2:
            raise ArgError('Expected one JSON object: {"op":"env_check","params":{}}')
        try:
            req = json.loads(sys.argv[1])
        except json.JSONDecodeError as error:
            raise ArgError(f"Invalid JSON: {error.msg}") from error
        if not isinstance(req, dict):
            raise ArgError("Request must be a JSON object")
        op = req.get("op")
        if not isinstance(op, str) or op not in ops:
            suggestion = difflib.get_close_matches(op if isinstance(op, str) else "", ops, n=1)
            raise ArgError(f"Unknown op {op!r}; valid: {'/'.join(ops)}" + (f"; did you mean {suggestion[0]}?" if suggestion else ""))
        params = req.get("params", {})
        if not isinstance(params, dict):
            raise ArgError('params must be an object, e.g. {"op":"run","params":{"project":"verify"}}')
        return 0 if ops[op](params) else 1
    except ArgError as error:
        print(f"[argument] {error}", file=sys.stderr)
        return 2
    except (OpError, OSError) as error:
        print(f"[execution] {error}", file=sys.stderr)
        return 1
    except (ValueError, TypeError) as error:
        print(f"[argument] {error}", file=sys.stderr)
        return 2


def prepare_output(path):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ArgError(f"Output must be new or empty to prevent stale results: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def extract_member(archive, member, target, limit=1024 * 1024 * 1024):
    """Copy selected ZIP content to a fixed filename, never trust member paths."""
    info = archive.getinfo(member)
    if info.file_size <= 0 or info.file_size > limit:
        raise ArgError(f"Invalid archive member size: {member}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, target.open("wb") as dest:
        copied = 0
        while chunk := source.read(1024 * 1024):
            copied += len(chunk)
            if copied > limit: raise ArgError(f"Archive member exceeds size limit: {member}")
            dest.write(chunk)
    return target


def read_config(path, defaults):
    """Keep imports usable even with invalid config; dispatch reports it without traceback."""
    values = dict(defaults)
    try:
        if path.exists():
            custom = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(custom, dict): raise ArgError("config must be a JSON object")
            values.update(custom)
        for key in ("adb_port", "frida_port", "vm_index"):
            if key in values:
                values[key] = integer(values[key], "config." + key, 0 if key == "vm_index" else 1, 1024 if key == "vm_index" else 65535)
        for key in ("mumu_manager", "unidbg_version", "java_home", "maven_home", "maven_path"):
            if key in values and (not isinstance(values[key], str) or not values[key].strip()):
                raise ArgError(f"config.{key} must be a nonempty string")
        return values, None
    except (OSError, ValueError, ArgError) as error:
        return dict(defaults), f"Invalid {path.name}: {error}"
