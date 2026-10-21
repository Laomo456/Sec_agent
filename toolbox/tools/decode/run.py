r"""decode 工具入口:编解码瑞士军刀——detect 自动识别 + 各类编解码/古典密码还原。

契约(runner.py):argv[1] = 参数 JSON(op 在顶层,params 嵌套);stdout 打紧凑 JSON;
退出码 0=ok,2=参数错,1=运行错。纯 stdlib 零依赖,无文件/网络副作用(全 op readonly)。

设计原则:
  - 解码结果同时给 text(尽力 utf-8/gbk 严格解)+ hex(二进制保底)
  - 容忍真实世界输入:base64 带换行、缺 padding 自动补、hex 带 0x/\x/空格
  - detect 只多解一层套娃、不做深搜——识别不出来引导模型去逆向找码表/密钥
"""
import base64
import binascii
import html as _html
import json
import re
import sys
import urllib.parse

MAX_INPUT = 262144            # 256KB:data 上限,防超大输入拖垮子进程
MAX_PREVIEW = 120             # detect / xor 暴力候选的预览长度
BRUTE_WINDOW = 4096           # xor 单字节暴力只对前 4KB 打分
B64_STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
B32_STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
B58_STD = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B62_STD = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "-": "-....-",
    "@": ".--.-.", ":": "---...", "'": ".----.", "!": "-.-.--", "(": "-.--.",
    ")": "-.--.-", '"': ".-..-.", "=": "-...-", "+": ".-.-.",
}
MORSE_REV = {v: k for k, v in MORSE.items()}
# 关键词加分:CTF flag / 常见敏感词出现在解码结果里是强信号
_KEYWORDS = ("flag{", "ctf", "key", "pass", "admin", "secret", "token", "http")
# rot 门槛词:移位后的字母串可打印占比恒为 1.0,必须靠词命中才能算识别成功
_EN_WORDS = ("the", "and", "this", "that", "you", "hello", "world", "welcome",
             "code", "test", "good", "like", "is", "my")


# CJK 加分门槛:GBK 对高位字节串几乎总能"严格解出"汉字汤(占位满、全可打印),
# 只有命中常用字才认定是真中文——真中文几乎必含常用字,乱码汤几乎不命中。
_COMMON_CJK = set("的一是不了在人有我他这个上们来到时大地为子中你说生国年着就那和要她出也得里"
                  "后自以会家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样"
                  "道想作种开密钥测试中文题解")


def _has_words(s: str) -> bool:
    low = f" {s.lower()} "
    return (any(k in low for k in _KEYWORDS)
            or any(f" {w} " in low for w in _EN_WORDS))


def _fail(detail: str, code: int = 1) -> int:
    print(json.dumps({"status": "error", "detail": detail}, ensure_ascii=False))
    return code


def _get_data(p: dict) -> str:
    d = str(p.get("data") or "")
    if not d.strip():
        raise ValueError("data 不能为空")
    if len(d) > MAX_INPUT:
        raise ValueError(f"data 超过 {MAX_INPUT // 1024}KB 上限")
    return d


# ── 通用小工具 ─────────────────────────────────────────────

def _best_text(b: bytes) -> tuple[str, str]:
    """严格解码:utf-8 → gbk,失败返回 ("","")。不做 latin-1 兜底(那只会产出垃圾文本)。"""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return "", ""


def _score_text(s: str) -> float:
    """可读性打分:可打印占比为基础(0~1),CJK 占比>30% +0.15、关键词命中 +0.1。
    加分允许超过 1.0——排序需要它区分"同样全可打印"的候选(如 xor 暴力里
    0x20 翻转大小写后仍全可打印,只有真答案带关键词/中文才分得出高下)。"""
    if not s:
        return 0.0
    score = sum(1 for c in s if c.isprintable() or c in "\n\r\t") / len(s)
    cjk = [c for c in s if "一" <= c <= "鿿"]
    if cjk:
        # 常用字覆盖率:真中文 40%+,乱码汤 ~1%——20% 是稳的分界线
        cover = sum(1 for c in cjk if c in _COMMON_CJK) / len(cjk)
        if len(cjk) / len(s) > 0.3 and cover >= 0.2:
            score += 0.15
    low = s.lower()
    if any(k in low for k in _KEYWORDS):
        score += 0.1
    return round(score, 3)


