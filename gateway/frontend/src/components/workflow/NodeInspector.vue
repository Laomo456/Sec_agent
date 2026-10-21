<script setup lang="ts">
/** 右侧节点配置面板：按节点类型渲染表单；底部显示本节点最近一次运行的输入输出。 */
import { NButton, NInput, NInputNumber, NSelect, NSlider, NSwitch, NTabs, NTabPane } from "naive-ui";
import { computed, ref, watch } from "vue";
import { getModelProfiles } from "../../api";
import { useToolsStore } from "../../stores/tools";
import { useWorkflowStore } from "../../stores/workflow";
import { CONDITION_OPS, NODE_DEFS, UNARY_OPS, uid } from "./nodeDefs";
import VarInput from "./VarInput.vue";

const store = useWorkflowStore();
const toolsStore = useToolsStore();
const node = computed(() => store.selected!);
const d = computed(() => node.value.data);
const def = computed(() => NODE_DEFS[node.value.type]);
const run = computed(() => store.nodeRuns[node.value.id]);
const tab = ref("config");
watch(() => store.selectedId, () => { tab.value = "config"; });
watch(() => run.value?.status, (s) => { if (s && s !== "running" && tab.value === "config" && store.running) tab.value = "config"; });

const errorPolicies = [
  { label: "中止工作流", value: "fail" },
  { label: "输出错误信息并继续", value: "continue" },
  { label: "走异常分支", value: "branch" },
];
const varTypes = [
  { label: "单行文本", value: "text" }, { label: "多行文本", value: "paragraph" },
  { label: "数字", value: "number" }, { label: "下拉选项", value: "select" },
  { label: "文件", value: "file" }, { label: "开关", value: "boolean" },
];
const outTypes = ["string", "number", "boolean", "object", "array", "any"].map((v) => ({ label: v, value: v }));
const listOps = [
  { label: "按分隔符拆分文本", value: "split" }, { label: "过滤（包含关键字）", value: "filter" },
  { label: "排序", value: "sort" }, { label: "切片", value: "slice" },
  { label: "去重", value: "unique" }, { label: "合并为文本", value: "join" },
];
const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"].map((v) => ({ label: v, value: v }));
const assignModes = [{ label: "覆盖", value: "set" }, { label: "追加到列表", value: "append" }, { label: "数值累加", value: "add" }];

/** 编辑操作前打撤销点（输入类字段不打，避免每个按键一个撤销点） */
const cp = () => store.checkpoint();
const pruned = () => store.pruneEdges(node.value.id);

function addCase() { cp(); d.value.cases.push({ id: uid("case"), logic: "and", conditions: [{ var: "", op: "contains", value: "" }] }); }
function removeCase(i: number) { cp(); d.value.cases.splice(i, 1); pruned(); }
function addClass() { cp(); d.value.classes.push({ id: uid("c"), name: `类别 ${d.value.classes.length + 1}` }); }
function removeClass(i: number) { cp(); d.value.classes.splice(i, 1); pruned(); }
function onErrorPolicy(v: string) { cp(); d.value.on_error = v; pruned(); }

/* 工具节点：选工具后按 schema 生成参数表单 */
const toolOptions = computed(() => store.tools.map((t) => ({ label: `${t.name}  ·  ${t.description.slice(0, 26)}`, value: t.name })));
/* 智能体节点：工具集下拉 = 对话面板同一份预设（内置 + 用户自定义）；加载失败时退回内置两项 */
const toolsetOptions = computed(() => toolsStore.loaded && toolsStore.presetOptions.length
  ? toolsStore.presetOptions
  : [{ label: "全部工具", value: "all" }, { label: "纯对话", value: "none" }]);
const toolCard = computed(() => store.tools.find((t) => t.name === d.value.tool));
const toolProps = computed(() => Object.entries<any>(toolCard.value?.params_schema?.properties || {}));
const toolRequired = computed(() => new Set<string>(toolCard.value?.params_schema?.required || []));

/* 模型档案下拉：与设置页同一份档案（轻量端点，每次选中模型节点时刷新） */
const MODEL_NODES = ["llm", "agent", "classifier", "extractor"];
const profileOptions = ref<any[]>([{ label: "默认（全局设置）", value: "" }]);
async function loadProfiles() {
  try {
    const ps = await getModelProfiles();
    profileOptions.value = [{ label: "默认（全局设置）", value: "" },
      ...ps.map((p: any) => ({ label: `${p.id} · ${p.model || "未命名模型"}`, value: p.id }))];
  } catch { /* 档案接口失败时退回仅默认项 */ }
}
watch(() => node.value?.type, (t) => { if (t === "tool") store.loadTools(); if (t === "agent") toolsStore.load(); if (MODEL_NODES.includes(t)) loadProfiles(); }, { immediate: true });
function setToolParam(k: string, v: any) { d.value.params = { ...(d.value.params || {}), [k]: v }; }
/** 非字符串参数（数组/数字/布尔）在输入框里以 JSON 文本编辑；含 {{变量}} 时原样保存交给引擎解析 */
function toolParamText(k: string): string {
  const v = d.value.params?.[k];
  if (v == null) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}
