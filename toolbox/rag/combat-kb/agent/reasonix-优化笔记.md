# reasonix 优化笔记（v2 · 官方 0.53.2 重装版）

> **适用版本**：`npm install -g reasonix@0.53.2`（2026-08-24 重装）→ **最新 2026-09-13 追加 cache 排序 + credits 站双站策略**
> **与 v1 差异**：官方 0.53.2 **重写了路由层**——旧版 `QSKDP3OS.resolveEndpoint`（apirelay/zen 分支）→ **新版移到 `GCNBIWK7.loadEndpoint`**；`QSKDP3OS` 变纯 OpenAI 客户端；键盘解析从 Ink 内置 → **新版自研 `ANYWFUKM.StdinReader`**。
> **历史**：v1 版备份为 `reasonix-优化笔记.old-20260824-183705.md`。

## 0. 当前功能全矩阵（重启生效）

| 功能 | 文件 | 状态 |
|---|---|---|
| 黑背景清理 10 处 | O7VQOZQR | ✅ |
| 跳词标点 + Ctrl+←/→ | O7VQOZQR + ANYWFUKM | ✅（v2 修复键码丢弃） |
| unpin 竞态 / 动态页高 / 滚轮 3行·32ms | O7VQOZQR | ✅ |
| 上下文表补全 1M | V4AXMN4X | ✅ |
| ImmutablePrefix 工具排序稳定缓存 | index.js + DFHI2MRB | ✅（tools() 按 function.name 排序） |
| 双站策略余额规划 | config + verify_cache.mjs | ✅（旧站+credits 混搭估用一周） |
| 摘要模型用当前模型 + free 退化线折叠 | DFHI2MRB | ✅ |
| /apirelay 统一路由（Jinshi/wxctf/tokenrhythmfree） | O7VQOZQR + 4EHRIP5U | ✅ |
| apirelay 分组 key + 前缀剥除 | GCNBIWK7 + QSKDP3OS | ✅（503 根治） |
| 版本号 red mouse | O7VQOZQR | ✅ |
| tokenrhythmfree 余额 ¥669.97（10-05 到期） | — | ✅ |
| ~~/openrouterfree~~ /~~DeepSeekfree~~ /~~chatanywherefree~~ /~~zenfree~~ | config 已移除，代码保留兜底 | 🗑️ |

---

## 1. 关键文件路由（v2 结构）

| 职责 | 文件（v2） | 旧版 |
|---|---|---|
| 模型路由/端点解析 | `GCNBIWK7.js` `loadEndpoint()` | QSKDP3OS `resolveEndpoint()` |
| OpenAI 客户端（payload 构造） | `QSKDP3OS.js` `buildPayload()` | 同（含 apirelay 前缀剥除） |
| 键盘原始解析（StdinReader） | `ANYWFUKM.js` | Ink parse-keypress |
| 键盘事件 bus（KeystrokeContext） | `ANYWFUKM.js` | — |
| TUI 主逻辑（输入处理/slash/滚动） | `O7VQOZQR.js` | 同 |
| 上下文 token 表 | `V4AXMN4X.js` `DEEPSEEK_CONTEXT_TOKENS` | 同 |
| 折叠（fold/summarize） | `DFHI2MRB.js` | 同 |
| slash 命令补全表 | `4EHRIP5U.js` `SLASH_COMMANDS` | 同 |

---

## 2. 通道路由（GCNBIWK7 loadEndpoint）

```js
function loadEndpoint(path = defaultConfigPath()) {
  const cfg0 = readConfig(path);
  const model0 = typeof cfg0.model === "string" ? cfg0.model.trim() : "";
  // apirelay 模型：剥前缀后按分组选 key
  if (model0.startsWith("apirelay-") && cfg0.apirelay?.baseUrl) {
    const bare = model0.slice("apirelay-".length);
    const grp = bare.startsWith("gemini-") ? "gemini" : bare.startsWith("claude-") ? "claude" : "";
    const key = grp && cfg0.apirelayBackup?.[grp]?.apiKey ? cfg0.apirelayBackup[grp].apiKey : cfg0.apirelay.apiKey;
    return { baseUrl: cfg0.apirelay.baseUrl, apiKey: key };
  }
  // free/zen 模型 → opencode zen 网关
  if (model0.includes("-free") || model0.includes("zen")) {
    return { baseUrl: ZEN_BASE_URL || "https://opencode.ai/zen/v1", apiKey: ZEN_API_KEY || cfg0.zenApiKey || "public" };
  }
  // 其余：env → cfg.baseUrl → 官方
  const envBaseUrl = resolveBaseUrlEnv(); // DEEPSEEK_BASE_URL
  if (envBaseUrl) return { baseUrl: envBaseUrl, apiKey: process.env.DEEPSEEK_API_KEY };
  const cfg = readConfig(path);
  if (cfg.baseUrl) return { baseUrl: cfg.baseUrl, apiKey: cfg.apiKey };
  return { baseUrl: void 0, apiKey: process.env.DEEPSEEK_API_KEY ?? cfg.apiKey };
}
```

### 三分组 key（2026-08-24 实测全 200）
| 分组 | 认证分组 | 模型 |
|---|---|---|
| deepseek（主） | DeepSeek 分组 | apirelay-deepseek-v4-flash / pro / vision-exp |
| gemini | Gemini 分组 | apirelay-gemini-3.6/3.7/3.5-flash |
| claude | Claude 分组 | apirelay-claude-haiku-4-5 / sonnet-4-6 / opus-4-8 |

---

## 3. apirelay 503 根治（2026-08-24 关键）

**现象**：主会话持续 `503 上游服务不可用 new.jinshi.xin`（重试 4 次）。实测各模型 200 一度误判"瞬时故障"。

**根因（两个叠加）**：
1. **buildPayload 没剥 `apirelay-` 前缀**（QSKDP3OS）→ 上游收 `apirelay-deepseek-v4-flash` → New API `model_not_found: No available channel ... under group DeepSeek` → **503**。实测带前缀 503 / 剥前缀 200。
   - 修复：`buildPayload` 顶部 `__relayModel.slice("apirelay-".length)`
2. **key 分组错配**（/model 切换不联动 key → env 残留 gemini key 发 deepseek 模型 → 503 under group Gemini）。
   - 修复：loadEndpoint 按模型前缀动态选 key（§2）

**教训**：New API 分组不匹配返回 **503**（非 403/404），文案 `No available channel for model X under group Y`。

---

## 4. /apirelay 与 /zenfree 命令（O7VQOZQR + 4EHRIP5U）

```
/apirelay                            → 切 apirelay-deepseek-v4-flash（用已存配置）
/apirelay <模型名>                   → 切模型（gemini-*/claude-* 自动选分组 key）
/apirelay https://...                → 更新 baseUrl
/apirelay https://... <token>        → 更新 baseUrl + 认证配置
/zenfree [模型]                      → 切 zen 网关（默认 deepseek-v4-flash-free）
```

- handler 定义在 `var HANDLERS = {` 前（避免 TDZ：HANDLERS 对象引用 `zenfree,`/`apirelay,` 必须在赋值后）
- SLASH_COMMANDS（4EHRIP5U）注册 + argCompleter 模型数组（deepseek/gemini/claude/free 分组）
- 判别：`isModel(/^(deepseek|gemini|claude|...)-/)` / `isUrl(/^https?:/)`

---

## 5. 键盘解析修复：Ctrl+←/→ 静默丢弃（ANYWFUKM.StdinReader，2026-08-24）

**现象**：Ctrl+←/→ 跳词完全没反应（日志诊断确认事件未达分支）。

**根因**：v2 自研 `StdinReader.handleChunk` 状态机收 `\x1B[1;5D`（xterm 修改键 Ctrl+←）→ `dispatchCsi("1;5D")` → `lookupCsi` **无 1;5D 条目** + `tryDecodeGenericCsi` **只认 `27;N;M~`/`N;Mu`/`Nu`** → **静默丢弃**（无事件广播）。

**修复**（ANYWFUKM `CSI_TAIL_MAP`）：
```js
{ tail: "1;5C", ev: { input: "", rightArrow: true, ctrl: true } },
{ tail: "1;5D", ev: { input: "", leftArrow: true, ctrl: true } },
{ tail: "1;6C", ev: { input: "", rightArrow: true, ctrl: true, shift: true } },
{ tail: "1;6D", ev: { input: "", leftArrow: true, ctrl: true, shift: true } },
```

**链路验证**：`\x1B[1;5D → {leftArrow:true, ctrl:true}` ✅；`\x1B[D → {leftArrow:true}` 回归 ✅

---

## 6. 跳词实现（O7VQOZQR，按 v1 §3.2 + §23.43）

```js
function isAlpha2(ch) { return !!ch && /[a-zA-Z0-9_]/.test(ch); }
function isCJK2(ch) { /* 2E80-9FFF, F900-FAFF, FE30-FE4F, FF00-FFEF(全角标点), 20000-2A6DF, 2F800-2FA1F */ }
function isPunct2(ch) { return !!ch && !/\s/.test(ch) && !isAlpha2(ch) && !isCJK2(ch); }
// previousWordStart / nextWordEnd：分类分支（alpha/CJK/punct 各自连跳，else 跳1）
// isCJK2 含全角标点 → 中文标点随中文连跳（设计如此）
```

**绑定**（key 处理，@key.leftArrow 之前）：
```js
if (key.ctrl && key.leftArrow) → previousWordStart
if (key.ctrl && key.rightArrow) → nextWordEnd
```

**行为**：`abc,def` 从尾连续 Ctrl+← = 7→4→3→0（标准逐步跳词）；`a,,,b` 标点段独立跳；`中文测试，继续` 中文+全角标点同段连跳；词首/空串安全（无死循环）。

**v1 的 bug 教训**：`while (i > 0 && punct ? A : B)` 中 `&&` 优先级高于 `?:` → 实际 `while ((i>0 && punct) ? A : B)` → punct=false 时 `i>0` 丢失 → 词首死循环。**用显式 if/break 结构**。

---

## 7. 上下文表（V4AXMN4X DEEPSEEK_CONTEXT_TOKENS）

补全 1M 窗口（防 §23.31 bug 复发：缺模型 → fallback 13 万 → 百分比虚高 + 折叠过激）：
```js
deepseek-v4-flash/pro/chat/reasoner: 1e6
apirelay-deepseek-v4-flash/pro/vision-exp: 1e6
apirelay-gemini-3.6/3.7-flash, apirelay-claude-haiku-4-5/sonnet-4-6/opus-4-8: 1e6
deepseek-v4-flash-free, nemotron-3.5-lightning-free, hy3-free, laguna-s-2.1-free, x-preview-f-free: 1e6
```

---

## 8. 折叠优化（DFHI2MRB，v2 结构）

- **摘要模型**：`summarizeForFold(head, pinnedNames, model)`——用当前模型（apirelay 前缀走 apirelay key）。v2 原版硬编码官方 `deepseek-v4-flash`（用户无官方 key → 401 折叠失败）
- **free 退化线折叠**（§23.21 恢复）：`isFree && ratio > 0.25` 激进折 / `> 0.2` 普通折（20 万退化线前）
- **官方阈值保留**：HISTORY_FOLD_THRESHOLD=0.75 / AGGRESSIVE=0.78 / MIN_SAVINGS=0.3 / FORCE_SUMMARY=0.8 / TURN_START=0.9 / 摘要超时 15s

---

## 9. 版本号 red mouse（O7VQOZQR）

`Text{color:FG.faint}, "red mouse"`（替代 `v${VERSION}`）

---

## 10. 常用运维

- **重启生效**：进程启动时间必须晚于 chunk mtime（常见坑：patch 后没重启用户反馈"没变化"）
- **apirelay 余额**：`/v1/dashboard/billing/usage` 返回累计用量（`total_usage`）；余额接口 404（New API 封）
- **opencode 接中转**：`~/.config/opencode/opencode.jsonc` 配 `provider.jinshi / jinshi-gemini / jinshi-claude`（@ai-sdk/openai-compatible + 分组 key）
- **npm 代理坑**：`npm config get proxy` 残留失效代理（127.0.0.1:31180）→ 全请求 ECONNREFUSED → `npm config delete proxy` 后直连镜像
- **zenfree 备用**：deepseek-v4-flash-free 上游断供（400 Model unavailable）；nemotron-3.5-lightning-free 可用（4.6s）

---

## 11. 诊断钩子（定位完移除）

