"""Portable unidbg scaffold and Maven runner. Exit 0=success, 1=execution, 2=arguments."""
import json
import os
import re
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mobile_runtime import (ArgError, EXTERNAL, OpError, ROOT, boolean, dispatch, files_root, package, read_config, resolve, run_process, text)
TOOL_DIR = Path(__file__).resolve().parent
CFG, CONFIG_ERROR = read_config(TOOL_DIR / "config.json", {"unidbg_version": "0.9.9"})

def configured(key, default):
    value = CFG.get(key)
    if value is None: return default
    if not isinstance(value, str) or not value.strip(): raise ArgError(f"config.{key} must be a path string")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path

def maven(project):
    home = configured("maven_home", TOOL_DIR / "bin" / "maven")
    if CFG.get("maven_path"):
        home = configured("maven_path", home).parent.parent
    java_home = configured("java_home", EXTERNAL / "java")
    java = java_home / "bin" / "java.exe"
    if not java.is_file() or not (java_home / "bin" / "javac.exe").is_file():
        raise OpError(f"JDK missing: {java_home}")
    boots = sorted((home / "boot").glob("plexus-classworlds-*.jar"))
    if len(boots) != 1 or not (home / "bin" / "m2.conf").is_file():
        raise OpError(f"Maven incomplete: {home}")
    env = dict(os.environ, JAVA_HOME=str(java_home), MAVEN_HOME=str(home))
    env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    argv = [java, "-Dfile.encoding=UTF-8", f"-Dmaven.home={home}",
            f"-Dclassworlds.conf={home / 'bin' / 'm2.conf'}",
            f"-Dmaven.multiModuleProjectDirectory={project}", "-cp", boots[0],
            "org.codehaus.plexus.classworlds.launcher.Launcher", "-B",
            "-s", EXTERNAL / "maven-settings.xml", f"-Dmaven.repo.local={EXTERNAL / 'maven-repository'}"]
    return argv, env

POM_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>agg</groupId>
  <artifactId>__ART__-harness</artifactId>
  <version>1.0</version>
  <properties>
    <maven.compiler.source>8</maven.compiler.source>
    <maven.compiler.target>8</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>com.github.zhkl0228</groupId>
      <artifactId>unidbg-android</artifactId>
      <version>__VER__</version>
    </dependency>
  </dependencies>
  <build>
    <finalName>__ART__-harness</finalName>
    <plugins>
      <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.15.0</version></plugin>
      <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-resources-plugin</artifactId><version>3.4.0</version></plugin>
      <plugin>
        <groupId>org.codehaus.mojo</groupId>
        <artifactId>exec-maven-plugin</artifactId>
        <version>3.1.0</version>
      </plugin>
    </plugins>
  </build>
</project>
"""

JAVA_TMPL = """package unidbg;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.AbstractJni;
import com.github.unidbg.linux.android.dvm.DalvikModule;
import com.github.unidbg.linux.android.dvm.VM;
import com.github.unidbg.memory.Memory;

import java.io.File;

