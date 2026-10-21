"""web_fetch：HTTP 请求工具（curl 子进程）。

- 自动跟随跳转（-L），默认浏览器 UA（很多站点直接拒 curl 默认 UA）
- 不用 -f：404/500 也要拿到响应体给模型看，状态码在尾部标记行里
- text_only：HTML → 纯文本（去 script/style/注释/标签，块级标签断行）。
  抓网页正文必开——原始 HTML 动辄几万 token，全是标签噪音，归一化截断后
  正文根本轮不到出现在 head/tail 里
- 大响应不用自己节流，框架归一化会截断 + read_output 钻取
"""
import json
import re
import subprocess
import sys
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def strip_html(html: str) -> str:
    """HTML → 可读纯文本。不追求精确还原，追求把正文从标签噪音里捞出来。"""
    html = re.sub(r"(?is)<(script|style|noscript|svg|head|iframe)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|li|tr|h[1-6]|section|article|title)>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        html = html.replace(ent, ch)
    lines = (re.sub(r"\s+", " ", l).strip() for l in html.split("\n"))
    return "\n".join(l for l in lines if l)


def main(params: dict) -> None:
    url = params["url"]
    # 非 ASCII 自动百分号编码：模型常直拼中文关键词（如 /s?wd=人工智能），
    # 不编码 curl 直接报 exit 3 (URL malformed)
    url = quote(url, safe=":/?&=#%[]@!$'()*+,;")
    method = params.get("method", "GET")
    timeout = max(5, min(int(params.get("timeout_s", 30)), 120))
    headers = {str(k): str(v) for k, v in (params.get("headers") or {}).items()}

    cmd = ["curl", "-sS", "-L", "--max-time", str(timeout),
           "-X", method,
           "-w", "\n[HTTP %{http_code} · %{size_download}B · %{time_total}s]"]
    if "User-Agent" not in headers:        # headers 里显式给的 UA 优先
        cmd += ["-A", DEFAULT_UA]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if params.get("cookie_jar"):           # 读写同一 jar：跨调用携带 cookie（百度反爬必须）
        cmd += ["-b", params["cookie_jar"], "-c", params["cookie_jar"]]
    if params.get("body") is not None:
        cmd += ["--data", params["body"]]
    cmd.append(url)

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    if out and params.get("text_only") and "<html" in out.lower():
        out = strip_html(out)
    if "网络不给力" in out or "百度安全验证" in out:   # 百度反爬特征串：带内提示，模型当轮自纠
        out += ("\n[提示] 百度反爬拦截（HTTP 200 假页，cookie 两步法也常被拦）。"
                "改用 cn.bing.com/search?q=关键词（裸请求即可，无需 cookie），别在百度上重试。")
    if out:
        print(out)
    if (proc.stderr or "").strip():
        print(f"[curl] {proc.stderr.strip()}", file=sys.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))