def _bytes_result(op: str, b: bytes, **extra) -> dict:
    text, enc = _best_text(b)
    return {"status": "ok", "op": op, "length": len(b), "hex": b.hex(),
            "text": text, "encoding": enc, **extra}


# ── 各编解码实现 ───────────────────────────────────────────

def op_base64(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    alphabet, urlsafe = str(p.get("alphabet") or ""), bool(p.get("urlsafe"))
    if alphabet and (len(alphabet) != 64 or len(set(alphabet)) != 64):
        raise ValueError("alphabet 必须是 64 个互不重复的字符")
    if encode:
        raw = data.encode("utf-8")
        out = base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)
        if alphabet:
            out = out.decode().translate(str.maketrans(B64_STD, alphabet)).encode()
        return {"status": "ok", "op": "base64", "mode": "encode",
                "encoded": out.decode(), "length": len(out)}
    s = re.sub(r"\s+", "", data)
    if alphabet:
        s = s.translate(str.maketrans(alphabet, B64_STD))
    elif urlsafe:
        s = s.translate(str.maketrans("-_", "+/"))
    s = s.rstrip("=")            # 先剥已有 padding 再按需补(容忍 == 结尾又缺段的输入)
    if len(s) % 4 == 1:
        raise ValueError("base64 长度不合法(%4 余 1),可能截断了")
    s += "=" * (-len(s) % 4)
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"base64 解码失败({exc});换表请传 alphabet 参数") from exc
    return _bytes_result("base64", raw, mode="decode",
                         custom_alphabet=bool(alphabet), urlsafe=urlsafe)


def op_base32(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        out = base64.b32encode(data.encode("utf-8")).decode()
        return {"status": "ok", "op": "base32", "mode": "encode",
                "encoded": out, "length": len(out)}
    s = re.sub(r"[\s=]+", "", data)
    if not s or not re.fullmatch(r"[A-Z2-7]+", s):
        raise ValueError("不是合法 base32 字符集(A-Z、2-7)")
    s += "=" * (-len(s) % 8)
    try:
        raw = base64.b32decode(s)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"base32 解码失败: {exc}") from exc
    return _bytes_result("base32", raw, mode="decode")


def _bignum_decode(s: str, alphabet: str) -> bytes:
    if not s or any(c not in alphabet for c in s):
        raise ValueError(f"不在 base{len(alphabet)} 字符集内")
    lead = len(s) - len(s.lstrip(alphabet[0]))
    n = 0
    for c in s:
        n = n * len(alphabet) + alphabet.index(c)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * lead + body


def _bignum_encode(b: bytes, alphabet: str) -> str:
    n, zeros = int.from_bytes(b, "big"), 0
    for byte in b:
        if byte:
            break
        zeros += 1
    out = ""
    while n:
        n, r = divmod(n, len(alphabet))
        out = alphabet[r] + out
    return alphabet[0] * zeros + out


def op_base58(p: dict) -> dict:
    return _bignum_op(p, "base58", B58_STD)


def op_base62(p: dict) -> dict:
    return _bignum_op(p, "base62", B62_STD)


def _bignum_op(p: dict, op: str, alphabet: str) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        out = _bignum_encode(data.encode("utf-8"), alphabet)
        return {"status": "ok", "op": op, "mode": "encode",
                "encoded": out, "length": len(out)}
    return _bytes_result(op, _bignum_decode(data, alphabet), mode="decode")


