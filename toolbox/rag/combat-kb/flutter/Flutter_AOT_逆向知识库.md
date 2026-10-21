# Flutter AOT 逆向知识库

---

## 一、Flutter AOT 编译基础

### 1.1 编译产物

Flutter AOT (Ahead-of-Time) 编译将 Dart 代码编译为原生机器码，产物：

| 文件 | 说明 |
|------|------|
| `libapp.so` | Dart AOT 编译产物，包含所有业务逻辑 |
| `libflutter.so` | Flutter 引擎运行时 |
| `isolate_snapshot_data` | Dart 虚拟机快照 |

### 1.2 与 Java/Kotlin App 的核心区别

```
Java App:   DEX → 可反编译为 Java 源码 → 易 Hook
Flutter AOT: Dart → 原生机器码 → 无反编译工具 → 难 Hook
```

- **无反编译工具**：没有像 jadx 这样的工具能把 AOT 还原为 Dart 代码
- **无法 Hook Dart 函数**：Frida Interceptor.attach 对 libapp.so 内部函数无效
- **只能 Memory.patchCode**：修改原生指令（如替换为 RET 指令跳过函数）

---

## 二、分析工具链

### 2.1 Blutter — AOT 反汇编

```bash
# 安装 (Python 3.10+)
pip install blutter

# 运行 (需要 root 或从设备 pull libapp.so)
blutter /path/to/libapp.so /path/to/output_dir
```

**输出：**
- `asm/` — 每个 Dart 函数的 ARM64 汇编 (.dart 注释格式)
- `pp.txt` — 函数名 → 地址映射表
- `objview.txt` — 对象布局信息

**示例 pp.txt 内容：**
```
0xa81a9c: doBuyButtonClick
0xa81e24: showBuyBottomSheet
0xa84190: showBuyVipDialog
```

### 2.2 地址计算

libapp.so 被加载到内存后的实际地址：

```
实际地址 = libapp.so 基址 + Blutter 偏移量
```

获取基址：
```bash
# 从 /proc/pid/maps 获取
grep libapp.so /proc/<PID>/maps
# 输出示例: 063a8000-0727b000 r--p ... libapp.so
```

### 2.3 Frida 脚本模板

```javascript
'use strict';
var BASE = ptr('0x063a8000');  // libapp.so 基址
var RET = 0xd65f03c0;          // ARM64 RET 指令

function patch(offset, name) {
    var addr = BASE.add(offset);
    Memory.patchCode(addr, 4, function(code) {
        code.writeU32(RET);
    });
    console.log('✓ ' + name);
}

// 关闭购买弹窗
patch(0xa81a9c, 'doBuyButtonClick');
```

---

## 三、AOT 汇编快速入门

### 3.1 常用 ARM64 指令

| 指令 | 机器码 | 说明 |
|------|--------|------|
| `RET` | `0xD65F03C0` | 函数返回 |
| `NOP` | `0xD503201F` | 空操作 |
| `B #offset` | `0x14000000 \| offset` | 无条件跳转 |
| `STP x29, x30, [SP,#-0x10]!` | `0xA9BF7BFD` | 函数入口 (EnterFrame) |
| `MOV x29, SP` | `0x910003FD` | 设置帧指针 |

### 3.2 函数入口模式

```assembly
; 标准 Dart AOT 函数入口
stp x29, x30, [SP, #-0x10]!   ; 保存帧指针和返回地址
mov x29, SP                    ; 设置帧指针
sub SP, SP, #0x28              ; 分配局部变量空间
```

### 3.3 参数传递

| 位置 | 说明 |
|------|------|
| `[x29+0x18]` | 第一个参数 (静态函数) |
| `[x29+0x10]` | 第二个参数 |
| `[x29+0x08]` | 第三个参数 (闭包时可能不同) |

---

## 四、逆向步骤

### 4.1 确定目标
1. 用 Blutter 反编译 `libapp.so`
2. 在 `pp.txt` 中搜索目标函数名
3. 在 `asm/` 对应文件中阅读反汇编

