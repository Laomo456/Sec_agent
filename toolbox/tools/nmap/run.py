"""nmap 端口扫描。参数收敛为安全子集（禁止任意 flag 注入，不用 shell）。
可执行文件优先用 tool.yaml 的 bin 绝对路径，未配置才走 PATH。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

TARGET_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")   # IP/CIDR/主机名的保守白名单

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def binary() -> str:
    """bin 相对路径 = 本工具目录下（自带二进制），绝对路径 = 外部安装。"""
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    bin_ = meta.get("bin") or "nmap"
    p = Path(bin_)
    return str(p if p.is_absolute() else Path(__file__).parent / p)


def main(params: dict) -> None:
    target = str(params.get("target", ""))
    if not target or not TARGET_RE.match(target) or ".." in target:
        print(f"invalid target: {target!r}", file=sys.stderr)
        sys.exit(2)
    exe = binary()
    if not Path(exe).exists():
        print(f"bin 路径不存在: {exe}（改 tool.yaml 的 bin）", file=sys.stderr)
        sys.exit(2)
    cmd = [exe, target]
    if params.get("ports"):
        cmd += ["-p", str(params["ports"])]
    if params.get("service_version"):
        cmd.append("-sV")
    if params.get("top_ports"):
        cmd += ["--top-ports", str(params["top_ports"])]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
