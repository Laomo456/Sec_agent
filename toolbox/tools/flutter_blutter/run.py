"""Flutter AOT analysis through bundled blutter and build dependencies."""
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mobile_runtime import (ArgError, EXTERNAL, OpError, boolean, dispatch, extract_member, output_dir, prepare_output, resolve, run_process, text)
TOOL_DIR = Path(__file__).resolve().parent
BLUTTER = TOOL_DIR / "bin" / "blutter" / "blutter.py"
DART_SDK = BLUTTER.parent / "dartsdk"

def build_env():
    env = dict(os.environ)
    vs = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    candidates = []
    if vs.is_file():
        rc, out = run_process([vs, "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"], timeout=20)
        if rc == 0: candidates += [Path(l.strip()) / "Common7/Tools/VsDevCmd.bat" for l in out.splitlines() if l.strip()]
    for base in ("ProgramFiles", "ProgramFiles(x86)"):
        location = Path(os.environ.get(base, "C:/Program Files")) / "Microsoft Visual Studio"
        candidates += sorted(location.glob("*/*/Common7/Tools/VsDevCmd.bat"), reverse=True)
    for dev in candidates:
        if not dev.is_file(): continue
        # cmd.exe needs its own Windows quoting, not list2cmdline's escaped quotes.
        command = '\"' + os.environ.get("COMSPEC", "C:/Windows/System32/cmd.exe") + '\" /d /s /c "chcp 65001 >nul && call \"' + str(dev) + '\" -arch=x64 >nul && set"'
        rc, values = run_process(command, timeout=30)
        if rc: continue
        for line in values.splitlines():
            key, sep, value = line.partition("=")
            if sep and key and not key.startswith("="): env[key.upper()] = value
        if shutil.which("cl.exe", path=env.get("PATH", "")): break
    env["PATH"] = os.pathsep.join([str(EXTERNAL / "cmake" / "bin"), str(EXTERNAL / "ninja"), env.get("PATH", "")])
    for tool in ("cl.exe", "cmake.exe", "ninja.exe", "git.exe"):
        if not shutil.which(tool, path=env["PATH"]): raise OpError(f"Missing {tool}: install Visual Studio C++ workload / bundled CMake + Ninja")
    for mod in ("requests", "elftools"):
        try: __import__(mod)
        except ImportError as e: raise OpError(f"Python dependency missing: {mod}") from e
    native = BLUTTER.parent / "external"
    for path in (native / "capstone" / "capstone_dll.lib", native / "icu-windows" / "lib64" / "icuuc.lib",
                 BLUTTER.parent / "bin" / "capstone.dll", BLUTTER.parent / "bin" / "icudt73.dll", BLUTTER.parent / "bin" / "icuuc73.dll"):
        if not path.is_file(): raise OpError(f"Native dependency missing: {path}")
    if not BLUTTER.is_file(): raise OpError(f"blutter source missing: {BLUTTER}")
    return env

def op_env_check(p):
    env = build_env()
    for name in ("cl.exe", "cmake.exe", "ninja.exe", "git.exe"): print(name, shutil.which(name, path=env["PATH"]))
    print(f"Native libraries verified. Dart cache: {DART_SDK} (download/build on first analysis)")
    return True

def _extract_libs(apk, dest):
    try:
        with zipfile.ZipFile(apk) as z:
            for name in ("libapp.so", "libflutter.so"):
                member = f"lib/arm64-v8a/{name}"
                if member not in z.namelist(): raise ArgError(f"APK lacks {member}; blutter requires Flutter AOT arm64 libraries")
                extract_member(z, member, dest / name)
    except zipfile.BadZipFile as e: raise ArgError(f"Invalid APK: {e}") from e
    return dest

def op_run(p):
    apk, folder = text(p, "apk"), text(p, "dir")
    if bool(apk) == bool(folder): raise ArgError("Provide exactly one of params.apk / params.dir")
    src = resolve(apk or folder)
    if not src.exists(): raise ArgError(f"Input not found: {src}")
    no_analysis = boolean(p.get("no_analysis", False), "no_analysis")
    env = build_env()
    outdir = output_dir(p, "out", "flutter", src.stem)
    with tempfile.TemporaryDirectory(prefix="agg_flutter_") as temp:
        if apk:
            if not src.is_file(): raise ArgError("apk must be a file")
            libs = _extract_libs(src, Path(temp))
        else:
            libs = src if (src / "libapp.so").is_file() else src / "lib" / "arm64-v8a"
            if not all((libs / f).is_file() for f in ("libapp.so", "libflutter.so")):
                raise ArgError("dir must contain libapp.so + libflutter.so directly or under lib/arm64-v8a")
        prepare_output(outdir)
        argv = [sys.executable, BLUTTER, libs, outdir]
        if no_analysis: argv.append("--no-analysis")
        rc, out = run_process(argv, timeout=780, cwd=BLUTTER.parent, env=env)
        print("\n".join(out.splitlines()[-30:]))
    if rc or not (outdir / "pp.txt").is_file() or not (outdir / "pp.txt").stat().st_size or not any((outdir / "asm").glob("*")):
        raise OpError(f"blutter analysis incomplete (rc={rc}); inspect download/build/unsupported Dart errors above")
    errors = out.count("Analysis error")
    if errors:
        raise OpError(f"blutter reported {errors} function analysis errors; partial products retained at {outdir}. "
                      "Use no_analysis=true for raw disassembly, or a newer upstream analyzer; do not treat partial analysis as complete.")
    if no_analysis: print("Raw disassembly mode: higher-level instruction analysis was explicitly disabled.")
    print(f"Products: {outdir}")
    return True

def op_clean(p):
    for path in (DART_SDK, BLUTTER.parent / "build"):
        if path.exists(): shutil.rmtree(path); print(f"Removed generated cache: {path}")
    return True

OPS = {"env_check": op_env_check, "run": op_run, "clean": op_clean}
if __name__ == "__main__": sys.exit(dispatch(OPS))
