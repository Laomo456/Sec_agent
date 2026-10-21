"""纯 stdlib DNS 查询客户端（工具卡 nslookup 的执行体）。

不包系统 nslookup.exe：其输出随系统语言本地化（中文 Windows 出中文）、
失败时常仍 exit 0，输出解析脆（ffuf 的教训：外部 CLI 的面不可控）。
自建 UDP/TCP 查询 + 响应解析（含名字压缩指针、逐类型 RDATA），
支持指定服务器、截断(TC 位)自动转 TCP、IP 输入自动 PTR 反解。

退出码：0=拿到有效应答（含 NXDOMAIN——域名不存在本身就是答案）；
1=网络全失败 / SERVFAIL / REFUSED；2=参数校验失败。
"""
import ipaddress
import json
import os
import random
import re
import socket
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DOMAIN_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")     # 域名 / IPv4 / IPv6
TYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16,
         "AAAA": 28, "SRV": 33, "CAA": 257, "ANY": 255}
RTYPES = {v: k for k, v in TYPES.items()}
RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN",
          4: "NOTIMP", 5: "REFUSED"}
FALLBACK_DNS = ["223.5.5.5", "119.29.29.29"]   # 系统 DNS 取不到时的公共兜底（国内直连友好）


def fail(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


# ── 报文构造 / 解析 ────────────────────────────────────────────

def encode_name(name: str) -> bytes:
    out = b""
    for label in name.split("."):
        if not label:
            continue
        try:                      # 中文域名走 IDNA；下划线等 IDNA 不收的按原字节
            b = label.encode("idna")
        except UnicodeError:
            b = label.encode("utf-8")
        if len(b) > 63:
            raise ValueError(f"标签超 63 字节: {label!r}")
        out += bytes([len(b)]) + b
    return out + b"\x00"


def build_query(qid: int, name: str, qtype: int) -> bytes:
    return (struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)   # RD=1 递归
            + encode_name(name) + struct.pack(">HH", qtype, 1))


def parse_name(msg: bytes, off: int):
    """解析（可能压缩的）域名。返回 (名字, 消费到的结束偏移)。
    指针只用于回溯取标签，结束偏移取第一次遇指针的位置（或 \x00 之后）。"""
    labels, end, jumps = [], None, 0
    while True:
        if off >= len(msg) or off < 0:
            raise ValueError("名字偏移越界")
        n = msg[off]
        if n & 0xC0 == 0xC0:
            if off + 1 >= len(msg):
                raise ValueError("压缩指针截断")
            ptr = ((n & 0x3F) << 8) | msg[off + 1]
            if end is None:
                end = off + 2
            off = ptr
            jumps += 1
            if jumps > 24:
                raise ValueError("压缩指针循环或过深")
        elif n == 0:
            return ".".join(labels) or ".", (end if end is not None else off + 1)
        else:
            if off + 1 + n > len(msg):
                raise ValueError("标签越界")
            labels.append(msg[off + 1:off + 1 + n].decode("ascii", "replace"))
            off += 1 + n


def parse_rdata(msg: bytes, off: int, rtype: int, rdlen: int) -> str:
    end = off + rdlen
    if rtype == 1 and rdlen == 4:
        return ".".join(str(b) for b in msg[off:off + 4])
    if rtype == 28 and rdlen == 16:
        return str(ipaddress.IPv6Address(msg[off:off + 16]))
    if rtype in (2, 5, 12):       # NS / CNAME / PTR（RDATA 里可带压缩指针）
        return parse_name(msg, off)[0]
    if rtype == 15:               # MX
        pref = struct.unpack_from(">H", msg, off)[0]
        return f"{parse_name(msg, off + 2)[0]} (pref={pref})"
    if rtype == 16:               # TXT：若干 段长前缀字符串 拼接
        parts, p = [], off
        while p < end:
            n = msg[p]
            parts.append(msg[p + 1:p + 1 + n].decode("utf-8", "replace"))
            p += 1 + n
        return "".join(parts)
    if rtype == 6:                # SOA
        mname, o = parse_name(msg, off)
        rname, o = parse_name(msg, o)
        serial, refresh, retry, expire, minimum = struct.unpack_from(">IIIII", msg, o)
        return (f"{mname} {rname} serial={serial} refresh={refresh} "
                f"retry={retry} expire={expire} min={minimum}")
    if rtype == 33:               # SRV
        prio, weight, port = struct.unpack_from(">HHH", msg, off)
        return f"{parse_name(msg, off + 6)[0]}:{port} (prio={prio} weight={weight})"
    if rtype == 257:              # CAA：flag + taglen + tag + value(到 RDATA 末尾)
        flag, tlen = msg[off], msg[off + 1]
        tag = msg[off + 2:off + 2 + tlen].decode("ascii", "replace")
        return f'{tag}="{msg[off + 2 + tlen:end].decode("utf-8", "replace")}" flag={flag}'
    return msg[off:end].hex()     # 未知类型 hex 兜底