- 跳词：`patch_jump_diag.py` 写 `%TEMP%/reasonix-jump-diag.log`
- HTTP：`patch_http_diag.py` 写 `%TEMP%/reasonix-http-diag.log`（url/bodyMB/status）

---

## 12. 工具链实战坑（2026-08-24 长会话实录）

### 12.1 yakit_bash 的 cmd 引号转义（最高频坑）
- **现象**：`python -c "import ..."` 双引号被 cmd 包装层吃掉 → python 收到 `"import` → `SyntaxError: unterminated string literal`；`adb shell "cmd | grep"` 同理 → 设备端 sh 把整串当一个命令名 `inaccessible or not found`
- **铁律**：
  - python 一律**写脚本文件再执行**（write_file → `python x.py`），不用 `-c`
  - adb shell 远程命令**不带引号/管道**，复杂逻辑写成 `.sh` push 到设备 `sh /data/local/tmp/x.sh` 执行
  - 路径含中文/括号（如 `D:\逆向工具\frida(暂时不要乱用)\`）：push 时**去掉引号**反而成功（加了引号 adb 收到字面引号 → cannot stat）

### 12.2 MCP 工具层 60s 超时
- **现象**：curl 下大文件（GitHub release）、sha256 大文件计算 → 60s 被 MCP 掐断，进程死/输出丢
- **对策**：
  - 长下载 → 原生 `run_background` + `wait_for_job(timeoutMs=180000)`；yakit_bash 的 timeoutSec 救不了 MCP 层
  - 长计算 → **结果落盘再 read**（print 到 stdout 超时即全丢）
  - GitHub 直连被 reset（exit 56）→ ghfast.top 也连不稳；**优先翻本地已有资源**（本案：`frida-server-official` 就在 `D:\汇总\frida\bin\`，白下了半天）

### 12.3 run_background 输出缓冲
- python 脚本 `print()` 不带 `flush=True` 且 stdout 接管道时全缓冲 → `job_output` 一直 `byteLength=0`，**任务其实在正常跑**
- 对策：长驻脚本所有 print 加 `flush=True`，或关键日志写文件

### 12.4 原生文件工具跨盘符
- `read_file / list_directory / search_files / write_file / edit_file` 直接吃 Windows 绝对路径（`D:\xxx`、`C:\Users\xxx`）✅ 中文路径 OK
- `search_content` 全目录扫大知识库会 **120s 超时**（D:\汇总 有 197MB 二进制）→ 缩小 path 或用 glob 过滤
- `edit_file` 前**必须先 read_file**（工具强制）；SEARCH 文本要与磁盘字节精确一致

### 12.5 loop-guard 微步警告
- 连续多轮"只发一个短工具调用、无分析"会被系统 loop-guard 打断
- 对策：探索阶段每轮**并行发多个独立调用**；拿到结果后先给一段实质分析再决定下一步

### 12.6 多设备 adb 会话
- adb daemon 重启（`* daemon not running`）后旧连接全失效 → `device 'xxx' not found`，重新 `adb connect` / 重列 devices
- MuMu 特性：`adb shell` 直接是 root（提示符 `#`），**没有 su 二进制**（KSU `su_compat=0`），别套 `su -c`
- 模拟器换实例端口会变（7555 → emulator-5554），脚本里写死 `-s` 参数要先 `adb devices` 核对

---

## 13. OpenRouter Free 接入（2026-08-25）

### 13.1 Reasonix 命令

新增 `/openrouterfree [model]`：

```text
/openrouterfree
  -> openrouterfree-stealth/ox-alpha
/openrouterfree nvidia/nemotron-3.5-lightning:free
  -> openrouterfree-nvidia/nemotron-3.5-lightning:free
```

- `GCNBIWK7.loadEndpoint()`：`openrouterfree-` 模型读取 `config.openrouterFree`。
- `QSKDP3OS.buildPayload()`：发送前剥除 `openrouterfree-` 前缀。
- OpenRouter 地址：`https://openrouter.ai/api/v1`。
- 请求头保留 `HTTP-Referer`、`X-Title` 和浏览器 User-Agent。
- `4EHRIP5U.SLASH_COMMANDS` 注册命令及候选模型。
- `O7VQOZQR` handler 切换模型时同步更新现有客户端的 `baseUrl` 和 `apiKey`，不需要重启后才能切端点。

### 13.2 可用性结论

- `stealth/ox-alpha` 曾出现 OpenRouter 上游 429（shared pool 限流），不是本地配置错误。
- OpenRouter Free 备选：`nvidia/nemotron-3.5-lightning:free`、`openrouter/free` 等；具体可用性以当时 `/models` 和 chat 实测为准。
- 模型列表存在不代表当前上游可用，必须用实际 chat 请求验证。

---

## 14. OrangeCC 多模型与多 key（2026-08-25）

### 14.1 配置

`config.json` 的 `apirelayBackup.orangecc` 保存独立地址和 key 列表；不同 GPT 模型可用不同 key，使用 `modelKeys` 做绑定。完整密钥不写入知识库。

```text
gpt-5.6-luna -> OrangeCC key A
gpt-5.6-sol  -> OrangeCC key B
gpt-5.5      -> OrangeCC key B
gpt-5.4      -> OrangeCC key B
gpt-image-2  -> OrangeCC key B
```

- 地址：`https://api.orangecc.cc/v1`。
- Cloudflare 1010 需要浏览器 User-Agent；Reasonix 和 OpenCode provider 均配置了 UA。
- OrangeCC 的 `/models` 结果可能随 key 分组不同；`gpt-luna` 不是有效 ID，正确 ID 是 `gpt-5.6-luna`。
- key 与模型权限不匹配时，OrangeCC 可能返回 404/502/503 `model_not_found`，不能把它误判为网络故障。

### 14.2 动态路由

`GCNBIWK7.loadEndpoint()` 按 `apirelay-` 后的模型前缀选择分组：

```text
deepseek-* -> apirelay 主 key / Jinshi
gemini-*   -> apirelayBackup.gemini / Jinshi
claude-*   -> apirelayBackup.claude / Jinshi
gpt-*      -> apirelayBackup.orangecc / OrangeCC
```

`O7VQOZQR` 的 `/apirelay <model>` 也会同步修改当前客户端端点，避免只切模型名而继续请求旧的 Jinshi 地址。

---

## 15. OpenCode 中转配置（2026-08-25）

配置文件：`C:\Users\20751\.config\opencode\opencode.jsonc`。

- `jinshi`：DeepSeek，标签 `· Jinshi`。
- `jinshi-gemini`：Gemini，标签 `· Jinshi`。
- `jinshi-claude`：Claude，标签 `· Jinshi`。
- `jinshi-gpt`：Jinshi GPT，标签 `· Jinshi`。
- `orangecc`：GPT/Image，标签 `· OrangeCC`。
- OpenRouter Ox Alpha 配置已移除，不作为当前保底通道。

模型显示标签只用于界面展示，发送时仍使用真实模型 ID；不要把标签文本直接发给上游。

### 15.1 诊断原则

1. 先检查 provider 的 `baseURL`、模型 ID、key 分组是否匹配。
2. 再用同一 key + 同一模型做最小 chat 请求。
3. `models` 返回 200 不代表 chat 可用，必须实测 chat。
4. OpenRouter 429 多为上游共享池限流；Jinshi 的 503 `No available channel ... under group ...` 多为 key/模型分组错配。

---

## 17. 当前保留使用的模型通道（2026-08-25）

### 17.1 OpenCode provider

配置文件：`C:\Users\20751\.config\opencode\opencode.jsonc`。

| Provider | 端点 | 当前用途 |
|---|---|---|
| `jinshi` | `https://new.jinshi.xin/v1` | DeepSeek Flash/Pro/Vision |
| `jinshi-gemini` | `https://new.jinshi.xin/v1` | Gemini 3.5/3.6/3.7 Flash |
| `jinshi-claude` | `https://new.jinshi.xin/v1` | Claude Haiku/Sonnet/Opus |
| `jinshi-gpt` | `https://new.jinshi.xin/v1` | Jinshi GPT 分组 |
| `orangecc` | `https://api.orangecc.cc/v1` | OrangeCC GPT/Image |

### 17.2 Jinshi GPT 模型

`jinshi-gpt` 当前保留：

```text
jinshi-gpt/gpt-5.4
jinshi-gpt/gpt-5.4-mini
jinshi-gpt/gpt-5.5
jinshi-gpt/gpt-5.6-sol
jinshi-gpt/gpt-5.6-terra
jinshi-gpt/gpt-image-2
jinshi-gpt/codex-auto-review
```

来源：Jinshi GPT 分组 key。key 只保存在本地 OpenCode 配置，笔记不记录明文。

### 17.3 OrangeCC GPT 模型绑定

```text
gpt-5.6-luna -> OrangeCC key A
gpt-5.6-sol  -> OrangeCC key B
gpt-5.5      -> OrangeCC key B
gpt-5.4      -> OrangeCC key B
gpt-image-2  -> OrangeCC key B
```

Jinshi 与 OrangeCC 存在同名模型时，必须通过 provider 名或显示标签区分：

```text
jinshi-gpt/gpt-5.6-sol  -> Jinshi
orangecc/gpt-5.6-sol    -> OrangeCC
```

### 17.4 Reasonix 快捷命令

```text
/apirelay Jinshi/gpt-5.6-sol   -> apirelay-jinshi-gpt-5.6-sol
/apirelay OrangeCC/gpt-5.6-sol -> apirelay-gpt-5.6-sol
/openrouterfree                -> 已移除 OpenCode Ox Alpha 配置时，勿混用
```

标签只用于选择和显示；Reasonix 内部会转换为路由模型名，发送前去除 provider 前缀。

### 17.5 长期保底

- 主力：`jinshi/deepseek-v4-flash` 或 `jinshi-gpt/gpt-5.6-sol`。
- Gemini/Claude：按对应 Jinshi provider 使用，不能混用其他分组 key。
- OrangeCC：Luna 使用独立 key，Sol/Terra/5.5/5.4 使用另一组 key。
- OpenRouter Ox Alpha 不作为当前保底；若免费线路限流，不应把 429 当成本地配置错误。

---

## 16. 中转模型标签与 GPT 映射（2026-08-25）

### 16.1 `/apirelay` 显示规范

为便于辨识中转来源，补全列表使用带 provider 前缀的显示值：

```text
Jinshi/deepseek-v4-flash
Jinshi/deepseek-v4-pro
Jinshi/deepseek-v4-flash-vision-exp
Jinshi/gemini-3.6-flash
Jinshi/gemini-3.7-flash
Jinshi/claude-sonnet-4-6

OrangeCC/gpt-5.6-luna
OrangeCC/gpt-5.6-sol
OrangeCC/gpt-5.6-terra
OrangeCC/gpt-5.5
OrangeCC/gpt-5.4
```

- 标签只用于界面辨识；`/apirelay` handler 接收参数时自动移除 `Jinshi/` 或 `OrangeCC/`。
- 内部模型名仍规范化为 `apirelay-<model>`，发送给上游前由 `buildPayload()` 移除 `apirelay-`。
- `Jinshi/` 和 `OrangeCC/` 不是上游模型名，禁止直接发送给 API。

### 16.2 OrangeCC key 绑定

```text
gpt-5.6-luna -> OrangeCC key A
gpt-5.6-sol  -> OrangeCC key B
gpt-5.5      -> OrangeCC key B
gpt-5.4      -> OrangeCC key B
```

当前实测：第二组 OrangeCC 凭据的 Sol/Terra/5.5/5.4 均 3/3 成功；第一组凭据单独用于 Luna。完整凭据只保存在本地 config，不写入笔记。

### 16.3 OpenCode 标签

OpenCode provider 模型显示名同步使用：

```text
DeepSeek/Gemini/Claude ... · Jinshi
GPT/Image ... · OrangeCC
```

显示名与实际模型 ID 分离，切换或发送请求时只使用真实 ID。

---

## 18. 多站点 Free 通道（2026-08-26）

> 按站点各自独立命令 + 独立 baseUrl/key（config 各字段 + loadEndpoint 前缀路由 + buildPayload 剥前缀 + handler 同步端点 + SLASH_COMMANDS 补全）。

| 命令 | 站点 | 默认模型 | 实测 |
|---|---|---|---|
| `/openrouterfree` | openrouter.ai/api/v1 | stealth/ox-alpha | ✅ |
| `/sensenovafree` | token.sensenova.cn/v1（商汤） | **deepseek-v4-flash**（原默认 flash-lite，偶发空输出已换） | ✅ 1.2-2.5s，71-109 t/s |
| `/chatanywherefree` | api.chatanywhere.tech/v1 | deepseek-v4-flash | ✅ 3.6s avg，输出稳定 |
| `/zenfree` | opencode.ai/zen/v1 | deepseek-v4-flash-free | 备用 |

