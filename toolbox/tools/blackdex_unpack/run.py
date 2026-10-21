r"""blackdex_unpack BlackDex 脱壳卡:免 root 免 frida 的设备侧脱壳 app 的装卸与产物回收。

契约(runner.py):argv[1] = 参数 JSON(op 顶层 + params 嵌套);stdout 打可读文本;
退出码 0=ok,2=参数/校验错,1=执行失败。

bin 待接入形态:
  bin/BlackDex.apk   https://github.com/CodingGay/BlackDex/releases 下载
  bin/adb.exe        可选;缺时复用 ../android_unpack/bin/adb.exe(MuMu 自带版)
点选目标 app 的那一步发生在模拟器 UI 里(人工/远控),本卡管其余全自动。
"""
import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mobile_runtime import (ArgError, OpError, ROOT, EXTERNAL, files_root, resolve, text,
                            integer, boolean, package, output_dir, run_process, dispatch, prepare_output, read_config)

TOOL_DIR = Path(__file__).resolve().parent
BIN = TOOL_DIR / "bin"
APK = BIN / "BlackDex.apk"
DL_URL = "https://github.com/CodingGay/BlackDex/releases"
REMOTE_DUMP = "/sdcard/BlackDex"

DEFAULT_CONFIG = {"adb_port": 16384}          # MuMu 默认;真机/其他模拟器改这里
CFG, CONFIG_ERROR = read_config(TOOL_DIR / "config.json", DEFAULT_CONFIG)

WS_ROOT = ROOT / "toolbox" / "workspace"
SESSION = os.environ.get("AGENT_SESSION", "default")
FILES = files_root()
DEVICE = f'127.0.0.1:{CFG["adb_port"]}'

ADB = BIN / "adb.exe"
if not ADB.exists():
    sibling = TOOL_DIR.parent / "android_unpack" / "bin" / "adb.exe"
    if sibling.exists():
        ADB = sibling




def _run(argv, timeout=60, cwd=None, env=None):
    return run_process(argv, timeout=timeout, cwd=cwd, env=env)


def adb(*args, timeout=60, reconnect=True):
    rc, out = _run([ADB, "-s", DEVICE, *args], timeout=timeout)
    if reconnect and re.search(r"device .*not found|device offline|cannot connect|error: closed", out, re.I):
        _run([ADB, "connect", DEVICE], timeout=15)
        rc, out = _run([ADB, "-s", DEVICE, *args], timeout=timeout)
    return rc, out


def device_ready() -> bool:
    rc, out = adb("get-state", timeout=15)
    return rc == 0 and out.strip() == "device"


# ── op 实现 ────────────────────────────────────────────────

def op_env_check(_p):
    lines = []
    ok = True
    have_apk = APK.exists()
    lines.append(f"{'✔' if have_apk else '✘'} BlackDex.apk  {APK}"
                 + ("" if have_apk else f"  → {DL_URL} 下载放此处(网络差用 ghproxy 镜像拼链接)"))
    ok &= have_apk
    lines.append(f"{'✔' if ADB.exists() else '✘'} adb  {ADB}"
                 + ("" if ADB.exists() else "  → 本卡 bin/ 或 android_unpack 卡 bin/ 放 adb.exe"))
    ok &= ADB.exists()
    if ADB.exists():
        _run([ADB, "connect", DEVICE], timeout=15)
        ready = device_ready()
        lines.append(f"{'✔' if ready else '✘'} 设备  {DEVICE}"
                     + ("" if ready else "  → android_unpack 卡 emu(action=start) 先开模拟器"))
        ok &= ready
        if ready:
            _, out = adb("shell", f"test -d {REMOTE_DUMP} && echo YES", timeout=15)
            print_only = "YES" in out
            lines.append(("✔" if print_only else "·") +
                         f" {REMOTE_DUMP} {'已有历史 dump' if print_only else '还没产物(装完点选目标后 wait)'}")
    print("\n".join(lines))
    print("\n下一步建议:" + ("install() → launch()" if ok else "按 ✘ 项补齐"))
    return ok