### 4.2 定位功能地址

常见目标：
- 购买弹窗: `buy`, `vip`, `recharge`, `pay`
- 视频播放: `play`, `exo_player`, `m3u8`
- 加密解密: `encrypt`, `decrypt`, `AES`, `HMAC`
- 用户模型: `UserModel`, `TokenModel`, `fromJson`

### 4.3 Patch 策略

**跳过整个函数 (RET)**
```javascript
Memory.patchCode(addr, 4, function(c) { c.writeU32(0xD65F03C0); });
```
适用：弹窗、购买、检查等有明确入口的函数

**修改条件跳转**
```javascript
// 将 B.EQ 改为 B.NE 或 NOP
Memory.patchCode(addr, 4, function(c) { c.writeU32(0xD503201F); }); // NOP
```

### 4.4 Frida 注意事项

- `Interceptor.attach()` **不适用于** `libapp.so` 中的 Dart 函数
- `Memory.patchCode()` **可用**，用于修改原生指令
- `Module.findBaseAddress("libapp.so")` 可能返回 null（可用 maps 文件代替）
- 需要先 `adb forward tcp:<PORT> tcp:<PORT>` 转发 Frida 服务端口

---

## 五、API 逆向要点

### 5.1 常见 API 加密模式

```
请求: 明文 JSON → AES/GZip 加密 → HTTP Body
响应: HTTP Body → AES/GZip 解密 → 明文 JSON
```

### 5.2 签名算法逆向

1. 在 Blutter 输出中搜索 `sign`, `md5`, `hmac`, `auth`
2. 跟踪 `_header()` 函数了解 HTTP 头构建
3. 静态密钥通常硬编码在代码中

### 5.3 密钥搜索

在 Blutter 输出的字符串常量中搜索：
```python
import re
with open('file.dart') as f:
    content = f.read()
# 搜索 32 位 hex 字符串 (常见密钥格式)
keys = re.findall(r'\"([0-9a-f]{32})\"', content)
# 搜索 16 位 hex 字符串
keys += re.findall(r'\"([0-9a-f]{16})\"', content)
```

---

## 六、常见问题

### Q: Interceptor.attach 为什么无效？
**A**: Dart AOT 编译后的函数不在常规的导出符号表中，Frida 无法 Hook。只能用 `Memory.patchCode` 直接改指令。

### Q: Module.findBaseAddress 返回 null？
**A**: libapp.so 可能被隐藏，用 `/proc/pid/maps` 手动查找。

### Q: 修改后 App 崩溃？
**A**: 确保 patch 地址在可执行段内，RET 指令需要 4 字节对齐。不要 Patch 函数中间的指令（会破坏执行流）。

### Q: 如何找到正确的函数地址？
**A**: 搜索敏感字符串 → 回溯调用链 → 找到函数入口。例如搜索 `"购买"` → 找到引用位置 → 向上找函数边界。

---

## 七、实例：糖心Vlog APK

### 7.1 App 信息
- 包名: `com.flutter324.ctnsp.xd04ib`
- 版本: `4.76.1`
- 架构: `arm64-v8a`

### 7.2 找到的关键函数

| 地址 (偏移) | 函数 | 说明 |
|-------------|------|------|
| `0xa81a9c` | doBuyButtonClick | 长视频购买按钮 |
| `0xa81e24` | showBuyBottomSheet | 购买底部弹窗 |
| `0xa84190` | showBuyVipDialog | VIP 购买弹窗 |
| `0xa85a80` | doBuyButtonClick (短) | 短视频购买按钮 |
| `0x9c9b68` | UserInfoModel.fromJson | 用户信息解析 |
| `0xa8a6e8` | LinesModel.fromJson | 视频线路解析 |
| `0x9d5cec` | /user/doVip | VIP 购买接口 |

### 7.3 提取的密钥

