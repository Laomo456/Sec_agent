"""upx：查壳/脱壳。action 三态：test(-t) / list(-l) / unpack(-d -o)。
相对路径落会话工作区；脱壳永不覆盖原文件；可执行文件优先用 tool.yaml 的 bin 绝对路径。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"


def binary() -> str:
    """bin 相对路径 = 本工具目录下（自带二进制），绝对路径 = 外部安装。"""
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    bin_ = meta.get("bin") or "upx"
    p = Path(bin_)
    return str(p if p.is_absolute() else Path(__file__).parent / p)


def resolve(p: str) -> Path:
    path = Path(p)
    if path.drive:
        return path
    return WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files" / path


def main(params: dict) -> None:
    f = resolve(params["file"])
    if not f.is_file():
        print(f"文件不存在: {f}", file=sys.stderr)
        sys.exit(2)

    action = params["action"]
    exe = binary()
    if not Path(exe).exists():
        print(f"bin 路径不存在: {exe}（改 tool.yaml 的 bin）", file=sys.stderr)
        sys.exit(2)
    cmd = [exe]
    if action == "test":
        cmd += ["-t", str(f)]
    elif action == "list":
        cmd += ["-l", str(f)]
    elif action == "unpack":
        out = resolve(params.get("output") or f"{f}.unpacked")
        cmd += ["-d", str(f), "-o", str(out)]
    else:
        print(f"未知 action: {action}", file=sys.stderr)
        sys.exit(2)

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print(proc.stdout or "")
    if (proc.stderr or "").strip():
        print("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
