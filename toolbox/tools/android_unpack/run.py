r"""android_unpack 安卓脱壳卡:MuMu 模拟器 + frida 运行时 dump 加固 app 的 dex。

契约(runner.py):argv[1] = 参数 JSON(op 顶层 + params 嵌套);stdout 打可读文本;
退出码 0=ok,2=参数/校验错,1=执行失败。

环境自包含(2026-09-20 实证管线产品化,2026-10-15 修 Android 15 兼容):
  - bin/adb.exe + AdbWinApi/AdbWinUsbApi.dll   MuMu 自带 adb(36.0.0,与 adbd 版本匹配)
  - bin/frida-server-<ver>-android-x86_64      自动选与 venv frida 同版的二进制
    ⚠️ 必须 17.x:16.7.19 原版在 MuMu Android 15 上启动即注入 zygote,后续 fork 全崩
      (SIGSEGV null deref @ frida-agent),17.x 重写 Android 后端后正常
  - frida / frida_dexdump 库装在 gateway venv(run.py 由 venv python 执行,sys.executable 可用)
外部依赖仅 MuMu 安装本身(驱动/VM 镜像无法搬进仓库),路径经 config.json 或
AGENT_MUMU_MANAGER 环境变量可覆盖。

反检测三板斧(360 加固实证,固化勿改):
  1. server 改名 .sysmon(无 frida 字串)  2. 监听 127.0.0.1:8899 非默认端口
  3. attach 已运行进程,不 spawn(避开 StubApp 启动期反调试)
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




WS_ROOT = ROOT / "toolbox" / "workspace"
SESSION = os.environ.get("AGENT_SESSION", "default")
FILES = files_root()
ADB = BIN / "adb.exe"


def _find_frida_server():
    try:
        import frida
        return BIN / f"frida-server-{frida.__version__}-android-x86_64"
    except ImportError:
        return BIN / "frida-server-missing-android-x86_64"


FRIDA_SERVER_BIN = _find_frida_server()
REMOTE_SERVER = "/data/local/tmp/.sysmon"
BOOT_TIMEOUT_S = 120          # 模拟器冷启动到 is_android_started
DEXDUMP_TIMEOUT_S = 840       # 留 60s 给 tool.yaml 900s 总预算
DEFAULT_CONFIG = {
    "mumu_manager": r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe",
    "adb_port": 16384,
    "frida_port": 8899,
    "vm_index": 0,
}

CFG, CONFIG_ERROR = read_config(TOOL_DIR / "config.json", DEFAULT_CONFIG)
if os.environ.get("AGENT_MUMU_MANAGER"):
    CFG["mumu_manager"] = os.environ["AGENT_MUMU_MANAGER"]
if not Path(CFG["mumu_manager"]).is_absolute():
    CFG["mumu_manager"] = str(ROOT / CFG["mumu_manager"])

DEVICE = f'127.0.0.1:{CFG["adb_port"]}'
FRIDA_HOST = f'127.0.0.1:{CFG["frida_port"]}'


# ── 基础执行器 ──────────────────────────────────────────────

def _run(argv, timeout=60, cwd=None, env=None):
    return run_process(argv, timeout=timeout, cwd=cwd, env=env)


def manager(*args, timeout=30):
    """MuMuManager 子命令;输出是 JSON 就解析后返回 dict,否则原样返回文本。"""
    exe = CFG["mumu_manager"]
    if not Path(exe).exists():
        raise OpError(f"MuMuManager 不存在:{exe}(改本卡 config.json 的 mumu_manager)")
    rc, out = _run([exe, *args], timeout=timeout)
    try:
        return json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return {"raw": out, "rc": rc}


def manager_info():
    return manager("info", "-v", str(CFG["vm_index"]))


def adb(*args, timeout=60, reconnect=True):
    """带自动 connect 的 adb -s 调用;设备掉线时重连一次再试。"""
    if not ADB.exists():
        raise OpError(f"本卡 bin/ 缺 adb.exe:{ADB}")
    rc, out = _run([ADB, "-s", DEVICE, *args], timeout=timeout)
    if reconnect and re.search(r"device .*not found|device offline|cannot connect|error: closed", out, re.I):
        _run([ADB, "connect", DEVICE], timeout=15)
        rc, out = _run([ADB, "-s", DEVICE, *args], timeout=timeout)
    return rc, out


def adb_root_ensure():
    """adbd 非 root 则 adb root(会重启 adbd,需重连)。"""
    _, out = adb("shell", "id", timeout=15)
    if "uid=0" in out:
        return "已是 root"
    adb("root", timeout=15)
    time.sleep(2)
    _run([ADB, "connect", DEVICE], timeout=15)
    _, out = adb("shell", "id", timeout=15)
    if "uid=0" not in out:
        raise OpError(f"adb root 失败:{out}(MuMu 设置里开 root 权限后 restart)")
    return "adb root 完成"


def device_ready() -> bool:
    rc, out = adb("get-state", timeout=15)
    return rc == 0 and out.strip() == "device"


def frida_probe():
    """从 venv 侧连 8899 验证 frida-server 可用;返回 (ok, 进程数|错误)。"""
    try:
        import frida
        dev = frida.get_device_manager().add_remote_device(FRIDA_HOST)
        return True, len(dev.enumerate_processes())
    except Exception as e:                                    # noqa: BLE001 统一兜底
        return False, str(e).split("\n")[0]



def op_env_check(_p):
    lines = []
    ok = True

    exe = CFG["mumu_manager"]
    have_mgr = Path(exe).exists()
    lines.append(f"{'✔' if have_mgr else '✘'} MuMuManager   {exe}"
                 + ("(config.json/env 覆盖)" if exe != DEFAULT_CONFIG["mumu_manager"] else ""))
    ok &= have_mgr

    info = manager_info() if have_mgr else {}
    running = bool(info.get("is_android_started"))
    lines.append(f"{'✔' if running else '·'} 模拟器[{info.get('android_version', '?')}] "
                 f"进程={info.get('is_process_started')} 安卓就绪={running}"
                 + ("" if running else "  → emu(action=start)"))
    ok &= running

    have_adb = ADB.exists()
    lines.append(f"{'✔' if have_adb else '✘'} 自带 adb     {ADB.name} @bin/"
                 + ("" if have_adb else "  缺失,从 MuMu nx_main 复制"))
    ok &= have_adb

    have_fs = FRIDA_SERVER_BIN.exists()
    lines.append(f"{'✔' if have_fs else '✘'} frida-server {FRIDA_SERVER_BIN.name} "
                 f"({FRIDA_SERVER_BIN.stat().st_size // 1048576}MB)" if have_fs
                 else f"✘ frida-server bin/ 缺失(ghproxy 重下 17.x android-x86_64 (must match Python frida))")
    ok &= have_fs

    try:
        import frida                                            # noqa: F401 验证导入
        ver = frida.__version__
        import frida_dexdump                                    # noqa: F401
        lines.append(f"✔ venv 库      frida {ver} + frida_dexdump")
    except ImportError as e:
        ok = False
        lines.append(f"✘ venv 库      {e}(pip install frida==17.23.3 frida-dexdump)")

    if running and have_adb:
        ready = device_ready()
        lines.append(f"{'✔' if ready else '✘'} adb 设备     {DEVICE}"
                     + ("" if ready else "  → emu(action=restart)"))
        ok &= ready
        if ready:
            _, out = adb("shell", "id", timeout=15)
            root = "uid=0" in out
            lines.append(f"{'✔' if root else '·'} root 权限    {'uid=0' if root else 'shell(frida ensure 会自动 adb root)'}")
            _, out = adb("shell", f"test -e {REMOTE_SERVER} && echo YES", timeout=15)
            remote_fs = "YES" in out
            lines.append(f"{'✔' if remote_fs else '·'} 设备侧 server {'已部署' if remote_fs else '未部署(frida ensure 会自动 push)'}")
            alive, detail = frida_probe()
            lines.append(f"{'✔' if alive else '·'} 8899 连通    "
                         + (f"可枚举 {detail} 进程" if alive else f"未起({detail[:60]});frida(action=ensure)"))

    print("\n".join(lines))
    print("\n下一步建议:" + (
        "环境就绪,主线 install→launch→dexdump" if ok and running
        else "按 ✘/· 项逐个补齐,或直接 frida(action=ensure) 一键部署"))
    return ok


def op_emu(p):
    action = p.get("action")
    if action not in ("start", "stop", "restart", "status"):
        raise ArgError(f"emu.action 必须是 start/stop/restart/status,收到 {action!r}")
    vi = str(CFG["vm_index"])
    if action == "status":
        info = manager_info()
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return True
    r = manager("control", "-v", vi, "launch" if action == "start" else action)
    if r.get("errcode", -1) != 0:
        raise OpError(f"emu {action} 失败:{r}")
    if action == "stop":
        print(f"模拟器已关停({DEVICE} 将失效)")
        return True
    # start / restart:等安卓就绪再连 adb
    t0 = time.time()
    while time.time() - t0 < BOOT_TIMEOUT_S:
        if manager_info().get("is_android_started"):
            break
        time.sleep(5)
    else:
        raise OpError(f"{BOOT_TIMEOUT_S}s 内安卓未就绪,MuMuManager info 查看实例状态")
    time.sleep(2)
    _, out = _run([ADB, "connect", DEVICE], timeout=20)
    ready = device_ready()
    print(f"模拟器就绪(耗时 {int(time.time() - t0)}s),adb {DEVICE} "
          f"{'已连接' if ready else '连接失败:' + out}")
    if ready:
        _, out = adb("shell", "getprop", "ro.product.cpu.abi", timeout=15)
        print(f"abi={out.strip()}  下一步: frida(action=ensure)")
    return ready


def op_frida(p):
    action = p.get("action")
    if action not in ("ensure", "stop", "status"):
        raise ArgError(f"frida.action 必须是 ensure/stop/status,收到 {action!r}")

    if action == "status":
        if not device_ready():
            print(f"设备未连接 → emu(action=start)")
            return False
        _, out = adb("shell", f"pidof .sysmon", timeout=15)
        pid = out.strip()
        print(f"设备侧 .sysmon pid: {pid or '未运行'}")
        alive, detail = frida_probe()
        print(f"venv 侧 {FRIDA_HOST}: {'可枚举 ' + str(detail) + ' 进程' if alive else '不通(' + str(detail)[:80] + ')'}")
        return bool(pid) and alive

    if action == "stop":
        adb("shell", "pkill -f .sysmon", timeout=15)
        adb("forward", "--remove", f"tcp:{CFG['frida_port']}", timeout=15)
        print("frida-server 已停止并移除端口转发")
        return True

    # ensure:幂等部署
    if not device_ready():
        raise OpError(f"设备 {DEVICE} 未连接,先 emu(action=start)")
    if not FRIDA_SERVER_BIN.is_file(): raise OpError(f"Matching frida-server missing: {FRIDA_SERVER_BIN}")
    import frida
    if int(frida.__version__.split(".")[0]) < 17: raise OpError("Android 15 requires frida 17.x or newer")
    print(adb_root_ensure())
    rc, version = adb("shell", f"{REMOTE_SERVER} --version", timeout=15)
    if rc != 0 or version.strip() != frida.__version__:
        _, running = adb("shell", "pidof .sysmon", timeout=15)
        if running.strip(): raise OpError("Remote frida version differs; stop this card's server before replacing it")
        rc, out = adb("push", str(FRIDA_SERVER_BIN), REMOTE_SERVER, timeout=120)
        if rc: raise OpError(f"Server push failed: {out}")
        print("Matching frida-server deployed")
    else:
        print("Remote frida-server version matches")
    adb("shell", f"chmod 755 {REMOTE_SERVER}", timeout=15)

    _, out = adb("shell", "pidof .sysmon", timeout=15)
    if not out.strip():
        adb("shell", f"nohup {REMOTE_SERVER} -l {FRIDA_HOST} >/dev/null 2>&1 &", timeout=15)
        time.sleep(1.5)
        _, out = adb("shell", "pidof .sysmon", timeout=15)
        if not out.strip():
            raise OpError("frida-server 启动后 pidof 找不到(看 logcat 排查)")
        print(f"③ frida-server 已启动(pid {out.strip()}),监听 {FRIDA_HOST}")
    else:
        print(f"③ frida-server 已在运行(pid {out.strip()}),复用")

    adb("forward", f"tcp:{CFG['frida_port']}", f"tcp:{CFG['frida_port']}", timeout=15)
    print(f"④ adb forward tcp:{CFG['frida_port']} 就绪")
    alive, detail = frida_probe()
    if not alive:
        raise OpError(f"8899 连通验证失败:{detail}")
    print(f"⑤ 连通验证通过,可枚举 {detail} 进程  下一步: install → launch → dexdump")
    return True


def op_install(p):
    apk = p.get("apk")
    if not apk:
        raise ArgError("install 需要 params.apk(APK 路径,相对=会话工作区)")
    path = resolve(apk)
    if not path.exists():
        raise ArgError(f"APK 不存在:{path}")
    if not device_ready():
        raise OpError(f"设备未连接,先 emu(action=start)")
    rc, out = adb("install", "-r", "-t", str(path), timeout=300)
    tail = "\n".join(out.strip().splitlines()[-3:])
    print(f"adb install -r {path.name}\n{tail}")
    if "Success" in out:
        print("安装成功  下一步: launch(pkg=包名)(不知道包名可 shell(cmd='pm list packages -3'))")
    return rc == 0 and "Success" in out


def op_launch(p):
    pkg = package(p["pkg"]) if p.get("pkg") is not None else None
    if not pkg:
        raise ArgError("launch 需要 params.pkg(应用包名)")
    if not device_ready():
        raise OpError("设备未连接,先 emu(action=start)")
    rc, out = adb("shell",
                  f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1",
                  timeout=60)
    if rc != 0 or "No activities found" in out or "monkey aborted" in out.lower():
        raise OpError(f"{pkg} 无 launcher Activity(包名对吗?shell 查 pm list packages | grep {pkg[:8]})")
    time.sleep(3)
    _, out = adb("shell", f"pidof {pkg}", timeout=15)
    pid = out.strip()
    print(f"monkey 启动完成,pid={pid or '(3s 内未见,加固壳可能还在初始化,稍后 ps(filter={pkg}))'}")
    return bool(pid)


def op_ps(p):
    flt = (text(p, "filter") or "").lower()
    rc, out = adb("shell", "ps -A", timeout=30)
    lines = out.strip().splitlines()
    if rc or not lines: raise OpError(f"Cannot list processes: {out}")
    head, rows = lines[0], lines[1:]
    if flt:
        rows = [r for r in rows if flt in r.lower()]
    apps = [r for r in rows if r.startswith("u0_a")]
    print(f"共 {len(rows)} 进程" + (f"(过滤 '{flt}')" if flt else "")
          + f",其中用户态 app {len(apps)} 个:")
    print(head)
    for r in rows[:200]:
        print(r)
    if len(rows) > 200:
        print(f"...(截断,共 {len(rows)} 行;用 filter 缩小)")
    if apps:
        pids = "  ".join(f"{r.split()[-1]}={r.split()[1]}" for r in apps[:10])
        print(f"\napp 进程(名字=pid):{pids}")
    return True


def op_dexdump(p):
    pid = integer(p["pid"], "pid", 1, 4194304) if p.get("pid") is not None else None
    pkg = package(p["pkg"]) if p.get("pkg") is not None else None
    if not pid and not pkg:
        raise ArgError("dexdump 需要 params.pkg 或 params.pid")
    deep = boolean(p.get("deep", True), "deep")
    if not device_ready():
        raise OpError("设备未连接,先 emu(action=start)")
    if pid is None:
        _, out = adb("shell", f"pidof {pkg}", timeout=15)
        pid = out.strip().split()[0] if out.strip() else ""
        if not pid:
            raise OpError(f"{pkg} 未在运行,先 launch(pkg={pkg})再等壳初始化")
    alive, detail = frida_probe()
    if not alive:
        raise OpError(f"frida-server 不通:{detail} → frida(action=ensure)")

    pid = integer(pid, "pid", 1, 4194304)
    outdir = prepare_output(output_dir(p, "output", "android", pkg or "pid" + str(pid)))

    argv = [sys.executable, "-m", "frida_dexdump", "-H", FRIDA_HOST,
            "-p", str(pid), "-o", str(outdir)]
    if deep:
        argv.append("-d")
    print(f"attach pid={pid} 深搜={'开' if deep else '关'} → {outdir}")
    rc, out = _run(argv, timeout=DEXDUMP_TIMEOUT_S)
    dexes = sorted(outdir.glob("*.dex"), key=lambda f: -f.stat().st_size)
    found = re.search(r"found (\d+) dex", out)
    print((out.strip().splitlines()[-1] if out.strip() else "(无输出)"))
    if rc != 0 or not dexes:
        tail = "\n".join(out.strip().splitlines()[-6:])
        raise OpError(f"0 个 dex 落盘({tail})(pid 对吗?加固壳初始化完了吗?试 deep=false 或换 pid)")
    total = sum(f.stat().st_size for f in dexes)
    print(f"\n dump 出 {len(dexes)} 个 dex,共 {total // 1048576}MB:")
    for f in dexes:
        print(f"  {f.stat().st_size // 1024:>7} KB  {f.name}")
    print(f"\n目录:{outdir}\n下一步: dex_scan(keyword=业务域名) 定位业务 dex,再交 jadx 卡反编译")
    return True


def op_dex_scan(p):
    d = resolve(text(p, "dir")) if p.get("dir") is not None else None
    if d is None:
        cands = sorted((FILES / "android").glob("*/"),
                       key=lambda x: x.stat().st_mtime, reverse=True) \
            if (FILES / "android").exists() else []
        cands = [c for c in cands if list(c.glob("*.dex"))]
        if not cands:
            raise ArgError("没找到历史 dump 目录;先 dexdump,或 params.dir 指定目录")
        d = cands[0]
        if len(cands) > 1:
            print(f"(未传 dir,用最近一次 dump:{d.name})")
    dexes = sorted(d.glob("*.dex"), key=lambda f: -f.stat().st_size)
    if not dexes:
        raise ArgError(f"{d} 里没有 .dex")
    kw = text(p, "keyword") or ""
    kwb = kw.lower().encode()

    API_RE = re.compile(rb"/(?:api|app|v\d+|gateway|svc|service)/[a-z0-9][a-z0-9_\-/.]{1,60}")
    URL_RE = re.compile(rb"https?://[\x21-\x7e]{5,80}")
    STR_RE = re.compile(rb"[\x20-\x7e]{6,}")

    hits, apis, urls = [], set(), set()
    for f in dexes:
        data = f.read_bytes()
        n = data.lower().count(kwb) if kw else 0
        if kw and n:
            hits.append((n, f))
        for m in API_RE.finditer(data):
            apis.add(m.group().decode())
        for m in URL_RE.finditer(data):
            u = m.group().decode()
            if not re.match(r"https?://(www\.|maps\.|schemas\.)", u):
                urls.add(u)
    print(f"扫描 {len(dexes)} 个 dex @ {d}")
    if kw:
        print(f"\n关键词 '{kw}' 命中({len(hits)} 个 dex,按命中数排序):")
        for n, f in sorted(hits, key=lambda x: -x[0]):
            print(f"  {n:>4} 次  {f.name}({f.stat().st_size // 1024}KB)")
        if not hits:
            print("  全部未命中 —— 换业务特征串(域名/接口前缀/app 名),或目标 dex 还没 dump 到")
        biz = [f for _, f in hits[:3]]
        if biz:
            print(f"业务 dex 候选:{'、'.join(f.name for f in biz)} → 交 jadx 卡反编译")
    print(f"\nAPI 路径({len(apis)} 条,取前 120):")
    for a in sorted(apis)[:120]:
        print("  " + a)
    if urls:
        print(f"\nURL({len(urls)} 条,取前 60):")
        for u in sorted(urls)[:60]:
            print("  " + u)
    return True


def op_shell(p):
    cmd = text(p, "cmd", required=True)
    if not cmd:
        raise ArgError("shell 需要 params.cmd")
    rc, out = adb("shell", cmd, timeout=120)
    lines = out.strip().splitlines()
    print("\n".join(lines[-400:]))
    if len(lines) > 400:
        print(f"...(截断,共 {len(lines)} 行)")
    return rc == 0


def op_pull(p):
    remote = text(p, "remote", required=True)
    if not remote:
        raise ArgError("pull 需要 params.remote(设备侧绝对路径)")
    if not remote.startswith("/"): raise ArgError("remote must be an absolute Android path")
    dest = resolve(text(p, "output")) if p.get("output") is not None else FILES / Path(remote).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    rc, out = adb("pull", remote, str(dest), timeout=300)
    print(out.strip().splitlines()[-1] if out.strip() else "(无输出)")
    if dest.exists():
        print(f"已拉回:{dest}({dest.stat().st_size // 1024}KB)")
    return rc == 0 and dest.exists()


def op_logcat(p):
    n = integer(p.get("lines", 200), "lines", 1, 10000)
    flt = text(p, "filter")
    cmd = f"logcat -d -t {n}"
    rc, out = adb("shell", cmd, timeout=60)
    if rc: raise OpError(f"logcat failed: {out}")
    lines = out.strip().splitlines()
    if flt:
        lines = [l for l in lines if flt.lower() in l.lower()]
    print(f"最近 {n} 行" + (f"过滤 '{flt}'" if flt else "") + f",{len(lines)} 条:")
    print("\n".join(lines[-200:]))
    return True


OPS = {
    "env_check": op_env_check, "emu": op_emu, "frida": op_frida,
    "install": op_install, "launch": op_launch, "ps": op_ps,
    "dexdump": op_dexdump, "dex_scan": op_dex_scan, "shell": op_shell,
    "pull": op_pull, "logcat": op_logcat,
}


def main():
    return dispatch(OPS, startup_error=CONFIG_ERROR)


if __name__ == "__main__":
    sys.exit(main())
