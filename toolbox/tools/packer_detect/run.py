"""packer_detect：判壳工具（docs/unpack-taxonomy.md §1 信号规格的固化实现）。

两路证据合成 verdict：
  1) DIE(diec.exe, 自带 bin/DIE)——签名定种（Packer/Protector/Compiler…）
  2) 自研纯 Python PE 解析——熵/.NET/导入退化/EP 末节/overlay/落地魔数/UPX 布局指纹

verdict 词表对齐 unpack skill §2 分支表：upx / upx_modified / packed_known /
vm_protected / pyinstaller / nuitka / electron / dotnet / packed_suspected /
no_packer / not_pe。判定优先级：.NET 最先 → 广义壳魔数 → 已知签名 → 熵+导入兜底。
"""
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"

# 已知保护/VM 壳节名（taxonomy §2）——命中即"识别即止"
VM_SECTIONS = {".themida", ".boom", ".taz", ".enigma1", ".enigma2", ".asprotect",
               "vmp0", "vmp1", "vmp2"}
VM_NAMES = {"themida", "vmprotect", "enigma", "asprotect", "winlicense"}
# 已知压缩壳节名前缀/全名（非 UPX）
PACKED_SECTIONS = {".aspack", ".adata", ".mew", "pec1", "pec2", "pec3",
                   "nsp0", "nsp1", "nsp2", ".pediy"}
# 正常编译器节名白名单（之外的"可读但陌生"节名只提示不强判）
COMMON_SECTIONS = {".text", ".data", ".rdata", ".bss", ".rsrc", ".reloc", ".idata",
                   ".edata", ".tls", ".pdata", ".xdata", ".CRT", ".didat", ".debug",
                   ".textbss", ".vmp", "CODE", "DATA", "BSS", ".code", ".import"}


def u16(b, o):
    return int.from_bytes(b[o:o + 2], "little")


