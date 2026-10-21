"""hashcat 双引擎垫片：bin/ 有真 hashcat 用 GPU 全算法，否则内置多进程 Python 引擎。

引擎分工：
  真 hashcat —— 字典/掩码/规则/全部算法（bcrypt、office、zip 慢哈希也行）
  Python 引擎 —— md5/sha1/sha256/sha512/ntlm 快哈希的字典/掩码攻击（CPU 多进程，
                  内置弱口令表或 @自定义字典；NTLM 走纯 Python MD4 实现）
纯本地计算，零网络请求。入口：run.py '<params JSON>'
"""
import hashlib
import itertools
import json
import multiprocessing
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOL_DIR = Path(__file__).resolve().parent
WORDLISTS = TOOL_DIR.parents[1] / "wordlists"
BUILTIN = WORDLISTS / "common_passwords.txt"
MODES = {"md5": 0, "sha1": 100, "sha256": 1400, "sha512": 1700, "ntlm": 1000}
HEX_LEN = {32: "md5（也可能是 ntlm，猜错就显式指定）", 40: "sha1", 64: "sha256", 128: "sha512"}
CHARSETS = {"d": "0123456789", "l": "abcdefghijklmnopqrstuvwxyz",
            "u": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "s": "!@#$%^&*()-_+=~`[]{}|\\:;\"'<>,.?/",
            "h": "0123456789abcdef", "H": "0123456789ABCDEF",
            "a": None}          # a = d+l+u+s 全集
MAX_TRIES_PRINT = 20


