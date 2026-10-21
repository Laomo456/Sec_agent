# -*- coding: utf-8 -*-
"""AGG 第三方工具一键安装器。

git 仓库不含 toolbox/tools/*/bin/ 下的第三方二进制(体积与许可原因),
本脚本从官方 Release 下载还原到 run.py 期望的路径。纯 stdlib,无依赖,
setup.bat 之后可直接 `py install_tools.py` 运行。

用法:
    py install_tools.py              # 安装全部可自动获取的工具
    py install_tools.py nuclei zap   # 只装指定工具
    py install_tools.py --list       # 看清单

环境变量:
    AGG_GH_PROXY   GitHub 代理前缀(如 https://ghproxy.net/),拼在
                   https://github.com/... 之前,国内网络可用

版本策略:projectdiscovery 系等走 releases/latest 动态解析(tag→资产名),
不锁补丁版本;zap/upx/frida/ysoserial 因 run.py 写死了带版本的路径或
上游无 Windows 新包,钉死在清单里标注的版本。

无法从官方渠道自动获取的(dalfox 新版无 Windows 资产、DIE、nmap、
flutter_blutter 自构建产物等)见结尾 MANUAL 提示,从本仓库 GitHub
Release 附件或原转移包获取。
"""
import io
import lzma
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # GBK 控制台打印中文会崩/花

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "toolbox" / "tools"
GH_PROXY = os.environ.get("AGG_GH_PROXY", "").rstrip("/")

UA = {"User-Agent": "agg-install-tools/1.0"}


def proxied(url: str) -> str:
    """对 github.com 的下载/跳转套代理前缀;其他域(dl.google.com 等)直连。"""
    if GH_PROXY and url.startswith("https://github.com/"):
        return f"{GH_PROXY}/{url}"
    return url


def fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def resolve_latest(repo: str) -> str:
    """走 releases/latest 302 到 tag 页,从最终 URL 抠 tag(api.github.com
    在部分网络不可达,这条只依赖 github.com 主站)。"""
    req = urllib.request.Request(proxied(f"https://github.com/{repo}/releases/latest"),
                                 headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        final = r.geturl()
    m = re.search(r"/releases/tag/([^/]+)$", final)
    if not m:
        raise RuntimeError(f"解析 {repo} 最新 tag 失败: {final}")
    return m.group(1)


def download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(proxied(url), headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, chunk = 0, 1 << 16
        while True:
            b = r.read(chunk)
            if not b:
                break
            f.write(b)
            done += len(b)
            if total:
                pct = done * 100 // total
                print(f"\r    {done >> 20} / {total >> 20} MB ({pct}%)", end="")
    print()


def unzip(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(dest)


def sole_child(d: Path) -> Path:
    """压缩包解开后的唯一顶层目录(如 ZAP_2.17.0/、jdk-17.0.20.1+1/)。"""
    kids = [p for p in d.iterdir()]
    if len(kids) == 1 and kids[0].is_dir():
        return kids[0]
    return d


# ══ 工具清单 ═════════════════════════════════════════════════════════
# tag=None → latest 动态解析;asset 里 {v} = tag 去掉前导 v/jdk- 等
# 每项 install(t):幂等,目标存在即跳过
TOOLS_MANIFEST = {}


def tool(name, desc):
    def deco(fn):
        fn.__desc__ = desc
        TOOLS_MANIFEST[name] = fn
        return fn
    return deco


@tool("nuclei", "漏扫引擎(projectdiscovery,latest)")
def install_nuclei():
    exe = TOOLS / "nuclei" / "bin" / "nuclei.exe"
    if exe.exists():
        return skip("nuclei")
    tag = resolve_latest("projectdiscovery/nuclei")          # v3.x.y
    v = tag.lstrip("v")
    url = f"https://github.com/projectdiscovery/nuclei/releases/download/{tag}/nuclei_{v}_windows_amd64.zip"
    print(f"[nuclei] {tag}")
    unzip(fetch(url), exe.parent)
    # 模板:run.py 缺 bin/templates 时回退官方默认目录,-update-templates 落到默认处
    try:
        subprocess.run([str(exe), "-update-templates", "-silent"],
                       capture_output=True, timeout=600)
        print("    模板已更新(官方默认目录)")
    except Exception as e:
        print(f"    模板自动更新失败({e}),首次使用时 nuclei 会自行处理")


@tool("katana", "爬虫(projectdiscovery,latest)")
def install_katana():
    if (TOOLS / "katana" / "bin" / "katana.exe").exists():
        return skip("katana")
    tag = resolve_latest("projectdiscovery/katana")
    v = tag.lstrip("v")
    print(f"[katana] {tag}")
    url = f"https://github.com/projectdiscovery/katana/releases/download/{tag}/katana_{v}_windows_amd64.zip"
    unzip(fetch(url), TOOLS / "katana" / "bin")


@tool("subfinder", "子域枚举(projectdiscovery,latest)")
def install_subfinder():
    if (TOOLS / "subfinder" / "bin" / "subfinder.exe").exists():
        return skip("subfinder")
    tag = resolve_latest("projectdiscovery/subfinder")
    v = tag.lstrip("v")
    print(f"[subfinder] {tag}")
    url = f"https://github.com/projectdiscovery/subfinder/releases/download/{tag}/subfinder_{v}_windows_amd64.zip"
    unzip(fetch(url), TOOLS / "subfinder" / "bin")


@tool("interactsh", "带外交互回调(projectdiscovery,latest)")
def install_interactsh():
    if (TOOLS / "interactsh" / "bin" / "interactsh-client.exe").exists():
        return skip("interactsh")
    tag = resolve_latest("projectdiscovery/interactsh")
    v = tag.lstrip("v")
    print(f"[interactsh] {tag}")
    url = f"https://github.com/projectdiscovery/interactsh/releases/download/{tag}/interactsh-client_{v}_windows_amd64.zip"
    unzip(fetch(url), TOOLS / "interactsh" / "bin")


@tool("ffuf", "Web 模糊测试(latest;原捆绑 2.1.0-dev)")
def install_ffuf():
    if (TOOLS / "ffuf" / "bin" / "ffuf.exe").exists():
        return skip("ffuf")
    tag = resolve_latest("ffuf/ffuf")
    v = tag.lstrip("v")
    print(f"[ffuf] {tag}")
    url = f"https://github.com/ffuf/ffuf/releases/download/{tag}/ffuf_{v}_windows_amd64.zip"
    unzip(fetch(url), TOOLS / "ffuf" / "bin")


@tool("fscan", "内网综合扫描(latest)")
def install_fscan():
    exe = TOOLS / "fscan" / "bin" / "fscan.exe"
    if exe.exists():
        return skip("fscan")
    tag = resolve_latest("shadow1ng/fscan")
    v = tag.lstrip("v")
    print(f"[fscan] {tag}")
    url = f"https://github.com/shadow1ng/fscan/releases/download/{tag}/fscan_{v}_windows_x64.exe"
    download(url, exe)


@tool("upx", "UPX 脱壳(钉 4.2.3:run.py 引用 upx-4.2.3-win64 目录名)")
def install_upx():
    if (TOOLS / "upx" / "upx-4.2.3-win64").exists():
        return skip("upx")
    print("[upx] v4.2.3")
    url = "https://github.com/upx/upx/releases/download/v4.2.3/upx-4.2.3-win64.zip"
    unzip(fetch(url), TOOLS / "upx")


@tool("zap", "OWASP ZAP(latest) + Temurin JDK17")
def install_zap():
    tdir = TOOLS / "zap"
    jar = tdir / "bin" / "zap" / "zap-2.17.0.jar"
    if not jar.exists():
        print("[zap] 2.17.0(钉版:run.py 写死 jar 名)")
        url = "https://github.com/zaproxy/zaproxy/releases/download/v2.17.0/ZAP_2.17.0_Crossplatform.zip"
        tmp = tdir / "bin" / "_zap_tmp"
        unzip(fetch(url), tmp)
        shutil.move(str(sole_child(tmp)), str(tdir / "bin" / "zap"))
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("[zap] 已存在")
    jdk = tdir / "bin" / "jdk" / "bin" / "java.exe"
    if not jdk.exists():
        # adoptium tag 形如 jdk-17.0.20.1+1 → 资产名 17.0.20.1_1(+ 转 _)
        tag = resolve_latest("adoptium/temurin17-binaries")
        ver = tag.removeprefix("jdk-")
        asset_ver = ver.replace("+", "_")
        print(f"[zap/jdk] Temurin {ver}")
        url = (f"https://github.com/adoptium/temurin17-binaries/releases/download/"
               f"{tag}/OpenJDK17U-jdk_x64_windows_hotspot_{asset_ver}.zip")
        tmp = tdir / "bin" / "_jdk_tmp"
        unzip(fetch(url), tmp)
        shutil.move(str(sole_child(tmp)), str(tdir / "bin" / "jdk"))
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("[zap/jdk] 已存在")


@tool("frida", "frida-server 17.23.3 → android_unpack(钉版,与模拟器架构匹配)")
def install_frida():
    dst = TOOLS / "android_unpack" / "bin" / "frida-server-17.23.3-android-x86_64"
    if dst.exists():
        return skip("frida")
    print("[frida] 17.23.3 android-x86_64")
    url = "https://github.com/frida/frida/releases/download/17.23.3/frida-server-17.23.3-android-x86_64.xz"
    dst.write_bytes(lzma.decompress(fetch(url)))


@tool("adb", "platform-tools(dl.google.com)→ android_unpack / blackdex_unpack")
def install_adb():
    for d in ("android_unpack", "blackdex_unpack"):
        if not (TOOLS / d / "bin" / "adb.exe").exists():
            print("[adb] platform-tools")
            url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
            tmp = TOOLS / "android_unpack" / "bin" / "_adb_tmp"
            unzip(fetch(url), tmp)
            src = tmp / "platform-tools" / "adb.exe"
            for dd in ("android_unpack", "blackdex_unpack"):
                (TOOLS / dd / "bin").mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, TOOLS / dd / "bin" / "adb.exe")
            shutil.rmtree(tmp, ignore_errors=True)
            break
    else:
        skip("adb")


@tool("ysoserial", "Java 反序列化 payload(官方 v0.0.6 fat jar)")
def install_ysoserial():
    jar = TOOLS / "ysoserial" / "bin" / "ysoserial-all.jar"
    if jar.exists():
        return skip("ysoserial")
    print("[ysoserial] v0.0.6")
    url = "https://github.com/frohoff/ysoserial/releases/download/v0.0.6/ysoserial-all.jar"
    download(url, jar)


@tool("blackdex", "BlackDex APK(latest)→ 装进模拟器脱壳")
def install_blackdex():
    apk = TOOLS / "blackdex_unpack" / "bin" / "BlackDex.apk"
    if apk.exists():
        return skip("blackdex")
    tag = resolve_latest("CodingGay/BlackDex")
    print(f"[blackdex] {tag}")
    url = f"https://github.com/CodingGay/BlackDex/releases/download/{tag}/app-BlackDex64.apk"
    download(url, apk)


@tool("rag", "Des-CTF 知识库语料(git clone Dest1ny-Sec/Des-CTF-Knowledge)")
def install_rag():
    dst = TOOLS.parent / "rag" / "Des-CTF-Knowledge-main"
    if dst.exists():
        return skip("rag")
    print("[rag] Des-CTF-Knowledge")
    tmp = TOOLS.parent / "rag" / "_desctf_tmp"
    r = subprocess.run(["git", "clone", "--depth", "1",
                        proxied("https://github.com/Dest1ny-Sec/Des-CTF-Knowledge"),
                        str(tmp)], capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git clone 失败: {r.stderr.strip()[:200]}"
                           f"(可设 AGG_GH_PROXY,或手动下载解压到 {dst})")
    shutil.move(str(tmp), str(dst))   # 保留 -main 后缀,语料目录名不漂移


# 无法官方自动获取 —— 从本仓库 Release 附件 / 原转移包复制
MANUAL = [
    ("dalfox/bin/dalfox.exe", "上游新版无 Windows 资产(旧版 exe 见 Release)"),
    ("packer_detect/bin/DIE/", "Detect It Easy 便携版"),
    ("hakoriginfinder/bin/", "hakoriginfinder.exe"),
    ("nmap/bin/", "nmap Windows 版"),
    ("unidbg_emu/bin/", "unidbg 预编译产物"),
    ("flutter_blutter/bin/", "blutter + Dart SDK 自构建产物(约 200MB)"),
    ("oneforall/home/", "OneForAll 工作目录(或 git clone shmilylty/OneForAll)"),
]

MANUAL_ROOT = [   # 仓库根下(不在 toolbox/tools/)
    ("external/", "JDK/dotnet/cmake/node/dnSpyEX 等第三方运行时(完整转移包附件)"),
]


def skip(name: str):
    print(f"[{name}] 已存在,跳过")


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        for n, fn in TOOLS_MANIFEST.items():
            print(f"  {n:14s} {fn.__desc__}")
        print("\n手动获取(本仓库 Release 附件 / 原转移包):")
        for p, why in MANUAL:
            print(f"  toolbox/tools/{p:28s} {why}")
        return 0
    todo = names or list(TOOLS_MANIFEST)
    ok, fail = [], []
    for n in todo:
        if n not in TOOLS_MANIFEST:
            print(f"[{n}] 未知工具(看 --list)")
            fail.append(n)
            continue
        try:
            TOOLS_MANIFEST[n]()
            ok.append(n)
        except Exception as e:
            print(f"[{n}] 失败: {e}")
            fail.append(n)
    print(f"\n完成 {len(ok)} 项" + (f",失败 {len(fail)}: {' '.join(fail)}" if fail else ""))
    print("以下内容无法自动下载,需要时从本仓库 GitHub Release 附件或原转移包复制:")
    for p, why in MANUAL:
        print(f"  - toolbox/tools/{p}  ({why})")
    for p, why in MANUAL_ROOT:
        print(f"  - {p}  ({why})")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
