"""fscan 内网综合扫描。target 走保守白名单(IP/CIDR/主机名)，禁止任意 flag 注入。
侵入性工具：skip_brute/skip_poc 提供降档开关，信息收集阶段推荐全开。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TARGET_RE = re.compile(r"^[a-zA-Z0-9._:/,-]+$")   # IP/CIDR/多目标逗号分隔


def binary() -> str:
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    p = Path(meta.get("bin") or "fscan")
    return str(p if p.is_absolute() else Path(__file__).parent / p)


def main(params: dict) -> None:
    target = str(params.get("target", ""))
    if not target or not TARGET_RE.match(target) or ".." in target:
        print(f"invalid target: {target!r}（IP/CIDR/主机名）", file=sys.stderr)
        sys.exit(2)

    exe = binary()
    if not Path(exe).exists():
        print(f"bin 路径不存在: {exe}（改 tool.yaml 的 bin 或重装）", file=sys.stderr)
        sys.exit(2)

    cmd = [exe, "-u", target]
    if params.get("ports"):
        cmd += ["-p", str(params["ports"])]
    if params.get("skip_brute"):
        cmd.append("-nobr")
    if params.get("skip_poc"):
        cmd.append("-nopoc")

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
