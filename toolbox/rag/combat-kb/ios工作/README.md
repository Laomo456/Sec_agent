# iOS 逆向工作知识库

> 设备：iPhone 6s Plus (xiaoji) · iOS 15.8.8 · Dopamine 无根越狱 · TrollStore 2.1
> 更新：2026-08-04

## 一、工具链（全部实测 ✅）

### C 盘（Python pip，`C:\Users\20751\AppData\Local\Programs\Python\Python314\`)
| 工具 | 版本 | 用途 |
|------|------|------|
| frida | 17.11.0 | hook / attach / spawn / 抓包 |
| frida-tools | 14.9.0 | frida-ps / frida CLI |
| tidevice | 0.12.11 | 启动 / 截图 / 装包 / developer image |
| fb-idb | 1.1.7 | ❌ 弃用（Py3.14 崩溃 + Windows 无 companion） |

### D 盘（Node.js）
- node: `D:\nodejs\node.exe` · npm: `D:\nodejs\npm.cmd`
- bagbak: 未装（`npm i -g bagbak` 砸壳用）

### 手机端
- frida-server 17.11.0（Silem 装，launchd 守护）
- TrollStore 2.1（Helper 2.1）
- developer-cmds（手机端命令行工具）

## 二、developer image（已解决）

- 位置：`C:\Users\20751\.tidevice\device-support\15.5.zip` + `15.5\` 文件夹（dmg+signature）
- 来源：`D:\Downloads\15.5.zip` / `D:\15.5\`
- 作用：tidevice launch/screenshot/xcuitest 的基础，已挂载成功（DeveloperImage 15.8 mounted）

## 三、USB 命令速查（全走 usbmuxd，与端口无关）

| 命令 | 用途 |
|------|------|
| `frida-ps -U` | 运行中进程 |
| `frida-ps -Uai` | 全部 app + BundleID |
| `frida -U -p <pid>` | attach |
| `frida -U -f <bundleid>` | spawn（普通 app 稳，系统 app 易崩） |
| `tidevice launch <bid>` | 干净启动（不注入，最稳） |
| `tidevice screenshot <file>` | 截图 |
| `tidevice applist` | 已装 app 列表 |
| `tidevice relay 8100 8100` | 端口转发（WDA 用） |

## 四、BundleID 速查

| App | BundleID |
|-----|----------|
| QQ浏览器 | com.tencent.mttlite |
| QQ | com.tencent.mqq |
| 快手 | com.smile.gifmaker |
| DeepSeek | com.deepseek.chat |
| App Store | com.apple.AppStore |
| WDA Runner | com.facebook.WebDriverAgentRunner.xctrunner.J2N8HPUXR7 |

## 五、踩坑记录（重要）

1. **spawn 系统 app 易崩**（App Store 闪退）→ 用 `tidevice launch` 启动 + attach 注入
2. **attach 老进程偶发 `(os/kern) failure`** → 重启 app 或 spawn 重来
3. **TrollStore 老版 173/175** → 更新到 2.x（已解决）
4. **WDA ipa `Unsupported Mach-O type`** → 构建源问题，暂时搁置；用 frida attach 注入模拟触摸替代
5. **tidevice 卡下载 developer image** → 手动放 zip 到 device-support 缓存（已解决）
6. **idb Windows 不可用** → 换 tidevice
7. **SpringBoard attach 后 ObjC undefined** → 系统进程桥接问题，附到普通 app 正常

## 六、架构认知

- Dopamine 无根：用户态为主，内核态仅启动时用（打完即走）
- 日常操作（hook/抓包/砸壳/读沙盒）= 纯用户态
- 写入位置 `/var/jb/`，不碰系统根分区，稳定

## 七、待办

- [ ] 打通 frida attach 模拟触摸+键盘输入（dump_ui.py 已写，未跑通）
- [ ] 装 bagbak（砸壳）
- [ ] App Store 装快手 → hook 复现
- [ ] WDA（备选，Mach-O 问题未解）