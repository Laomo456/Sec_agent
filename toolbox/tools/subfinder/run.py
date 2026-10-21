"""subfinder 子域名枚举。被动聚合公开来源(证书透明/搜索引擎等)。
-silent 只输出裸域名列表(便于后续消费)；-all 穷尽来源(慢，部分需 API key)。
域名走保守白名单，不用 shell。"""
import json
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DOMAIN_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$")


def main(params: dict) -> None:
    domain = str(params.get("domain", ""))
    if not domain or not DOMAIN_RE.match(domain):
        print(f"invalid domain: {domain!r}", file=sys.stderr)
        sys.exit(2)
    cmd = ["subfinder", "-d", domain, "-silent"]
    if params.get("all_sources"):
        cmd.append("-all")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