def u32(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def binary() -> str:
    """bin 相对路径 = 本工具目录下（bin/DIE/diec.exe），绝对路径 = 外部安装。"""
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    bin_ = meta.get("bin") or "diec"
    p = Path(bin_)
    return str(p if p.is_absolute() else Path(__file__).parent / p)


def resolve(p: str) -> Path:
    path = Path(p)
    if path.drive:
        return path
    import os
    return WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files" / path


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    counts = [0] * 256
    for byte in b:
        counts[byte] += 1
    n = len(b)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


# ---------------------------------------------------------------- PE 解析
def parse_pe(data: bytes):
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    lf = u32(data, 0x3C)
    if lf + 24 > len(data) or data[lf:lf + 4] != b"PE\x00\x00":
        return None
    nsec = u16(data, lf + 6)
    optsz = u16(data, lf + 20)
    opt = lf + 24
    magic = u16(data, opt)
    if magic not in (0x10B, 0x20B):
        return None
    plus = magic == 0x20B
    entry_rva = u32(data, opt + 16)
    ndirs_off = opt + (108 if plus else 92)
    ndirs = min(u32(data, ndirs_off), 16)
    dd_off = opt + (112 if plus else 96)
    dirs = [(u32(data, dd_off + 8 * i), u32(data, dd_off + 8 * i + 4))
            for i in range(ndirs)]

    st = opt + optsz
    sections = []
    for i in range(nsec):
        b = data[st + 40 * i: st + 40 * (i + 1)]
        if len(b) < 40:
            break
        sections.append({
            "name": b[:8].rstrip(b"\x00").decode("ascii", "replace"),
            "vsize": u32(b, 8), "vaddr": u32(b, 12),
            "rawsz": u32(b, 16), "rawptr": u32(b, 20),
            "chars": u32(b, 36),
            "entropy": entropy(data[u32(b, 20):u32(b, 20) + u32(b, 16)])
                          if u32(b, 16) and u32(b, 20) + u32(b, 16) <= len(data) else None,
        })

    def rva_off(rva):
        for s in sections:
            if s["vaddr"] <= rva < s["vaddr"] + max(s["vsize"], s["rawsz"]):
                off = s["rawptr"] + (rva - s["vaddr"])
                return off if s["rawsz"] and off < len(data) else None
        return None

    # 导入表：descriptor 数组（20B/项）→ DLL 名 + 函数计数
    dlls, unparsable = [], False
    imp_rva = dirs[1][0] if len(dirs) > 1 else 0
    if imp_rva:
        off0 = rva_off(imp_rva)
        if off0 is None:
            unparsable = True
        else:
            step = 8 if plus else 4
            for i in range(96):
                d = data[off0 + 20 * i: off0 + 20 * (i + 1)]
                if len(d) < 20 or d == b"\x00" * 20:
                    break
                noff = rva_off(u32(d, 12))
                name = "?"
                if noff:
                    end = data.find(b"\x00", noff)
                    name = data[noff:end].decode("ascii", "replace") if end > noff else "?"
                toff = rva_off(u32(d, 0) or u32(d, 16))
                cnt = 0
                if toff:
                    for j in range(8192):
                        if int.from_bytes(data[toff + j * step:toff + (j + 1) * step],
                                          "little") == 0:
                            break
                        cnt += 1
                dlls.append((name, cnt))
    elif not dirs:
        unparsable = True

    end_raw = max((s["rawptr"] + s["rawsz"] for s in sections), default=0)
    ep_sec = next((i for i, s in enumerate(sections)
                   if s["vaddr"] <= entry_rva < s["vaddr"] + max(s["vsize"], s["rawsz"])), None)

    return {
        "plus": plus, "machine": {0x14C: "x86", 0x8664: "amd64"}.get(u16(data, lf + 4), hex(u16(data, lf + 4))),
        "entry_rva": entry_rva, "dirs": dirs, "sections": sections,
        "dotnet": bool(len(dirs) > 14 and dirs[14][0]),
        "imports": dlls, "imports_unparsable": unparsable,
        "overlay_bytes": max(0, len(data) - end_raw),
        "ep_section_index": ep_sec,
    }


# ---------------------------------------------------------------- DIE
def run_die(exe: str, file: Path, deep: bool):
    cmd = [exe, "-j"] + (["-d", "-u"] if deep else []) + [str(file)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[warn] diec 执行失败: {e}", file=sys.stderr)
        return None
    raw = proc.stdout or ""
    start = raw.find("{")   # -u 模式 [!]/[HEUR] 提示行混在 JSON 前，切掉
    if start < 0:
        return None
    try:
        return json.loads(raw[start:])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- 判定
def detect(data: bytes, pe, die) -> dict:
    sig = {}
    secs = pe["sections"] if pe else []
    sec_names = [s["name"] for s in secs]
    entropies = [(s["name"], s["entropy"]) for s in secs if s["entropy"] is not None]
    max_ent = max((e for _, e in entropies), default=0.0)

    if pe:
        # .NET：DataDirectory[14]（COM descriptor）或 DIE .NET 库标记
        die_libs = " ".join(v.get("string", "") for v in die_values(die))
        if pe["dotnet"] or ".NET" in die_libs:
            sig["dotnet"] = True
        # 落地魔数 / 广义壳字符串
        if b"MEI\x0c\x0b\x0a\x0b\x0e" in data:
            sig["magic"] = "PyInstaller MEI cookie"
        if b"UPX!" in data:
            sig.setdefault("magic", "UPX! (l_info)")
        if b"__compiled__" in data or b"nuitka" in data.lower():
            sig["strings"] = "nuitka/__compiled__"
        if b"app.asar" in data:
            sig.setdefault("strings", "app.asar")
        # 节名签名
        known = [n for n in sec_names if n in VM_SECTIONS]
        if known:
            sig["vm_section"] = known
        packed_sec = [n for n in sec_names if n in PACKED_SECTIONS]
        if packed_sec:
            sig["packed_section"] = packed_sec
        upx_sec = [n for n in sec_names if n.upper().startswith("UPX")]
        if upx_sec:
            sig["upx_section"] = upx_sec
        # 节名空泛化（空串/不可打印）
        if any(not n or not n.isprintable() for n in sec_names):
            sig["section_names_stripped"] = True
        # UPX 布局指纹：首节 raw=0 且 vsize 巨大（解压目标区），次节承载高熵数据
        if (len(secs) >= 2 and secs[0]["rawsz"] == 0 and secs[0]["vsize"] > 0x10000
                and secs[1]["rawsz"] > 0x10000):
            sig["upx_layout"] = True
        # 熵 / 导入退化 / EP 末节 / overlay
        if max_ent > 7.2:
            sig["high_entropy"] = [(n, round(e, 2)) for n, e in entropies if e > 7.2]
        n_dll = len(pe["imports"])
        n_api = sum(c for _, c in pe["imports"])
        if pe["imports_unparsable"]:
            sig["imports_degraded"] = "导入表不可解析"
        elif n_dll and (n_dll <= 2 or n_api <= 12):
            sig["imports_degraded"] = f"{n_dll} DLL / {n_api} API"
        if pe["ep_section_index"] is not None and pe["ep_section_index"] == len(secs) - 1:
            sig["ep_in_last_section"] = True
        if pe["overlay_bytes"] > 1024:
            sig["overlay"] = f"{pe['overlay_bytes']} B"

    # DIE 签名侧
    packers = [v for v in die_values(die) if v.get("type") == "Packer"]
    prots = [v for v in die_values(die) if "Protection" in (v.get("type") or "")]
    if packers:
        sig["die_packer"] = [v["string"] for v in packers]
    vm_prots = [v["string"] for v in prots
                if any(k in v["string"].lower() for k in VM_NAMES)]
    if vm_prots:
        sig["die_protector"] = vm_prots

    # ---- verdict（优先级见模块 docstring）----
    if not pe:
        verdict = "not_pe"
    elif sig.get("dotnet"):
        verdict = "dotnet"
    elif "MEI" in sig.get("magic", "") or "PyInstaller" in sig.get("magic", ""):
        verdict = "pyinstaller"
    elif sig.get("strings") == "nuitka/__compiled__":
        verdict = "nuitka"
    elif (sig.get("upx_section") or sig.get("die_packer")
          and any("upx" in p.lower() for p in sig["die_packer"])
          or "UPX" in sig.get("magic", "")):
        die_upx = " ".join(sig.get("die_packer", [])).lower()
        if ("modified" in die_upx
                or (sig.get("section_names_stripped") and "UPX" not in sig.get("magic", ""))):
            verdict = "upx_modified"   # 魔数/节名被动过 → upx -d 会失败，先走救援
        else:
            verdict = "upx"
    elif sig.get("vm_section") or sig.get("die_protector"):
        verdict = "vm_protected"
    elif sig.get("die_packer") or sig.get("packed_section"):
        verdict = "packed_known"
    elif sig.get("high_entropy") and (sig.get("imports_degraded") or sig.get("upx_layout")):
        if sig.get("section_names_stripped") or sig.get("upx_layout"):
            verdict = "upx_modified"   # 节名/魔数被抹，但 UPX 布局指纹在 → 走救援
        else:
            verdict = "packed_suspected"
    elif sig.get("strings") == "app.asar":
        verdict = "electron"
    else:
        verdict = "no_packer"

    known_name = ""
    if verdict == "packed_known":
        known_name = (sig.get("die_packer") or sig.get("packed_section") or [""])[0]
    if verdict == "vm_protected":
        known_name = (sig.get("die_protector") or sig.get("vm_section") or [""])[0]
    return {"verdict": verdict, "known_name": known_name, "signals": sig}


def die_values(die):
    if not die:
        return []
    return [v for det in die.get("detects", []) for v in det.get("values", [])]


# ---------------------------------------------------------------- 魔数/字节搜索
def parse_pattern(q: str) -> bytes:
    """搜索串 → bytes 正则。hex:/ascii: 前缀显式指定；默认纯十六进制字符（含空格/?）→ hex，
    其余按 ascii 字面量。?? = 任意字节通配（与 IDA find_bytes 语义一致）。"""
    q = q.strip()
    if q.lower().startswith("hex:"):
        return _hex_regex(q[4:])
    if q.lower().startswith("ascii:"):
        return re.escape(q[6:].encode("utf-8", "replace"))
    body = q.replace(" ", "")
    if body and all(c in "0123456789abcdefABCDEF?" for c in body):
        return _hex_regex(q)
    return re.escape(q.encode("utf-8", "replace"))


def _hex_regex(q: str) -> bytes:
    toks = q.split()
    if len(toks) <= 1:                      # 无空格串："555058" 两两切
        body = q.replace(" ", "")
        if len(body) % 2:
            raise ValueError(f"hex 长度为奇数: {q!r}")
        toks = [body[i:i + 2] for i in range(0, len(body), 2)]
    parts = []
    for t in toks:
        if not t:
            continue
        if set(t) == {"?"}:
            parts.append(b".")
        elif len(t) == 2 and all(c in "0123456789abcdefABCDEF" for c in t):
            parts.append(b"\\x" + t.lower().encode("ascii"))
        else:
            raise ValueError(f"非法 hex 字节 {t!r}（示例：'55 50 58'、'55 ?? 58'）")
    if not parts:
        raise ValueError("空模式")
    return b"".join(parts)


def section_of(pe, off: int):
    """文件偏移落在哪个节（rawptr..rawptr+rawsz），节外标 overlay/头部。"""
    if not pe:
        return None
    for s in pe["sections"]:
        if s["rawptr"] <= off < s["rawptr"] + s["rawsz"]:
            return s["name"] or "(空)"
    return "overlay" if pe["overlay_bytes"] and off >= max(
        (s["rawptr"] + s["rawsz"] for s in pe["sections"]), default=0) else "头部"


def search_report(data: bytes, pe, q: str, limit: int = 20) -> None:
    """魔数/字节模式搜索：命中偏移 + 节归属 + 上下文窗口。0 命中也是结论（魔数不存在）。"""
    rx = parse_pattern(q)
    hits = [m.start() for m in re.finditer(rx, data, re.DOTALL)]
    print(f"\n[search] {q!r} → 命中 {len(hits)} 处"
          + ("（仅列前 %d）" % limit if len(hits) > limit else ""))
    for off in hits[:limit]:
        sec = section_of(pe, off) or "-"
        win = data[off:off + 16]
        hexs = " ".join(f"{b:02x}" for b in win[:8]) + (" …" if len(win) > 8 else "")
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in win)
        print(f"  off 0x{off:08x} [{sec}]  {hexs}  |{text}|")
    if not hits:
        print("  0 命中——魔数不存在，这本身就是结论（如排除 UPX 壳）")


ADVICE = {
    "upx": "upx 工具 action=unpack（不覆盖原文件，产物落工作区）",
    "upx_modified": "先找回魔数/节名（taxonomy §5 布局）再 upx -d；upx 工具 rescue 参数计划中",
    "packed_known": "动态 dump OEP：dump_oep 计划中，先 x64dbg 手工（ESP 定律/PAGE_GUARD）",
    "vm_protected": "识别即止：报证据（节名/熵/反调试），禁止进循环烧预算",
    "pyinstaller": "pyinstxtractor 逆出 pyc（工具计划中，先手工脚本定位 MEI cookie）",
    "nuitka": "无 pyc 可还原，转 re-analysis 原生逆向流程",
    "electron": "asar 解包即得 JS 源码（node 工具或手工解包）",
    "dotnet": "立即换轨：PE 管线失效；de4dot 反混淆 / dnSpy 级分析（外置工具链）",
    "packed_suspected": "高熵+导入退化：按未知压缩壳走通用动态 dump",
    "no_packer": "无壳证据，直接进反编译/字符串分析",
    "not_pe": "非 PE 文件，参考下方 DIE 原始识别",
}


def main(params: dict) -> None:
    f = resolve(params["file"])
    if not f.is_file():
        print(f"文件不存在: {f}", file=sys.stderr)
        sys.exit(2)
    deep = params.get("deep", True)

    data = f.read_bytes()
    pe = parse_pe(data)

    exe = binary()
    die = run_die(exe, f, deep) if Path(exe).exists() else None
    if die is None:
        print("[warn] DIE 签名引擎不可用（bin/DIE/diec.exe），仅输出自研 PE 信号", file=sys.stderr)

    r = detect(data, pe, die)

    print(f"packer_detect: {f.name} ({len(data)} B)")
    if pe:
        print(f"PE: {pe['machine']} {'PE32+' if pe['plus'] else 'PE32'}, "
              f"EP rva=0x{pe['entry_rva']:x}"
              + (f"(节[{pe['ep_section_index']}]="
                  f"{pe['sections'][pe['ep_section_index']]['name'] or '(空)'})"
                 if pe["ep_section_index"] is not None else ""))
        for s in pe["sections"]:
            ent = f"{s['entropy']:.2f}" if s["entropy"] is not None else "-"
            print(f"  节 {s['name'] or '(空)':<10} raw={s['rawsz']:>9}  熵={ent}")
        if pe["imports"]:
            detail = ", ".join(f"{n}({c})" for n, c in pe["imports"][:8])
            more = " …" if len(pe["imports"]) > 8 else ""
            print(f"  导入: {len(pe['imports'])} DLL / "
                  f"{sum(c for _, c in pe['imports'])} API [{detail}{more}]")
        if pe["overlay_bytes"] > 0:
            print(f"  overlay: {pe['overlay_bytes']} B")
    else:
        print("PE: 非 PE 文件（MZ/PE 头缺失）")

    q = params.get("search")
    if q:
        try:
            search_report(data, pe, str(q))
        except ValueError as e:
            print(f"[error] search 参数无效: {e}；'UPX!' 按 ascii，'55 50 58' 按 hex，"
                  f"加 hex:/ascii: 前缀可显式指定", file=sys.stderr)
            sys.exit(2)

    print(f"\n[verdict] {r['verdict']}" + (f" ({r['known_name']})" if r["known_name"] else ""))
    if r["signals"]:
        print("命中信号:")
        for k, v in r["signals"].items():
            print(f"  {k}: {v}")
    else:
        print("命中信号: 无")
    print(f"建议: {ADVICE[r['verdict']]}")

    if die:
        for det in die.get("detects", []):
            print(f"DIE[{det.get('filetype', '?')}]:")
            for v in det.get("values", [])[:12]:
                print(f"  {v.get('string', v.get('name', '?'))}")


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
