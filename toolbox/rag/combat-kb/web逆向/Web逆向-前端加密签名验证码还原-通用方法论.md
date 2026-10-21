# Web 逆向 — 前端加密 / 签名 / 验证码还原 通用方法论

> 适用范围: 带前端 JS 的 Web 系统、WebVPN、统一身份认证、移动端 H5 API
> 核心原则: **先静态定位，再补环境复刻，最后运行时对拍；能离线解决就不要上浏览器**
> 案例标注: 具体数值以 `[案例]` 标注，方法本身通用
> 前置认知: 前端一切都能被读，**"加密"在前端只等于"编码"**——只要算法在客户端跑过一遍，它就能被复现

---

## 一、目标分类（先知道自己在还原什么）

| 类别         | 典型场景                    | 主要手段                  | 成果形态                 |
| ---------- | ----------------------- | --------------------- | -------------------- |
| **前端逻辑还原** | SPA 路由、WebVPN 跳转、动态端点拼接 | 去混淆 + 通读源码            | 可读 JS + 端点清单         |
| **加密算法还原** | host 加密、参数加密、响应解密       | 找 key/iv → 判定标准性 → 复刻 | 可调用的 encrypt/decrypt |
| **签名机制还原** | API 鉴权（sign/token 头）    | 读算法 → 离线自洽构造          | 可离线构造任意请求            |
| **验证码绕过**  | 滑块 / 点选 / 图形            | 复刻请求体 + 补环境           | 自动过验证码的脚本            |
| **指纹与版本**  | 判定产品/版本以匹配 Nday         | 版权头 / 类名 / 堆栈行号       | 精确版本区间               |

**判断入口在哪一层**（`[案例]`）：
- 页面里出现 `Bp[...]` 之类的下标访问 → 字符串数组混淆（§二）
- 页面里出现 `var __xxx_key = "..."` → 站点级配置下发，直接可取（§三）
- 请求体带 `sign`/`token`/`nonce` 且算法不在页面里 → 打包进 native 或服务端（§八）
- 有滑块/点选图 → anji-plus 系（§九，命中率极高）

---

## 二、字符串数组混淆的还原（最高频，先做这个）

**特征**：文件形如

```js
(function(Bp, x){ /* 一大坨 */ })([
  "字符串1", "字符串2", ..., "字符串N"
], 0x1a2b);   // 末尾还可能有 uglify 的轮转常量
```

源码里所有字符串都写成 `Bp[下标]`。**去混淆三件事：找到数组、切出元素、代回下标。**

### 2.1 陷阱：不能按逗号切分

数组元素里**存在本身就是逗号的元素**（如 `","`）。按逗号 split 必错。**必须扫引号串**。

### 2.2 切数组：从尾部反查更稳

```python
# 从文件尾找数组终点，再向左取 IIFE 实参起点
tail = src.rfind('])')                  # 数组与参数的收尾
head = src.rfind('})([', 0, tail)       # IIFE 实参起点
if head == -1:
    head = src.rfind('])(', 0, tail)    # 另一种收尾写法
arr_txt = src[src.index('[', head):tail+1]
```

### 2.3 扫引号串（处理转义）

```python
def scan_strings(t):
    out, i, n = [], 0, len(t)
    while i < n:
        c = t[i]
        if c in '"\'`':
            q, buf, i = c, [], i + 1
            while i < n:
                if t[i] == '\\':
                    buf.append(t[i:i+2]); i += 2; continue
                if t[i] == q:
                    break
                buf.append(t[i]); i += 1
            out.append(''.join(buf))
        i += 1
    return out
```

### 2.4 代回下标（关键：验证完整性）

```python
def deref(src, arr):
    def rep(m):
        idx = int(m.group(1))
        return json.dumps(arr[idx], ensure_ascii=False) if 0 <= idx < len(arr) else m.group(0)
    out = re.sub(r'\bBp\[(\d+)\]', rep, src)
    # ★ 完整性判据：源码里出现过的下标最大值，必须 < 数组长度
    used = [int(x) for x in re.findall(r'\bBp\[(\d+)\]', src)]
    return out, (max(used) if used else -1), len(arr)