# ── 纯 Python MD4（NTLM 用；hashlib 在 OpenSSL3 下没有 md4）─────────
def die(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(2)          # 工具卡约定：2 = 参数校验错误，1 = 跑了没结果


def _lrot(x: int, n: int) -> int:
    x &= 0xFFFFFFFF                    # 和可能超 32 位，先截断再转
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


_MD4_R2 = [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15]
_MD4_R3 = [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15]


def _md4_hex(msg: bytes) -> str:
    buf = bytearray(msg)
    bit_len = (len(msg) * 8) & 0xFFFFFFFF
    buf += b"\x80"
    while len(buf) % 64 != 56:
        buf += b"\x00"
    buf += bit_len.to_bytes(4, "little") + b"\x00" * 4
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    F = lambda x, y, z: (x & y) | (~x & z)
    G = lambda x, y, z: (x & y) | (x & z) | (y & z)
    H = lambda x, y, z: x ^ y ^ z
    for off in range(0, len(buf), 64):
        X = [int.from_bytes(buf[off + i * 4: off + i * 4 + 4], "little") for i in range(16)]
        a, b, c, d = h
        for i in range(16):
            a = _lrot(a + F(b, c, d) + X[i], (3, 7, 11, 19)[i % 4])
            a, b, c, d = d, a, b, c
        for i in range(16):
            a = _lrot(a + G(b, c, d) + X[_MD4_R2[i]] + 0x5A827999,
                      (3, 5, 9, 13)[i % 4])
            a, b, c, d = d, a, b, c
        for i in range(16):
            a = _lrot(a + H(b, c, d) + X[_MD4_R3[i]] + 0x6ED9EBA1,
                      [3, 9, 11, 15][i % 4])
            a, b, c, d = d, a, b, c
        h = [(v + w) & 0xFFFFFFFF for v, w in zip(h, (a, b, c, d))]
    return "".join(x.to_bytes(4, "little").hex() for x in h)


def _hasher(algo: str):
    if algo == "ntlm":
        return lambda s: _md4_hex(s.encode("utf-16le"))
    return lambda s: hashlib.new(algo, s.encode("utf-8")).hexdigest()


# ── 多进程 worker ─────────────────────────────────────────
_T = {}            # targets: hash -> 占位
_H = None


def _w_init(targets, algo):
    global _T, _H
    _T = set(targets)
    _H = _hasher(algo)


def _w_check(cand: str):
    h = _H(cand)
    if h in _T:
        return h, cand
    return None


# ── 候选生成 ──────────────────────────────────────────────
def _dict_words(spec: str):
    if spec and spec.startswith("@"):
        p = Path(spec[1:])
        if not p.is_absolute():
            p = WORDLISTS / spec[1:]
        if not p.exists():
            die(f"字典不存在: {p}（大字典放 {WORDLISTS} 目录）")
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                w = line.rstrip("\r\n")
                if w and not w.startswith("#"):
                    yield w
    else:
        if not BUILTIN.exists():
            die(f"内置字典缺失: {BUILTIN}")
        for w in _dict_words("@" + str(BUILTIN)):
            yield w


def _variants(w: str):
    """常见变体：够用就好，别把组合空间炸了。"""
    for out in (w, w + "1", w + "123", w + "1234", w + "!", w + "@123", w + "666",
                w + "888", w + "000", w + "520", w + "@qq.com", w.capitalize(),
                w.upper(), w + "2024", w + "2025", w + "2026"):
        if out:
            yield out


def _mask_parse(mask: str):
    """掩码 → parts 列表 [(is_literal, 字面字符或字符集)]。?d?l?u?s?h?H?a 变量位、?? 转义。"""
    parts = []
    i = 0
    while i < len(mask):
        if mask[i:i + 2] == "??":
            parts.append((True, "?"))
            i += 2
        elif mask[i] == "?" and i + 1 < len(mask) and mask[i + 1] in CHARSETS:
            cs = CHARSETS[mask[i + 1]]
            if cs is None:                       # ?a 全集
                cs = CHARSETS["d"] + CHARSETS["l"] + CHARSETS["u"] + CHARSETS["s"]
            parts.append((False, cs))
            i += 2
        else:
            parts.append((True, mask[i]))
            i += 1
    return parts


def _mask_space(parts) -> int:
    total = 1
    for lit, g in parts:
        if not lit:
            total *= len(g)
    return total


def _mask_candidates(mask: str, cap: int):
    parts = _mask_parse(mask)
    var = [p[1] for p in parts if not p[0]]
    if not var:                                  # 纯字面 = 单个候选
        yield mask
        return
    total = _mask_space(parts)
    if total > cap:
        die(f"掩码空间 {total:,} 超过上限 {cap:,}：加已知位缩小范围（?u?l?l?l?d?d 这类）")
    print(f"[掩码] {mask} = {total:,} 个候选", flush=True)
    for combo in itertools.product(*var):
        it = iter(combo)
        yield "".join(next(it) if not lit else ch for lit, ch in parts)


# ── 真 hashcat ────────────────────────────────────────────
def find_hashcat():
    for p in sorted(TOOL_DIR.glob("bin/**/hashcat*.exe")) + sorted(TOOL_DIR.glob("bin/hashcat*")):
        if p.is_file():
            return p
    return shutil.which("hashcat")


def _real_crack(binpath: Path, targets: list, algo: str, params: dict):
    """真 hashcat 跑一轮；返回 (跑成了没有, {hash: plain})。崩了让上层退回内置引擎。"""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="hc_") as td:
        hf = Path(td) / "hashes.txt"
        hf.write_text("\n".join(targets), encoding="utf-8")
        pot = Path(td) / "pot.potfile"
        atk = "3" if params.get("attack") == "mask" else "0"
        cmd = [str(binpath), "-m", str(MODES[algo]), "-a", atk,
               "--potfile-path", str(pot), str(hf)]
        if atk == "3":
            cmd.append(str(params.get("mask") or "?d?d?d?d?d?d"))
        else:
            spec = str(params.get("dict") or "builtin")
            dictfile = BUILTIN if not spec.startswith("@") else Path(spec[1:])
            cmd.append(str(dictfile))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=280, cwd=str(binpath.parent))
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[warn] 真 hashcat 执行异常：{e}", file=sys.stderr)
            return False, {}
        found = {}
        if pot.exists():
            for line in pot.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[0] in targets:
                    found[parts[0]] = parts[-1]
        print(f"[引擎] 真 hashcat（exit={proc.returncode}）")
        return True, found


