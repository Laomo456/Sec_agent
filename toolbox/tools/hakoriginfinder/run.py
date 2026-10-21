"""hakoriginfinder：候选源站喂 stdin，target 走 -h。候选做白名单清洗后传。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")
CAND_RE = re.compile(r"^[a-zA-Z0-9.:-]+$")   # IP / 域名 / host:port


def binary() -> str:
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    p = Path(meta.get("bin") or "hakoriginfinder")
    return str(p if p.is_absolute() else Path(__file__).parent / p)


def main(params: dict) -> None:
    target = str(params.get("target", ""))
    if not URL_RE.match(target):
        print(f"target 必须是 http(s) URL: {target!r}", file=sys.stderr)
        sys.exit(2)
    cands = [c.strip() for c in re.split(r"[,\n]", str(params.get("candidates", "")))
             if c.strip()]
    bad = [c for c in cands if not CAND_RE.match(c)]
    if not cands or bad:
        print(f"invalid candidates: {bad[:3]!r}", file=sys.stderr)
        sys.exit(2)

    exe = binary()
    if not Path(exe).exists():
        print(f"bin 路径不存在: {exe}", file=sys.stderr)
        sys.exit(2)

    cmd = [exe, "-h", target]
    if params.get("ports"):
        ports = str(params["ports"])
        if not re.match(r"^[0-9,]+$", ports):
            print(f"invalid ports: {ports!r}", file=sys.stderr)
            sys.exit(2)
        cmd += ["-p", ports]

    proc = subprocess.run(cmd, input="\n".join(cands) + "\n",
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
