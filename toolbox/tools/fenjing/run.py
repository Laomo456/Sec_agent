"""fenjing SSTI 模板注入检测/利用（Jinja2/Flask，自动 WAF 绕过）。

op 分发：scan 整站扫 / crack 打指定参数 / gen_payload 本地生成 payload。
非交互铁律：scan/crack 始终带 -e（fenjing 不带 -e 成功后会进交互 shell 挂死），
crack 缺 exec_cmd 直接拒绝。argv 白名单拼装不经 shell；--tamper-cmd 会在本机
执行命令，刻意不暴露。入口：venv console script > PATH。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOL_DIR = Path(__file__).resolve().parent
WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"

INPUTS_RE = re.compile(r"^[a-zA-Z0-9_.,\[\]-]+$")   # 参数名列表（含 JSON 风格 a.b）
DETECT_MODES = ("fast", "accurate")
ENVIRONMENTS = ("jinja2", "flask")
MAX_OUT = 65536


def resolve_fenjing() -> str | None:
    script = Path(sys.executable).parent / "fenjing.exe"
    if script.exists():
        return str(script)
    return shutil.which("fenjing")


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def _common_flags(params: dict, cmd: list) -> list:
    """scan/crack 共用的可选项。"""
    if params.get("cookies"):
        cmd += ["--cookies", str(params["cookies"])]
    if params.get("user_agent"):
        cmd += ["--user-agent", str(params["user_agent"])]
    if params.get("extra_params"):
        cmd += ["--extra-params", str(params["extra_params"])]
    if params.get("extra_data"):
        cmd += ["--extra-data", str(params["extra_data"])]
    if params.get("detect_mode") in DETECT_MODES:
        cmd += ["--detect-mode", params["detect_mode"]]
    if params.get("environment") in ENVIRONMENTS:
        cmd += ["--environment", params["environment"]]
    if params.get("find_flag"):
        cmd += ["--find-flag", "true"]
    if params.get("no_verify_ssl"):
        cmd.append("--no-verify-ssl")
    if params.get("proxy"):
        cmd += ["--proxy", str(params["proxy"])]
    return cmd


def _url(params: dict) -> str:
    url = str(params.get("url") or "")
    if not url.startswith(("http://", "https://")):
        die(f"url 必须以 http:// 或 https:// 开头: {url!r}")
    return url


def main(params: dict) -> None:
    op = params.get("op") or "scan"
    exe = resolve_fenjing()
    if not exe:
        die("fenjing 未安装：gateway/.venv/Scripts/python.exe -m pip install fenjing")

    if op == "gen_payload":
        keywords = str(params.get("keywords") or "").strip()
        command = str(params.get("command") or "").strip()
        if not keywords or not command:
            die("gen_payload 需要 keywords（被 WAF 的关键字，逗号/换行分隔）和 command")
        words = [w.strip() for w in re.split(r"[,\n]", keywords) if w.strip()]
        if not words:
            die("keywords 解析为空")
        kw_dir = WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "fenjing"
        kw_dir.mkdir(parents=True, exist_ok=True)
        kw_file = kw_dir / "keywords.txt"
        kw_file.write_text("\n".join(words), encoding="utf-8")
        cmd = [exe, "crack-keywords", "-k", str(kw_file), "-c", command]
    elif op in ("scan", "crack"):
        cmd = [exe, op, "-u", _url(params)]
        # 非交互：scan 默认 echo 1 探测确认；crack 必须明确给命令
        exec_cmd = str(params.get("exec_cmd") or "").strip()
        if op == "crack":
            inputs = str(params.get("inputs") or "").strip()
            if not inputs or not INPUTS_RE.match(inputs):
                die("crack 需要 inputs（注入参数名，逗号分隔，如 name 或 username,password）")
            if not exec_cmd:
                die("crack 需要 exec_cmd（成功后执行的命令）；不填会进交互模式，被刻意禁止")
            cmd += ["-i", inputs]
            if params.get("method") in ("GET", "POST", "PUT"):
                cmd += ["-m", params["method"]]
            if params.get("action"):
                cmd += ["-a", str(params["action"])]
        else:
            exec_cmd = exec_cmd or "echo 1"
        cmd += ["-e", exec_cmd]
        cmd = _common_flags(params, cmd)
    else:
        die(f"未知 op: {op!r}（scan / crack / gen_payload）")

    # fenjing 是 Python CLI：强制 UTF-8 输出，否则 Windows 管道下中文全成 GBK 乱码
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(cmd, capture_output=True, env=env)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + f"\n[已截断，共 {len(proc.stdout or b'')} 字节]"
    sys.stdout.write(out)
    if err.strip():
        # 日志肥(WAF 分析逐条 INFO)；命令回显在 stdout 开头，日志只留尾部结论段
        ERR_CAP = 16384
        if len(err) > ERR_CAP:
            err = f"[…前 {len(err) - ERR_CAP} 字节日志省略…]\n" + err[-ERR_CAP:]
        sys.stdout.write("\n[stderr]\n" + err)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