```

> **必须做完整性校验**：`max(用到的下标) < len(数组)`。若 `max >= len`，说明数组切少了或切错了，还原不完整，**此时得到的"可读版"会静默丢字符串，后面所有结论都会错**。

### 2.5 还有一层：轮转（rotation）

uglify 会在 IIFE 第二参数给一个数字，并在函数体开头写 `while(--x){ Bp.push(Bp.shift()) }`。**如果直接还原出的字符串读不通（乱码/错位），就是漏了这一步**——需要在代回前先把数组按该数字轮转一次。

`[案例]` 2172 条字符串的表，末尾 IIFE 为 `(function(Bp,x){...})([...], 0x1a2b)` 形态但**未启用轮转**，直接代回即得可读版（427 KB），字符串表里直接命中 `key/iv/cbc/ecb/encrypt/decrypt` 等关键词。

---

## 三、加密算法：怎么找到 key / iv

**找 key 的四个位置，按命中率排序：**

| 位置 | 形态 | 可靠性 |
|---|---|---|
| 页面内联变量 | `var __xxx_key = "..."` / `__xxx_crypt_key` | ★★★ 直接可读 |
| 配置文件 / 接口下发 | `/api/config`、`/xxx/getKey` | ★★★ 需一次请求 |
| 字符串表兜底值 | 去混淆后在表里 grep `key`/`iv`/`secret` | ★★ 常是 fallback |
| 硬编码在 native / 打包代码 | 需 §六 运行时抓 | ★ 最后手段 |

**取值优先级**（`[案例]`）：
```js
// 典型写法：站点配置 > 内置 fallback
key = site_config.__xxx_host_crypt_key || 'wrdvpnisthebest!'   // 反例：默认值就是弱密码
iv  = site_config.__xxx_host_crypt_iv  || key
```
> **默认值当 fallback 是常见设计缺陷**：一旦站点没配，就等于把密钥写在了公开 JS 里。

**确认 key 是否真的生效，有一步必须做**：拿到候选 key 后，用**实测密文反推比对**，不能只看源码声明（§四）。

---

## 四、判定：标准算法 还是 魔改（这一步决定成败）

**只读到 key/iv 不算成功**——必须验证"用标准库能不能复现实测密文"。

### 4.1 闭环自证（先排除自己的实现错误）

先做**与目标无关**的自洽测试：
```python
# 随机 IV → 加密 → 解密，必须往返一致
pt = b'A' * 16
ct = AES.new(k, MODE_CBC, iv).encrypt(pad(pt, 16))
assert unpad(AES.new(k, MODE_CBC, iv).decrypt(ct), 16) == pt
```
往返不一致 ⇒ **你的实现有问题，别往下走**。

### 4.2 正推比对（决定性）

用候选 key/iv **正推**已知明文（如各候选 hostname），与**实测密文**逐字节比对：

```python
for cand in candidates:
    got = encrypt_hex(cand, key, iv)
    if got == observed_ciphertext:
        print("命中", cand); break
else:
    print("全部不命中 ⇒ key 不对，或算法不是标准实现")