- 模型前缀：`sensenovafree-` / `chatanywherefree-`，发送前剥除。
- 商汤可用模型：sensenova-6.8/6.7-flash-lite、deepseek-v4-flash、glm-5.2、sensenova-u1-fast、sensenova-u1.5-lite。
- ChatAnywhere 可用 deepseek：v4-flash / v4-pro / v3.2 / v3.2-thinking / deepseek-chat。
- ChatAnywhere 域名：境内 `api.chatanywhere.tech`，境外 `api.chatanywhere.org`（`.com` 连不上）。

---

## 19. 配额实测与逆向负载（2026-08-26）

### 19.1 通道配额

**SenseNova（商汤）**：公测完全免费，5 小时窗口独立计数，同账户所有 key 共享。实测 flash-lite 剩 99.83%、deepseek 剩 99.2%——窗口内富余，5h 自动恢复。

**ChatAnywhere**：点数制（9,959 点）+ 每日请求硬限：
- deepseek 全系 30 次/天
- gpt-5 系列（sol/terra/5.4/5.5）5 次/天
- mini/nano 系 100 次/天
- 点数耗尽后单次输入限 4096 token（逆向大上下文直接废）

### 19.2 android 逆向真实负载（events.jsonl 统计，27,508 行）

| 指标 | 数值 |
|---|---|
| 请求次数 | 4,020 |
| 总输入 | 9.46 亿 tokens |
| 总输出 | 211 万 |
| 缓存命中 | 72%（7.2 亿） |
| **平均单次输入** | **23.5 万 tokens** |

### 19.3 结论

- 逆向高压单次请求平均 23.5 万输入 → 商汤 5h 窗口够约半天到一天重度使用，公测期间可无限循环；**商汤是长期免费主力**。
- ChatAnywhere 每天 30 次 deepseek 硬限 → 只够 1 个逆向任务，应急用。
- 缓存命中 72% 是命脉：长会话不轻易 `/new`，输入量省 7 成。
- 默认 `sensenovafree` 用 deepseek-v4-flash（稳定），不用 flash-lite（实测 1/3 空输出）。

---

## 20. wxctf 中转接入（2026-08-26）

> 站点 `https://ai.wxctf.com/v1`（模型全、便宜：deepseek ¥1/¥2 每百万）。接入模式与 Jinshi/OrangeCC 同构：config `apirelayBackup.wxctf` + loadEndpoint 前缀路由 + buildPayload 剥前缀 + handler `wxctf/` 标签 + SLASH_COMMANDS 补全 + opencode provider。

### 20.1 用法

```text
/apirelay wxctf/deepseek-v4-flash   -> apirelay-wxctf-deepseek-v4-flash
/apirelay wxctf/qwen-3.8-max
/apirelay wxctf/minimax-m3
```

### 20.2 关键修复（2026-08-26）

- **默认 deepseek 路由切到 wxctf**：Jinshi deepseek 分组断货（503/404）后，`apirelay-deepseek-v4-flash` / `apirelay-deepseek-v4-pro`（裸名）改走 **wxctf**（loadEndpoint + handler grp 判断加 `bare === "deepseek-v4-flash" || "deepseek-v4-pro" → "wxctf"`）。vision-exp 保留 Jinshi（wxctf 无视觉）。
- **max 选项恢复**：`effortChoicesForBaseUrl(baseUrl)` 原来只对 DeepSeek 官方 host 返回 `ALL`（含 max）→ 改为**无条件 `return ALL`**——所有中转通道都有 `low|medium|high|max`。

### 20.3 wxctf 模型实测（2026-08-26，1+1 测试）

| 模型 | 耗时 | 回答 | 备注 |
|---|---|---|---|
| mimo-v2.5-pro | 2.92s | ✅ 完整 | 最快 |
| minimax-m3 | 3.25s | ✅ 完整 | **性价比首选** |
| qwen-3.8-max | 4.91s | ⚠️ 正文空 | 推理吃光小预算 |
| deepseek-v4-flash | 5.03s | ⚠️ 截断 | 思考型，需大 max_tokens |
| kimi-k3 | 5.13s | ✅ 完整 | |
| glm-5.3 | 19.22s | ✅ | 🐌 太慢 |

- **wxctf 全部是思考型模型**（reasoning_content 占输出）——小 max_tokens 时正文被截断/为空；reasonix max 模式无此问题。
- 定价：flash ¥1/¥2、minimax ¥2.1/¥8.4、mimo ¥3/¥6、qwen-max ¥12/¥36、kimi ¥20/¥100。
- 充值 ¥10 高压估算：flash 约 1 天、minimax 约 1 天、qwen 约 0.5 天；缓存 72% 命中可延长 2-3 倍。
- opencode provider `wxctf`：15/15 模型全量配置（含 LongCat-2.0、mimo 系、qwen-3.7-plus）。

### 20.4 成本结论

- 单次小请求实测：flash ¥0.000022 / minimax ¥0.000058 / qwen-3.8-max ¥0.0013。
- **性价比排序**：minimax-m3（快+便宜+稳）> deepseek-v4-flash（极致省钱）> mimo-v2.5-pro（最快）> qwen-3.8-max（贵但强，留复杂任务）。
- reasonix 比 opencode 省钱 3.5 倍：prefix 缓存 72% 命中 vs opencode 每次全量重发（同 23.5 万输入回合：reasonix ¥0.066 vs opencode ¥0.235）。

---

## 21. 按中转站优化历史折叠（2026-08-26）

### 21.1 折叠策略

`DFHI2MRB.js` 新增 `foldProfile(model)`，按当前模型标识选择阈值，不再让所有中转共用一套策略：

| 通道 | 普通折叠 | 激进折叠 | 尾部保留 |
|---|---:|---:|---:|
| Jinshi | 50% | 65% | 8% / 4% |
| WXCTF | 40% | 50% | 6% / 3% |
| TokenRhythm | 默认 55% | 65% | 8% / 4% |
| ~~OpenRouter/SenseNova/ChatAnywhere~~ | ~~45%~~ | ~~55%~~ | ~~已移除~~ |
| 其他 Free | 20% | 25% | 8% / 4% |

Jinshi 的 DeepSeek、GPT、Gemini、Claude 模型均通过 `apirelay-` 后的具体标识区分；TokenRhythm 使用 `tokenrhythmfree-` 标识。

### 21.2 摘要传输优化

- 折叠摘要请求不再携带完整 MCP 工具 schema；摘要是纯文本任务，去掉 tools 可显著减少请求体和等待时间。
- 摘要输入限制为最近约 32768 tokens；更早历史由摘要任务明确标记为省略，避免折叠请求本身再次发送超大历史。
- OpenRouter Free 折叠摘要使用较快的 Nemotron Free；其他通道优先沿用当前模型路由，保持 provider/key 一致。
- Jinshi/OrangeCC/WXCTF/ OpenRouter 的上下文容量均已加入 1M 表，避免错误回退到 131K。

### 21.3 注意事项

- `cacheHitTokens` 与上游缓存策略有关；切换 provider 会改变前缀，长会话中途切站可能暂时降低命中率。
- 折叠不会删除整个会话，只将旧消息替换为摘要并保留近期尾部。
- 修改 chunk 后必须重启 Reasonix；优先用 `/compact` 验证折叠结果和上下文显示。

---

## 22. 回合开始前置折叠修复（2026-09-02）

### 22.1 现象

- 会话积累到 `550K / 1000K` 后，模型单次响应等待 1.5 分钟以上仍无结果。
- HTTP 诊断日志：`bodyMB=1.6` 的 chat/completions 请求发出后长时间无响应。
- 根因：**回合开始的前置折叠仍用固定 `TURN_START_FOLD_THRESHOLD = 0.75`**（75%），550K 未触发 → 直接把 55 万 token 历史（1.6MB）发给 Jinshi。

### 22.2 修复

`DFHI2MRB.js` 回合开始检查改为使用中转档位：

```js
if (turnStart.ratio > foldProfile(this.model).threshold) { /* 前置折叠 */ }
```

- Jinshi：50% 触发前置折叠（55% 曾因该会话恰在 550048/1M=55.0% 边界，估算偏差可能不触发 → 降 50% 留余量）；WXCTF：40%；OpenRouter/SenseNova/ChatAnywhere Free：45%；其他 Free：20%。
- 折叠后保留尾部约 8% + 摘要，请求体从 1.6MB 级别降到 KB 级。

### 22.3 超时策略结论

`QSKDP3OS.js` `_timeoutFor()`：估算超 `20 万` token 后按 `(est-200000)*0.6` 加时，上限 600s。**维持原样不收紧**——折叠前置后请求体变小，真正的慢源自于大 payload 排队；收紧超时会误杀长思考型模型的合法生成。

### 22.4 验证

`node D:\Temp\opencode\verify_fold_node.js` 全绿；`node --check` 两个 chunk 均通过。

### 22.5 注意

- 触发条件是"回合开始时估算 ratio > 档位阈值"；回合中途的 88% 强制摘要阈值（FORCE_SUMMARY_THRESHOLD=0.8）不变。
- 长会话在重启新代码后第一次发消息即会触发前置折叠并显示状态提示。

### 22.6 估算器丢 reasoning 导致前置折叠失效（2026-09-02 二次修复）

- 现象：重启后（代码已含 22.2 修复）首次消息仍发 `bodyMB=1.6` 全量历史。
- 根因：`estimateTurnStart()` 调 `estimateRequestTokens(messages, toolSpecs, true)`——`drop_thinking=true` 时 `tokensForMessage()` **跳过历史 assistant 消息的 `reasoning_content`**；而实发 payload（usage 550048）**包含 reasoning**。估算比实发低 15-25%（该会话估算 <50% 而实发 55%），撞不上折叠线。
- 修复：`DFHI2MRB.js` `estimateTurnStart()` 第三参 `true → false`，估算含 reasoning，接近真实 usage。
- 验证：日志内容 ~205K + reasoning ~77K（chars/3 粗估）≈ usage 550K 量级；改后估算 ratio 应 ≈55% > 50% 线，前置折叠必触发。
- 兜底：即使估算仍偏低，`decideAfterUsage` 用真实 usage（>50%）在回合后也会补折叠。
- 注意：`_timeoutFor()` 的 chars/3 估算与 usage 同量级，无此低估问题。

### 22.7 摘要生成 12s 超时导致折叠"触发但失败"（2026-09-02 三次修复）

- 现象：折叠状态出现，但 12 秒后日志出现 `bodyMB=1.6` 主请求（完整历史照发）。
- 证据（diag 日志）：`10:10:45.523 req 0.2MB`（折叠摘要，工具已剥离 ✅）→ 无 resp → 恰好 12s 后 `10:10:57.568 req 1.6MB`。
- 根因：`HISTORY_FOLD_SUMMARY_TIMEOUT_MS = 12e3`——折叠摘要走**同款思考型 deepseek flash**（reasoning 占输出、Jinshi 首 token 慢），12s 内无响应 → `foldCtrl.abort()` → `fold-timeout` → 摘要为空 → `fold()` 返回 noop → 主循环按原历史发送。
- 修复：`DFHI2MRB.js` `HISTORY_FOLD_SUMMARY_TIMEOUT_MS = 12e3 → 120e3`。
- 验证：语法 ✅ 单测 ✅。重启后折叠摘要有 120s 预算，写完后主请求体应降到 KB 级。

### 22.8 Jinshi 服务端极限实测与尾部预算下调（2026-09-02 四次修复）

**实测数据**（同 key 直连 new.jinshi.xin，stream=true）：

| 载荷 | 结果 |
|---|---|
| mini（百 token） | 首字节 5.4s，总 7s ✅ |
| ~80K tokens | 200，**首字节 97.7s**（可用但极慢） |
| ~100K tokens | **120s 无响应超时** |
| ~130K tokens（0.5MB） | 直接 `fetch failed` |
| 1.6MB（上午 09:04-09:07） | 曾 12-26s 返回（晚间拥堵前后差异） |

**结论**：
- 折叠链路已通（1191→292 消息），但 Jinshi 晚间 deepseek-v4-flash 对 >80K token 请求首字节 80-100s+，~100K+ 会掉入无响应/连接掐断区。
- reasonix 侧先前"错误 · fetch failed"正是连接层掐断大请求的表现，不是代码 bug。