function setToolParamText(k: string, type: string, text: string) {
  if (type === "string" || /\{\{/.test(text) || text === "") return setToolParam(k, text);
  try { setToolParam(k, JSON.parse(text)); } catch { setToolParam(k, text); }
}

const pretty = (v: any) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));
const dur = computed(() => {
  const ms = run.value?.duration_ms;
  return ms == null ? "" : ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
});
const statusLabel: Record<string, string> = { running: "运行中", succeeded: "成功", failed: "失败", skipped: "已跳过", exception: "异常继续",
  stopped: "已停止", stopping: "停止中", skipping: "跳过中", manual: "手动结果", cached: "缓存" };

/* 单节点调试：为引用到的上游变量提供输入框 */
const debugRefs = computed(() => {
  const s = JSON.stringify(d.value);
  const out = new Set<string>();
  for (const m of s.matchAll(/\{\{\s*([\w-]+)\.([\w.\[\]-]+)\s*\}\}/g)) out.add(`${m[1]}.${m[2].split(/[.\[]/)[0]}`);
  return [...out];
});
const debugInputs = ref<Record<string, string>>({});
const debugOpen = ref(false);
function runDebug() {
  const inputs: Record<string, any> = {};
  for (const [k, v] of Object.entries(debugInputs.value)) {
    try { inputs[k] = JSON.parse(v); } catch { inputs[k] = v; }
  }
  store.runNode(node.value.id, inputs);
  tab.value = "last";
}
const canDebug = computed(() => !["start", "end", "iteration", "loop", "note", "aggregator"].includes(node.value.type)
  && !node.value.type.endsWith("_start"));
/* 头部快捷操作（触摸端没有右键菜单，从这里复制/删除）；守卫与 store 侧一致 */
const duplicable = computed(() => node.value.type !== "start" && !node.value.type.endsWith("_start") && !def.value.container);
const deletable = computed(() => !(node.value.type === "start" && !node.value.parentNode) && !node.value.type.endsWith("_start"));
</script>

