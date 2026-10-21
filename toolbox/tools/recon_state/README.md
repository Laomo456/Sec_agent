# recon_state

普通工具卡，主智能体主动调用。现有 gateway、agent、异步子任务、前端和 wpbox 协议均无需修改。默认「全部工具」可见，「渗透全家桶」已加入；另有「攻击面子代理」工具集供原 subagent 使用。

状态保存在 `toolbox/workspace/<session>/files/recon/state.json`，一次变更在进程间锁内原子替换单个 JSON，避免多次工具调用丢更新。新版本未知时拒绝写入；不同会话不会串台。这里只限制本卡读写自身会话的路径，未增加针对探测工具的权限拦截。

## 最小调用流程

```json
{"op":"init","params":{"target":"example.test","scope":{"domains":["example.test"]}}}
{"op":"upsert","params":{"items":[{"entry":"https://api.example.test/docs","type":"api-doc","priority":"P1"}]}}
{"op":"status"}
{"op":"advance","params":{"phase":"CLUSTER"}}
{"op":"advance","params":{"phase":"DISPATCH"}}
{"op":"claim","params":{"limit":6}}
```

claim 返回任务编号、条目、子角色 system 和独立报告路径。主代理自行调用 subagent；子任务 role/system/task 中补齐范围、条目、路径和报告要求，toolset_id 可用 `ts_recon_probe`。随后把回执的 batch_id/agent_id 与 E<N> 通过 bind 关联。claim 不派发，bind 也不检查子代理已经执行成功。

```json
{"op":"bind","params":{"task_id":"E1","batch_id":"实际回执值","agent_id":"实际回执值"}}
{"op":"advance","params":{"phase":"WAIT"}}
```

等待原有完成通知，之后 status 查询实际后台状态；checkpoint 可填写实际通知 ID、agent_id 或台账版本，以区分有新事件的查询。status 采用项目已有的动态工具标记 unknown，避免同轮缓存旧结果；实际通知上下文同时避免触发相同参数重复调用保护，不用随机参数轮询。再导入子代理报告：

```json
{"op":"report","params":{"task_id":"E1","path":"recon/batch/E1.json"}}
```

报告必需 task_id、surfaces、findings、negatives、next_directions、blocked_by 数组与 timeout 布尔值。surfaces 每项引用分配的 surface_id，给出 confirm_state、task_state 和 evidence；CONFIRMED/NEGATIVE 不能省略证据。若缺条目，保留已知部分，其余进入补测。完整报告可独立于后台 timeout 被采用。无产物时 release 记录 TIMEOUT/INCOMPLETE，不伪造成功。

独立报告接受后保存带集中生成 ID 的规范版本，MD 由该 JSON 生成。重复导入原始报告或规范报告均幂等；已接收任务的另一份不同内容不能覆盖，补测新建 E<N>。新发现先另 upsert。

P0/P1 与冲突项由主代理实际重放，再调用 review，evidence 记录结果，replay_call_id 引用主代理实际工具调用。相同响应不会强迫伪造不同内容：证据可去重，复核调用来源独立保存。findings 默认 CANDIDATE；指定 finding_ids 复核后才标 reviewed。

## 操作与语义

| op | 作用 |
| --- | --- |
| init | 初始化，已有相同目标不覆盖；scope 仅记录背景 |
| status | 阶段、三轴计数、各队列、已绑定子代理的 job_status 和建议 |
| list | 按三轴/族/优先级筛选分页，默认 50，最多 500 |
| upsert | 入口集中生成稳定 ID，合并发现事实；大输入支持 path 读取数组 |
| claim | 同族领取最多 6 条；new 为新增/变化待测，retry 为超时/不完整 |
| bind | 关联 subagent 回执，不改后台运行状态 |
| release | 释放台账认领；不取消后台任务；迟到结果不能覆盖新尝试 |
| report | 原子校验/接收整份机器报告，保留负结论/待办/缺失项 |
| review | 主代理复核记录，附实际 replay_call_id；可确认具体 finding_ids |
| mark | 维护暂缓/无效/待授权等状态，不允许无证据直接填 CONFIRMED |
| chain | 存已有入口与证据之间的关系假设，不执行动作 |
| advance | 按阶段推进；阻止在途、待复核时假收口 |
| new_round | 建立上一轮事实基线，保持 ID；不把未覆盖对象批量标 STALE |
| export | 生成 AGGREGATE.json/.md 和规范子报告；不调用 wpbox |

三轴与现有 job.status/wpbox.status 独立。报告接收后后台任务是否 timeout 的历史仍保留。后台结束不会自动更新业务三轴；主代理需 report/release，status 只返回实况。领取与提交失败应 release，避免遗留认领。

每轮每条默认最多尝试 3 次；TIMEOUT/INCOMPLETE 即使 UNCHANGED 也在 retry 队列，INVALID/STALE/NEEDS_AUTH 不自动领取。达到次数上限后 status 仍保留待办；主代理决定是否提高 max_attempts。task_state=SKIPPED 表示明确放弃。

阶段为 ENUMERATE→CLUSTER→DISPATCH→WAIT→REVIEW→COMMIT→SUMMARIZE→COMPLETE；REVIEW 可回 DISPATCH，COMMIT/SUMMARIZE 可回 REVIEW。完成前 export 最新版本；如有未完项，advance(COMPLETE,note) 明确部分完成，汇总保留待办。完成后要修改需 new_round。

`recon/state.json` 是权威台账，报告和汇总是可恢复视图。中断可能造成两个导出视图暂时版本不一致；再次 export 即可恢复，不能据旧 MD 覆盖台账。没有把跨多个文件替换宣称为数据库事务。

## 边界

- 不联网、不派发、不自动轮询、不新增权限/限速/停止控制；授权和执行决定仍由主智能体负责。正常回答结束不取消后台任务，停止需用原有会话/子任务功能。
- 子代理仍共享会话文件；独立写入是任务约定与现有工具集选择，不是新增文件沙箱。避免让子代理调用本卡。
- review 要求证据和调用编号，但不验证模型是否真的重放；scope 不作为强制白名单。wpbox 入库结果也由主代理核实。
- body 采样哈希不能代表全文件哈希。优先级不是严重度，攻击面不等于漏洞。
- ID 规则固定版本 1，查询部分参与入口身份，保留路径大小写/转义。所有记录留在当前会话，不自动导入其他会话/旧 MD。 
- 没有任务/条目删除操作，保留观测与尝试历史；台账增长后可通过 list 分页和文件输入降低上下文开销。当前每次调用读取单个 JSON，适合中小规模任务。

离线回归：在项目根运行 `gateway/.venv/Scripts/python.exe -u toolbox/tests/recon_state_test.py`，包含真实 MCP native/meta 调用与多进程竞争领取。测试不访问目标站点或 wpbox 用户资产库。