def op_hex(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        out = data.encode("utf-8").hex()
        return {"status": "ok", "op": "hex", "mode": "encode",
                "encoded": out, "length": len(out)}
    s = re.sub(r"0[xX]", "", data).replace("\\x", "").replace("\\X", "")
    s = re.sub(r"[\s,;]+", "", s)
    if not re.fullmatch(r"[0-9a-fA-F]*", s) or len(s) % 2:
        raise ValueError("不是合法 hex(需要偶数个 0-9a-f 字符)")
    return _bytes_result("hex", bytes.fromhex(s), mode="decode")


def op_url(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        out = urllib.parse.quote(data, safe="")
        return {"status": "ok", "op": "url", "mode": "encode",
                "encoded": out, "length": len(out)}
    out = (urllib.parse.unquote_plus(data) if bool(p.get("plus"))
           else urllib.parse.unquote(data))
    return {"status": "ok", "op": "url", "mode": "decode", "text": out}


def op_unicode_escape(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        out = data.encode("unicode_escape").decode("ascii")
        return {"status": "ok", "op": "unicode_escape", "mode": "encode",
                "encoded": out, "length": len(out)}
    # 输入应为 ASCII 转义串:latin-1 编码保持 \uXXXX 字面量不动
    try:
        raw = data.encode("latin-1")
    except UnicodeEncodeError:
        raw = data.encode("utf-8")
    out = raw.decode("unicode_escape")
    return {"status": "ok", "op": "unicode_escape", "mode": "decode", "text": out}


def op_html(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        out = _html.escape(data, quote=False)
        return {"status": "ok", "op": "html", "mode": "encode",
                "encoded": out, "length": len(out)}
    return {"status": "ok", "op": "html", "mode": "decode",
            "text": _html.unescape(data)}


def _rot_letters(s: str, shift: int, encode: bool) -> str:
    d = shift if encode else -shift
    out = []
    for c in s:
        if "a" <= c <= "z":
            out.append(chr((ord(c) - 97 + d) % 26 + 97))
        elif "A" <= c <= "Z":
            out.append(chr((ord(c) - 65 + d) % 26 + 65))
        else:
            out.append(c)
    return "".join(out)


def _rot47(s: str) -> str:          # rot47 对 47 取模自反,加解密同函数
    return "".join(chr(33 + (ord(c) - 33 + 47) % 94) if 33 <= ord(c) <= 126 else c
                   for c in s)


def op_rot(p: dict) -> dict:
    data = _get_data(p)
    rot47, encode = bool(p.get("rot47")), bool(p.get("encode"))
    try:
        shift = int(p.get("shift", 13)) % 26
    except (TypeError, ValueError):
        raise ValueError("shift 必须是整数")
    out = _rot47(data) if rot47 else _rot_letters(data, shift, encode)
    return {"status": "ok", "op": "rot", "mode": "rot47" if rot47 else
            ("encode" if encode else "decode"), "text": out,
            "shift": 47 if rot47 else shift}


def op_xor(p: dict) -> dict:
    data = _get_data(p)
    data_bytes = (bytes.fromhex(re.sub(r"[\s,]+", "", data))
                  if bool(p.get("data_hex")) else data.encode("utf-8"))
    key = str(p.get("key") or "")
    if key:
        try:
            key_bytes = (bytes.fromhex(re.sub(r"[\s,]+", "", key))
                         if bool(p.get("key_hex")) else key.encode("utf-8"))
        except ValueError as exc:
            raise ValueError(f"key_hex 不是合法 hex: {exc}") from exc
        if not key_bytes:
            raise ValueError("key 解出来是空的")
        out = bytes(b ^ key_bytes[i % len(key_bytes)]
                    for i, b in enumerate(data_bytes))
        return _bytes_result("xor", out, mode="key",
                             key_hex=key_bytes.hex())
    # 无 key:暴力 256 个单字节,按可读性打分取 top
    window = data_bytes[:BRUTE_WINDOW]
    cands = sorted(
        ((k, _score_text(_best_text(bytes(b ^ k for b in window))[0]))
         for k in range(256)), key=lambda x: (-x[1], x[0]))[:8]
    return {"status": "ok", "op": "xor", "mode": "brute_single_byte",
            "candidates": [{"key_hex": f"{k:02x}",
                            "key_char": chr(k) if 32 <= k < 127 else "",
                            "score": sc,
                            "preview": _best_text(bytes(b ^ k for b in window))[0]
                            [:MAX_PREVIEW]}
                           for k, sc in cands if sc > 0]}


def op_binary(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        out = "".join(f"{b:08b}" for b in data.encode("utf-8"))
        return {"status": "ok", "op": "binary", "mode": "encode",
                "encoded": out, "length": len(out)}
    s = re.sub(r"[\s]+", "", data)
    if not s or not re.fullmatch(r"[01]+", s):
        raise ValueError("binary 输入只能包含 0/1 和空白")
    pad = -len(s) % 8
    if pad:
        s = "0" * pad + s          # 前 0 视为被截掉的高位
    raw = bytes(int(s[i:i + 8], 2) for i in range(0, len(s), 8))
    return _bytes_result("binary", raw, mode="decode", padded_bits=pad)


def op_morse(p: dict) -> dict:
    data, encode = _get_data(p), bool(p.get("encode"))
    if encode:
        words, skipped = [], []
        for word in data.upper().split():
            letters = []
            for c in word:
                if c in MORSE:
                    letters.append(MORSE[c])
                else:
                    skipped.append(c)
            words.append(" ".join(letters))
        r = {"status": "ok", "op": "morse", "mode": "encode",
             "encoded": " / ".join(words), "length": sum(len(w) for w in words) + 1}
        if skipped:
            r["note"] = f"无摩斯映射被跳过的字符: {''.join(sorted(set(skipped)))}"
        return r
    s = data.replace("_", "-")
    words = re.split(r"\s*/\s*|\s{2,}", s.strip())
    out, unknown = [], 0
    for word in words:
        letters = []
        for tok in word.split():
            if tok in MORSE_REV:
                letters.append(MORSE_REV[tok])
            else:
                unknown += 1
        out.append("".join(letters))
    r = {"status": "ok", "op": "morse", "mode": "decode", "text": " ".join(out)}
    if unknown:
        r["note"] = f"{unknown} 个未知码点被跳过"
    return r


def _cjk_ratio(s: str) -> float:
    return (sum(1 for c in s if "一" <= c <= "鿿") / len(s)) if s else 0.0


_MOJI_CHAINS = [
    # Windows 编辑器里看到的乱码多半是 cp1252 显示的(‹•€ 这些字符不在 latin-1 里)
    ("utf-8 字节被按 cp1252 显示", lambda s: s.encode("cp1252").decode("utf-8")),
    ("utf-8 字节被按 latin-1 显示", lambda s: s.encode("latin-1").decode("utf-8")),
    ("utf-8 字节被按 gbk 显示", lambda s: s.encode("gbk").decode("utf-8")),
    ("gbk 字节被按 latin-1 显示", lambda s: s.encode("latin-1").decode("gbk")),
    ("gbk 字节被按 cp1252 显示", lambda s: s.encode("cp1252").decode("gbk")),
    ("gbk 字节被按 utf-8 显示", lambda s: s.encode("utf-8").decode("gbk")),
]


def op_mojibake(p: dict) -> dict:
    data = _get_data(p)
    results = []
    for name, fn in _MOJI_CHAINS:
        try:
            text = fn(data)
        except (UnicodeError, ValueError):
            continue
        ratio = _cjk_ratio(text)
        if text != data and (ratio > 0.15 or _score_text(text) >= 0.9):
            results.append({"chain": name, "cjk_ratio": round(ratio, 3),
                            "score": _score_text(text), "text": text[:5000]})
    if not results:
        raise ValueError("四种错配组合都解不出中文/可读文本;"
                         "可能是别的编码对(如 big5/shift_jis),或本身不是乱码")
    results.sort(key=lambda r: (-r["cjk_ratio"], -r["score"]))
    return {"status": "ok", "op": "mojibake", "results": results}


# ── detect:自动识别 ────────────────────────────────────────

def _battery(data: str, quick: bool = False) -> list:
    """返回 [(codec, text, score)]。quick=True 只试 base64/hex(用于二层套娃)。"""
    out = []
    stripped = re.sub(r"\s+", "", data)

    def add(codec: str, b: bytes, note: str = ""):
        text, _ = _best_text(b)
        if text:
            out.append((codec + (f"({note})" if note else ""), text,
                        _score_text(text)))

    if re.fullmatch(r"[0-9a-fA-F]+", stripped) and len(stripped) >= 6 \
            and len(stripped) % 2 == 0:
        try:
            add("hex", bytes.fromhex(stripped))
        except ValueError:
            pass
    b64_like = re.fullmatch(r"[A-Za-z0-9+/=_-]+", stripped)
    if b64_like and len(stripped) >= 8 and len(stripped) % 4 != 1:
        urlsafe = "-" in stripped or "_" in stripped
        try:
            s = stripped.translate(str.maketrans("-_", "+/")) if urlsafe else stripped
            s += "=" * (-len(s) % 4)
            add("base64", base64.b64decode(s, validate=True),
                "urlsafe" if urlsafe else "")
        except (binascii.Error, ValueError):
            pass
    if quick:
        return out
    if re.fullmatch(r"[A-Z2-7=]+", stripped) and len(stripped) >= 8:
        try:
            add("base32", base64.b32decode(stripped + "=" * (-len(stripped) % 8)))
        except (binascii.Error, ValueError):
            pass
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", stripped) and len(stripped) >= 6:
        try:
            add("base58", _bignum_decode(stripped, B58_STD))
        except ValueError:
            pass
    if "%" in data:
        try:
            text = urllib.parse.unquote(data)
            if text != data:
                out.append(("url", text, _score_text(text)))
        except Exception:
            pass
    if "\\u" in data.lower() or "\\x" in data.lower():
        try:
            raw = data.encode("latin-1")
            out.append(("unicode_escape", raw.decode("unicode_escape"),
                        _score_text(raw.decode("unicode_escape"))))
        except (UnicodeError, ValueError):
            pass
    if "&#" in data or re.search(r"&[a-zA-Z]+;", data):
        text = _html.unescape(data)
        if text != data:
            out.append(("html", text, _score_text(text)))
    if re.fullmatch(r"[01\s]+", data.strip()) and len(stripped) >= 8:
        pad = -len(stripped) % 8
        bits = "0" * pad + stripped
        try:
            add("binary", bytes(int(bits[i:i + 8], 2)
                                for i in range(0, len(bits), 8)))
        except ValueError:
            pass
    if re.fullmatch(r"[.\-\s/_]+", data.strip()) and set(data) & set(".-"):
        r = op_morse({"data": data})
        if r.get("text"):
            out.append(("morse", r["text"], _score_text(r["text"])))
    # rot:26 个移位全试,取最优;必须词命中,否则任意字母串都会因可打印占比误报
    best_rot = max(((_score_text(_rot_letters(data, k, True)), k)
                    for k in range(1, 26)), default=(0, 0))
    rot_text = _rot_letters(data, best_rot[1], True)
    if best_rot[0] >= 0.85 and _has_words(rot_text):
        out.append((f"rot{best_rot[1]}", rot_text, best_rot[0]))
    if any("\x80" <= c <= "\xff" or c == "锟" for c in data):
        try:
            for r in op_mojibake({"data": data})["results"][:1]:
                out.append((f"mojibake({r['chain']})", r["text"], r["score"]))
        except ValueError:
            pass
    return out


def op_detect(p: dict) -> dict:
    data = _get_data(p)
    level1 = _battery(data)
    # 二层套娃:对一层结果里可读性高的再解一层(限 base64/hex)
    cands = []
    for codec, text, score in level1:
        cands.append((codec, text, score))
        if score >= 0.9 and len(text) >= 8:
            for codec2, text2, score2 in _battery(text, quick=True):
                cands.append((f"{codec}>{codec2}", text2, score2))
    cands.sort(key=lambda x: (-x[2], x[0]))
    seen, uniq = set(), []
    for codec, text, score in cands:
        if text in seen:
            continue
        seen.add(text)
        uniq.append({"codec": codec, "score": score,
                     "preview": text[:MAX_PREVIEW]})
    return {"status": "ok", "op": "detect", "input_len": len(data),
            "candidates": uniq[:6],
            "hint": "" if uniq else
                    "未识别:可能是指自定义码表 base64(逆向找表传给 base64 的 "
                    "alphabet)、三层以上套娃(拿候选/中间结果再 detect 一次)、"
                    "或真加密(AES/RC4——先逆向找密钥)"}


OPS = {
    "detect": op_detect, "base64": op_base64, "xor": op_xor, "hex": op_hex,
    "rot": op_rot, "base32": op_base32, "base58": op_base58,
    "base62": op_base62, "binary": op_binary, "url": op_url,
    "unicode_escape": op_unicode_escape, "html": op_html, "morse": op_morse,
    "mojibake": op_mojibake,
}


def main() -> int:
    try:
        req = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except (json.JSONDecodeError, IndexError):
        return _fail("参数必须是 JSON:{\"op\": ..., \"params\": {...}}", 2)
    op = str(req.get("op") or "")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        return _fail("params 必须是对象", 2)
    fn = OPS.get(op)
    if fn is None:
        return _fail(f"未知 op: {op!r}。可用: {'/'.join(OPS)}", 2)
    try:
        result = fn(params)
    except ValueError as exc:
        return _fail(str(exc), 2)
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
