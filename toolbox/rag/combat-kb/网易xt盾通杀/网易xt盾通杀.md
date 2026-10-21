# 网易 XT 盾通杀流程

## 原理

NetEase XT (NetHTProtect) 加密 Unity IL2CPP 的 `global-metadata.dat`，但运行时必须在进程内存中解密才能让游戏运行。

**关键漏洞：** XT 加密的是磁盘文件，运行时必须解密，解密后的明文就在进程内存里。不管 XT 版本怎么升级，这个漏洞修不了——不解谜游戏跑不了。

## 适用游戏
- 所有使用 NetEase XT / NetHTProtect 保护的 Unity IL2CPP 游戏
- Android 模拟器 (MuMu/BlueStacks) / 真机 通用
- 不需要逆向 XT 算法、不需要找解密 key

## 流程

### 1. 进游戏 → 找到 metadata 映射地址

```bash
adb shell su -c "cat /proc/$(pgrep com.package)/maps | grep global-metadata"
```

输出类似：
```
77797540a000-777976d2c000 rw-p 00000000 ... global-metadata.dat
```

注意看 `rw-p` 权限，说明是运行时解密后的可读写映射。

### 2. 从进程内存 dump 解密后的 metadata

```bash
# addr=0x77797540a000, size=0x1923000 (约25MB)
adb shell su -c "dd if=/proc/<PID>/mem bs=4096 skip=$((0x77797540a000/4096)) count=$((0x1923000/4096)) of=/data/local/tmp/metadata.bin"
adb pull /data/local/tmp/metadata.bin .
```

或者用 Frida 在内存中读取：

```javascript
var base = ptr(0x77797540a000);
var size = 0x1923000;
// 读取 byteArray 并保存
```

### 3. 去掉 XT 自定义头

解密后的 metadata 开头有 XT 的自定义加密头（336 字节），之后才是标准 Unity 元数据。

搜索 Unity 魔数 `0xFAB11BAF`（小端：AF 1B B1 FA）：

```python
with open('metadata.bin', 'rb') as f:
    data = f.read()
magic = b'\xaf\x1b\xb1\xfa'
pos = data.find(magic)
if pos >= 0:
    with open('metadata_clean.bin', 'wb') as f:
        f.write(data[pos:])
    print(f'XT 头 {pos} 字节已去除')
```

### 4. 从 APK 提取 libil2cpp.so

```bash
adb shell "unzip /data/app/~~.../base.apk lib/arm64-v8a/libil2cpp.so -d /data/local/tmp/"
adb pull /data/local/tmp/lib/arm64-v8a/libil2cpp.so .
```

### 5. 运行 Il2CppDumper

```bash
Il2CppDumper.exe libil2cpp.so metadata_clean.bin output_dir
```

### 6. 成果

```
output_dir/
├── dump.cs              → 所有 C# 类/字段/方法
├── il2cpp.h             → C 结构体定义
├── script.json          → Frida 钩子脚本
├── stringliteral.json   → 所有游戏字符串
└── DummyDll/            → 伪 DLL 可丢进 dnSpy 反编译
```

## 后续用途

| 数据 | 用途 |
|------|------|
| dump.cs 字段偏移 | Frida 直接读写内存改值 |
| Method RVA 地址 | Frida Hook 游戏函数 |
| DummyDll 源码 | dnSpy 看游戏逻辑 |
| 协议类名 | 分析 nysv 协议结构 |

## 实战案例：奥特曼传奇英雄2

| 项目 | 值 |
|------|-----|
| 包名 | `com.joym.atmlegend2.yofun.mumu` |
| Unity 版本 | IL2CPP v31 |
| 主程序集 | Assembly-CSharp + LATMOL |
| 内存映射 | `77797540a000-777976d2c000` |
| XT 头大小 | 336 字节 |
| Il2CppDumper | `D:\Il2CppDumper\` |
| 工作目录 | `C:\Users\20751\Desktop\atm\` |
