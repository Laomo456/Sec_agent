"""声明式工具的执行体（http / command 两种 kind），由 runner 以子进程方式调用：

    python kinds_runner.py <tool.yaml 路径> '<params-json>'

和 script 工具走完全相同的管道（子进程隔离、超时、stdout 归一化、落盘审计），
区别只是"实现"写在 tool.yaml 里而不是 run.py。

模板语法：{{参数名}}，缺省参数替换为空串；{{参数名|默认值}} 带默认值。
  http:    url / headers / query / body 里都能用；body 为 dict 时按 JSON 发送
  command: argv 列表逐项替换（不经 shell，参数注入无从谈起）；
           某项替换后为空串则丢弃（可选参数整项省略）
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WS_ROOT = Path(__file__).resolve().parent / "workspace"
_VAR = re.compile(r"\{\{\s*([A-Za-z_][\w]*)\s*(?:\|([^}]*))?\}\}")
# 命令工具可执行程序白名单：与 run_cmd 同一安全边界，另外允许 bin_map 显式映射的路径
COMMAND_ALLOW = {"python", "pip", "curl", "ping", "nslookup", "ipconfig", "whoami",
                 "echo", "where", "findstr", "git", "node", "npm", "tar", "powershell"}


def render(tpl, params: dict):
    """递归渲染。字符串恰好是一个 {{x}} 时保留原类型（让 JSON body 里的数字/布尔不变成字符串）。"""
    if isinstance(tpl, str):
        m = _VAR.fullmatch(tpl.strip())
        if m:
            v = params.get(m.group(1))
            return v if v not in (None, "") else (m.group(2) or "")
        return _VAR.sub(lambda m: _s(params.get(m.group(1), m.group(2) or "")), tpl)
    if isinstance(tpl, list):
        return [render(x, params) for x in tpl]
    if isinstance(tpl, dict):
        return {k: render(v, params) for k, v in tpl.items()}
    return tpl


def _s(v) -> str:
    if v is None:
        return ""
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)


def run_http(spec: dict, params: dict, timeout: int) -> int:
    import httpx
    method = str(spec.get("method", "GET")).upper()
    url = render(spec.get("url", ""), params)
    if not re.match(r"^https?://", str(url)):
        print(f"URL 不合法: {url!r}", file=sys.stderr)
        return 2
    headers = {k: _s(v) for k, v in (render(spec.get("headers") or {}, params)).items() if _s(v)}
    query = {k: v for k, v in (render(spec.get("query") or {}, params)).items() if v not in (None, "")}
    kw = {}
    body = spec.get("body")
    if body is not None and method not in ("GET", "HEAD"):
        rendered = render(body, params)
        if isinstance(rendered, (dict, list)):
            kw["json"] = rendered
        else:
            kw["content"] = _s(rendered).encode()
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.request(method, str(url), headers=headers, params=query, **kw)
    text = r.text
    pick = spec.get("pick")          # 可选：从 JSON 响应里只取某个路径，如 data.items
    if pick and "json" in r.headers.get("content-type", ""):
        try:
            v = r.json()
            for part in str(pick).split("."):
                v = v[int(part)] if isinstance(v, list) else v.get(part)
            text = json.dumps(v, ensure_ascii=False, indent=1)
        except Exception as e:
            text += f"\n[pick 失败: {e}]"
    print(text)
    print(f"\n[HTTP {r.status_code} · {len(r.content)}B]")
    return 0 if r.status_code < 400 or spec.get("allow_error") else 1


def run_command(spec: dict, params: dict, tool_dir: Path, timeout: int) -> int:
    argv = [x for x in (render(a, params) for a in spec.get("argv") or []) if _s(x) != ""]
    argv = [_s(x) for x in argv]
    if not argv:
        print("command.argv 为空", file=sys.stderr)
        return 2
    exe = argv[0]
    bin_map = spec.get("bin_map") or {}
    if exe in bin_map:
        p = Path(str(bin_map[exe])).expanduser()
        argv[0] = str(p if p.is_absolute() else (tool_dir / p).resolve())
    elif exe not in COMMAND_ALLOW:
        print(f"程序 {exe!r} 不在命令白名单 {sorted(COMMAND_ALLOW)}，"
              "需要时在 command.bin_map 里显式映射", file=sys.stderr)
        return 2
    cwd = WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files"
    cwd.mkdir(parents=True, exist_ok=True)
    stdin = render(spec.get("stdin"), params) if spec.get("stdin") else None
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, input=_s(stdin) if stdin else None,
                          encoding="utf-8", errors="replace", timeout=timeout)
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    return proc.returncode


def main() -> int:
    yml, params = Path(sys.argv[1]), json.loads(sys.argv[2])
    meta = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    kind = meta.get("kind")
    timeout = max(1, int(meta.get("timeout", 60)) - 2)   # 留余量给外层 runner 的超时
    if kind == "http":
        return run_http(meta.get("http") or {}, params, timeout)
    if kind == "command":
        return run_command(meta.get("command") or {}, params, yml.parent, timeout)
    print(f"未知 kind: {kind}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:           # 声明写错时给出可读的错误，不要 traceback 一屏
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