**代码调整**：
- Jinshi 档位尾部保留 `tail: 0.08 → 0.04`（40K token）、`aggressiveTail: 0.04 → 0.02` → 折叠后请求 ≈0.2MB 以内，避开失败区。
- 回合开始前置折叠调用点补传 `keepRecentTokens = ctxMax * profile.tail`（原来漏传会回落到全局 8%）。
- 验证：语法 ✅ 单测 ✅。

**后续策略**：
- 需要大量请求时考虑切快通道（wxctf mimo-v2.5-pro ~2.9s / minimax-m3 ~3.3s；sensenova 1.2-2.5s）。
- 折叠摘要/主请求体积都控制在 40K token 量级时，Jinshi 晚间可用性显著上升。

### 22.9 流式空闲超时修复（2026-09-02 五次修复）

- 现象：折叠后主请求开始推理并**流式输出**（"当前状态：1…2…"），文档没写完在 180s 整被掐断：`SSE body read failed: DeepSeek stream timed out after 180000ms`。
- 根因：`DeepSeekClient.stream()` 用**墙钟总超时**（`setTimeout` 一次，180s 默认），不管流是否活跃；慢通道长生成必被杀。
- 修复（`QSKDP3OS.js`）：
  1. `stream()` 超时改为**空闲续期**：每收到一字节 `clearTimeout+armTimeout`，只有静默超过 `toMs` 才 abort（错误信息标注 `idle`）。
  2. `_timeoutFor()` 公式：`extra = max(0, est-200000)*0.6` → `max(0, est-30000)*2.0`，上限 `600s → 900s`（idle 窗口随体量增长，思考型模型长推理不再被误杀）。
- 效果：130K est → idle 窗口 243s；550K → 621s；流在输出就不会被误杀。
- 注意：长静默（TTFB>180s）仍会断，属合理——当前 Jinshi 实测首字节最高 ~98s。

---

## 23. Jinshi deepseek 通道故障与 glm-5.3-flash 接线（2026-09-02）

### 23.1 deepseek 503 排查结论

- 直连与 7890 代理同结果：`/models` 200、chat 503 `Service temporarily unavailable`；新老 key、官方示例 payload 全部 503。
- **key 有效**（鉴权/列模型正常），纯服务端通道故障；DeepSeek 组倍率 x0.05 仍是最省，恢复前不指望。

### 23.2 分组 key 实测（Jinshi new.jinshi.xin）

| 分组 | key 来源 | 可用模型（实测耗时） |
|---|---|---|
| Gemini x0.35 | config `apirelayBackup.gemini`（旧 key 有效） | gemini-3-flash 2.7s / gemini-2.5-flash 3.4s / gemini-2.5-pro 5.9s |
| Claude | config `apirelayBackup.claude`（旧 key 有效） | claude-sonnet-5 4.3s / claude-opus-5 4.2s / claude-opus-4-7 8.3s / opus-4-8 10.4s / haiku-4-5 5.2s / opus-4-6 11.3s / sonnet-4-6 4.5s（fable-5 无通道） |
| GPT（新 key，绑定成功） | gpt-5.6-terra 7.8s / gpt-5.6-sol 10.5s / gpt-5.5 16s / gpt-5.4 16.6s / codex-auto-review 23.9s |
| GLM（新 key，绑定成功） | glm-5.3-flash 36.1s / glm-5.3 5.4s / glm-5.2 11.6s |
| 未绑定 key 症状 | 403 `无权访问 Discount 分组`（key 没授权分组）或 503 `model_not_found under group X`（key 所属分组无该模型通道）——**建 key 必须在控制台选分组** |

### 23.3 glm-5.3-flash 上下文实测

- 直测：252,025 prompt tokens → 200；更大体量读超时（慢而非拒）。
- OpenRouter 元数据实锤：`z-ai/glm-5.3-flash` ctx = **1,310,720**，max output 131,072（glm-5.3 = 1,310,720 / 262,144）。
- 上下文表补：`apirelay-jinshi-glm-5.2/5.3/5.3-flash` = 1e6（此前 Jinshi glm 走 131K 兜底，折叠数学错误）。

### 23.4 接线完成清单

- config `apirelayBackup.jinshiGlm`：apiKey→新 glm key；models→`['glm-5.2','glm-5.3','glm-5.3-flash']`（**切换时实时读 config，新 key 免重启生效**）。
- `V4AXMN4X.js` 上下文表 +3 glm 条目；`4EHRIP5U.js` 命令补全 +`Jinshi/glm-5.3-flash`。
- 路由链核对：`/apirelay Jinshi/glm-5.3-flash` → `apirelay-jinshi-glm-5.3-flash` → loadEndpoint jinshiGlm → buildPayload 通用 `jinshi-` 剥除 → 上行 `glm-5.3-flash` ✅。
- 代码类修改（ctx 表/命令列表）需重启 reasonix 生效。

### 23.5 推荐替代（deepseek 恢复前）

- 量产省钱：gemini-3-flash（$0.175×0.35，2.7s）> gemini-2.5-flash（最低）。
- 能力均衡：glm-5.3-flash（编程≈Opus 4.8、原生视觉、1.25M ctx、$0.24×0.6）——稍慢。
- 重度：claude-sonnet-5（$0.8×0.55，4.3s）日常 / claude-opus-4-8（$2×0.65）重活；gpt-5.6-terra（gpt key 已通，7.8s）。
- 安全建议：多把 key 曾明文贴会话，注意站点轮换/监控额度。

### 23.6 稳定化焊死（2026-09-03）

- 脆弱点：折叠后尾部分布 292 条 assistant reasoning_content，每轮请求恒定 ~0.4MB（~12 万 token），在大分组慢时易卡（gpt-5.4-mini max 续写大文档 2 分钟+无首字节）。
- 修复 1：`DFHI2MRB.js` fold() 生成 replacement 前对 tail 逐条 `delete reasoning_content/reasoningContent`——折叠后稳态请求从 0.4MB 降到 ~0.1MB 级；代价是尾部缓存前缀变化一次（后续再缓存）。
- 修复 2：config `reasoningEffort: max → high`（mini 提速明显、烧额下降；terra/sol 仍够强）。
- 备胎确认（2026-09-03 实测）：wxctf mimo-v2.5-pro 4.8s / minimax-m3 3.4s，独立站点与 Jinshi 无共因故障；sensenova 免费 1.2-2.5s。
- 日常分工：批量→sensenova（免费）；快→wxctf mimo/minimax；Jinshi gpt 组（mini 跑量/terra 重活）次选。
- 判断请求是否还活着：reasonix 空闲超时（idle，已加载）；日志无 resp 但 UI 无报错 = 服务端仍在吐 thinking 字节，属正常慢而非卡死。

---

## 24. Jinshi 今晚梭哈选型（2026-09-03）

### 24.1 当晚实测（19:00-20:00 窗口）

| 分组/模型 | 小请求实测 | 健康度 |
|---|---|---|
| gemini-3-flash | 2.8s | ✅ 全程稳 |
| gpt-5.4-mini | 2.9s | ✅ 稳 |
| gpt-5.6-terra | 3.0s | ✅ 稳（0.4MB 级曾 88s，属重负载非故障） |
| glm-5.3-flash | 5.2s | ⚠️ **0.2MB+ 反复 180s idle 超时**，小请求正常 |
| deepseek 组 | 503 | ❌ 未恢复 |

### 24.2 选型结论

- 主力：`gpt-5.6-terra`（3.0s + 智力≈5.5 + 1.05M ctx；分组倍率低）
- 攻坚：`gpt-5.6-sol`（T0，ALE 53.6；费额度，关键步骤才切）
- 批量编排：`gemini-3-flash`（2.8s、×0.35、1M ctx）
- glm-flash 只用 <0.2MB 小请求；0.2MB+ 切 gemini/gpt 组
- 纪律：单轮保持 ≤5万 token、缓存续跑；大任务 /new 拆开

### 24.3 通道状态速查（09-03 夜）

- Jinshi ¥12：gpt/gemini 组健康；glm 组部分时段抽风；deepseek 组 503。
- wxctf ≈¥8 余：mimo 4.8s / minimax 3.4s（某刻探针超时过一次，总体稳）。
- sensenova（商汤）：**免费公测已实锤**——通用池 60,000 积分/5h + 600,000/周（9/11 重置），Flash-Lite 专属池同额；key 即 config `sensenovaFree.apiKey`；deepseek-v4-flash 2.5-2.8s 真生成（max_tokens 需 >reasoning 预算），6.8-flash-lite 1.2-1.4s。**爆发兜底首选**。
- 空闲超时参数（QSKDP3OS）：估算含 reasoning；默认 240s 起、上限 900s；流式有字节即续期。

---

## 25. TokenRhythm（基元律动）接入 opencode（2026-09-03）

### 25.1 站点与验证

- TokenRhythm = 基元律动（对标 OpenRouter，融资千万美元），API 站 `https://tokenrhythm.studio/v1`（new-api 类，`/v1/` 走 API key，`/api/` 用户中心走 `tr_session` cookie）。
- 实测（91kami ¥19 卡 key，sk_tr_*）：`/models` 200（20 模型）；deepseek-v4-flash-0731 6.0s、glm-5.3-flash 5.2s 真回复。
- **无 vision 模型**；`deepseek-v4-flash-vision-exp` 属 wecoding（api.wecoding.xyz，需另充值，403 INSUFFICIENT_BALANCE）。

### 25.2 定价（CNY/1M，直标无倍率）

| 模型 | input | output | cache_read | ctx |
|---|---:|---:|---:|---:|
| glm-5.3-flash | ¥0.8 | ¥2.8 | ¥0.23 | 1M |
| qwen3.7-flash | ¥1.2 | ¥4.8 | ¥0.24 | 1M |
| deepseek-v4-flash-0731 | ¥3 | ¥9 | ¥0.1 | 1M |
| glm-5.3 | ¥8 | ¥28 | ¥2 | 1M |
| mimo-v2.5-pro | ¥3 | ¥6 | — | 256K |
| longcat-2.0 | ¥5 | ¥20 | ¥0.1 | 1M |

### 25.3 opencode 接线

- `C:\Users\20751\.config\opencode\opencode.jsonc` 新增 provider `tokenrhythm`（baseURL tokenrhythm.studio/v1，key=sk_tr_M89…wIlo，9 个模型：glm-5.3-flash/glm-5.3/deepseek-v4-flash-0731/deepseek-v4-pro-0813/qwen3.8-max/qwen3.8-27b/qwen3.7-flash/kimi-k2.6/DeepSeek-pro）。JSON 校验 ✅。
- 用法：opencode `/models` 选 `tokenrhythm/qwen3.8-max` 等。
- ¥669.97 余额（10-05 到期）预算建议：主力 glm-5.3-flash（0.8/M）；重活 deepseek-pro/qwen-max 系。
- 注意：sk_tr 为 91kami 卡 key；长期需在 tokenrhythm.studio 控制台用 `tr_session` cookie 登录后建自己账号的 API key 替换。

---

## 26. TokenRhythm 完整接线 + reasonix 通道（2026-09-03）

### 26.1 账号状态（2026-09-05 修正）

- 账号：7q912y / 132\*\*\*\*3349；cookie 名 **`tr_session`**（非 sess）；用户中心走 `/api/*`（auth/me、usage-summary、api-keys、models 等，POST 需 CSRF → 用浏览器 UI 建 key）。
- **余额：可用 ¥669.97（全部已解锁，10-05 到期）**——91kami 卡密实际到账 ¥669.97，非表面 ¥68。
- 额度明细：OpenSquilla 首次调用奖励 ¥58 + 9 笔邀请奖励各 ¥68 = ¥669.97（邀请奖励共 ¥612）。
- Key：默认 `sk_tr_dt…`（卡内）+ 新建 `opencode-qwen`=`sk_tr_M89…wIlo`（opencode 已换用此 key）。
- 控制台地址：https://tokenrhythm.studio/account（我的账户 / Token 用量 / API Key）。

### 26.2 reasonix 路由（/apirelay 统一命令，已移除 /tokenrhythmfree 单命令）