/** __ART__ harness——scaffold 生成,可自由修改后重跑 run。 */
public class __CLASS__ {
    public static void main(String[] args) throws Exception {
        String soPath = "__SO_PATH__";
        String processName = "__PKG__";
        try (AndroidEmulator emulator = AndroidEmulatorBuilder.__FOR__()
                .setProcessName(processName).build()) {
            Memory memory = emulator.getMemory();
            memory.setLibraryResolver(new AndroidResolver(23));
            VM vm = emulator.createDalvikVM();       // 无 apk 上下文;需要 Java 层回调时换 createDalvikVM(new File("xx.apk"))
            vm.setVerbose(true);
            vm.setJni(new AbstractJni() {});         // so 里查包名/签名等 JNI 环境时,在此覆写喂假值

            DalvikModule dm = vm.loadLibrary(new File(soPath), false);
            if (dm.getModule().findSymbolByName("JNI_OnLoad", false) != null) {
                dm.callJNI_OnLoad(emulator);
                System.out.println("[+] JNI_OnLoad completed");
            } else {
                System.out.println("[+] Library loaded (no JNI_OnLoad export)");
            }
            System.out.println("[+] base=0x" + Long.toHexString(dm.getModule().base));

            // TODO 按需换目标调用(二选一):
            // ① JNI 方法(知道 Java 类名/签名):
            //   DvmClass c = vm.resolveClass("com/target/Native");
            //   DvmObject<?> ret = c.callStaticJniMethodObject(emulator,
            //       "sign(Ljava/lang/String;)Ljava/lang/String;", new StringObject(vm, "hello"));
            //   System.out.println("[+] ret=" + ret.getValue());
            // ② 纯 native 导出/偏移:
            //   Number n = dm.getModule().callFunction(emulator, 0x123456);
        }
    }
}
"""




def op_env_check(p):
    argv, env = maven(ROOT)
    rc, out = run_process(argv + ["--version"], env=env, timeout=30)
    print(out)
    print(f"Project dependency cache: {EXTERNAL / 'maven-repository'}")
    print("scaffold -> run. An environment check does not prove a target SO can execute.")
    return rc == 0

def op_scaffold(p):
    so = resolve(text(p, "so", required=True))
    if not so.is_file(): raise ArgError(f"SO not found: {so}")
    name = text(p, "name", so.stem)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name): raise ArgError("name must contain 1..80 letters/digits/_/-")
    cls = "Harness_" + name.replace("-", "_")
    pkg = package(text(p, "pkg", "com.target.harness"))
    arch = p.get("arch", 64)
    if isinstance(arch, bool): raise ArgError("arch must be 32/64/arm/arm64")
    arch = {"arm": "32", "armeabi-v7a": "32", "arm64": "64", "arm64-v8a": "64"}.get(str(arch), str(arch))
    if arch not in ("32", "64"): raise ArgError("arch must be 32/64/arm/arm64")
    version = CFG["unidbg_version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}(?:-SNAPSHOT)?", version):
        raise ArgError("config.unidbg_version must be a Maven version")
    project = files_root() / "unidbg" / name
    if project.exists(): raise ArgError(f"Project already exists; choose another name: {project}")
    src = project / "src" / "main" / "java" / "unidbg"
    src.mkdir(parents=True)
    (project / "pom.xml").write_text(POM_TMPL.replace("__ART__", name).replace("__VER__", version), encoding="utf-8")
    # Relative from project root; moving the whole agg directory keeps the harness usable.
    relative = os.path.relpath(so, project).replace(os.sep, "/")
    java = JAVA_TMPL.replace("__CLASS__", cls).replace("__ART__", name).replace("__PKG__", pkg)
    java = java.replace("__SO_PATH__", json.dumps(relative, ensure_ascii=True)[1:-1])
    java = java.replace("__FOR__", "for32Bit" if arch == "32" else "for64Bit")
    (src / f"{cls}.java").write_text(java, encoding="utf-8")
    print(f"Project: {project}\nMain: unidbg.{cls}\nNext: run(project=unidbg/{name})")
    return True

def op_run(p):
    project = text(p, "project", required=True)
    pdir = resolve(project if "/" in project or "\\" in project or Path(project).is_absolute() else f"unidbg/{project}")
    if not (pdir / "pom.xml").is_file(): raise ArgError(f"pom.xml not found: {pdir}")
    mains = sorted((pdir / "src" / "main" / "java" / "unidbg").glob("*.java"))
    main = text(p, "main")
    if not main:
        if len(mains) != 1: raise ArgError("Specify params.main when the harness has zero/multiple source files")
        main = "unidbg." + mains[0].stem
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*(?:[.][A-Za-z_$][A-Za-z0-9_$]*)+", main):
        raise ArgError("main must be a qualified Java class name")
    offline = boolean(p.get("offline", False), "offline")
    argv, env = maven(pdir)
    if offline: argv.append("-o")
    argv += ["-f", pdir / "pom.xml", "compile", "org.codehaus.mojo:exec-maven-plugin:3.1.0:java", f"-Dexec.mainClass={main}"]
    rc, out = run_process(argv, timeout=780, cwd=pdir, env=env)
    print("\n".join(out.splitlines()[-60:]))
    if rc: raise OpError(f"Maven failed ({rc}); inspect dependency/compiler/JNI errors above")
    return True

OPS = {"env_check": op_env_check, "scaffold": op_scaffold, "run": op_run}
if __name__ == "__main__": sys.exit(dispatch(OPS, startup_error=CONFIG_ERROR))
