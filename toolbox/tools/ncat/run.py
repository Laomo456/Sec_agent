"""ncat 连接 + 受控监听：banner 抓取、发包收包、接反弹 shell。

connect：参数收敛为安全子集（host/port/开关），argv 从零拼起、不经 shell。
listen：`ncat -l` 等连入、收数据、可先发一条 data 作为首包命令——
仍然没有 -e/-c/--lua-exec（对连入端执行命令）与 -k（多路并发），一次一连接。
二进制优先级：tool.yaml 的 bin（相对本工具目录）→ PATH 的 ncat。
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOST_RE = re.compile(r"^[a-zA-Z0-9._:-]+$")     # 域名 / IPv4 / [IPv6 去括号]
MAX_OUT = 65536


def binary() -> str:
    """bin 相对路径 = 本工具目录下（复用 nmap 自带发行包），绝对路径 = 外部安装。"""
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    bin_ = meta.get("bin") or "ncat"
    p = Path(bin_)
    if not p.is_absolute():
        p = Path(__file__).parent / p
    if p.exists():
        return str(p)
    return shutil.which("ncat") or str(p)    # 都没有时返回原路径，报错指回 tool.yaml


def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _hexdump(data: bytes) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hexs:<47}  {text}")
    return "\n".join(lines)


def main(params: dict) -> None:
    listen = params.get("mode") == "listen"
    host = str(params.get("host") or "").strip()
    if not listen and (not host or not HOST_RE.match(host) or ".." in host):
        print(f"invalid host: {host!r}（只要 IP/主机名，不要带 scheme 或路径）", file=sys.stderr)
        sys.exit(2)
    if listen and host and (not HOST_RE.match(host) or ".." in host):
        print(f"invalid bind host: {host!r}", file=sys.stderr)
        sys.exit(2)
    try:
        port = int(params.get("port"))
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        print(f"invalid port: {params.get('port')!r}", file=sys.stderr)
        sys.exit(2)

    wait = _clamp(params.get("wait"), 1, 600, 30 if listen else 5)
    idle = _clamp(params.get("idle"), 1, 60, 6)
    data = params.get("data") or ""
    if params.get("crlf"):
        data += "\r\n"

    if listen:
        # 一次一连接：-l 监听，data = 发给连入方的首包（如 "id\n" 喂反弹 shell）
        cmd = [binary(), "-v", "-l", "-i", str(idle)]
        if host:
            cmd.append(host)                      # 绑定地址；留空 = 0.0.0.0（接收外部反弹）
        cmd.append(str(port))
    else:
        cmd = [binary(), "-v", "-w", str(wait), "-i", str(idle)]
        if params.get("udp"):
            cmd.append("-u")
        if params.get("ssl"):
            cmd.append("--ssl")
        cmd += [host, str(port)]

    try:
        proc = subprocess.run(cmd, input=data.encode("utf-8"),
                              capture_output=True, timeout=wait + idle + 15)
    except subprocess.TimeoutExpired as e:
        # 超时也要把已收到的部分交出来（e.stdout 是 bytes）
        got = e.stdout or b""
        print(_hexdump(got) if params.get("hex") else got.decode("utf-8", "replace"))
        where = "监听窗口内" if listen else "连接后"
        print(f"[ncat 超时被终止，{where}共收到 {len(got)} 字节]", file=sys.stderr)
        sys.exit(0)
    except FileNotFoundError:
        print(f"找不到 ncat（bin 路径与 PATH 都没有），改 tool.yaml 的 bin", file=sys.stderr)
        sys.exit(2)

    out = proc.stdout or b""
    if len(out) > MAX_OUT:
        note = f"\n[已截断：收到 {len(out)} 字节，显示前 {MAX_OUT}]"
        shown = out[:MAX_OUT]
    else:
        note, shown = "", out
    print(_hexdump(shown) if params.get("hex") else shown.decode("utf-8", "replace"), end="")
    if note:
        print(note)
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    if err:
        print("\n[stderr]\n" + err)
    code = proc.returncode
    if code != 0 and "Idle timeout expired" in err:
        code = 0    # 空闲超时 = 服务端不关连接时的正常会话结束，不算失败
    sys.exit(code)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