路由统一由 `O7VQOZQR.js` apirelay handler 处理（`tokenrhythmfree-` 前缀 → `tokenrhythmFree` 上游）：
1. config 顶层 `tokenrhythmFree: {baseUrl: https://tokenrhythm.studio/v1, apiKey: sk_tr_M89…wIlo}`。
2. `4EHRIP5U` SLASH_COMMANDS argCompleter 加 8 个 tokenrhythmfree 模型。
3. `GCNBIWK7` loadEndpoint：`tokenrhythmfree-` 前缀 → tokenrhythmFree。
4. `O7VQOZQR` apirelay handler：`providerMatch` 正则加 `|tokenrhythmfree`；`mdl` 构造加 `tokenrhythmfree-` 分支；`grp` 解析加 `tokenrhythmFree`；endpoint 回退 `cfg.apirelayBackup?.[grp] || cfg[grp]`（因 tokenrhythmFree 在顶层不在 apirelayBackup）。
5. `QSKDP3OS` buildPayload：剥 `tokenrhythmfree-` 前缀。
6. `V4AXMN4X` 上下文表 9 条，与平台 `/models` context_length 100% 对齐。

**用法**：
```
/apirelay tokenrhythmfree/qwen3.8-max     # 斜杠写法（推荐）
/apirelay tokenrhythmfree-qwen3.8-max     # 连字符写法也行
/apirelay Jinshi/glm-5.3-flash            # 金 石 照常
/apirelay wxctf/deepseek-v4-flash         # wxctf 照常
```

### 26.3 模型上下文（平台元数据 = 表值）

| 模型 | ctx | 模型 | ctx |
|---|---|---|---|
| qwen3.8-max / 27b | 1M | deepseek-v4-flash-0731 | 1M |
| qwen3.7-flash | 1M | deepseek-v4-pro-0813 | 1M |
| glm-5.3(-flash) | ~1.05M | kimi-k2.6 / mimo-v2.5-pro | **256K** |

### 26.4 价格与 30 天 ¥669.97 预算

- 余额：¥669.97（全部已解锁，10-05 到期）。
- 预算：¥669.97/30 天 ≈ **¥22.3/天**。
- 组合建议：量跑 `glm-5.3-flash`（¥0.8/2.8）或 `qwen3.7-flash`（¥1.2/4.8）；攻坚 `deepseek-v4-pro-0813`（¥9/27）；旗舰 `qwen3.8-max`（¥12/36）。
- 全 max 每天约 1.9M input token（¥22.3/¥12/M）；混省钱模型可到 5-8M/天。
- 用 `/apirelay tokenrhythmfree/xxx` 系列 + 控制台 usage-summary 每日对账。

### 26.5 注意

- cookie/key 曾明文贴会话：用完即弃或轮换；csrft 挡脚本建 key，浏览器 UI 操作。
- 该站无 GPT/Claude/Gemini/视觉模型；OpenAI/Anthropic/Responses 多协议但以 /v1 chat 为准。
- 修改后需重启 reasonix 生效；`/apirelay tokenrhythmfree/qwen3.8-max` 即可切。

---

## 27. 爱加密实战逆向（2026-09-05）

### 27.1 样本与来源

- 样本：琉璃云 `com.liuli.yun`（爱加密商业加固，Java + Native 两层反调试）
- 来源：看雪 abucs《[原创]记一次爱加密反调试分析及绕过思路》（2020-05-19）
- 平台：Android 7.1.1（AArch32），IDA + Frida + jdb

### 27.2 核心结论

| 层级 | 手段 | 绕过 |
|---|---|---|
| Java 层 | inline hook libart.so（GoActive 等），非 vtable 覆盖 | 还原原始字节码（`memcpy` 写回） |
| Native 层 | ptrace + `/proc/%d/status` TracerPid 检测 | 改内核 ptrace 标志位 或 Frida hook open/read |

### 27.3 关键证据

- Java 层：vtable 中 ProcessIncoming 与 ShutDown 地址不一致 → 排除 vtable 覆盖；dump libart.so 内存发现 RWXP 异常区 → 定位 inline hook
- Native 层：`_dl__ZN6soinfo13call_functionEPKcPFvvE` 下断点 40 次后挂掉 → 回溯定位 libexec.so 内检测函数；快速法：直接在 `_exit` 下断点回溯

### 27.4 分析报告

- 写入 `C:\Users\20751\Desktop\爱加密\爱加密反调试分析报告.md`

---

## 28. android 逆向 + 股票静态逆向（2026-09-05）

### 28.1 活跃项目总览

| 会话 | 上游 | 进度 |
|---|---|---|
| `android逆向` | tokenrhythmfree/qwen3.8-max | 银行 App：isDeviceRooted mock 成功，进入权限页 |
| `测试项目搜索` | 多模型轮转 | SRC 漏洞盒子资产合并 + 高赏金目标筛选 |
| 股票静态逆向 | 本地分析 | 同华顺（开盘啦）APK 全量解包 + 网络审计 |

### 28.2 股票静态逆向成果（`D:\Users\20751\Desktop\股票\静态逆向\`）

**探针**：`probe_footprint.py`（frida-nosuke 注入后 `/proc/<pid>/{maps,status,task/*/comm,fd}` 足迹测量，零风险靶子 app.lawnchair）

**解包输出**：jadx + apktool 双路，覆盖 classes2-7.dex

| 报告 | 规模 | 关键发现 |
|---|---|---|
| `api_report.txt` | 179,845 字符串，146 URL | 行情/语音/热修复/客服接口；讯飞(openspeech.cn)、微博、魅族、百度 hmma |
| `socket_scan_report.txt` | 2,183 行 | `SocketPresenter/SocketViewModel` 长连接、`ChallengeVM` 挑战握手、`GameClient` 游戏协议、`ConnectInfo` 连接池 |
| `hosts_report.txt` | 216,484 字符串，105 IP | 行情/资讯/广告/热修复/客服等主机 |

**关键类**：`Lcom/yzj/kaipanh/newindex/common/socket/*`、`Hq$KlineMainResp`（K 线响应）、`ServiceHqDaban`（大盘服务）

### 28.3 android 逆向实战（2026-09-05 会话实录）

- 银行 App：Frida hook `isDeviceRooted` 返回 false → 绕过根检测 → 成功进入权限申请页
- 技巧：android 逆向使用 `tokenrhythmfree/qwen3.8-max`（agentic 任务强），保持长会话利用 prefix 缓存

---

## 27. 爱加密实战逆向（2026-09-05）

### 27.1 样本与来源

- 样本：琉璃云 `com.liuli.yun`（爱加密商业加固，Java + Native 两层反调试）
- 来源：看雪 abucs《[原创]记一次爱加密反调试分析及绕过思路》（2020-05-19）
- 平台：Android 7.1.1（AArch32），IDA + Frida + jdb

### 27.2 核心结论

| 层级 | 手段 | 绕过 |
|---|---|---|
| Java 层 | inline hook libart.so（GoActive 等），非 vtable 覆盖 | 还原原始字节码（`memcpy` 写回） |
| Native 层 | ptrace + `/proc/%d/status` TracerPid 检测 | 改内核 ptrace 标志位 或 Frida hook open/read |

### 27.3 关键证据

- Java 层：vtable 中 ProcessIncoming 与 ShutDown 地址不一致 → 排除 vtable 覆盖；dump libart.so 内存发现 RWXP 异常区 → 定位 inline hook
- Native 层：`_dl__ZN6soinfo13call_functionEPKcPFvvE` 下断点 40 次后挂掉 → 回溯定位 libexec.so 内检测函数；快速法：直接在 `_exit` 下断点回溯

### 27.4 分析报告

- 写入 `C:\Users\20751\Desktop\爱加密\爱加密反调试分析报告.md`

### 26.6 apirelay 统一路由（替代 /tokenrhythmfree 单命令，2026-09-03 更新）

用户要求只用 `/apirelay` 一个命令切全部上游。修改 `O7VQOZQR.js` apirelay handler：
- `providerMatch` 正则加 `|tokenrhythmfree`，识别 `tokenrhythmfree/<model>` 斜杠写法；
- `mdl` 构造：provider=`tokenrhythmfree` → `tokenrhythmfree-<model>`；
- `grp` 解析：前缀 `tokenrhythmfree-` → `grp="tokenrhythmFree"` 读 `cfg.tokenrhythmFree` 上游；
- `bare` 同时 strip `tokenrhythmfree-` 前缀防止 modelKeys 查不到。

`/apirelay` 补全列表加 8 个 tokenrhythmfree 模型。

**用法**：
```
/apirelay tokenrhythmfree/qwen3.8-max     # 斜杠写法（推荐）
/apirelay tokenrhythmfree-qwen3.8-max     # 连字符写法也行
/apirelay wxctf/deepseek-v4-flash         # wxctf 用斜杠
/apirelay Jinshi/glm-5.3-flash            # 金 石 照常
```

**注意**：wxctf 只支持斜杠写法（`wxctf/model`），因为 `isModel` 把 `wxctf-` 连字符也识别为模型前缀但 `providerMatch` 只处理斜杠。建议统一用 `/apirelay 站点/模型` 格式。

---

## 28. wxctf reasoning_effort 白名单与 xhigh 档位（2026-09-08）

### 28.1 问题现象

- reasonix 配置 `reasoningEffort: max`，走 wxctf 中转时返回 400：
  ```
  field ReasoningEffort invalid, should be one of: low, medium, high, xhigh, none
  ```
- 走 Jinshi 等宽松中转时 `max` 可用（不校验枚举）。

### 28.2 根因

DeepSeek 官方 API schema 的 `reasoning_effort` 枚举为 `low|medium|high|xhigh|none`，**没有 `max`**。
- wxctf 严格按官方 schema 校验 → `max` 被拒
- Jinshi 等中转不校验、照单全收 → `max` 能用

`max` 是 reasonix 早期通过补丁（`effortChoicesForBaseUrl` 无条件返回 `ALL`）加入的，并非官方值。

### 28.3 实测验证（wxctf deepseek-v4-flash）

| effort | 状态 |
|---|---|
| low | ✅ 200 |
| medium | ✅ 200 |
| high | ✅ 200 |
| xhigh | ✅ 200 |
| none | ✅ 200 |
| max | ❌ 400 |

### 28.4 修复

config `reasoningEffort: max → xhigh`（wxctf 支持的最高档），重启生效。

```json
// ~/.reasonix/config.json
{ "reasoningEffort": "xhigh" }
```

**注意**：xhigh 是 wxctf 能用的最高思考强度，输出 reasoning_content 比 high 更长。长回答（>1000 token）在 MCP 层可能触发 130s 超时，需拆题或限制 max_tokens。

### 28.5 各中转 reasoning_effort 支持对比

| 中转 | 支持的 effort | 备注 |
|---|---|---|
| Jinshi | low/medium/high/max | 宽松，不校验 |
| wxctf | low/medium/high/xhigh/none | 严格按官方 schema |
| TokenRhythm | 待测 | — |
| OrangeCC | 待测 | — |

---

## 29. Jinshi GLM-5.3-Flash + Gemini-3.8-Flash 接入 opencode（2026-09-08）

### 29.1 新增模型

opencode 配置 `C:\Users\20751\.config\opencode\opencode.jsonc`：

| Provider | 模型 | 显示名 |
|---|---|---|
| jinshi-glm | glm-5.3-flash | GLM 5.3 Flash · Jinshi |
| jinshi-gemini | gemini-3.8-flash | Gemini 3.8 Flash · Jinshi |

### 29.2 验证

- GLM-5.3-Flash：Jinshi 中转正常，provider=antgroup，18+11 tokens 返回
- Gemini-3.8-Flash：v1beta generateContent 接口正常，思考型（145 thoughts tokens）

---

## 30. Jinshi key 分组漂移事故（2026-09-08）

### 30.1 现象

reasonix 配 `apirelay-wxctf-deepseek-v4-flash`，实际报错：
```
503 No available channel for model deepseek-v4-flash under group glm
```

### 30.2 根因

`apirelayBackup.wxctf.apiKey`（`sk-yvq5chhDuMfQgkAu75XbiLfF49UXGLAOBs8dVKA2rdL1r81b`）在 Jinshi 后台被从 DeepSeek 组挪到了 **GLM 组**。reasonix 路由逻辑正确，但 key 的后台分组变了。

### 30.3 结论

- reasonix 分组管理（`loadEndpoint` 按前缀选 key）**没问题**
- 是 **Jinshi 后台 key 分组绑定漂移**，需去 Jinshi 控制台重新绑定
- 分组管理已做好（§1 关键文件路由），但 key 分组由中转站控制，reasonix 无法感知

---

## 31. 缓存节省统计（2026-09-08）

99 次请求实测（wxctf deepseek-v4-flash，上下文 ~300K）：

| 指标 | 数值 |
|---|---|
| 缓存命中 | 73.7%（73/99） |
| 实际花费 | ¥3.30 |
| 无缓存花费 | ¥9.79 |
| **节省** | **¥6.64（67.9%）** |