def op_install(_p):
    if not APK.exists():
        raise OpError(f"bin/BlackDex.apk 缺失:{DL_URL} 下载后放 {APK}")
    if not device_ready():
        raise OpError(f"设备 {DEVICE} 未连接(android_unpack 卡 emu(action=start) 先开模拟器)")
    rc, out = adb("install", "-r", "-t", str(APK), timeout=120)
    print("\n".join(out.strip().splitlines()[-3:]))
    if rc != 0 or "Success" not in out:
        raise OpError(f"安装失败(rc={rc});模拟器 ABI 不匹配时下对应架构的 BlackDex 包")
    print("BlackDex 安装成功  下一步: launch()")
    return True


def _find_pkg():
    rc, out = adb("shell", "pm", "list", "packages", "-3", timeout=30)
    if rc: raise OpError(f"Cannot list installed packages: {out}")
    installed = {l.removeprefix("package:").strip() for l in out.splitlines()}
    known = [n for n in ("top.niunaijun.blackdexa64", "top.niunaijun.blackdexa32", "top.niunaijun.blackdex") if n in installed]
    if not known: raise OpError("Known BlackDex package not installed; install first or pass pkg explicitly")
    return known[0]


def op_launch(p):
    if not device_ready():
        raise OpError(f"设备 {DEVICE} 未连接,先开模拟器")
    pkg = package(text(p, "pkg") or _find_pkg())
    rc, out = adb("shell", f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1", timeout=60)
    if "No activities found" in out or rc != 0:
        raise OpError(f"启动失败:{out[-200:]}(pkg={pkg})")
    print(f"BlackDex 已启动({pkg})")
    print(f"→ 在模拟器窗口里点选目标 app 开始 dump;点完这边 wait() 等产物")
    return True


def _snapshot(pkg=None):
    remote = REMOTE_DUMP + ("/" + pkg if pkg else "")
    cmd = "if [ -d " + remote + " ]; then find " + remote + " -type f -name '*.dex' -exec stat -c '%n|%s|%Y' {} \\; ; fi"
    rc, out = adb("shell", cmd, timeout=10)
    if rc: raise OpError(f"Cannot inspect BlackDex output: {out[-300:]}")
    files = {}
    for line in out.splitlines():
        parts = line.rsplit("|", 2)
        if len(parts) == 3 and parts[0].startswith(remote + "/") and parts[0].endswith(".dex") and parts[1].isdigit() and parts[2].isdigit() and int(parts[1]) > 0:
            files[parts[0]] = (int(parts[1]), int(parts[2]))
    return files

def op_wait(p):
    timeout = integer(p.get("timeout", 120), "timeout", 1, 240)
    pkg = text(p, "pkg")
    if pkg: pkg = package(pkg)
    if not device_ready(): raise OpError("Device not connected")
    baseline = _snapshot(pkg)
    print("Waiting for new/changed nonempty DEX files; existing unchanged dumps use pull directly.")
    deadline = time.monotonic() + timeout
    previous = {}
    while time.monotonic() < deadline:
        current = _snapshot(pkg)
        changed = {name: value for name, value in current.items() if baseline.get(name) != value}
        if changed and changed == previous:
            print(f"Ready: {len(changed)} new/updated DEX files stable across two polls. Next: pull")
            return True
        previous = changed
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise OpError(f"No stable new/changed DEX within {timeout}s; select target in BlackDex UI, or pull existing results")


def op_pull(p):
    if not device_ready(): raise OpError("Device not connected")
    if not _snapshot(): raise OpError("No nonempty remote DEX files; wait for a successful dump first")
    dest = prepare_output(output_dir(p, "output", "blackdex", "dump"))
    rc, out = adb("pull", REMOTE_DUMP, str(dest), timeout=240)
    print(out[-1000:])
    dexes = [f for f in dest.rglob("*.dex") if f.stat().st_size > 0]
    if rc or not dexes: raise OpError(f"Pull failed/incomplete (rc={rc}): {dest}")
    print(f"Pulled {len(dexes)} DEX files: {dest}")
    return True


def op_shell(p):
    cmd = text(p, "cmd", required=True)
    if not cmd:
        raise ArgError("shell 需要 params.cmd")
    rc, out = adb("shell", cmd, timeout=120)
    lines = out.strip().splitlines()
    print("\n".join(lines[-200:]))
    return rc == 0


OPS = {"env_check": op_env_check, "install": op_install, "launch": op_launch,
       "wait": op_wait, "pull": op_pull, "shell": op_shell}


def main():
    return dispatch(OPS, startup_error=CONFIG_ERROR)


if __name__ == "__main__":
    sys.exit(main())