def parse_response(msg: bytes):
    """返回 (rcode, [(段名, 属主, 类型号, ttl, 值), ...])。"""
    qid, flags, qd, an, ns, ar = struct.unpack_from(">HHHHHH", msg, 0)
    rcode = flags & 0xF
    off = 12
    for _ in range(qd):           # 跳过 question 段（应答里原样带回）
        _, off = parse_name(msg, off)
        off += 4
    rows = []
    for count, tag in ((an, "answer"), (ns, "authority"), (ar, "additional")):
        for _ in range(count):
            name, off = parse_name(msg, off)
            rtype, rclass, ttl, rdlen = struct.unpack_from(">HHIH", msg, off)
            off += 10
            try:
                value = parse_rdata(msg, off, rtype, rdlen)
            except Exception as e:            # 单条 RDATA 坏不拖垮整个应答
                value = f"<解析失败: {e}>"
            off += rdlen
            rows.append((tag, name, rtype, ttl, value))
    return rcode, rows


# ── 传输 ──────────────────────────────────────────────────────

def udp_exchange(server: str, port: int, payload: bytes, qid: int, timeout_s: int) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout_s)
        s.sendto(payload, (server, port))
        while True:               # 丢弃 ID 不匹配的杂包
            data, _ = s.recvfrom(65535)
            if len(data) >= 12 and struct.unpack_from(">H", data, 0)[0] == qid:
                return data


def tcp_exchange(server: str, port: int, payload: bytes, qid: int, timeout_s: int) -> bytes:
    with socket.create_connection((server, port), timeout=timeout_s) as s:
        s.settimeout(timeout_s)
        s.sendall(struct.pack(">H", len(payload)) + payload)
        head = b""
        while len(head) < 2:
            chunk = s.recv(2 - len(head))
            if not chunk:
                raise ConnectionError("TCP 流在对长度前缀前断开")
            head += chunk
        want = struct.unpack(">H", head)[0]
        data = b""
        while len(data) < want:
            chunk = s.recv(want - len(data))
            if not chunk:
                raise ConnectionError("TCP 流在应答中途断开")
            data += chunk
        if len(data) >= 12 and struct.unpack_from(">H", data, 0)[0] == qid:
            return data
        raise ConnectionError("TCP 应答 ID 不匹配")


def query(servers: list, port: int, payload: bytes, qid: int,
          timeout_s: int, force_tcp: bool):
    """逐服务器尝试 UDP（TC 截断自动转 TCP 重查）；UDP 全挂再对首家试一次 TCP。"""
    tried = []
    for server in servers:
        if force_tcp:
            try:
                return tcp_exchange(server, port, payload, qid, timeout_s), server
            except OSError as e:
                tried.append(f"{server}(TCP): {e}")
                continue
        try:
            msg = udp_exchange(server, port, payload, qid, timeout_s)
        except OSError as e:
            tried.append(f"{server}: {e}")
            continue
        if len(msg) >= 4 and msg[2] & 0x02:    # TC 位：截断 → TCP 重查同一家
            try:
                return tcp_exchange(server, port, payload, qid, timeout_s), server
            except OSError as e:
                tried.append(f"{server}(TCP 重查): {e}")
                continue
        return msg, server
    try:                                        # UDP 全失败：TCP 最后一搏
        return tcp_exchange(servers[0], port, payload, qid, timeout_s), servers[0]
    except OSError as e:
        raise RuntimeError("所有 DNS 服务器都失败: " + " | ".join(tried + [f"{servers[0]}(TCP): {e}"]))