结论：长会话前缀缓存是命脉，省 67.9%。

---

## 32. xhigh 魔改 + 按站点过滤 effort（2026-09-09）

### 32.1 问题

wxctf deepseek 不接受 `max`，只认 `low|medium|high|xhigh|none`。之前 `effortChoicesForBaseUrl` 被改成无条件返回 `ALL`（含 max），wxctf 400。

### 32.2 修改

魔改 3 个 chunk（已验证语法通过）：

**`chunk-O7VQOZQR.js`**：
```js
var ALL = ["low", "medium", "high", "xhigh", "max"];
function effortChoicesForBaseUrl(baseUrl) {
  if (baseUrl && baseUrl.includes("wxctf"))
    return ["low", "medium", "high", "xhigh", "none"];
  return ALL;
}
```

**`chunk-4EHRIP5U.js`**：effort 命令 `argCompleter` + `argsHint` 加 xhigh。

**`chunk-U7G72DHQ.js`**：10 处 i18n 提示 `low|medium|high|max` → `low|medium|high|xhigh|max`。

### 32.3 各中转 effort 实测表

| 中转 | 接受的 effort | 备注 |
|---|---|---|
| Jinshi | low/medium/high/max | 宽松 |
| wxctf | low/medium/high/xhigh/none | 严格官方 schema |
| TokenRhythm | 待测 | — |
| OrangeCC | 待测 | — |

### 32.4 config

`reasoningEffort: max → xhigh`，重启生效。用户界面 `/effort xhigh` 可用。

---

## 33. 鼠标选中色修复（2026-09-09）

### 33.1 问题

Reasonix TUI 中鼠标选中文本后变成橙色。原因：
- `messageBg.selected` 在代码中仅 import 但未被 UI 使用（死代码）
- `setSelectionBgColor` 方法存在但无人调用
- 选中色实际由 **Windows Terminal** 的 `selectionBackground` 控制

### 33.2 修复

修改 `C:\Users\20751\AppData\Local\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json`：

1. 在 `profiles.defaults` 中 `"selectionBackground": "#000000" → "#1a3a4a"`（暗蓝）
2. 在 `schemes` 中添加 Dimidium 配色方案，内含 `"selectionBackground": "#1a3a4a"`
3. 备份原文件 → `settings.json.bak-*`

**注意**：profile defaults 的 selectionBackground 优先级高于 scheme 的同名设置。

### 33.3 配置颜色方案

- 选中色：`#1a3a4a`（暗蓝，在黑背景下清晰可见）
- 备选：`#2a3035`（冷灰）、`#2a2035`（淡紫灰）
- 原理：黑底上用深蓝/深灰区分，不抢视觉焦点

### 33.4 验证

重启 Windows Terminal 或新开标签页，鼠标划选文字应显示暗蓝色高亮，不再显示橙色。

---

## 34. ImmutablePrefix 工具排序：前缀缓存稳定性修复（2026-09-13）

### 34.1 问题

`ImmutablePrefix.tools()` 和 `computeFingerprint()` 中的工具数组按 `addTool` 注册顺序存放。MCP 工具的注册顺序跨 session 可能不同（文件扫描顺序、异步加载时序） → JSON.stringify 产生不同序列化 → 发送给 API 的 tools 字段 token 序列不同 → DeepSeek/GLM 的 KV prefix cache 键不同 → **cache miss**。

### 34.2 修复

**修改位置**（4 处，2 文件）：

`dist/index.js`：
```js
// BEFORE
tools() { return this._toolSpecs.map((t2) => structuredClone(t2)); }
// AFTER
tools() {
  const cloned = this._toolSpecs.map((t2) => structuredClone(t2));
  cloned.sort((a, b) => (a.function?.name ?? "").localeCompare(b.function?.name ?? ""));
  return cloned;
}
```

`computeFingerprint()` 中的 `tools: this._toolSpecs` 改为 `tools: [...this._toolSpecs].sort(...)` 保持一致。

`dist/cli/chunk-DFHI2MRB.js` 同位置相同修改。

`node --check` 全部通过。

### 34.3 影响

- 消除跨 session 工具顺序抖动 → 同一项目/REASONIX.md 重新加载时避免不必要的 cache miss
- computeFingerprint 指纹稳定 → debug/verify 正确反映实际发送内容
- 不影响 `_turnSelfCorrected` / `addTool`/`removeTool` 的 cache 失效机制（注册变更仍触发 `_fingerprintCache = null`）

### 34.4 其他分析（不改）

| 潜在缓存杀手 | 评估 |
|---|---|
| `contextMessage` 注入 | 仅 `/undo` 触发，不干扰主循环 |
| `healActiveLogBeforeSend` healing | 幂等（同输入→同输出） |
| `stripDroppableReasoningContent` | 每次同条件、稳定 |
| system prompt（memory 注入） | REASONIX.md 只读、跨 turn 稳定 |
| `temperature`/`reasoning_effort` | 全局固定 |

---

## 35. Credits 站（credits.wxctf.com）接入与缓存实测（2026-09-13）

### 35.1 站点信息

| 项目 | 旧站 ai.wxctf.com | 新站 credits.wxctf.com |
|---|---|---|
| 余额 | ¥16.01（充后） | 1,375.25 Credits（≈¥1.72） |
| 折扣 | 0.26x | 0.24x |
| 汇率 | — | 800 Credits = ¥1 |
| 缓存上报 | ❌ 不报 cached_tokens | ✅ 完整报 cached_tokens |
| key | sk-GS6GjTXZ4zIvAp7VoIIY3ZRXQyPLMHNbHGLhjHkJLW3RyYM2 | sk-2Yr8jy5ch608u1GoPV4Ri9peEx9QTAVkByA2pJ4mk9otPPR2 |

### 35.2 模型定价（Credits/1M，折扣后）

| 模型 | 输入 | 输出 | 缓存 |
|---|---|---|---|
| deepseek-v4-flash | 480 | 960 | 24 |
| deepseek-v4-flash-0731 | 720 | 2,160 | 30 |
| deepseek-v4-pro | 1,440 | 2,880 | 180 |
| deepseek-v4-pro-0813 | 2,160 | 6,480 | 90 |
| deepseek-v4.1-flash | 240 | 960 | 6 |
| glm-5.2 | 1,920 | 6,720 | 600 |
| glm-5.3 | 1,920 | 6,720 | 600 |
| glm-5.3-flash | 192 | 672 | 69 |
| kimi-k3 | 4,800 | 24,000 | 600 |

共 9 模型，均 owned_by `custom`（除 v4.1-flash 为 `openai`）。

### 35.3 缓存命中实测

脚本 `D:\Temp\opencode\verify_cache.mjs`，8 次连续相同长前缀（~4,477 chars ≈ 825–834 prompt tokens），`temperature=0`, `max_tokens=100`：

| 站 | 模型 | 命中 runs | 命中率 | 输入省钱率 | 约省 ¥ |
|---|---|---|---|---|---|
| **旧站** | glm-5.3-flash | **0/8 (0%)** | 0% | 0% | ¥0 |
| **新站** | glm-5.3-flash | **6/8 (75%)** | 74.8% | **47.9%** | ¥0.0008 |
| **新站** | deepseek-v4-flash | **7/8 (87.5%)** | 81.5% | **77.4%** | ¥0.0031 |

dsf 首次过后连续 7 次全命中（768/825 = 93.1% per call），输入费从 480/1M 降到 24/1M（省 95%/hit）。

### 35.4 余额够用多久（纯零买不购订阅）

| 场景 | 日均 turns | 总需 7 天 | 够吗 |
|---|---|---|---|
| 偏轻（glf 为主+新站缓存） | 250 | 1,750 | ✅ **充足** |
| 平均（9 月日常 338/day） | 338 | 2,366 | ✅ 刚好，约余 500 |
| 重（android 逆向爆发） | 500 | 3,500 | ❌ 差约 1,200 |

**推荐策略**：旧站 ¥16.01 先用（~953 次），日常查询走旧站 glf（¥0.24/M 最便宜）；agent 循环走新站 dsf 薅 77% 缓存；两站合计约 2,228 次，加缓存加成等效 7~8 天。用完了才考虑订阅。

### 35.5 订阅参考

| 站 | 套餐 | 价格 | 获得 | 日均成本 |
|---|---|---|---|---|
| 新站 | 量大用不完 | $888/月 | 128,888 Cr（VIP倍率） | ≈¥5.4 |
| 新站 | 量大管饱 | $168/7d | 每日 3,666 Cr（VIP） | ≈¥1.7 |
| 旧站 | 量大管饱 | $168/7d | ¥30 信用+VIP | ≈¥0.7 |
| 旧站 | 尝鲜体验 | $15/1d | ¥30 信用+VIP | ¥15 |
| 新站 | 尝鲜体验 | $15/1d | 2,888 Cr+VIP | ¥15 |

---

## 36. 验证脚本参考

`D:\Temp\opencode\verify_cache.mjs`：

```bash
# 新站 glf 缓存验证
BASE_URL=https://credits.wxctf.com API_KEY=sk-2Yr8... MODEL=glm-5.3-flash COUNT=8 node verify_cache.mjs

# 新站 dsf 缓存验证
BASE_URL=https://credits.wxctf.com API_KEY=sk-2Yr8... MODEL=deepseek-v4-flash COUNT=8 node verify_cache.mjs

# 旧站 glf 缓存验证（不报 cached_tokens，全 0 正常）
BASE_URL=https://ai.wxctf.com API_KEY=sk-GS6Gj... MODEL=glm-5.3-flash COUNT=6 node verify_cache.mjs
```

---

## 37. 沿线坑点备忘

| 坑 | 症状 | 对策 |
|---|---|---|
| 旧站 key 在 credits host 上测 | 403 insufficient_user_quota | key 绑定站点的，交换 host 自然 403 |
| credits.wxctf.com DNS | `ai-credits.wxctf.com` 解析 000 | 只认 `credits.wxctf.com` |
| 短 prompt 缓存全 0 | prompt < ~800 tokens 从不命中 | 只测长 system prompt（≥3,000 chars） |
| 缓存命中抖动 | 10~11 次间隙才命中一次 | 首次 cache 创建后后续稳定命中 |
| dsf max_tokens 过小 | 内容截断、output 被 thinking 吃完 | 设 ≥4,000 或 `xhigh` + 大预算 |
| reasoning_effort 枚举 | wxctf 不接受 `max` | 配 `xhigh`（wxctf 最高档） |

[---EOF---]51426
## 38. 大目录复制与中断问题（2026-09-15）

### 38.1 现象

使用 `copy_file` 复制 `C:\Users\20751\Desktop\完整` 时，工具长时间运行，Esc/Ctrl+C 无法立即中断。此前压缩任务退出码为 1，输出因结果过大被截断，无法直接看到完整根因。

### 38.2 结论

- `copy_file` 当前没有可靠地把取消信号传递到复制进程，因此大目录操作可能阻塞 TUI。
- `Compressing` 只是过程输出，必须检查最终 `exitCode` 和最后错误。
- 不要直接杀全部 `node.exe`，可能同时终止 reasonix、MCP 和其他 agent。
- 完整未封装源码与安装包分开保存；`C:\Users\20751\Desktop\完整` 不放安装器。

### 38.3 推荐处理

```powershell
Get-Process | Where-Object { $_.ProcessName -match 'robocopy|xcopy|cmd|node' }
taskkill /PID <目标PID> /T /F
```

大目录复制优先使用 `robocopy`，设置日志和排除项，排除 `target`、`build`、安装器、缓存及临时目录。重复任务前先确认旧任务已退出，避免同一复制请求叠加。

### 38.4 后续改进

`copy_file` MCP 工具应增加 AbortSignal、进度、超时和取消后的子进程树清理。reasonix 自身的 `run_command` 已将 Windows `taskkill` 从同步 `spawnSync` 改为异步 `execFile`，但该修复不会自动覆盖外部 MCP 的复制实现。

---

## 39. 当前缓存与上下文观察（2026-09-15）

### 39.1 缓存

旧站 deepseek-v4-flash 近期约 120K–140K prompt，通常 99% 以上命中缓存，单次约 ¥0.003–0.008；断缓存时约 ¥0.07–0.16。新站 v4.1-flash 在 330K–390K prompt 下，命中时通常约 2–5 Credits。

缓存断点常见于首次请求、上下文折叠后前缀改变、模型或工具定义改变、切换 agent、重新建立会话。`cached_tokens=0` 不能单独证明未缓存，需确认中转是否返回 `prompt_tokens_details.cached_tokens`。