<template>
  <aside v-if="store.selected && def" class="insp glass" :style="{ '--c': def.color }">
    <header>
      <span class="ic"><svg viewBox="0 0 24 24"><path :d="def.icon" /></svg></span>
      <div class="ttl">
        <input v-model="d.title" class="title-input" aria-label="节点名称" />
        <div class="desc">{{ def.desc }}</div>
      </div>
      <div class="hacts">
        <button v-if="duplicable" class="hb" title="复制节点" aria-label="复制节点" @click="store.duplicateNode(node.id)">
          <svg viewBox="0 0 24 24"><path d="M9 9h10v10H9zM5 15V5h10" /></svg>
        </button>
        <button v-if="deletable" class="hb danger" title="删除节点" aria-label="删除节点"
                @click="store.removeNodes([node.id]); store.selectedId = ''">
          <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" /></svg>
        </button>
        <button class="x" aria-label="关闭" @click="store.selectedId = ''">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </header>
    <div class="idrow"><span>ID</span><code>{{ node.id }}</code>
      <span v-if="run" class="rs" :class="run.status">{{ statusLabel[run.status] || run.status }} {{ dur }}</span>
    </div>

    <n-tabs v-model:value="tab" type="line" size="small" class="tabs" animated>
      <n-tab-pane name="config" tab="配置">
        <div class="form">
          <!-- ═══ 开始 ═══ -->
          <template v-if="node.type === 'start'">
            <div class="field">
              <span class="lab">输入变量 <span class="sub">运行时由用户填写</span></span>
              <div class="list">
                <div v-for="(v, i) in d.variables" :key="i" class="item">
                  <div class="row">
                    <n-input v-model:value="v.name" size="small" placeholder="变量名" class="grow code" />
                    <n-select v-model:value="v.type" :options="varTypes" size="small" style="width: 110px" />
                    <button class="del" @click="cp(); d.variables.splice(i, 1)">✕</button>
                  </div>
                  <div class="row">
                    <n-input v-model:value="v.label" size="small" placeholder="显示名称" class="grow" />
                    <span class="hint">必填</span><n-switch v-model:value="v.required" size="small" />
                  </div>
                  <n-input v-if="v.type === 'select'" :value="(v.options || []).join(',')" size="small"
                           placeholder="选项，逗号分隔" @update:value="(s: string) => v.options = s.split(',').map((x: string) => x.trim()).filter(Boolean)" />
                  <div class="row" v-if="v.type === 'boolean'">
                    <span class="hint">默认值</span><n-switch v-model:value="v.default" size="small" />
                  </div>
                </div>
                <button class="add" @click="cp(); d.variables.push({ name: `var_${d.variables.length + 1}`, label: '', type: 'text', required: false })">＋ 添加输入变量</button>
              </div>
              <div class="row" v-if="d.variables?.some((v: any) => v.type === 'file')">
                <span class="hint">上传后自动运行</span><n-switch v-model:value="d.auto_run_on_upload" size="small" />
              </div>
            </div>
          </template>

          <!-- ═══ 结束 ═══ -->
          <template v-else-if="node.type === 'end'">
            <div class="field">
              <span class="lab">输出变量</span>
              <div class="list">
                <div v-for="(o, i) in d.outputs" :key="i" class="row">
                  <n-input v-model:value="o.name" size="small" placeholder="名称" class="code" style="width: 110px" />
                  <VarInput v-model="o.value" :node-id="node.id" mode="ref" class="grow" />
                  <button class="del" @click="cp(); d.outputs.splice(i, 1)">✕</button>
                </div>
                <button class="add" @click="cp(); d.outputs.push({ name: `out_${d.outputs.length + 1}`, value: '' })">＋ 添加输出</button>
              </div>
            </div>
          </template>

          <!-- ═══ 直接回复 / 模板 ═══ -->
          <template v-else-if="node.type === 'answer'">
            <div class="field"><span class="lab">回复内容 <span class="sub">支持 {{ '{' + '{变量}' + '}' }}</span></span>
              <VarInput v-model="d.text" :node-id="node.id" :rows="6" placeholder="输入回复内容，键入 {{ 插入变量" />
            </div>
          </template>
          <template v-else-if="node.type === 'template'">
            <div class="field"><span class="lab">模板</span>
              <VarInput v-model="d.template" :node-id="node.id" :rows="8" mono placeholder="例如：用户问的是 {{start.query}}" />
            </div>
          </template>

          <!-- ═══ LLM ═══ -->
          <template v-else-if="node.type === 'llm'">
            <div class="field"><span class="lab">系统提示词</span>
              <VarInput v-model="d.system" :node-id="node.id" :rows="4" placeholder="设定角色与规则" />
            </div>
            <div class="field"><span class="lab">用户提示词 <span class="req">*</span></span>
              <VarInput v-model="d.prompt" :node-id="node.id" :rows="6" placeholder="键入 {{ 插入上游变量" />
            </div>
            <div class="field"><span class="lab">温度 {{ d.temperature }}</span>
              <n-slider v-model:value="d.temperature" :min="0" :max="2" :step="0.1" />
            </div>
            <div class="row">
              <div class="field grow"><span class="lab">输出格式</span>
                <n-select v-model:value="d.output" size="small" :options="[{ label: '文本', value: 'text' }, { label: 'JSON', value: 'json' }]" />
              </div>
              <div class="field grow"><span class="lab">最大 Token</span>
                <n-input-number v-model:value="d.max_tokens" size="small" :min="1" placeholder="默认" clearable />
              </div>
            </div>
            <div class="field"><span class="lab">模型档案 <span class="sub">在设置页配置</span></span>
              <n-select v-model:value="d.profile" size="small" :options="profileOptions"
                        placeholder="默认（全局设置）" />
              <div v-if="d.model" class="row">
                <span class="hint">旧版名称覆盖：{{ d.model }}</span>
                <button class="del" @click="cp(); d.model = ''">清除</button>
              </div>
            </div>
            <div class="row"><n-switch v-model:value="d.memory" size="small" /><span class="hint">共享对话记忆：多个开启此项的 LLM 节点共用一段多轮上下文</span></div>
          </template>

          <!-- ═══ 智能体 ═══ -->
          <template v-else-if="node.type === 'agent'">
            <div class="field"><span class="lab">工具集 <span class="sub">与对话面板预设一致</span></span>
              <n-select :value="d.toolset_id || 'all'" @update:value="d.toolset_id = $event" size="small"
                :options="toolsetOptions" />
            </div>
            <div class="field"><span class="lab">任务 <span class="req">*</span></span>
              <VarInput v-model="d.task" :node-id="node.id" :rows="6" placeholder="描述要完成的任务，智能体会自主调用工具" />
            </div>
            <div class="field"><span class="lab">附加指令</span>
              <VarInput v-model="d.system" :node-id="node.id" :rows="3" placeholder="可选：约束风格或步骤" />
            </div>
            <div class="field"><span class="lab">最大轮次</span>
              <n-input-number v-model:value="d.max_iterations" size="small" :min="1" :max="50" />
            </div>
            <div class="field"><span class="lab">模型档案 <span class="sub">在设置页配置</span></span>
              <n-select v-model:value="d.profile" size="small" :options="profileOptions"
                        placeholder="默认（全局设置）" />
            </div>
          </template>

          <!-- ═══ 问题分类器 ═══ -->
          <template v-else-if="node.type === 'classifier'">
            <div class="field"><span class="lab">输入</span><VarInput v-model="d.input" :node-id="node.id" mode="ref" /></div>
            <div class="field">
              <span class="lab">类别 <span class="sub">每个类别一个出口</span></span>
              <div class="list">
                <div v-for="(c, i) in d.classes" :key="c.id" class="row">
                  <span class="ih"><span class="badge">{{ i + 1 }}</span></span>
                  <n-input v-model:value="c.name" size="small" placeholder="类别描述，如：售后问题" class="grow" />
                  <button class="del" :disabled="d.classes.length <= 1" @click="removeClass(i)">✕</button>
                </div>
                <button class="add" @click="addClass">＋ 添加类别</button>
              </div>
            </div>
            <div class="field"><span class="lab">分类说明</span>
              <n-input v-model:value="d.instruction" type="textarea" size="small" :autosize="{ minRows: 2 }" placeholder="可选：补充分类规则" />
            </div>
            <div class="field"><span class="lab">模型档案 <span class="sub">在设置页配置</span></span>
              <n-select v-model:value="d.profile" size="small" :options="profileOptions"
                        placeholder="默认（全局设置）" />
            </div>
          </template>

          <!-- ═══ 参数提取 ═══ -->
          <template v-else-if="node.type === 'extractor'">
            <div class="field"><span class="lab">输入</span><VarInput v-model="d.input" :node-id="node.id" mode="ref" /></div>
            <div class="field"><span class="lab">要提取的参数</span>
              <div class="list">
                <div v-for="(p, i) in d.params" :key="i" class="item">
                  <div class="row">
                    <n-input v-model:value="p.name" size="small" placeholder="参数名" class="grow code" />
                    <n-select v-model:value="p.type" :options="outTypes" size="small" style="width: 96px" />
                    <button class="del" @click="cp(); d.params.splice(i, 1)">✕</button>
                  </div>
                  <div class="row">
                    <n-input v-model:value="p.description" size="small" placeholder="描述（帮助模型理解）" class="grow" />
                    <span class="hint">必填</span><n-switch v-model:value="p.required" size="small" />
                  </div>
                </div>
                <button class="add" @click="cp(); d.params.push({ name: '', type: 'string', description: '', required: false })">＋ 添加参数</button>
              </div>
            </div>
            <div class="field"><span class="lab">模型档案 <span class="sub">在设置页配置</span></span>
              <n-select v-model:value="d.profile" size="small" :options="profileOptions"
                        placeholder="默认（全局设置）" />
            </div>
          </template>

          <!-- ═══ 条件分支 ═══ -->
          <template v-else-if="node.type === 'if_else'">
            <div class="list">
              <div v-for="(c, ci) in d.cases" :key="c.id" class="item">
                <div class="ih">
                  <span class="badge">{{ ci === 0 ? "IF" : `ELIF ${ci}` }}</span>
                  <button v-if="c.conditions.length > 1" class="logic" @click="c.logic = c.logic === 'and' ? 'or' : 'and'">
                    {{ c.logic === "and" ? "且 AND" : "或 OR" }}
                  </button>
                  <button v-if="d.cases.length > 1" class="del" @click="removeCase(ci)">删除分支</button>
                </div>
                <div v-for="(cond, k) in c.conditions" :key="k" class="item" style="background: none">
                  <div class="row">
                    <VarInput v-model="cond.var" :node-id="node.id" mode="ref" class="grow" />
                    <button class="del" @click="cp(); c.conditions.splice(k, 1)">✕</button>
                  </div>
                  <div class="row">
                    <n-select v-model:value="cond.op" :options="CONDITION_OPS" size="small" style="width: 130px" />
                    <VarInput v-if="!UNARY_OPS.has(cond.op)" v-model="cond.value" :node-id="node.id" placeholder="比较值" class="grow" />
                  </div>
                </div>
                <button class="add" @click="cp(); c.conditions.push({ var: '', op: 'contains', value: '' })">＋ 添加条件</button>
              </div>
              <button class="add" @click="addCase">＋ 添加 ELIF 分支</button>
              <div class="item"><div class="ih"><span class="badge">ELSE</span><span class="hint">以上条件都不满足时</span></div></div>
            </div>
          </template>

          <!-- ═══ 代码 ═══ -->
          <template v-else-if="node.type === 'code'">
            <div class="field"><span class="lab">输入参数 <span class="sub">作为 main() 的参数</span></span>
              <div class="list">
                <div v-for="(v, i) in d.inputs" :key="i" class="row">
                  <n-input v-model:value="v.name" size="small" placeholder="参数名" class="code" style="width: 100px" />
                  <VarInput v-model="v.value" :node-id="node.id" mode="ref" class="grow" />
                  <button class="del" @click="cp(); d.inputs.splice(i, 1)">✕</button>
                </div>
                <button class="add" @click="cp(); d.inputs.push({ name: `arg${d.inputs.length + 1}`, value: '' })">＋ 添加参数</button>
              </div>
            </div>
            <div class="field"><span class="lab">Python 代码</span>
              <n-input v-model:value="d.code" type="textarea" class="code" :autosize="{ minRows: 8, maxRows: 24 }"
                       placeholder="def main(arg1):\n    return {'result': arg1}" @keydown.tab.prevent="d.code += '    '" />
              <span class="hint">隔离子进程执行，超时 {{ d.timeout || 30 }}s；print 的内容会出现在 __logs 里</span>
            </div>
            <div class="field"><span class="lab">输出变量 <span class="sub">声明 main 返回 dict 的键，供下游引用</span></span>
              <div class="list">
                <div v-for="(o, i) in d.outputs" :key="i" class="row">
                  <n-input v-model:value="o.name" size="small" placeholder="键名" class="grow code" />
                  <n-select v-model:value="o.type" :options="outTypes" size="small" style="width: 96px" />
                  <button class="del" @click="cp(); d.outputs.splice(i, 1)">✕</button>
                </div>
                <button class="add" @click="cp(); d.outputs.push({ name: '', type: 'string' })">＋ 添加输出</button>
              </div>
            </div>
          </template>

          <!-- ═══ HTTP ═══ -->
          <template v-else-if="node.type === 'http'">
            <div class="row">
              <n-select v-model:value="d.method" :options="methods" size="small" style="width: 96px" />
              <VarInput v-model="d.url" :node-id="node.id" placeholder="https://api.example.com/..." class="grow" mono />
            </div>
            <div v-for="key in ['headers', 'params']" :key="key" class="field">
              <span class="lab">{{ key === "headers" ? "请求头" : "查询参数" }}</span>
              <div class="list">
                <div v-for="(h, i) in d[key]" :key="i" class="row">
                  <n-input v-model:value="h.key" size="small" placeholder="键" class="code" style="width: 120px" />
                  <VarInput v-model="h.value" :node-id="node.id" placeholder="值" class="grow" />
                  <button class="del" @click="d[key].splice(i, 1)">✕</button>
                </div>
                <button class="add" @click="d[key].push({ key: '', value: '' })">＋ 添加</button>
              </div>
            </div>
            <div v-if="!['GET', 'HEAD'].includes(d.method)" class="field">
              <span class="lab">请求体
                <n-select v-model:value="d.body_type" size="tiny" style="width: 90px; margin-left: auto"
                          :options="[{ label: 'JSON', value: 'json' }, { label: '原始文本', value: 'raw' }]" />
              </span>
              <VarInput v-model="d.body" :node-id="node.id" :rows="5" mono placeholder='{"q": "{{start.query}}"}' />
            </div>
            <div class="row">
              <span class="hint">超时(秒)</span><n-input-number v-model:value="d.timeout" size="small" :min="1" :max="300" style="width: 100px" />
              <span class="grow" /><span class="hint">4xx/5xx 不报错</span><n-switch v-model:value="d.allow_error" size="small" />
            </div>
          </template>

          <!-- ═══ 工具 ═══ -->
          <template v-else-if="node.type === 'tool'">
            <div class="field"><span class="lab">选择工具</span>
              <n-select v-model:value="d.tool" :options="toolOptions" filterable size="small"
                        :loading="!store.toolsLoaded" placeholder="从 toolbox 中选择" @update:value="d.params = {}" />
              <span v-if="toolCard" class="hint">{{ toolCard.description }}</span>
              <span v-if="store.toolsLoaded && !store.tools.length" class="hint">未获取到工具：检查 config.yaml 的 tools 段</span>
            </div>
            <div v-for="[k, s] in toolProps" :key="k" class="field">
              <span class="lab mono">{{ k }} <span v-if="toolRequired.has(k)" class="req">*</span>
                <span class="sub">{{ s.type }}</span></span>
              <n-select v-if="s.enum" :value="d.params?.[k]" :options="s.enum.map((x: string) => ({ label: x, value: x }))"
                        size="small" @update:value="(v: any) => setToolParam(k, v)" />
              <VarInput v-else :model-value="toolParamText(k)" :node-id="node.id"
                        :rows="s.type === 'string' && /content|body|code/.test(k) ? 4 : 1"
                        :placeholder="s.description || ''"
                        @update:model-value="(v: string) => setToolParamText(k, s.type, v)" />
            </div>
          </template>

          <!-- ═══ 迭代 ═══ -->
          <template v-else-if="node.type === 'iteration'">
            <div class="field"><span class="lab">迭代列表 <span class="req">*</span></span>
              <VarInput v-model="d.input" :node-id="node.id" mode="ref" placeholder="选择一个数组变量" />
              <span class="hint">文本会按行拆分成列表。容器内用 {{ node.id }}.item / .index 取当前项</span>
            </div>
            <div class="field"><span class="lab">收集结果 <span class="sub">每一轮取这个变量，汇总为 output 列表</span></span>
              <VarInput v-model="d.output_var" :node-id="`${node.id}_start`" mode="ref" placeholder="选择容器内节点的输出" />
            </div>
            <div class="row"><n-switch v-model:value="d.parallel" size="small" /><span class="hint">并行执行</span>
              <template v-if="d.parallel"><span class="grow" /><span class="hint">并发数</span>
                <n-input-number v-model:value="d.parallel_limit" size="small" :min="1" :max="16" style="width: 90px" /></template>
            </div>
            <div class="row"><span class="hint">最多处理</span><n-input-number v-model:value="d.max_items" size="small" :min="1" :max="1000" style="width: 110px" /><span class="hint">项</span></div>
          </template>

          <!-- ═══ 循环 ═══ -->
          <template v-else-if="node.type === 'loop'">
            <div class="field"><span class="lab">最大轮次</span>
              <n-input-number v-model:value="d.max_loops" size="small" :min="1" :max="100" />
            </div>
            <div class="field"><span class="lab">终止条件 <span class="sub">每轮结束后检查，满足即退出</span></span>
              <div class="item">
                <div class="ih">
                  <button v-if="d.break_case.conditions.length > 1" class="logic"
                          @click="d.break_case.logic = d.break_case.logic === 'and' ? 'or' : 'and'">
                    {{ d.break_case.logic === "and" ? "且 AND" : "或 OR" }}</button>
                </div>
                <div v-for="(cond, k) in d.break_case.conditions" :key="k" class="list">
                  <div class="row">
                    <VarInput v-model="cond.var" :node-id="`${node.id}_start`" mode="ref" class="grow" />
                    <button class="del" @click="d.break_case.conditions.splice(k, 1)">✕</button>
                  </div>
                  <div class="row">
                    <n-select v-model:value="cond.op" :options="CONDITION_OPS" size="small" style="width: 130px" />
                    <n-input v-if="!UNARY_OPS.has(cond.op)" v-model:value="cond.value" size="small" class="grow" />
                  </div>
                </div>
                <button class="add" @click="d.break_case.conditions.push({ var: '', op: 'ge', value: '' })">＋ 添加条件</button>
              </div>
            </div>
            <div class="field"><span class="lab">循环输出</span>
              <VarInput v-model="d.output_var" :node-id="`${node.id}_start`" mode="ref" placeholder="最后一轮取的变量" />
            </div>
          </template>

          <!-- ═══ 聚合 ═══ -->
          <template v-else-if="node.type === 'aggregator'">
            <div class="field"><span class="lab">候选变量 <span class="sub">按顺序取第一个有值的</span></span>
              <div class="list">
                <div v-for="(_, i) in d.variables" :key="i" class="row">
                  <VarInput v-model="d.variables[i]" :node-id="node.id" mode="ref" class="grow" />
                  <button class="del" @click="d.variables.splice(i, 1)">✕</button>
                </div>
                <button class="add" @click="d.variables.push('')">＋ 添加变量</button>
              </div>
            </div>
          </template>

          <!-- ═══ 赋值 ═══ -->
          <template v-else-if="node.type === 'assigner'">
            <div class="list">
              <div v-for="(it, i) in d.items" :key="i" class="item">
                <div class="row">
                  <span class="hint mono">env.</span>
                  <n-input v-model:value="it.name" size="small" placeholder="变量名" class="grow code" />
                  <n-select v-model:value="it.mode" :options="assignModes" size="small" style="width: 110px" />
                  <button class="del" @click="d.items.splice(i, 1)">✕</button>
                </div>
                <VarInput v-model="it.value" :node-id="node.id" placeholder="值或 {{变量}}" />
              </div>
              <button class="add" @click="d.items.push({ name: '', mode: 'set', value: '' })">＋ 添加赋值</button>
            </div>
            <span class="hint">会话变量在整个运行内共享，循环的多轮之间也保留。下游用 {{ '{' + '{env.变量名}' + '}' }} 读取。</span>
          </template>

          <!-- ═══ 列表操作 ═══ -->
          <template v-else-if="node.type === 'list_op'">
            <div class="field"><span class="lab">输入</span><VarInput v-model="d.input" :node-id="node.id" mode="ref" /></div>
            <div class="field"><span class="lab">操作</span><n-select v-model:value="d.op" :options="listOps" size="small" /></div>
            <div v-if="['split', 'join'].includes(d.op)" class="field"><span class="lab">分隔符</span>
              <n-input v-model:value="d.sep" size="small" class="code" placeholder="\n 表示换行" /></div>
            <div v-if="d.op === 'filter'" class="field"><span class="lab">包含关键字</span>
              <VarInput v-model="d.value" :node-id="node.id" /></div>
            <div v-if="d.op === 'sort'" class="row">
              <n-input v-model:value="d.key" size="small" placeholder="按字段（可留空）" class="grow code" />
              <span class="hint">倒序</span><n-switch v-model:value="d.desc" size="small" />
            </div>
            <div v-if="['slice', 'filter', 'sort', 'unique'].includes(d.op)" class="row">
              <span class="hint">取第</span><n-input-number v-model:value="d.start" size="small" :min="0" style="width: 86px" />
              <span class="hint">到</span><n-input-number v-model:value="d.end" size="small" clearable placeholder="末尾" style="width: 96px" />
              <span class="hint">项</span>
            </div>
          </template>

          <!-- ═══ 注释 ═══ -->
          <template v-else-if="node.type === 'note'">
            <n-input v-model:value="d.text" type="textarea" :autosize="{ minRows: 5 }" />
          </template>

          <!-- ═══ 公共：输出变量 / 错误处理 / 调试 ═══ -->
          <template v-if="def.vars(d).length && !['start', 'note'].includes(node.type)">
            <div class="sep" />
            <div class="field"><span class="lab">输出变量</span>
              <div class="outvars">
                <span v-for="v in def.vars(d)" :key="v.name" class="ov" :title="v.desc">{{ v.name }}<i>{{ v.type }}</i></span>
              </div>
            </div>
          </template>
          <template v-if="!['start', 'end', 'note', 'if_else', 'aggregator', 'template', 'iteration', 'loop'].includes(node.type) && !def.hidden">
            <div class="sep" />
            <div class="row">
              <div class="field grow"><span class="lab">失败时</span>
                <n-select :value="d.on_error || 'fail'" :options="errorPolicies" size="small" @update:value="onErrorPolicy" />
              </div>
              <div class="field" style="width: 90px"><span class="lab">重试次数</span>
                <n-input-number v-model:value="d.retry" size="small" :min="0" :max="5" placeholder="0" />
              </div>
            </div>
            <div class="field"><span class="lab">节点超时（秒） <span class="sub">0 = 不限</span></span>
              <n-input-number v-model:value="d.node_timeout" size="small" :min="0" :max="3600" placeholder="0" />
            </div>
            <div v-if="node.type === 'tool'" class="field"><span class="lab">工具调用超时（秒） <span class="sub">外部工具/MCP 无响应时掐断，默认 600</span></span>
              <n-input-number v-model:value="d.tool_timeout" size="small" :min="5" :max="3600" placeholder="600" />
            </div>
          </template>
          <template v-if="canDebug">
            <div class="sep" />
            <button class="add" @click="debugOpen = !debugOpen">{{ debugOpen ? "收起" : "▶ 单独调试此节点" }}</button>
            <div v-if="debugOpen" class="debug">
              <span class="hint">为引用到的上游变量填测试值（JSON 或纯文本）</span>
              <div v-for="r in debugRefs" :key="r" class="row">
                <code class="hint mono" style="width: 130px; overflow: hidden; text-overflow: ellipsis">{{ r }}</code>
                <n-input v-model:value="debugInputs[r]" size="small" class="grow" />
              </div>
              <n-button size="small" type="primary" secondary :loading="store.debugNodeId === node.id && store.running"
                        :disabled="store.running || !!store.pendingCommand" @click="runDebug">运行此节点</n-button>
            </div>
          </template>
        </div>
      </n-tab-pane>
      <n-tab-pane name="last" tab="上次运行">
        <div class="form">
          <div v-if="!run" class="empty">这个节点还没运行过</div>
          <template v-else>
            <div v-if="run.error" class="errbox">{{ run.error }}</div>
            <div v-if="run.stream && run.status === 'running'" class="field">
              <span class="lab">实时输出</span><pre class="out">{{ run.stream }}</pre>
            </div>
            <div v-for="(v, k) in run.outputs || {}" :key="k" class="field">
              <span class="lab mono">{{ k }}</span><pre class="out">{{ pretty(v) }}</pre>
            </div>
          </template>
        </div>
      </n-tab-pane>
    </n-tabs>
  </aside>