# ── op 分发 ───────────────────────────────────────────────
def op_status():
    p = find_hashcat()
    if p:
        print(f"[引擎] 真 hashcat: {p}")
        try:
            out = subprocess.run([str(p), "-I"], capture_output=True, text=True,
                                 timeout=30, encoding="utf-8", errors="replace")
            print((out.stdout or "")[:800])
        except Exception as e:
            print(f"设备探测失败: {e}")
    else:
        print("[引擎] 内置 Python 多进程（未找到真 hashcat 二进制）")
        print(f"  可破: md5 / sha1 / sha256 / sha512 / ntlm 的字典与掩码攻击")
        print(f"  内置字典: {BUILTIN}")
        print(f"  想上 GPU 全算法（bcrypt/office/zip 慢哈希）: 把官方 hashcat 解压到 "
              f"{TOOL_DIR / 'bin'} 即自动启用")
    print(f"  自定义字典目录: {WORDLISTS}（@rockyou.txt 这类传法）")


def op_identify(hashes: list):
    h = hashes[0].strip()
    kind = None
    if re.fullmatch(r"[0-9a-fA-F]{32}", h):
        kind = "32 位 hex → md5(0) 或 ntlm(1000)（Python 引擎 auto 默认 md5）"
    elif re.fullmatch(r"[0-9a-fA-F]{40}", h):
        kind = "40 位 hex → sha1(100)"
    elif re.fullmatch(r"[0-9a-fA-F]{64}", h):
        kind = "64 位 hex → sha256(1400)"
    elif re.fullmatch(r"[0-9a-fA-F]{128}", h):
        kind = "128 位 hex → sha512(1700)"
    elif h.startswith("$2a$") or h.startswith("$2y$") or h.startswith("$2b$"):
        kind = "bcrypt(3200) —— 慢哈希，需要真 hashcat 或 john"
    elif h.startswith("$6$"):
        kind = "sha512crypt / Linux shadow(1800) —— 需要真 hashcat 或 john"
    elif h.startswith("$1$"):
        kind = "md5crypt(500) —— 需要真 hashcat 或 john"
    elif h.startswith("$apr1$"):
        kind = "Apache md5crypt(1600)"
    elif h.startswith("$office$"):
        kind = "Office 文档加密(9600 系) —— 需要真 hashcat 或 john"
    elif h.startswith("$zip2?$") or h.startswith("$zip$"):
        kind = "ZIP 加密(13600/17200 系) —— 需要真 hashcat 或 john"
    elif h.startswith("$pdf$"):
        kind = "PDF 加密(10500 系)"
    elif re.fullmatch(r"[0-9a-fA-F]{16}", h):
        kind = "16 位 hex → MySQL323(200) / 老系统哈希，需人工判断"
    elif "：" in h or ":" in h:
        kind = "带冒号的复合格式（hash:salt / user:hash）——本卡取前段按无盐爆破"
    if not kind:
        kind = "未识别：非标准 hex 长度，也不是 $ 前缀的已知格式"
    print(f"{h[:24]}{'…' if len(h) > 24 else ''}  {kind}")
    if kind.startswith(("32 位",)):
        print("提示：md5 和 ntlm 同为 32 位 hex。web/数据库泄露多为 md5，抓的 Windows 凭据是 ntlm，")
        print("      auto 破不动就换 algo 显式试另一个。")