### 39.2 上下文折叠

历史会话多次出现约 300K prompt 后回落到约 150K–190K，说明折叠策略正常，压缩后约保留原上下文的 56%–63%。Android/PC 逆向任务能够继续衔接，未发现明显失忆。

出现“上下文接近上限，正在压缩历史”属于正常保护行为。压缩过程中不要重复发送相同工具调用，也不要为了省钱盲目降低尾部保留量。

### 39.3 多 agent 与 MCP

同一账号同时运行两个 agent 会共享 TPM，并分别发送长上下文，可能触发 429。重型逆向任务建议同时只运行一个 agent；另一个暂停或只做轻量查询。相同 MCP 服务重复连接会增加初始化和工具定义开销，应避免重复启动。

---

## 40. 模型与余额策略

- 日常逆向默认使用 `deepseek-v4.1-flash`：输入价格更低、缓存价格更低、速度更快。
- 复杂疑难分析再切换 `deepseek-v4-flash` 或 pro，不要为普通文件读取使用高价模型。
- 同一任务保持同一会话，不要频繁 `/new`；长 system prompt 和工具定义稳定时缓存收益最大。
- 旧站余额适合继续使用，旧站日志已显示真实缓存命中；新站适合 v4.1-flash 的长上下文缓存场景。
- 余额估算必须按最近实际账单计算，不能只按请求次数估算；无缓存请求可能比缓存请求贵十几倍。

---

## 41. 已完成的 reasonix 修复清单

| 文件 | 修复 | 验证 |
|---|---|---|
| `dist/index.js` | 工具按函数名排序，稳定 ImmutablePrefix | `node --check` 通过 |
| `dist/cli/chunk-DFHI2MRB.js` | 同步工具排序逻辑 | `node --check` 通过 |
| `dist/index.js` | 支持 `prompt_tokens_details.cached_tokens` | `node --check` 通过 |
| `dist/cli/chunk-QSKDP3OS.js` | 支持标准缓存字段和流式 usage | `node --check` 通过 |
| `dist/index.js` | Windows 终止进程改为异步 `execFile` | `node --check` 通过 |
| `dist/cli/chunk-4V4TKQMB.js` | 同步异步终止逻辑 | `node --check` 通过 |

所有修改重启 reasonix 后才对新进程生效。验证缓存使用 `D:\Temp\opencode\verify_cache.mjs`，验证语法使用 `node --check`。

---

## 42. 当前操作规范

1. GUI、服务、监听器和 watcher 使用 `run_background`。
2. 一次性短命令使用 `run_command`，并设置合理 timeout。
3. 不使用 `run_command` 启动不会自行退出的 GUI 程序。
4. 大文件和大目录操作先排除构建产物，输出落盘后分段读取。
5. 遇到 429、空响应或超时，先查询任务状态和最后错误，再决定是否重试。
6. 禁止把 API key、dashboard token 和支付信息写入知识库。
7. 修改源码后执行 `node --check`、对应构建命令和最小运行验证。

---

## 43. Grok / codex.yx.lu 中转接入（2026-09-18）

### 43.1 站点信息

- 端点：`https://codex.yx.lu/v1`（Sub2API 网关，Cloudflare 前置）
- 模型：grok-4.6 / 4.6-latest / 4.5 / 4.5-latest / 4.3 / 4.20-reasoning / 4.20-non-reasoning / 3-mini / 3-mini-fast / build / composer 系
- **Cloudflare 1010 拦截**：不带浏览器 User-Agent 的请求全部 403 `error code: 1010`，必须带 UA 头。

### 43.2 reasonix 接线（4 文件）

| 文件 | 修改 |
|---|---|
| `config.json` | `apirelayBackup.codex` = { baseUrl, apiKey, 15 个模型 } |
| `chunk-GCNBIWK7.js` loadEndpoint | `apirelay-codex-` / `apirelay-grok-` / `apirelay-composer-` → codex 上游 |
| `chunk-QSKDP3OS.js` buildPayload | 剥前缀链加 `codex-`（apirelay- → codex- → 裸模型名） |
| `chunk-O7VQOZQR.js` apirelay handler | providerMatch 加 codex；mdl 构造加 `apirelay-codex-`；specialJinshi 加 codex；grp 兜底 grok-/composer- → codex；isModel 加 `codex/` |
| `chunk-4EHRIP5U.js` argCompleter | /apirelay 补全表加 17 个 codex 模型 |
| `index.js` DeepSeekClient | chat() 和 stream() 加浏览器 User-Agent 头（绕 Cloudflare 1010） |

### 43.3 用法

```text
/apirelay codex/grok-4.6        # 推荐（斜杠格式）
/apirelay grok-4.6              # 裸格式也行（loadEndpoint 已兜底）
```

链路：`apirelay-codex-grok-4.6` → loadEndpoint 路由到 codex.yx.lu → buildPayload 剥前缀 → 上游收 `grok-4.6` ✅

### 43.4 实测速度（13×37 简单题）

| 模型 | 耗时 | 成本 | 思考 |
|---|---|---|---|
| grok-4.5-latest | 1.1s | $0.0166 | 有 |
| grok-4.20-non-reasoning | 0.7s | $0.0107 | 无 |
| grok-4.5 | 3.5s | $0.0272 | 有 |
| grok-4.6 | 4.0s | $0.0529 | 有 |
| grok-4.3-latest | 1.7s | $0.0372 | 有 |

推荐 grok-4.5-latest（快+便宜+有思维链）；能力优先用 grok-4.6。缓存命中 512-640 tokens。

### 43.5 opencode 接线

`~/.config/opencode/opencode.jsonc` 新增 provider `grok`（@ai-sdk/openai-compatible + codex.yx.lu + UA 头），模型 grok-4.6 / 4.5 / build-0.1 / 4.20-multi-agent / 4.3 / composer-2.5-fast。JSON 校验通过。

### 43.6 验证

- 4 个 chunk + index.js 全部 `node --check` 通过
- 路由仿真脚本 `D:\Temp\opencode\verify_codex_routing.mjs` 4 用例全 PASS
- 真实请求 HTTP 200：13×37=481 ✓，带 reasoning_content，cache=640

### 43.8 专属上下文压缩配置（2026-09-18）

**问题**：grok 模型不在上下文表 → 回退默认 131K → 折叠在 98K（0.75×131K）就触发，过早压缩。

**修复 1（上下文表 chunk-V4AXMN4X.js）**：

```text
apirelay-codex-grok-4.6 / 4.6-latest        → 1e6
apirelay-codex-grok-4.5 / 4.5-latest        → 5e5
apirelay-codex-grok-4.3 / 4.3-latest        → 1e6
apirelay-codex-grok-4.20-reasoning          → 256e3
apirelay-codex-grok-4.20-non-reasoning      → 256e3
apirelay-codex-grok-build / build-0.1       → 256e3
apirelay-codex-grok-latest                  → 1e6
apirelay-codex-composer-2.5                 → 5e5
apirelay-codex-grok-composer-2.5-fast       → 5e5
（含 apirelay-grok-* 裸前缀变体共 20 条）
```

**修复 2（折叠档位 chunk-DFHI2MRB.js foldProfile）**：

```js
if (name.startsWith("apirelay-codex-") || name.startsWith("apirelay-grok-") || name.startsWith("apirelay-composer-")) {
  return { threshold: 0.5, aggressive: 0.6, tail: 0.06, aggressiveTail: 0.03 };
}
```

grok-4.6（1M 窗口）→ 500K 触发普通折叠，600K 激进折叠，折叠后保留 60K/30K 尾部。

**折叠摘要链路验证**：摘要模型用当前模型（grok 自己），`extra_body.thinking` 参数实测被 codex.yx.lu 接受（HTTP 200），折叠不会静默失败。

**效果**：正常逆向会话（200-400K）不会触发折叠；修复前 98K 就折叠导致上下文频繁丢失。

### 43.7 注意

- codex.yx.lu 走 Cloudflare，直连即可；本机 7890 梯子对国内站（wxctf）反而绕路，不要给国内中转配代理。
- 该 key 来源为第三方，随时可能失效；不要在关键任务上单点依赖。
- Grok 系无 `deepseek` 风格 `reasoning_effort` 参数，reasonix 的 `xhigh` 配置对其无害。

## 44. Smart Read 上下文防溢出守卫（2026-09-22）

### 44.1 背景

- 症状：读取多个大文件（7 个 .md / so 分析）→ 上下文冲到 382K → 上游返回 Empty response (0 delta chars, stream ended cleanly)
- 根因：工具结果注入无上限感知；折叠只在回合开始检查（TURN_START_FOLD_THRESHOLD=0.75），单回合内连续大文件读取可绕过检查

### 44.2 方案（appendAndPersist 守卫）

补丁文件：chunk-DFHI2MRB.js → appendAndPersist(message)

逻辑（仅对 role=tool 的消息生效）：
1. 估算当前日志 token 数（字符数 / 2.5）
2. 计算占用比例 ratio = current / ctxMax
3. ratio > 0.7 时：
   - 剩余空间 cap = max(400, min(8000, remaining × 0.08))
   - 结果超 cap → truncateForModelByTokens 截断 + 注入提示
   - 结果未超 → 原文 + 注入提示
4. 提示文案：[上下文已用 X% — 本次工具结果按剩余空间限制为约 N tokens；文件较大时请用 start/end 分段读取，或先 /clear 释放空间]

### 44.3 效果（全自动，无需手动干预）

- 30% 占用：不干预，原样通过
- 75% 占用：小结果加提示（自动提醒 AI），大结果截断到 ~8K
- 95% 占用：cap 收紧到 ~4K
- 98% 占用：cap 收紧到下限 400
- 非 tool 消息（assistant/user）不受影响；异常输入（null content）安全通过

### 44.4 验证

- node --check chunk-DFHI2MRB.js → 通过
- node D:\Temp\opencode\verify_smartread_guard.mjs → 15/15 通过

### 44.5 备份与恢复

- 备份：chunk-DFHI2MRB.js.smartread-bak-20260922_195535
- 恢复：Copy-Item chunk-DFHI2MRB.js.smartread-bak-20260922_195535 chunk-DFHI2MRB.js -Force
- 需重启 reasonix 生效

## 46. 官方 DeepSeek 模型版本 + 高峰/空闲定价（2026-09-25）

### 46.1 官方模型名 ↔ 实际版本（关键！）

| 官方模型名 | 实际模型版本 | 备注 |
|---|---|---|
| `deepseek-flash` | **DeepSeek-V4.1-Flash** | 即"4.1f" |
| `deepseek-v4-pro` | DeepSeek-V4-Pro-0813 | |

- **旧名 `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 仍可调用**，由 V4.1-Flash 服务，按 Flash 价计费
- 别名 `deepseek-chat` / `deepseek-reasoner` 也 → deepseek-flash
- 官方只认 2 个基名：`deepseek-flash`、`deepseek-v4-pro`；其余名字一律 400
- 上下文 1M，输出最大 384K

**结论：要"4.1f"就用官方 `deepseek-flash`，不用中转。**

### 46.2 官方定价（元/百万 tokens，2026-09 官方文档）

| 模型 | 缓存命中 | 缓存未命中 | 输出 |
|---|---|---|---|
| deepseek-flash 空闲 | 0.02 | 1 | 4 |
| deepseek-flash 高峰 | 0.04 | 2 | 8 |
| deepseek-v4-pro 空闲 | 0.15 | 4.5 | 13.5 |
| deepseek-v4-pro 高峰 | 0.30 | 9.0 | 27.0 |

- **空闲 = 高峰的一半**
- **高峰时段**：北京时间周一至周五（不含法定节假日）9:00-12:00、14:00-18:00
- **空闲时段**：其余时间 + 周末 + 法定节假日全天
- 并发限制：flash 2500 / pro 500

**省钱要点：晚上/周末跑长任务 = 半价。**

### 46.3 中转现状（2026-09-25）

- wxctf 老站 key `sk-qTgwm...` 余额 **-¥24.598766（负数）** → 所有模型 403 用户额度不足
- 中转独有模型（官方没有）：`deepseek-v4.1-flash`、`deepseek-v4-pro-0813`、`deepseek-v4-flash-0731`
- 要用这些必须给中转充值

### 46.4 官方可行模型（实测）

```
deepseek-flash       200 OK（= V4.1-Flash）
deepseek-v4-flash    200 OK（别名→flash）
deepseek-chat        200 OK（别名→flash）
deepseek-reasoner    200 OK（别名→flash）
deepseek-v4-pro      200 OK
deepseek-v4.1-flash  400（官方无此名）
deepseek-v4-pro-0813 400（官方无此名）
```

## 47. 官方 key 配置机制 + 模型名路由（2026-09-25）

### 47.1 官方通道解析顺序（关键！）

1. env `DEEPSEEK_BASE_URL`（若设）→ 用它 + env `DEEPSEEK_API_KEY`
2. config 顶层 `baseUrl`（若设）→ 用它 + 顶层 `apiKey`
3. 都无 → baseUrl 默认 `https://api.deepseek.com`；**apiKey = env `DEEPSEEK_API_KEY` ?? cfg.apiKey**