</template>

<style scoped>
.insp {
  position: absolute; top: 12px; right: 12px; bottom: 12px; width: 400px; z-index: 12;
  border-radius: 18px; background: rgba(12, 14, 24, 0.9); display: flex; flex-direction: column;
  box-shadow: var(--shadow); animation: slide-in 0.3s var(--ease);
}
@keyframes slide-in { from { opacity: 0; transform: translateX(20px); } to { opacity: 1; transform: none; } }
header { display: flex; align-items: flex-start; gap: 11px; padding: 16px 16px 6px; }
.ic {
  width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0; display: grid; place-items: center;
  background: color-mix(in srgb, var(--c) 20%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 40%, transparent);
}
.ic svg { width: 17px; height: 17px; stroke: var(--c); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.ttl { flex: 1; min-width: 0; }
.title-input {
  width: 100%; box-sizing: border-box; border: 1px solid transparent; background: none; outline: 0;
  color: var(--text); font-size: 15px; font-weight: 600; padding: 2px 6px; margin-left: -6px; border-radius: 7px;
}
.title-input:hover, .title-input:focus { border-color: var(--line-2); background: rgba(255,255,255,0.03); }
.desc { font-size: 11.5px; color: var(--text-3); margin-top: 2px; }
.hacts { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
.hb { border: 0; background: none; padding: 5px; border-radius: 8px; cursor: pointer; }
.hb svg { width: 15px; height: 15px; stroke: var(--text-3); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.hb:hover { background: var(--glass-hover); }
.hb:hover svg { stroke: var(--text); }
.hb.danger:hover { background: rgba(248, 113, 113, 0.14); }
.hb.danger:hover svg { stroke: var(--err); }
.x { border: 0; background: none; padding: 5px; border-radius: 8px; cursor: pointer; }
.x svg { width: 15px; height: 15px; stroke: var(--text-3); stroke-width: 2; }
.x:hover { background: var(--glass-hover); }
.idrow { display: flex; align-items: center; gap: 8px; padding: 2px 16px 6px; font-size: 11px; color: var(--text-3); }
.idrow code { font-family: var(--mono); color: var(--text-2); background: rgba(255,255,255,0.04); padding: 1px 6px; border-radius: 5px; }
.rs { margin-left: auto; font-size: 11px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line); }
.rs.succeeded { color: var(--ok); border-color: rgba(52,211,153,0.4); }
.rs.failed { color: var(--err); border-color: rgba(248,113,113,0.4); }
.rs.running { color: var(--accent); border-color: rgba(143,124,255,0.4); }
.tabs { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 16px; }
.tabs :deep(.n-tabs-pane-wrapper) { flex: 1; min-height: 0; overflow-y: auto; margin: 0 -16px; padding: 0 16px; }
.form { display: flex; flex-direction: column; gap: 16px; padding: 10px 0 24px; }
.field { display: flex; flex-direction: column; gap: 7px; }
.lab { font-size: 12px; color: var(--text-2); display: flex; align-items: center; gap: 6px; }
.lab .req { color: var(--err); }
.lab .sub { color: var(--text-3); font-size: 11px; font-weight: 400; }
.mono { font-family: var(--mono); }
.row { display: flex; align-items: center; gap: 6px; }
.row > * { min-width: 0; }
.grow { flex: 1; }
.list { display: flex; flex-direction: column; gap: 7px; }
.item { padding: 10px; border-radius: 12px; border: 1px solid var(--line); background: rgba(255,255,255,0.02); display: flex; flex-direction: column; gap: 8px; }
.ih { display: flex; align-items: center; gap: 8px; font-size: 11.5px; font-weight: 600; }
.ih .badge { font: 600 10.5px var(--mono); color: var(--c); background: color-mix(in srgb, var(--c) 15%, transparent); padding: 1px 7px; border-radius: 6px; }
.del { border: 0; background: none; color: var(--text-3); cursor: pointer; padding: 3px 7px; border-radius: 6px; font-size: 12px; margin-left: auto; }
.del:hover { color: var(--err); background: rgba(248, 113, 113, 0.1); }
.add {
  border: 1px dashed var(--line-2); background: none; color: var(--text-2); border-radius: 10px;
  padding: 7px; font-size: 12px; cursor: pointer; transition: all 0.2s;
}
.add:hover { border-color: var(--c); color: var(--text); background: color-mix(in srgb, var(--c) 8%, transparent); }
.hint { font-size: 11px; color: var(--text-3); line-height: 1.55; }
.code { font-family: var(--mono) !important; }
.sep { height: 1px; background: var(--line); margin: 2px 0; }
.logic { border: 1px solid var(--line-2); background: none; color: var(--accent); font: 600 10.5px var(--mono); border-radius: 6px; padding: 1px 8px; cursor: pointer; }
.outvars { display: flex; flex-wrap: wrap; gap: 6px; }
.ov { font: 11px var(--mono); color: var(--text-2); background: rgba(255,255,255,0.04); border: 1px solid var(--line); border-radius: 7px; padding: 3px 8px; }
.ov i { font-style: normal; color: var(--accent-2); margin-left: 5px; }
.empty { padding: 30px 0; text-align: center; color: var(--text-3); font-size: 12.5px; }
.errbox { font-size: 12px; color: #fecaca; background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.3); border-radius: 10px; padding: 9px 11px; white-space: pre-wrap; word-break: break-all; }
.out { margin: 0; font: 11.5px/1.55 var(--mono); background: rgba(3,4,10,0.6); border: 1px solid var(--line); border-radius: 10px; padding: 9px 11px; white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow: auto; }
.debug { border: 1px solid var(--line); border-radius: 12px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
@media (max-width: 768px) { .insp { width: auto; left: 12px; top: auto; height: 62%; } }
</style>
