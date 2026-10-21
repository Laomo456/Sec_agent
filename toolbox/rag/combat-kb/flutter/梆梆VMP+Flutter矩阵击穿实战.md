# 梆梆 VMP + Flutter AOT 矩阵击穿实战手册

> 2026-09-07 | 杏吧视频 xinbasp.apk 实战提炼
> 定位：补充 Flutter_AOT_逆向知识库.md 的**加固击穿 + 矩阵追溯**部分

---

## 一、梆梆企业版 VMP 识别特征

```
assets/
├── mogosec_classes          ← DER RSA 公钥（壳签名校验用）
├── libmogosecurity.so       ← 60 字节占位符（非真实 so）
├── libmogosec_dex.so        ← 60 字节占位符
├── libmogosec_so            ← 60 字节占位符
├── libcmvmp.so              ← 60 字节占位符
└── xinstall                 ← 渠道归因版本号

lib/arm64-v8a/
├── libapp.so                ← Flutter AOT 业务代码（未被壳加密）
├── libflutter.so            ← Flutter 引擎
└── libkiwi.so               ← 原生签名/加密库（关键！）

classes.dex / classes2.dex / classes3.dex  ← 空壳（jadx 报 No classes）
```

**判定口诀**：mogosec 占位 + dex 空壳 + libkiwi.so = 梆梆 VMP + Flutter

---

## 二、脱壳流程（分钟级）

```
1. frida-dexdump 内存 dump（运行时脱壳，绕过 VMP）
   frida-dexdump -H 127.0.0.1:31337 -p <PID> -o <outdir>
   → 产出 35 个 dex（含运行时动态加载的）

2. jadx 反编译（Java 层只是 Flutter 壳，8148 类但业务在 so）
   jadx -d <outdir> <dex1> <dex2> ... <dex35>

3. Blutter 反编译 libapp.so（Flutter 业务逻辑全在这）
   → 产出 pp.txt（字符串池）+ asm/（反汇编）+ blutter_frida.js
```

**关键**：梆梆壳加密的是 dex，libapp.so 是明文——所以 frida-dexdump + Blutter 双管齐下。

---

## 三、Blutter 部署踩坑（MuMu15 + Windows）

### 3.1 中文路径致命

```
错误: cl.exe C1083: 无法打开包含文件 'D:/逆向工具/blutter/...'
原因: MSVC 编译器不认中文路径
解决: 复制到纯英文路径（junction 不行，Python resolve() 穿透）
      cmd /c mklink /J C:\blutter D:\逆向工具\blutter
      然后 rmdir /s /q C:\blutter\build（删旧 cache）
      用 C:\blutter 路径运行
```

### 3.2 环境变量脚本

```python
# run_blutter.py — 设置 VS 环境 + 运行
import os, sys, subprocess
VS = r'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools'
SDK = r'C:\Program Files (x86)\Windows Kits\10'
# ... 设置 PATH/INCLUDE/LIB ...
os.chdir(r'C:\blutter')  # 必须英文路径
subprocess.call([sys.executable, 'blutter.py',
    r'C:\heihui_arm64',    # 输入目录（libapp.so + libflutter.so）
    r'C:\heihui_blutter_out'])
```

### 3.3 Dart 版本自动识别

Blutter 从 libapp.so 头部读取 snapshot hash，自动匹配 Dart SDK 版本：
```
Dart version: 3.10.1, Snapshot: 1ce86630892e2dca9a8543fdb8ed8e22
flags: product no-code_comments compressed-pointers arm64 android
```

---

## 四、密钥提取三板斧

### 4.1 Blutter pp.txt 字符串池扫描

```bash
# 搜加密相关
findstr /i "encrypt decrypt aes md5 hmac secret key" pp.txt

# 搜域名
findstr /i "https:// domain api" pp.txt

# 搜认证头
findstr /i "X-AUTH token" pp.txt
```

**实战命中**：
```
[pp+0x18180] String: "X-AUTH-TOKEN"
[pp+0x11660] String: "X-AUTH-UUID"
[pp+0x3b40]  String: "kiwiAppKey"
[pp+0xf838]  String: "https://mgapi.md201.cc"
```

### 4.2 反汇编直接读密钥

Blutter 输出的 asm/ 文件里，密钥以字符串常量形式出现在指令中：

```assembly
; HttpCrypto.key() @ 0x9c5784
0x9c57a4: add x2, PP, #0x1b, lsl #12  ; [pp+0x1b5b8] "gFzviOY0zOxVq1cu"
                                      ^^^^^^^^^^^^^^^^
                                      这就是 AES Key（16 字节 = AES-128）

; HttpCrypto.iv() @ 0x9c5550
0x9c5570: add x2, PP, #0x1b, lsl #12  ; [pp+0x1b5b0] "ZmA0Osl677UdSrl0"
                                      ^^^^^^^^^^^^^^^^
                                      这就是 IV
```

### 4.3 Python 实弹验证

