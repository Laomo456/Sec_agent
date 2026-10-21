"""ysoserial Java 反序列化 payload 生成。纯本地计算（不发请求）。
序列化对象是二进制——stdout 打 base64 + 预览，原始字节落 workspace，
路径打印给模型（file_write/run_cmd 可消费）。java 用本机 PATH 上的 1.8。"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOL_DIR = Path(__file__).parent


def jar() -> str:
    meta = yaml.safe_load((TOOL_DIR / "tool.yaml").read_text(encoding="utf-8"))
    p = Path(meta.get("bin") or "bin/ysoserial-all.jar")
    return str(p if p.is_absolute() else TOOL_DIR / p)


def workspace_dir() -> Path:
    sid = os.environ.get("AGENT_SESSION") or "manual"
    d = TOOL_DIR.parents[1] / "workspace" / sid / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(params: dict) -> None:
    payload = str(params.get("payload", ""))
    if not payload or not payload.isalnum():
        print(f"invalid payload: {payload!r}", file=sys.stderr)
        sys.exit(2)

    j = jar()
    if not Path(j).is_file():
        print(f"jar 不存在: {j}", file=sys.stderr)
        sys.exit(2)

    cmd = ["java", "-jar", j, payload]
    if params.get("command") is not None:
        cmd.append(str(params["command"]))

    proc = subprocess.run(cmd, capture_output=True, timeout=100)
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace")
        # 用法错误时 jar 会打印可用 payload 清单——原样透传，模型自纠错
        print(f"生成失败（payload 名或参数不对？）:\n{err[:2000]}", file=sys.stderr)
        sys.exit(2)

    bin_path = workspace_dir() / f"ysoserial_{payload}_{int(time.time())}.bin"
    bin_path.write_bytes(proc.stdout)

    b64 = base64.b64encode(proc.stdout).decode()
    print(f"payload: {payload}  bytes: {len(proc.stdout)}")
    print(f"bin: {bin_path}")
    print(f"base64 ({len(b64)} chars):")
    print(b64)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