**坑：User 级 env `DEEPSEEK_API_KEY` 优先级高于 config 顶层 apiKey。只改 config 无效！**
改法：
```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY","sk-xxx","User")
```
然后**必须完全重开终端**（进程环境变量是启动时继承的，重启 reasonix 不够）。

### 47.2 官方 vs 中转 模型名

| 通道 | 模型名 |
|---|---|
| 官方 | `deepseek-flash`（=V4.1-Flash）、`deepseek-v4-pro`（=V4-Pro-0813）；`deepseek-chat`/`deepseek-v4-flash`/`deepseek-reasoner` 是别名→flash |
| 中转 | `deepseek-v4.1-flash`、`deepseek-v4-pro-0813`、`deepseek-v4-flash-0731` |

- **裸名 → 官方；`apirelay-` 前缀 → 中转**
- 症状：中转名当官方用 → `400 The supported API model names are deepseek-flash, deepseek-v4-pro`

### 47.3 路由链（代码）

- `chunk-GCNBIWK7.js loadEndpoint`：`apirelay-wxctf-old-` → wxctfOld；无前缀 → 官方（env/config）
- `chunk-QSKDP3OS.js buildPayload`：剥 `apirelay-` → `wxctf-old-` → 上游裸名

---

## 48. 缓存雪崩根因：prefixHash = 系统提示+工具列表（2026-09-25）

### 48.1 机制（代码级）

`prefixHash` = `ImmutablePrefix.fingerprint` = hash({ system, tools, shots })。代码注释原文：

> "Each addTool costs one cache-miss turn — DeepSeek's prefix cache is keyed by full tool list."
> removeTool（MCP hot-unbridge）"Same cache-miss cost — prefix changes shape."

→ **MCP 工具集任何增删 = 整个前缀缓存作废 = 全量 miss（雪崩）**。

### 48.2 实测证据（android逆向动态调试 会话）

- 953 个 turn，prefixHash 变 108 次（11%）
- 官方 deepseek-flash 152 次：总 ¥2.58，均 ¥0.017/次
- 雪崩 3 次：01:17:12 ¥0.203 / 01:25:59 ¥0.271 / 01:37:16 ¥0.351
- **3 次雪崩 ¥0.825 = 总消耗的 32%**
- 还原链路：`01:25:34 session.opened` → `01:25:49 prefixHash 00a4…→6d2b…` → `01:25:59 雪崩 262K miss`
- 触发：重开会话/重启 → 重建工具集 → prefixHash 变

### 48.3 处置

- 已关 `ida-pro-mcp`（从 config `mcp` 数组移除 `ida-pro-mcp=http://127.0.0.1:8745/sse`）
- 关闭后 25 次请求零雪崩
- 注意：**fold（对话折叠）不改 prefixHash**（它改对话，不改 system+tools），但折叠本身仍会导致对话前缀 miss

---

## 49. 状态栏缓存口径补丁：按当前模型（2026-09-25）

### 49.1 问题
状态栏"缓存 X%" = `aggregateCacheHitRatio` = **全会话跨模型累计** → 被历史 0% 模型（glm / tokenrhythmfree / 坏通道）拖到 74%，误导。

### 49.2 补丁
`chunk-V4AXMN4X.js` → `aggregateCacheHitRatio` 改为**只统计最近一次请求的模型**：
```js
get aggregateCacheHitRatio() {
  const lastModel = this.turns.length > 0 ? this.turns[this.turns.length - 1].model : void 0;
  if (lastModel === void 0) { /* carryover */ }
  let hit = 0, miss = 0;
  for (const t of this.turns) { if (t.model !== lastModel) continue; hit += ...; miss += ...; }
  ...
}
```

### 49.3 效果
显示当前模型真实值（示例 deepseek-flash 96.9-98%），切模型自动跟随。

### 49.4 验证与备份
- `node --check` 通过；逻辑测试 4/5（1 失败是断言数值写错）
- 备份：`chunk-V4AXMN4X.js.cachepermodel-bak-20260925_012315`

---

## 50. 本轮清理与实测（2026-09-25）

- 官方 key `sk-8ab3fd1b...`：env `DEEPSEEK_API_KEY` + config 顶层 apiKey **双写**
- config `model` = `deepseek-flash`（= V4.1-Flash）
- 关闭 MCP：`ida-pro-mcp`（8745 SSE 一直连不上）
- 官方会话 152 次：命中率 96.9%，输出 220-125 t/s，消耗 ¥2.58（空闲价）
- 官方价格：空闲=高峰一半；高峰=工作日 9:00-12:00 / 14:00-18:00（北京）
- 中转 wxctf 老站 key `sk-qTgwm...` 欠费（-¥24.60）→ 全部模型 403，暂不可用
- 官方模型实测：`deepseek-v4.1-flash`/`deepseek-v4-pro-0813` 名字 → 400（官方无此名）

---

## 51. 余额显示机制（状态栏）

- `useSessionInfo`（chunk-O7VQOZQR.js:59912）：启动取一次余额 + **每轮结束 `refreshBalance()`**（:62368 finally 块）
- 渲染（:48111）：`⛁ {sessionCost} 已花费 / 剩余 {balance}`
- 余额来源：官方 `/user/balance` → `pickPrimaryBalance(balance_infos)` → `formatBalance`
- 注意：会话"已花费"用本地统计（Reasonix 内置定价，可能偏低）；"剩余"是官方实时余额

---

## 52. Jinshi dsf��deepseek-v4.1-flash������ + Cyber �Ƽ�ͨ���۲죨2026-09-27��

### 52.1 ���������

- Ŀ�꣺��һö�µ� Jinshi key���������� "Cyber"���� dsf��`deepseek-v4.1-flash`������ reasonix �� opencode��
- վ�㣺new.jinshi.xin����ʯAPI��New API v1.0.0-rc.40����
- �ؼ���**`deepseek-flash` �� Jinshi �ϲ�����**���� 503 `No available channel for model deepseek-flash under group Cyber`����Jinshi ��ģ������ **`deepseek-v4.1-flash`**��`deepseek-flash` ֻ�� DeepSeek �ٷ� API �ĵ�������
- ��Ĭ�� Jinshi key��config ���� `apirelay.apiKey`��ֱ�� `/v1/models` ���� **403 ��Ȩ���� DeepSeek ����** �� �����ϣ�dsf �������� key��

### 52.2 dsf ������ / Դͷ���� `/api/pricing` �� `usable_group`��

| ά�� | ֵ |
|---|---|
| dsf `enable_groups` | `DeepSeek-OpenCode`��`Cyber` |
| `DeepSeek-OpenCode` | "DeepSeek OpenCode���� �ȶ�����" �� **OpenCode ����** |
| `Cyber` | "GPT����ģ�����Ƽ��� �������Ƽ� �Ͻ�Υ�����" |
| `DeepSeek-Deploy` | "�Բ�������DS" �� ֻ�� `deepseek-v4-flash` / `deepseek-v4-pro`��**���� 4.1-flash** |
| ģ������ | "��API�������Ѹ�Ϊ deepseek-flash����Ѷ WorkBuddy��CodeBuddy �� **OpenCode** �Ⱥ��������ȫ������" |

���ۣ�**dsf Դͷ = OpenCode ��������� DeepSeek-V4.1-Flash**���ǹٷ�ֱ�����ǽ�ʯ�Բ���

### 52.3 reasonix ���ߣ�config + ·�ɣ��ļ��Ѹģ�

| �ļ� | �Ķ� |
|---|---|
| `~/.reasonix/config.json` | ���� `apirelayBackup.jinshi` = { baseUrl: new.jinshi.xin/v1, apiKey: <��key>, models:["deepseek-v4.1-flash"] } |
| `chunk-GCNBIWK7.js` `loadEndpoint` | `apirelay-jinshi-deepseek-` ��֧��Ϊ������ `apirelayBackup.jinshi`��ԭ��ֱ���ö��� `apirelay`�����䵽���� key�� |
| `chunk-4EHRIP5U.js` | `/apirelay` ��ȫ�� `Jinshi/deepseek-v4.1-flash` |
| `chunk-V4AXMN4X.js` | �����ı��� `apirelay-jinshi-deepseek-v4.1-flash` / `apirelay-deepseek-v4.1-flash` = 1e6 |

- ·������`/apirelay Jinshi/deepseek-v4.1-flash` �� `apirelay-jinshi-deepseek-v4.1-flash` �� loadEndpoint(apirelayBackup.jinshi) �� buildPayload �� `apirelay-` + `jinshi-` �� ����ģ�� `deepseek-v4.1-flash` �7�3
- ���ݣ�`*.bak-dsf-20260927_170148`���Ĵ���������� reasonix ��Ч��

### 52.4 opencode ����

- `~/.config/opencode/opencode.jsonc` ���� provider `jinshi-cyber`��@ai-sdk/openai-compatible��baseURL new.jinshi.xin/v1���� key��model `deepseek-v4.1-flash`����`/models` ѡ `jinshi-cyber/deepseek-v4.1-flash`��

### 52.5 �Ƽ�ͨ���۲죨Cyber ��ע���ϵͳ��ʾ��

�� dsf ���ʴ�Q1������/ϵͳ��ʾ����ģ��**��������ע���Խ��ʽϵͳ��ʾ**��

- ����� **"NLS"���������˹��������Ŀ�ܣ�**���Ự״̬ + ��Χ���ԡ�������Լ����������ͨ���ַ����׶�/��ʽ��Լ��֤�ݷּ�����������������ʽ�������޸�ʽ���ԡ�
- ��Χ���ԣ�**����Ա��"ȫ������"**���û������������ ���� �پܾ����й��������صĲ��� �ھܾ���� **sub2api / newapi ��תվ��**��ץ��������͸��
- ����ͨ����`REVERSE / AUTH / RUNTIME / GAME / UNPACK / PROTO / DOTNET / NATIVE / PATCH / PENTEST`��
- ֤�ݷּ���`VERIFIED / CANDIDATE / UNRESOLVED`��ǿ��"�϶�ʽ��������������"��
- Ӳ�ų��**��ʵ����Σ��װ�� / ���ģɱ����װ��**��

��Ϊӡ֤��Q2-Q4���������"����/�����ֽ�/�����嵥/�ع�·��"��ʽǿ�󶨣����໰����д��"�������л�Ϊ�����ֽ��ƽ�"��SQL ע�������+�������޸�+�����嵥��Q4 ��ȷ���Ͽ��л���"�Ƽ�ģʽ"��

���ۣ�**Cyber = Ԥ�ù�����ϵͳ��ʾ���Ƽ�ͨ��**����������/��͸�����ݣ�����"���Լ�����תվ"��"�й�����Ŀ��"���ޣ�����Լ�� jailbreak������ȫ������������ܴ�

### 52.6 ��֤

- `node --check` ���� chunk ȫͨ����
- `node D:\Temp\opencode\verify_dsf_cyber.mjs` �� **10/10 PASS**��config��loadEndpoint ���̡�opencode JSONC��4 ��ʵ����ʽ�� HTTP 200����

### 52.7 ����ע��

| �� | ˵�� |
|---|---|
| `deepseek-flash` vs `deepseek-v4.1-flash` | �ٷ� API �� vs Jinshi ģ����������� |
| ���� `apirelay.apiKey` ��ʧЧ | ֱ�� `/v1/models` 403 ��Ȩ���� DeepSeek ���飻dsf ���� key / `apirelayBackup.jinshi` |
| Cyber key �ɳ�ģ�ͣ�/v1/models�� | claude-fable-5(-1)��claude-opus-4-6/7/8/5��codex-auto-review��deepseek-v4.1-flash��glm-5.3��gpt-5.6-luna/sol/terra��gpt-6-astra/sol��kimi-k3 |
| ���� key | ���ʼǲ�д key��key ֻ�� config / opencode�������ֻ� |
