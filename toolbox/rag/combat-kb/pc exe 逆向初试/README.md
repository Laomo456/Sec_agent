# PC EXE 逆向初试

> `本地so分析` 的 Windows 版。环境: Win11 | Python 3.14 pefile capstone | rizin CLI | frida 17.11

## 工作流

```
EXE/DLL
  ├─ 1. PE头 + 节表 + 导入表     (pefile: 架构/壳/功能面)
  ├─ 2. strings + 导出表          (rz-bin: 线索/API清单)
  ├─ 3. 壳检测                    (节名/熵/自定义段)
  ├─ 4. 静态反汇编                (capstone: 关键函数)
  ├─ 5. 动态验证                  (frida: WinHTTP/BCrypt)
  └─ [安装器] 魔数识别            (Inno Setup: overlay → zlb/LZMA)
```

## PE 速查

```python
pe = pefile.PE("x.exe")
nt = pe.NT_HEADERS
nt.FILE_HEADER.Machine              # 0x8664=x64 0x14c=x86
nt.OPTIONAL_HEADER.AddressOfEntryPoint
nt.OPTIONAL_HEADER.Subsystem         # 2=GUI 3=Console
```

| 节名 | 异常 |
|---|---|
| `.text` 熵 > 7.5 | 指令加密/壳 |
| UPX0/UPX1/UPX2 | UPX 壳 |
| 自定义节(LZMADEC/.gxfg) | 可疑代码/数据 |

## Frida 17.x API (踩坑)

`Module.findExportByName` 已删除。用:

```javascript
var mod = Process.getModuleByName('WINHTTP.dll');
var exports = mod.enumerateExports();
var addr = exports.find(e => e.name === 'WinHttpSendRequest').address;
Interceptor.attach(addr, { onEnter: ... });
```

## 常用 hook 目标

| 函数 | DLL | 抓什么 |
|---|---|---|
| WinHttpSendRequest | WINHTTP.dll | HTTP 请求体 |
| WinHttpOpenRequest | WINHTTP.dll | 请求路径 |
| WinHttpConnect | WINHTTP.dll | 目标域名 |
| BCryptEncrypt | bcrypt.dll | 加密输入 |
| CreateFileW | kernel32.dll | 文件写操作 |
| RegOpenKeyExW | advapi32.dll | 注册表读取 |