| 密钥 | 值 |
|------|-----|
| App API Key | `928547a24cbf62a315d8cf0f46d67a17` |
| X-JSL-AUTH Key | `cd271bb945844572818ba0bda1b59e85` |
| 视频 AES Key | `977b00697985feed47748f721010f568` |
| 视频 IV | `64cf692cebc516e4a86ac6081aae50ce` |

### 7.4 签名算法 (X-JSL-API-AUTH)

```python
import hashlib, time

STATIC_KEY = 'cd271bb945844572818ba0bda1b59e85'

def make_auth(body=''):
    now = int(time.time())
    expiry = now + 30
    raw_str = str(now)
    expiry_str = str(expiry)
    md5p = hashlib.md5(raw_str.encode()).hexdigest()[:16]
    inner = f'md5|{STATIC_KEY}|{expiry_str}|{md5p}|{body}'
    inner_md5 = hashlib.md5(inner.encode()).hexdigest()
    return f'md5|{expiry_str}|{md5p}|{inner_md5}'
```

### 7.5 闭包与加密调用链

```
_request (url, data, key, cancelToken, fail)
  ↓ 创建闭包捕获 key
闭包 (response)
  ↓ response.data.field_23 → 密钥字符串
decryptRaw(密钥字符串, 加密数据)
  ↓
generateKey(密钥字符串)
  ↓ HMAC-SHA256(key=app_key, data=hex_decode(密钥字符串))
AES-256-CBC 解密 → GZip 解压 → JSON
```

> **注意**：generateKey 的输入参数不是 getApiKey() 的返回值，而是闭包捕获的变量。具体值需要运行时截取。

---

## 八、Dart AOT 逆向限制（核心结论）

| 限制 | 原因 | 影响 |
|------|------|------|
| **Interceptor 无效** | Dart 函数不在 ELF 导出表 | 无法 hook 任何 Dart 函数 |
| **Module.find 失败** | libapp.so 被隐藏 | 需手动从 maps 读基址 |
| **只能 Memory.patchCode** | AOT 编译为原生码 | 只能改指令，不能插桩 |
| **参数在寄存器传递** | AOT 优化 | 栈上参数位置可能和预期不同 |

**这些限制是 Dart AOT 架构层面的，不是 Frida 版本能解决的。魔改版和原版在这些限制面前表现一样。**

---

## 九、Frida 版本对比：魔改版 vs 原版

### 8.1 对比表

| 方面 | 魔改版 (nosuke/sys-helper) | 原版 Frida |
|------|---------------------------|-----------|
| **隐蔽性** | ✅ 改进程名、改端口、去特征 | ❌ 特征明显易被检测 |
| **反检测** | ✅ 能过多数 App 的 Frida 检测 | ❌ 裸奔易被扫描 |
| **稳定性** | ❌ 偶发断连、重连慢、端口占用 | ✅ 稳定可靠 |
| **兼容性** | ❌ Memory.patchCode 正常，但部分 API 异常 | ✅ 全功能可用 |
| **Interceptor** | ❌ 对 libapp.so 完全无效 | ❌ 同样无效（Dart AOT 限制） |
| **功能完整度** | ❌ 有裁剪，部分 Frida API 缺失 | ✅ 完整无缺 |
| **SELinux** | ✅ 内置绕过，开箱即用 | ❌ 需要手动 setenforce 0 |
| **调试便利** | ❌ 报错信息不完整，排查困难 | ✅ 报错清晰，容易定位 |
| **版本跟进** | ❌ 落后上游版本 | ✅ 官方持续更新 |

### 8.2 选型建议

| 场景 | 推荐版本 | 原因 |
|------|---------|------|
| 普通 App 逆向 | **原版** | 稳定、功能全、好调试 |
| 银行/金融/游戏 App | **魔改版** | 需要过检测 |
| Flutter AOT 逆向 | **原版即可** | Interceptor 都不好用，魔改没优势 |
| 生产环境长期挂 | **原版** | 稳定优先 |
| 过 SELinux 严格设备 | **魔改版** | 省去 setenforce 步骤 |