```

### 4.3 反解 + crib-drag（判定"魔改"）

若正推全不命中，做反向与差分：
- 用同一 key **反解实测密文**（CBC 与 ECB 都试）→ 若均抛 `Malformed UTF-8` 或出乱码，偏向"非标准"
- **两段已知明文长度不同的密文做差分**：若两段 IV 段相同而 `A^B` 与 `IV_A^IV_B` 不等，说明**产生密文的链路与公开可复现链路不一致**

### 4.4 结论怎么下（`[案例]`）

- key/iv **确认等于默认值**，但标准 AES-128-CBC 正推 12 个候选 hostname **全部不命中**
- 反向解 CBC/ECB **均出乱码**
- 源码里有 `new Un(RawAES(key, iv, 16))` 这种**非常规分组用法**，且调用栈显示 `xorBlock` 被自家 hook 包过
- ⇒ **判定：这是自研 AES 变体，不是标准 AES。离线复现不成立，必须走运行时**

> **这一步的价值在于：把"再试试"变成"这条路封了"。** 不要在没有正面证据时反复调参，浪费几个小时。

---

## 五、补环境 vs 运行时 hook：怎么选

**判据很简单——看密钥/算法是"客户端自造"还是"服务端下发"。**

| 情况 | 选什么 | 理由 |
|---|---|---|
| 算法与密钥**全在 JS 里可见** | **补环境（Node 复刻）** | 快、可批量、可离线 |
| 密钥**服务端下发**但可请求到 | 补环境 + 先请求取密钥 | 同上 |
| 算法**被 hook 包过 / 自研变体** | **运行时 hook（CDP）** | 离线无法复刻 |
| 需要**真实明文↔密文配对** | 运行时（有已认证会话时） | 离线造不出配对 |

### 5.1 补环境（Node 沙箱加载前端 JS）

**坑：UMD 会走 CommonJS 分支，导致 `window` 上没有你要的东西。** 解法是造一个只有 `window`、无 `module` 的沙箱：

```js
const fs = require('fs');
const src = fs.readFileSync('crypto-js.js', 'utf8');
const win = {};
new Function('window', 'self', 'globalThis', src + '\n;return window;')(win, win, win);
// 此时库挂在 win 上，而不是 module.exports
```

**自检**：补完环境后，先用**已知输入**验证能跑通，再去碰目标数据。

### 5.2 运行时 hook（走 CDP，见 §六）

补环境失败的判据（三条同时成立就放弃补环境）：
1. 标准库 + 实测 key/iv 正推已知明文**不命中**
2. 反解实测密文**出乱码**
3. 源码/调用栈显示算法被**自家 hook 包裹**或用了非同寻常的分组参数

---

## 六、CDP 抓运行时真值

**目标：不下断点、不改代码，先拿到运行时的事实。**

### 6.1 附着到独立 profile 的浏览器

```bash
chrome --remote-debugging-port=9222 --user-data-dir=<独立profile>
```
> **必须用独立 profile**，别污染日常浏览器。

### 6.2 hook 网络（拿请求体真值）

```python
# 注入 fetch/XHR hook，把请求记到全局
js = """
(() => {
  window.__cap = [];
  const of = window.fetch;
  window.fetch = function(...a){
    try{ window.__cap.push({u:String(a[0]), body:a[1]&&a[1].body}); }catch(e){}
    return of.apply(this, a);
  };
})();
"""
# Runtime.evaluate(js) → 触发页面自身行为 → Runtime.evaluate('JSON.stringify(window.__cap)')
```

### 6.3 劫持 eval 拿被包裹的函数源码

`[案例]` WebVPN 用 `eval(vpnGlobal, vpn_rewrite_js(fn.toString().slice(14,-2), 2))` 动态执行。劫持 `vpn_eval` 即可回收**改写前的原始函数体**：

```python
# Page.addScriptToEvaluateOnNewDocument 在文档最前注入，早于页面脚本
inject = """
(() => {
  const ov = window.vpn_eval;
  window.vpn_eval = function(...a){
    try{ window.__evalLog = (window.__evalLog||[]); window.__evalLog.push({args:a.map(String)}); }catch(e){}
    return ov.apply(this, a);
  };
})();
"""
```

### 6.4 回收全局快照

```python
# 导航后回收页面上的加密相关全局
Runtime.evaluate("JSON.stringify(Object.keys(window).filter(k=>/crypt|key|iv|vpn/i.test(k)).reduce((o,k)=>(o[k]=window[k],o),{}))")
```

**边界认知（重要）**：CDP/hook **能改客户端的一切，改不了服务端裁决**。
`[案例]` 实测四组：
- A 改客户端表单值 → 成功（证明 hook 能力）
- B 用改后的值提交 → 401（证明服务端不采信）
- C 页面里伪造"已登录"态 → 访问受保护路由仍 302 回登录页
- D 冻结 `Date.now`、塞假 cookie → 无效

> **认证是两方协议，凭据必须在服务端目录里对上。** 客户端花活只在"客户端自校验"的场景有效。

---

## 七、产品与版本指纹

**别猜产品，去证据里抠。** 三个高价值来源：

### 7.1 JS 版权头（最快）

```js
/*! Verify&admin MIT License by anji-plus */      → anji-plus AJ-Captcha
/* webpack ... */                                  → 打包器（webpack/rollup 要分清）
```
`[案例]` 看版权头直接定位验证码组件，**另一处曾把 Rollup 误判成 webpack**，后来从产物形态纠正。

### 7.2 类名 / 包路径（判定框架与中间件）

错误页或堆栈里的**完整类名**是硬指纹：
```
org.apereo.cas.*                 → Apereo CAS
org.pac4j.core.client.Clients    → pac4j（CAS 的委派客户端）
org.springframework.boot.actuate.* → Spring Boot（actuator 是否暴露）
org.apache.catalina.*            → Tomcat
```

### 7.3 堆栈行号（精确版本，最高价值）

**行号能定位到小版本区间**：
```
java.lang.Thread.run(Thread.java:750)        → JDK 8u1xx 系列
java.lang.reflect.Method.invoke(Method.java:498) → JDK 8
DispatcherServlet.java:940 / 901 / 1160      → Spring 4.3.x
AuthenticatorBase.invoke(AuthenticatorBase.java:483) → Tomcat 8.5 系
```

**拿到行号后去 NVD / CIRCL / VulDB 核对 CVE 与影响版本**。`[案例]` 由此匹配到 Apereo CAS 的 MFA 绕过（CVSS 9.8，`getRemoteAddr()` 判定可被 `X-Forwarded-For` 伪造绕过）——**且第一前置条件（代理在 CAS 前）已成立**。

> **注意"已弃用类"也是指纹**：`WebRequestTraceFilter` 自 Spring Boot 2.4 起已被移除，出现它说明是**自行配置**的，可推断版本下限。

---

## 八、签名机制还原

**标准形态**：
```js
sign = md5( "&".join( sorted( f"{k}={v}" for k in fields ) ) )
```
`[案例]` 移动端 H5 的签名：字段 `{appKey, param, time, secure, acceptSecure}`，**无盐**，`appKey` 硬编码在前端。

### 8.1 必测的三个点（这三个都是常见设计缺陷）

**① 密钥是否由客户端提交？**
把 `appKey` 换成 `"ZZZZZZ"` / `""` / 任意值，**签名自洽** → 若仍 200，说明服务端**用客户端提交的值去验签** ⇒ 签名零保护。

**② 签名覆盖范围是否完整？**
在 body 里**追加一个额外字段**，签名不动：
- 仍 200 ⇒ **签名只覆盖固定字段，其余字段可自由注入**
- 500 鉴权失败 ⇒ 签名覆盖整个 body（正常）

**③ 有没有防重放？**
- 同一条已签名请求**连发 N 次**都成功 ⇒ 无 nonce
- `time` 传 `0` / `1` / `±1年` / `-30天` 都成功 ⇒ **时间戳完全不校验**

### 8.2 怎么下结论

- ①②③ 全通过 ⇒ **该签名方案不提供任何真实性/完整性保证**，攻击者可**完全离线**构造任意请求（无需抓包、无需真实密钥）
- 但要**同时说清它的边界**：身份通常仍取自 `token`，签名失效≠越权

---

## 九、验证码还原（anji-plus 系命中率极高）

**识别特征**：`/captcha/get`、`/captcha/check`，类型 `blockPuzzle`（滑块）/ `clickWord`（点选），版权头 `anji-plus`。

### 9.1 请求体构造（从前端读，别猜）

```js
// 滑块：提交的是"算出来的距离"，不是像素位移
pointJson    = aesEncrypt(JSON.stringify({x: moveLeftDistance * 310 / img_width, y: 5.0}), secretKey)
captchaVerification = aesEncrypt(token + '---' + pointJson明文, secretKey)
// 字段: {captchaType, clientUid: localStorage['slider'], ts: Date.now(), token, pointJson}
```

**加密原语**：`AES-128-ECB + PKCS7 + Base64`（注意：**这里常是 ECB，不是 CBC**，别抄错模式）。
`token` / `secretKey` 由 `/captcha/get` 下发，**每次刷新都变**（`secretKey` utf8 长度恒为 16 ⇒ AES-128）。

### 9.2 拿真值的正确姿势

**不要硬猜 `secretKey`**——用 §6.2 的 hook 让页面自己发一次 `/captcha/get`，回收请求体与响应即可拿到真值。

### 9.3 端到端对拍的成功判据（关键）

```python
# 用真 secretKey/token 复刻构造器，POST /captcha/check，看返回码：
# 6110 = 验证码已失效      ← token 被消费或过期
# 6111 = 验证失败          ← ★ 结构/加密正确，只是坐标不对
# 其它  = 参数/解密错误
```
> **能拿到 `6111` 就说明"请求体构造 + 加密"已被服务端正确解析**——这就是对拍通过。别追求一次就对坐标。

### 9.4 边界（哪些地方是防住的）

- 票据**一次性**：同 `token` 重复提交、乱猜距离、缺 `ts`、极端 `ts` 全部失败
- 会话**绑定**：全新会话直接 POST `verify` 而不取图 → 全部失败
- ⇒ **顺带验证了一件事**：门禁是强制的，能绕的只是"人机"这层意义

### 9.5 另一类：验证码答案直接下发（更严重）

**判据**：取图接口的响应里是否有答案字段。
`[案例]` 拖拽验证码的响应是
```json
{"h": 146, "p": "data:image/png;base64,...", "s": "data:image/png;base64,..."}
```
`h` 就是答案高度，重复取图 `h = [146, 75, 69, 84, 132]` 每次都不同。

**闭环验证**：取图拿 `h` → 用 `h` 当 `w` 提交 verify → `{"success": true}` → 再登录时返回的是**认证层**错误（`INVALID_ACCOUNT`）而非 `CAPTCHA_FAILED` ⇒ **已真正越过门禁**。
**必须做对照实验**（不取图 / 同会话续发 / 仅取图）才能证明"唯一通过路径就是答案泄露"，否则可能只是门禁本身没开。

---

## 十、踩坑清单（都是实测踩过的）

| 坑 | 现象 | 真因 | 正确做法 |
|---|---|---|---|
| **编码** | 响应里中文全是 `å\x8c\x97...`，正则匹配不到 | 响应头**不带 `charset`**，HTTP 库默认按 ISO-8859-1 解码 | 一律 `r.content.decode('utf-8', 'replace')`；**注意长度类判据不受影响，内容类判据会全废** |
| **响应截断** | 大批端点被判成"非 JSON" | `read(N)` 截断导致 `json.loads` 失败 | 读取上限给足（8 MB）；**"响应越大越可能是泄漏"，把它当优先目标而不是丢弃** |
| **计数器漂移** | 两次请求的响应长度不同，误判为布尔差分 | 页面里有自增计数器（如 `document.write(605)` 每次 +1） | **做差分前先确认基线稳定**；跨基值重复 + 引入带外信号（时延/报错） |
| **响应键名** | 明明有数据却解析出 0 条 | 键名不是惯例的 `list`（可能是 `userList`/`rows`/`records`） | **先打印顶层键名**，别假设 |
| **参数名** | 翻页参数传了没反应 | 参数名不对（是 `offset` 而非 `page`） | 看响应里回显的字段名（`offset`/`limit`/`totalPages`）反推 |
| **PHP/JSON 注释** | 脚本语法错误 | 字符串里嵌了 ASCII 双引号 | 中文引号 `「」` 替代，或用单引号包 |
| **PowerShell 写文件** | 中文变乱码 | `Set-Content` 默认编码 | 用编辑器工具或 `-Encoding UTF8` |

---

## 十一、判据纪律（决定了结论能不能交）

**核心：任何结论都要能被自己的对照组推翻。**

1. **阳性对照必做**
   `[案例]` 找"动态内容读取端点"时，我的指纹检测函数一直返回 `None`（编码问题），于是"全部 404 ⇒ 不存在"这个结论**不可信**。加上阳性对照（**指纹必须能在静态页自身里找到**）后，负结论才成立。
   **没有阳性对照的"没找到"，不算结论。**

2. **单样本差异不算证据**
   长度差 1 个字符、状态码差一位，都可能是噪声。要求：**稳定复现 + 跨基值一致 + 至少一个带外信号**（时延/报错/联合）。

3. **分页机制先于规模结论**
   说"能导出 N 条"之前，必须先证明**能翻页**：改分页参数，比较**记录内容指纹**是否变化。
   `[案例]` 我先用错参数名（`page`）得出"无法翻页"，又误把 `offset` 当记录偏移算出"只能导 5,230 条"——两次都错。最终用**边界算术**定案：`5220×10 + 5 = 52,205` 与 `totalCount` 精确吻合。

4. **格式匹配 ≠ 字段性质**
   `[案例]` "18 位数字"匹配出来就写"身份证号"——**判据太弱**。正确做法是跑完整校验：
   - 第 7-14 位是合法出生日期（`YYYYMMDD`）
   - 第 18 位是 **ISO 7064:1983 MOD 11-2 加权校验码**（随机通过率仅 1/11）
   - 前 6 位是真实行政区划代码
   - **交叉印证**：第 17 位编码性别，与独立的 `sex` 字段比对（`[案例]` 28/28 吻合）
   - 三项联合通过率 < 1/1000 才叫"确认"

5. **"没测到" ≠ "不存在"**
   报告里必须分栏写清：**已验证的负面结果** / **明确未验证项（含原因）** / **方法缺陷**。
   `[案例]` 我主动写进了"读取上限 300KB 导致大响应被丢弃 ⇒ 是否还有第三个泄漏端点仍是未知状态"，而不是含糊过去。

---

## 十二、工具速查

| 环节 | 工具 | 备注 |
|---|---|---|
| 去混淆 / 切数组 | Python（`re` + 引号扫描） | 别用在线美化工具，会丢转义 |
| 加密复算 | PyCryptodome / Node `crypto` | 两种实现对拍，排除自身错误 |
| 运行时抓取 | CDP（websocket + `Runtime.evaluate`） | 独立 profile；`addScriptToEvaluateOnNewDocument` 抢在页面脚本前 |
| 网络 hook | 注入 `fetch`/`XHR` 包装 | 只记录，不阻断，避免改变行为 |
| 指纹核对 | NVD / CIRCL / VulDB / CT 日志 | 行号 → 版本 → CVE |
| 滑块对拍 | 自研脚本 + 真 `secretKey` | 目标：拿到 `6111` |

**流程总结一句话**：
> **读源码定位 → 复刻到本地自证 → 与实测对拍 → 对不上就判"魔改/服务端下发"，转运行时；全过程带阳性对照。**

---

tags: web逆向, 前端加密, 签名, 验证码, 去混淆, CDP, anji-plus, 指纹, 方法论
