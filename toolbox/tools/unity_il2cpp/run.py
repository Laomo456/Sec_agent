"""Il2CppDumper with archive extraction and project-owned .NET runtime."""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mobile_runtime import (ArgError, EXTERNAL, OpError, dispatch, extract_member, output_dir, prepare_output, resolve, run_process, text)
TOOL_DIR = Path(__file__).resolve().parent
DLL = TOOL_DIR / "bin" / "Il2CppDumper" / "Il2CppDumper.dll"
DOTNET = EXTERNAL / "dotnet" / "dotnet.exe"
ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")

def runtime():
    if not DLL.is_file() or not DOTNET.is_file(): raise OpError(f"Missing Il2CppDumper/.NET: {DLL}, {DOTNET}")
    config = json.loads(DLL.with_suffix(".runtimeconfig.json").read_text(encoding="utf-8"))
    framework = config["runtimeOptions"]["framework"]
    rc, out = run_process([DOTNET, "--list-runtimes"], timeout=20)
    required = framework["version"].split(".")[:2]
    versions = [line.split()[1] for line in out.splitlines() if line.startswith(framework["name"] + " ")]
    if rc or not any(v.split(".")[:2] == required for v in versions):
        raise OpError(f"Requires {framework['name']} {'.'.join(required)}; found {versions}")
    cfg = DLL.parent / "config.json"
    options = json.loads(cfg.read_text(encoding="utf-8"))
    if options.get("RequireAnyKey", True): raise OpError("Il2CppDumper config.RequireAnyKey must be false")
    return [DOTNET, DLL]

def op_env_check(p):
    print("Runtime verified:", runtime())
    print("dump(input=APK/IPA/ZIP or native binary, metadata=required for native binary)")
    return True

def archive_inputs(src, dest, abi=None, metadata_override=None):
    if abi is not None and abi not in ABIS: raise ArgError(f"abi must be one of {ABIS}")
    try:
        with zipfile.ZipFile(src) as z:
            names = z.namelist()
            metas = [n for n in names if n.endswith("/global-metadata.dat") or n == "global-metadata.dat"]
            if metadata_override is None and len(metas) != 1: raise ArgError(f"Archive must contain exactly one global-metadata.dat; found {len(metas)}")
            binary = None
            for arch in ([abi] if abi else ABIS):
                matches = [n for n in names if n.endswith(f"lib/{arch}/libil2cpp.so")]
                if len(matches) > 1: raise ArgError("Multiple native libraries; provide a single APK/ABI")
                if matches: binary = matches[0]; break
            if not binary and not abi:
                # Unity iOS embeds IL2CPP in UnityFramework or the app's main Mach-O.
                candidates = [n for n in names if n.endswith("/UnityFramework.framework/UnityFramework")]
                if not candidates:
                    candidates = [n for n in names if n.startswith("Payload/") and n.count("/") == 2 and not Path(n).suffix]
                candidates = [n for n in candidates if not z.getinfo(n).is_dir()]
                if len(candidates) == 1: binary = candidates[0]
            if not binary: raise ArgError("No unique IL2CPP binary found; extract manually and pass input + metadata")
            lib = extract_member(z, binary, dest / "native.bin")
            meta = metadata_override if metadata_override is not None else extract_member(z, metas[0], dest / "global-metadata.dat")
            print(f"Archive selected: {binary} + {meta}")
            return lib, meta
    except zipfile.BadZipFile as e: raise ArgError(f"Invalid APK/IPA/ZIP: {e}") from e

def op_dump(p):
    src = resolve(text(p, "input", required=True))
    if not src.is_file(): raise ArgError(f"input not found: {src}")
    meta_arg = text(p, "metadata")
    meta = resolve(meta_arg) if meta_arg else None
    if meta is not None and not meta.is_file(): raise ArgError(f"metadata not found: {meta}")
    archive = src.suffix.lower() in (".apk", ".ipa", ".zip")
    if not archive and meta is None: raise ArgError("Native input requires params.metadata")
    argv = runtime()
    outdir = prepare_output(output_dir(p, "output", "unity", src.stem))
    # Extraction files cannot be mistaken for successful dumper products.
    with tempfile.TemporaryDirectory(prefix="agg_il2cpp_") as temp:
        binary = src
        if archive:
            binary, extracted = archive_inputs(src, Path(temp), text(p, "abi"), meta)
            if meta is None: meta = extracted
        rc, out = run_process(argv + [binary, meta, outdir], timeout=240, cwd=DLL.parent)
        print("\n".join(out.splitlines()[-25:]))
    required = [outdir / "dump.cs", outdir / "script.json"]
    if rc != 0 or not all(f.is_file() and f.stat().st_size > 0 for f in required):
        raise OpError(f"Il2CppDumper did not complete (rc={rc}); no complete core products. Output: {outdir}")
    print(f"Products: {outdir}")
    return True

OPS = {"env_check": op_env_check, "dump": op_dump}
if __name__ == "__main__": sys.exit(dispatch(OPS))
