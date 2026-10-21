"""run_cmd：白名单命令执行。白名单唯一来源是同目录 tool.yaml 的 enum，
run.py 读取它做二次校验（schema 校验是第一道），不用 shell、参数走数组。
工作目录 = 会话工作区（workspace/<会话>/files），相对路径脚本与 cookie 文件直接用。"""
import json
import os
import subprocess
import sys
import locale
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"


def session_cwd() -> Path:
    d = WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta() -> dict:
    return yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))


def allowlist() -> list:
    return _meta()["schema"]["properties"]["command"]["enum"]


def bin_map() -> dict:
    """命令名 → 绝对路径（如 python 锁定 venv），未映射的走 PATH。"""
    values = _meta().get("bin_map") or {}
    root = Path(__file__).resolve().parents[2]
    resolved = {}
    for name, value in values.items():
        path = Path(str(value)).expanduser()
        resolved[name] = str(path if path.is_absolute() else (root / path).resolve())
    return resolved


def decode_output(raw: bytes) -> str:
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        if os.name == 'nt':
            import ctypes
            encoding = f'cp{ctypes.windll.kernel32.GetOEMCP()}'
        else:
            encoding = locale.getencoding()
        return raw.decode(encoding, errors='replace')


def main(params: dict) -> None:
    cmd = str(params.get("command", ""))
    allowed = allowlist()
    if cmd not in allowed:
        print(f"命令 {cmd!r} 不在白名单。允许: {allowed}", file=sys.stderr)
        sys.exit(2)
    exe = bin_map().get(cmd, cmd)
    cwd = session_cwd()
    # 超时读 tool.yaml 的 timeout（留 20s 余量给外层收尾），不再硬编码 280
    to = max(10, int(_meta().get("timeout", 300)) - 20)
    proc = subprocess.run([exe, *[str(a) for a in params.get("args", [])]],
                          capture_output=True, cwd=cwd, timeout=to)
    sys.stdout.write(f"[cwd] {cwd}\n")
    sys.stdout.write(decode_output(proc.stdout))
    if proc.stderr.strip():
        sys.stdout.write("\n[stderr]\n" + decode_output(proc.stderr))
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
