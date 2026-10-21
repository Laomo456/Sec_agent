# 安全政策

## 报告平台自身的漏洞

AGG 是安全工具平台,本身也可能有洞。发现后请**不要开公开 issue**,改用
GitHub 私有渠道:

- GitHub Security Advisories(仓库 Security → Report a vulnerability)
- 或私信仓库维护者

请附:影响范围、复现步骤、你建议的修复方向。我们会在 7 天内确认收悉,
修复后会在 CHANGELOG 与 Advisory 里致谢(除非你要求匿名)。

**支持范围**:gateway / toolbox / frontend 自有代码。
`external/` 下的第三方运行时与 MCP 对端程序的漏洞,请报给对应上游项目。

## 使用边界(重要)

本平台仅用于**已授权的渗透测试、CTF 竞赛与防御性安全研究**。

- 对无授权目标使用漏洞类工具(nuclei / sqlmap / fscan 等)是明确禁止的
- 工具卡 `danger` 标注是防线之一,不要绕过;新增工具必须如实标注
- 平台默认只监听 `127.0.0.1`,不要改成 `0.0.0.0` 暴露公网——网关无鉴权,
  等于把 agent 和全部工具面裸奔给所有人

## 机密处理约定

- `config.yaml` 的 `api_key` 保持空串入库;本地填写后不要提交
- 令牌类(如 `CTF2_TOKEN`)一律走环境变量,不落盘
- 会话/扫描输出/凭据样本永远不进 git(`.gitignore` 已排除运行时目录)