```python
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64, requests, json

KEY = b'gFzviOY0zOxVq1cu'
IV  = b'ZmA0Osl677UdSrl0'

def encrypt(obj):
    c = AES.new(KEY, AES.MODE_CBC, IV)
    return base64.b64encode(c.encrypt(json.dumps(obj).encode())).decode()

def decrypt(b64):
    c = AES.new(KEY, AES.MODE_CBC, IV)
    return unpad(c.decrypt(base64.b64decode(b64)), 16).decode()

# 验证：无鉴权请求 → 响应解密
r = requests.post('https://mgapi.md201.cc/Web/ComicsList',
    data=encrypt({'page':1}).encode(),
    headers={'Content-Type':'application/json'})
print(decrypt(r.text))
# → {"code":"400","message":"系统繁忙","data":null}  ✅ 完美命中
```

**注意**：package:encrypt 的 AES 默认模式是 **CBC**（不是 SIC/CTR），IV 16 字节。

---

## 五、houdini 转译下的 hook 限制

### 5.1 Interceptor 无效

```
现象: Interceptor.attach(DECRYPT_ADDR) 装上但永远不触发
原因: MuMu15 用 houdini 转译 ARM64 → x86_64
      ARM 代码跑在 rwxp 转译缓存段，hook 断点打在原始 r--p 段无效
```

### 5.2 替代方案：只读内存扫描

```python
# 原理：Dart 解密后的明文 JSON 会驻留在堆上一段时间
# 用 frida 枚举 rw- 段 → 逐段读 → Python 侧正则匹配

js = r'''
rpc.exports = {
    ranges: function() {
        return Process.enumerateRanges('rw-').map(r => ({base: r.base.toString(), size: r.size}));
    },
    read: function(addr, size) { return ptr(addr).readByteArray(size); }
};
'''

# Python 侧扫描
for r in ranges:
    data = script.exports.read(r['base'], min(r['size'], 8*1024*1024))
    for m in re.finditer(rb'\{"code"', data):
        print(f'HIT @ {r["base"]}+{m.start()}: {data[m.start():m.start()+200]}')
```

**实战命中 145 条明文**：域名池、API 响应、分发链接、推广 URL 全在堆里。

---

## 六、矩阵同源验证

### 6.1 分发链追踪

```
落地页 HTML（kbsp002/xmd002）
  ↓ 提取 androidUrl
https://18mddw.rsd-zx.com/kbsp.apk    ← 姐妹包 1
https://18mddw.rsd-zx.com/91gc.apk    ← 姐妹包 2

  ↓ 提取 tongjiApi
https://ziyantongji2.oxndby.com/AppInstall/register  ← 无鉴权分发 API
```

### 6.2 字节级同源验证

```python
import zipfile
for apk in ['xinbasp.apk', 'kbsp.apk', '91gc.apk']:
    z = zipfile.ZipFile(apk)
    data = z.read([n for n in z.namelist() if n.endswith('libapp.so')][0])
    print(apk, b'gFzviOY0zOxVq1cu' in data, b'mgapi.md201.cc' in data)

# 输出：三个 True True → 同一把钥匙，同一个后端
```

### 6.3 未授权接口（AEPay 系统）

| 接口 | 方法 | 风险 |
|------|------|------|
| `/AppInstall/register` | POST | 无鉴权返回任意 appCode 最新分发链接 |
| `/AppInstall/startup` | POST | 无鉴权写接口，触发任务推送 |
| `/Member/Reg` | POST | 泄露字段结构（UserName/Password/PhoneNumber） |

---

## 七、踩坑速查

| 坑 | 症状 | 解法 |
|----|------|------|
| Blutter 中文路径 | cl.exe C1083 | 复制到纯英文路径（junction 不行） |
| aapt 中文路径 | I/O error | 复制到英文路径再分析 |
| houdini hook 不触发 | Interceptor 装上但无输出 | 改用内存扫描 |
| Dart 不走系统代理 | mitmproxy 零流量 | 内存扫描 or hosts 劫持 |
| nsv11 状态残留 | "timed out waiting for stop" | killall nsv11 → 重启 → 等 3s → 再 attach |
| AES 模式猜错 | 解密出乱码 | 试 CBC（package:encrypt 默认） |
| frida-dexdump 目录不存在 | FileNotFoundError | 先 mkdir 输出目录 |

---

## 八、工具链总结

```
识别:     aapt dump badging（包名/版本） + 目录结构（mogosec 占位）
脱壳:     frida-dexdump（dex） + 直接提 libapp.so（未被壳加密）
反编译:   jadx（Java 壳） + Blutter（Flutter 业务）
密钥:     Blutter pp.txt 扫描 + asm/ 反汇编直读
验证:     pycryptodome AES-CBC 解密
矩阵:     落地页 HTML 提取 → curl 下载姐妹包 → 字节比对
接口:     curl + encrypt/decrypt 客户端
```

全部 Python + CLI，分钟级完成，不需要 IDA/JEB 重型工具。