def op_crack(params: dict):
    raw = str(params.get("hashes") or "").strip()
    if not raw:
        die("crack 需要 hashes（逗号分隔或 @文件）")
    if raw.startswith("@"):
        p = Path(raw[1:])
        lines = [l.strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
                 if l.strip() and not l.startswith("#")]
    else:
        lines = [x.strip() for x in raw.split(",")]
    targets = []
    for l in lines:
        h = l.split(":", 1)[0].strip()          # hash:salt 取前段
        if h:
            targets.append(h)
    targets = list(dict.fromkeys(targets))

    algo = str(params.get("algo") or "auto")
    if algo == "auto":
        by_len = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
        ln = len(targets[0])
        algo = by_len.get(ln, "")
        if not algo:
            die(f"auto 认不出 {ln} 位长度：用 op=identify 看看，再显式传 algo")
        if ln == 32:
            print("[auto] 32 位 hex 默认按 md5 爆破（如果是 Windows 凭据请传 algo:ntlm）")

    binp = find_hashcat()
    if binp:
        ok, found = _real_crack(binp, targets, algo, params)
        if ok:
            for t in targets:
                print(f"{t} -> {found.get(t, '（未破解）')}")
            print(f"[统计] 目标 {len(targets)} 破解 {len(found)}")
            sys.exit(0 if found else 1)
        print("[warn] 退回内置 Python 引擎", file=sys.stderr)

    t0 = time.time()
    found = {}
    attack = params.get("attack") or "dict"
    # 校验要在进 Pool 之前做：生成器里 sys.exit 只会杀掉 imap 的喂料线程，主进程死等
    if attack == "mask":
        total = _mask_space(_mask_parse(str(params.get("mask") or "?d?d?d?d?d?d")))
        cap = int(params.get("max_candidates") or 5_000_000)
        if total > cap:
            die(f"掩码空间 {total:,} 超过上限 {cap:,}：加已知位缩小范围（?u?l?l?l?d?d 这类）")
    elif str(params.get("dict") or "builtin").startswith("@"):
        dp = Path(str(params["dict"])[1:])
        if not dp.is_absolute():
            dp = WORDLISTS / str(params["dict"])[1:]
        if not dp.exists():
            die(f"字典不存在: {dp}（大字典放 {WORDLISTS} 目录）")
    workers = max(1, min(int(params.get("workers") or 0) or
                         min(8, (multiprocessing.cpu_count() - 1)), 16))
    fn = _hasher(algo)
    tried = 0

    def gen():
        nonlocal tried
        if attack == "mask":
            mask = str(params.get("mask") or "?d?d?d?d?d?d")
            cap = int(params.get("max_candidates") or 5_000_000)
            for w in _mask_candidates(mask, cap):
                yield w
        else:
            rules = bool(params.get("rules"))
            for w in _dict_words(str(params.get("dict") or "builtin")):
                if rules:
                    yield from _variants(w)
                else:
                    yield w

    with multiprocessing.Pool(workers, initializer=_w_init,
                              initargs=(targets, algo)) as pool:
        for hit in pool.imap_unordered(_w_check, gen(), chunksize=2048):
            tried += 1
            if hit:
                h, plain = hit
                if h not in found:
                    found[h] = plain
                    print(f"{h} -> {plain}", flush=True)
                    if len(found) == len(targets):
                        pool.terminate()
                        break
    dt = max(time.time() - t0, 1e-6)
    print(f"[统计] 目标 {len(targets)} 破解 {len(found)} / 尝试 {tried:,} 次 / "
          f"{dt:.1f}s / {tried / dt:,.0f} 次/s / {workers} 进程")
    miss = [t for t in targets if t not in found]
    if miss:
        for m in miss[:MAX_TRIES_PRINT]:
            print(f"{m} -> （未破解）")
        if len(miss) > MAX_TRIES_PRINT:
            print(f"… 还有 {len(miss) - MAX_TRIES_PRINT} 个未破解")
        print("下一步：@大字典重跑 / rules:true 变体 / mask 掩码 / identify 看类型是否猜错")
    sys.exit(0 if found else 1)


def main():
    params = json.loads(sys.argv[1])
    op = params.get("op") or "crack"
    if op == "status":
        op_status()
    elif op == "identify":
        raw = str(params.get("hashes") or "").strip()
        if not raw:
            die("identify 需要 hashes")
        op_identify([x.strip() for x in raw.split(",")])
    else:
        op_crack(params)


if __name__ == "__main__":
    main()
