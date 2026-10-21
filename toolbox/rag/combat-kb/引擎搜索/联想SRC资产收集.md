# 联想 SRC 资产收集记录
> 时间: 2026-08-31 | SRC平台: 漏洞盒子 https://lsrc.vulbox.com/

---

## 一、资产枚举结果
| 域名 | 枚举数 | 存活数 |
|------|--------|--------|
| lenovo.com | 175 | 78 |
| lenovo.com.cn | 66 | 38 |
| motorola.com | 99 | 59 |
| thinkpad.com | 2 | 1 |
| lenovomm.com | 17 | 13 |
| **总计** | **359** | **189** |

## 二、高价值目标
### CAS 认证系统（核心P0）
- ipgkms.lenovo.com (生产)
- ipgkmsuat.lenovo.com (UAT)
- ipgpms.lenovo.com (生产)
- ipgpmsuat.lenovo.com (UAT)
- ipgpassportuat.lenovo.com

### 管理后台（P0/P1）
- classification.lenovo.com (联想登录页)
- secpw.lenovo.com (密码安全)
- vendorsecurity.lenovo.com (供应商安全)
- watermark.lenovo.com (水印系统)
- nactools.lenovo.com (内部工具)

### AI/云服务（P0）
- aicloud.lenovo.com.cn (AI专属云)
- ilive.lenovo.com.cn (头一波)
- research.lenovo.com (研究院)
- cube.lenovo.com (智慧客服)

## 三、测试方向优先级
1. CAS 认证系统（ipgkms 系列）
2. 管理后台默认凭据/越权
3. 摩托罗拉系统 API
4. 403 目标目录枚举