def system_dns_servers() -> list:
    """Windows 注册表逐接口读静态/动态 DNS；非 Windows 或读不到 → 公共 DNS 兜底。"""
    servers = []

    def add(v):
        for s in str(v).replace(",", " ").split():
            if s not in servers:
                servers.append(s)

    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Services\Tcpip"
                             r"\Parameters\Interfaces")
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(key, sub) as sk:
                    for vname in ("NameServer", "DhcpNameServer"):
                        try:
                            val, _ = winreg.QueryValueEx(sk, vname)
                            if val:
                                add(val)
                        except OSError:
                            pass
            except OSError:
                pass
    except (ImportError, OSError):
        pass
    return servers[:4]


# ── 主流程 ────────────────────────────────────────────────────

def reverse_ptr(ip) -> str:
    if ip.version == 4:
        return ".".join(reversed(ip.exploded.split("."))) + ".in-addr.arpa"
    return ".".join(reversed(ip.exploded.replace(":", ""))) + ".ip6.arpa"


def main(params: dict) -> None:
    domain = str(params.get("domain") or "").strip().rstrip(".")
    if not domain or not DOMAIN_RE.match(domain) or ".." in domain or len(domain) > 253:
        fail(f"invalid domain: {params.get('domain')!r}（只要域名/IP，不要带 scheme 或路径）")

    qtype_name = str(params.get("type") or "A").upper()
    if qtype_name not in TYPES:
        fail(f"未知记录类型 {qtype_name!r}，可用: {'/'.join(TYPES)}")

    # IP 输入 → PTR 反解（显式给了非 PTR 类型则按用户的意思查）
    try:
        ip = ipaddress.ip_address(domain)
    except ValueError:
        ip = None
    if ip is not None:
        if qtype_name in ("A", "PTR"):
            ptr = reverse_ptr(ip)
            if qtype_name == "A":
                print(f"[{domain} 是 IP，自动转 PTR 反查]")
                qtype_name = "PTR"
            domain = ptr
    if qtype_name == "PTR" and ip is None and ".in-addr.arpa" not in domain:
        fail("PTR 反查请直接给 IP（或 xxx.in-addr.arpa 名）")

    port = clamp(params.get("port"), 1, 65535, 53)
    timeout_s = clamp(params.get("timeout"), 1, 10, 3)
    server = str(params.get("server") or "").strip()
    if server:
        if not DOMAIN_RE.match(server):
            fail(f"invalid server: {server!r}")
        try:                      # 服务器给了主机名 → 先用系统解析器把它变成 IP
            server = socket.getaddrinfo(server, port, socket.AF_INET,
                                        socket.SOCK_DGRAM)[0][4][0]
        except socket.gaierror as e:
            fail(f"DNS 服务器 {server!r} 解析失败: {e}", 1)
        servers, src = [server], "指定"
    else:
        servers = system_dns_servers() or FALLBACK_DNS
        src = "系统" if servers != FALLBACK_DNS else "公共(未取到系统 DNS)"

    qid = random.randint(0, 0xFFFF)
    payload = build_query(qid, domain, TYPES[qtype_name])
    try:
        msg, used = query(servers, port, payload, qid, timeout_s,
                          bool(params.get("tcp")))
    except RuntimeError as e:
        print(f"DNS 查询失败（servers={'/'.join(servers)}）：{e}", file=sys.stderr)
        sys.exit(1)

    rcode, rows = parse_response(msg)
    print(f";; {domain} {qtype_name} @{used} [{src}] → {RCODES.get(rcode, rcode)}"
          f" answers={sum(1 for r in rows if r[0] == 'answer')}")

    shown_owner = domain.lower()
    tag_cn = {"authority": "权威段", "additional": "附加段"}
    seen_tags = set()
    for tag, name, rtype, ttl, value in rows:
        label = RTYPES.get(rtype, f"TYPE{rtype}")
        owner = "" if name.lower() in (shown_owner, ".") else f"{name}: "
        if tag != "answer" and tag not in seen_tags:
            seen_tags.add(tag)
            print(f";; {tag_cn[tag]}")
        print(f"{label}\t{owner}{value}\tttl={ttl}")
    if rcode == 3:
        print(";; 域名不存在（NXDOMAIN）")
    elif rcode == 0 and not any(r[0] == "answer" for r in rows):
        print(";; 无该类型记录（NODATA）")
    if rcode in (1, 2, 4, 5):
        print(f"DNS 服务器返回失败: {RCODES.get(rcode, rcode)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
