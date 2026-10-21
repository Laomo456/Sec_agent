<script setup lang="ts">
/** 工具工坊：可视化创建/编辑自建工具（HTTP / 命令 / Python 脚本）。
 *  参数逐行设计 → 自动生成 JSON Schema；实现里用 {{参数名}} 引用；右侧实时预览 tool.yaml 与模型看到的签名。 */
import { NButton, NInput, NInputNumber, NPopconfirm, NSelect, NSwitch, useMessage } from "naive-ui";
import { computed, ref, watch } from "vue";
import { createTool, deleteTool, getToolSource, tryTool, updateTool } from "../../api";
import { useToolsStore } from "../../stores/tools";

const props = defineProps<{ name: string | null }>();
const emit = defineEmits<{ (e: "saved"): void; (e: "switch", name: string | null): void }>();
const store = useToolsStore();
const msg = useMessage();

interface Param { name: string; type: string; required: boolean; description: string; enum: string; default: string }
interface KV { key: string; value: string }

const blank = () => ({
  name: "", kind: "http" as "http" | "command" | "script", description: "", category: "custom",
  danger: "readonly", timeout: 60, keywords: "", search_text: "",
  params: [{ name: "query", type: "string", required: true, description: "", enum: "", default: "" }] as Param[],
  http: { method: "GET", url: "https://", headers: [] as KV[], query: [] as KV[], body: "", pick: "", allow_error: false },
  command: { argv: "python\n-c\nprint('{{query}}')", stdin: "" },
  script: "def main(params):\n    # params 是校验过的参数 dict；print 的内容就是工具输出\n    print(params)\n",
  example: "",
});
const f = ref(blank());
const original = ref<string | null>(null);
const saving = ref(false);
const tryParams = ref("{}");
const tryResult = ref<any>(null);
const trying = ref(false);

const userTools = computed(() => store.tools.filter((t) => t.origin === "user"));
const kinds = [
  { key: "http", label: "HTTP 接口", desc: "调用一个 REST API，参数填进 URL / 请求头 / 请求体", icon: "M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" },
  { key: "command", label: "命令行", desc: "执行白名单内的程序，参数填进命令行（不经 shell）", icon: "M4 17l6-5-6-5M12 19h8" },
  { key: "script", label: "Python 脚本", desc: "写一个 main(params) 函数，print 的内容即输出", icon: "M8 7l-5 5 5 5M16 7l5 5-5 5" },
] as const;
const types = ["string", "integer", "number", "boolean", "array", "object"].map((v) => ({ label: v, value: v }));
const dangers = [
  { label: "只读 —— 只查询不修改", value: "readonly" },
  { label: "有副作用 —— 会写入/发送/执行", value: "intrusive" },
  { label: "破坏性 —— 可能删除或不可逆", value: "destructive" },
];

/* ── 载入已有工具 ── */
async function load(name: string | null) {
  tryResult.value = null;
  if (!name) { f.value = blank(); original.value = null; syncTry(); return; }
  try {
    const m = await getToolSource(name);
    const props0 = m.schema?.properties || {};
    const req = new Set(m.schema?.required || []);
    const toKV = (o: any) => Object.entries(o || {}).map(([key, value]) => ({ key, value: String(value) }));
    f.value = {
      ...blank(), name: m.name, kind: m.kind || "script", description: m.description || "",
      category: m.category || "custom", danger: m.danger || "intrusive", timeout: m.timeout || 60,
      keywords: (m.keywords || []).join(", "), search_text: m.search_text || "",
      params: Object.entries<any>(props0).map(([n, s]) => ({
        name: n, type: s.type || "string", required: req.has(n), description: s.description || "",
        enum: (s.enum || []).join(", "), default: s.default != null ? String(s.default) : "" })),
      example: m.example && Object.keys(m.example).length ? JSON.stringify(m.example) : "",
    };
    if (m.http) f.value.http = { ...f.value.http, ...m.http, headers: toKV(m.http.headers), query: toKV(m.http.query),
      body: m.http.body == null ? "" : typeof m.http.body === "string" ? m.http.body : JSON.stringify(m.http.body, null, 2) };
    if (m.command) f.value.command = { argv: (m.command.argv || []).join("\n"), stdin: m.command.stdin || "" };
    if (m.script) f.value.script = m.script;
    original.value = m.name;
    syncTry();
  } catch (e: any) { msg.error(`读取失败：${e.message}`); }
}
watch(() => props.name, load, { immediate: true });

/* ── 表单 → 工具定义 ── */
const schema = computed(() => {
  const properties: Record<string, any> = {};
  for (const p of f.value.params) {
    if (!p.name.trim()) continue;
    const s: any = { type: p.type };
    if (p.description) s.description = p.description;
    if (p.type === "array") s.items = { type: "string" };
    if (p.enum.trim()) s.enum = p.enum.split(",").map((x) => x.trim()).filter(Boolean);
    if (p.default !== "") s.default = p.type === "integer" || p.type === "number" ? Number(p.default)
      : p.type === "boolean" ? p.default === "true" : p.default;
    properties[p.name.trim()] = s;
  }
  const required = f.value.params.filter((p) => p.required && p.name.trim()).map((p) => p.name.trim());
  return { type: "object", properties, ...(required.length ? { required } : {}) };
});
const kvObj = (rows: KV[]) => Object.fromEntries(rows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.value]));
const spec = computed(() => {
  const x = f.value;
  let example: any = {};
  try { example = x.example.trim() ? JSON.parse(x.example) : {}; } catch { example = {}; }
  const s: any = {
    name: x.name.trim(), kind: x.kind, description: x.description.trim(), category: x.category.trim() || "custom",
    danger: x.danger, timeout: x.timeout, keywords: x.keywords.split(/[,，]/).map((k) => k.trim()).filter(Boolean),
    search_text: x.search_text, schema: schema.value, example,
  };
  if (x.kind === "http") {
    let body: any = x.http.body.trim();
    if (body) { try { body = JSON.parse(body); } catch { /* 原样文本 */ } }
    s.http = { method: x.http.method, url: x.http.url.trim(), headers: kvObj(x.http.headers), query: kvObj(x.http.query),
      ...(body ? { body } : {}), ...(x.http.pick.trim() ? { pick: x.http.pick.trim() } : {}),
      ...(x.http.allow_error ? { allow_error: true } : {}) };
  } else if (x.kind === "command") {
    s.command = { argv: x.command.argv.split("\n").map((a) => a.trim()).filter(Boolean),
      ...(x.command.stdin.trim() ? { stdin: x.command.stdin } : {}) };
  } else {
    s.script = x.script;
  }
  return s;
});

/** 模型看到的签名（与 toolbox registry.signature 同规则） */
const sig = computed(() => {
  const short: Record<string, string> = { string: "str", integer: "int", number: "num", boolean: "bool", object: "obj" };
  const parts = f.value.params.filter((p) => p.name.trim()).map((p) => {
    const t = p.enum.trim() ? p.enum.split(",").map((e) => e.trim()).join("|") : p.type === "array" ? "str[]" : short[p.type] || p.type;
    return `${p.name.trim()}${p.required ? "" : "?"}: ${t}`;
  });
  return `${f.value.name || "tool_name"}(${parts.join(", ")})`;
});

/** 实现里引用了但没声明的参数、声明了但没用到的参数 */
const refs = computed(() => {
  const text = JSON.stringify(f.value.kind === "http" ? f.value.http : f.value.kind === "command" ? f.value.command : {});
  return new Set([...text.matchAll(/\{\{\s*(\w+)/g)].map((m) => m[1]));
});
const declared = computed(() => new Set(f.value.params.map((p) => p.name.trim()).filter(Boolean)));
const undeclared = computed(() => [...refs.value].filter((r) => !declared.value.has(r)));
const unused = computed(() => f.value.kind === "script" ? [] : [...declared.value].filter((d) => !refs.value.has(d)));

const problems = computed(() => {
  const p: string[] = [];
  if (!/^[a-z][a-z0-9_]{1,39}$/.test(f.value.name)) p.push("工具名：小写字母开头，只含小写字母/数字/下划线");
  if (!f.value.description.trim()) p.push("描述不能为空");
  if (f.value.kind === "http" && !/^https?:\/\/./.test(f.value.http.url)) p.push("URL 需以 http(s):// 开头");
  if (f.value.kind === "script" && !f.value.script.includes("def main")) p.push("脚本需定义 def main(params)");
  if (undeclared.value.length) p.push(`引用了未声明的参数：${undeclared.value.join("、")}`);
  const dup = f.value.params.map((x) => x.name.trim()).filter((n, i, a) => n && a.indexOf(n) !== i);
  if (dup.length) p.push(`参数重名：${dup.join("、")}`);
  return p;
});

/* tool.yaml 预览（简易 YAML 序列化，够看即可） */
function yaml(v: any, ind = 0): string {
  const pad = "  ".repeat(ind);
  if (Array.isArray(v)) return v.length ? v.map((x) => `\n${pad}- ${typeof x === "object" ? yaml(x, ind + 1).trimStart() : JSON.stringify(x)}`).join("") : " []";
  if (v && typeof v === "object") {
    const ks = Object.keys(v);
    return ks.length ? ks.map((k) => `\n${pad}${k}:${typeof v[k] === "object" && v[k] !== null ? yaml(v[k], ind + 1) : " " + JSON.stringify(v[k])}`).join("") : " {}";
  }
  return ` ${JSON.stringify(v)}`;
}
const yamlPreview = computed(() => {
  const { script, ...rest } = spec.value;
  return (`origin: "user"` + yaml(rest)).trim() + (script ? `\n# + run.py（${script.split("\n").length} 行）` : "");
});

function syncTry() {
  const o: Record<string, any> = {};
  for (const p of f.value.params) {
    if (!p.name.trim() || !p.required) continue;
    o[p.name.trim()] = p.type === "integer" || p.type === "number" ? 0 : p.type === "boolean" ? false : p.type === "array" ? [] : "";
  }
  tryParams.value = JSON.stringify(o, null, 2);
}
function addParam() { f.value.params.push({ name: "", type: "string", required: false, description: "", enum: "", default: "" }); }
function insertRef(name: string, target: "url" | "body" | "argv") {
  const t = `{{${name}}}`;
  if (target === "url") f.value.http.url += t;
  else if (target === "body") f.value.http.body += t;
  else f.value.command.argv += (f.value.command.argv.endsWith("\n") || !f.value.command.argv ? "" : "\n") + t;
}

async function save() {
  if (problems.value.length) { msg.warning(problems.value[0]); return; }
  saving.value = true;
  try {
    const r = original.value ? await updateTool(original.value, spec.value) : await createTool(spec.value);
    msg.success(original.value ? "已更新，热加载立即生效" : "已创建，热加载立即生效");
    original.value = r.name;
    emit("saved");
    emit("switch", r.name);
  } catch (e: any) { msg.error(e.message); }
  finally { saving.value = false; }
}
async function runTry() {
  if (!original.value) { msg.info("先保存再试运行"); return; }
  let params: any;
  try { params = JSON.parse(tryParams.value || "{}"); } catch { msg.error("参数不是合法 JSON"); return; }
  trying.value = true;
  try { tryResult.value = await tryTool(original.value, params); }
  catch (e: any) { tryResult.value = { status: "error", detail: e.message }; }
  finally { trying.value = false; }
}
async function remove() {
  if (!original.value) return;
  try {
    await deleteTool(original.value);
    msg.success("已删除");
    emit("saved");
    emit("switch", null);
  } catch (e: any) { msg.error(e.message); }
}
/** 模板里不能直接写双花括号字面量（会被当插值解析），统一放这里 */
const OPEN = "{" + "{", CLOSE = "}" + "}";
const refLabel = (n: string) => OPEN + n + CLOSE;
const PH = { kv: `值，可用 ${OPEN}参数${CLOSE}`, body: `{"q": "${OPEN}query${CLOSE}", "limit": "${OPEN}limit|10${CLOSE}"}` };
const tryText = computed(() => {
  const r = tryResult.value;
  if (!r) return "";
  return r.output ?? r.output_head ?? r.detail ?? JSON.stringify(r, null, 2);
});
</script>
<template>
  <div class="ws">
    <!-- 左：我的工具 -->
    <aside class="mine">
      <button class="nb" :class="{ on: !original && !name }" @click="emit('switch', null)">＋ 新建工具</button>
      <div class="mt">我的工具 · {{ userTools.length }}</div>
      <button v-for="t in userTools" :key="t.name" class="mi" :class="{ on: original === t.name }" @click="emit('switch', t.name)">
        <code>{{ t.name }}</code><span>{{ t.description }}</span>
      </button>
      <p v-if="!userTools.length" class="hint">还没有自建工具。内置工具不能在这里修改，但可以在工具库里禁用。</p>
    </aside>

    <!-- 中：表单 -->
    <div class="form">
      <section class="blk">
        <div class="bt"><span class="n">1</span>实现方式</div>
        <div class="kinds">
          <button v-for="k in kinds" :key="k.key" class="kd" :class="{ on: f.kind === k.key }" @click="f.kind = k.key">
            <svg viewBox="0 0 24 24"><path :d="k.icon" /></svg>
            <b>{{ k.label }}</b><span>{{ k.desc }}</span>
          </button>
        </div>
      </section>

      <section class="blk">
        <div class="bt"><span class="n">2</span>基本信息</div>
        <div class="g2">
          <label><span class="lt">工具名 <i>*</i></span><n-input v-model:value="f.name" placeholder="get_weather" class="mono" /></label>
          <label><span class="lt">分类</span><n-input v-model:value="f.category" placeholder="custom" /></label>
        </div>
        <label><span class="lt">描述 <i>*</i><span class="sub">一句话，模型靠它决定用不用</span></span>
          <n-input v-model:value="f.description" placeholder="查询城市的实时天气" maxlength="120" show-count /></label>
        <div class="g2">
          <label><span class="lt">副作用等级</span><n-select v-model:value="f.danger" :options="dangers" /></label>
          <label><span class="lt">超时（秒）</span><n-input-number v-model:value="f.timeout" :min="1" :max="600" /></label>
        </div>
        <label><span class="lt">检索关键词<span class="sub">逗号分隔，中英都写上，两段式检索靠它命中</span></span>
          <n-input v-model:value="f.keywords" placeholder="天气, 气温, weather, 下雨" /></label>
        <label><span class="lt">用法要点<span class="sub">会进入工具说明，写清楚什么时候用、有什么坑</span></span>
          <n-input v-model:value="f.search_text" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }"
                   placeholder="例如：城市名用中文或拼音都行；接口有频率限制，同一城市不要重复查" /></label>
      </section>

      <section class="blk">
        <div class="bt"><span class="n">3</span>参数<span class="sub">自动生成 JSON Schema，调用时先校验再执行</span></div>
        <TransitionGroup name="row" tag="div" class="params">
          <div v-for="(p, i) in f.params" :key="i" class="pr">
            <n-input v-model:value="p.name" size="small" placeholder="参数名" class="mono pn" />
            <n-select v-model:value="p.type" :options="types" size="small" class="pt" />
            <n-input v-model:value="p.description" size="small" placeholder="说明" class="pd" />
            <label class="rq"><n-switch v-model:value="p.required" size="small" />必填</label>
            <button class="del" aria-label="删除参数" @click="f.params.splice(i, 1)">✕</button>
            <n-input v-if="p.type === 'string'" v-model:value="p.enum" size="small" placeholder="可选值（逗号分隔，留空不限）" class="pe" />
            <n-input v-model:value="p.default" size="small" placeholder="默认值" class="pdf" />
          </div>
        </TransitionGroup>
        <button class="add" @click="addParam">＋ 添加参数</button>
      </section>

      <section class="blk">
        <div class="bt"><span class="n">4</span>实现<span class="sub">用 <code v-pre>{{参数名}}</code> 引用参数，<code v-pre>{{参数名|默认值}}</code> 带默认值</span></div>
        <div v-if="declared.size" class="refs">插入：
          <button v-for="n in declared" :key="n" @click="insertRef(n, f.kind === 'command' ? 'argv' : 'url')">{{ refLabel(n) }}</button>
        </div>

        <template v-if="f.kind === 'http'">
          <div class="url">
            <n-select v-model:value="f.http.method" size="small" style="width: 100px"
                      :options="['GET','POST','PUT','PATCH','DELETE'].map((m) => ({ label: m, value: m }))" />
            <n-input v-model:value="f.http.url" size="small" class="mono" placeholder="https://api.example.com/v1/search" />
          </div>
          <div v-for="key in (['query', 'headers'] as const)" :key="key" class="kvs">
            <div class="kt">{{ key === "query" ? "查询参数" : "请求头" }}</div>
            <div v-for="(r, i) in f.http[key]" :key="i" class="kv">
              <n-input v-model:value="r.key" size="small" placeholder="键" class="mono" />
              <n-input v-model:value="r.value" size="small" :placeholder="PH.kv" class="mono" />
              <button class="del" @click="f.http[key].splice(i, 1)">✕</button>
            </div>
            <button class="add sm" @click="f.http[key].push({ key: '', value: '' })">＋</button>
          </div>
          <label v-if="f.http.method !== 'GET'"><span class="lt">请求体<span class="sub">JSON 会按 JSON 发送；整个值是 <code v-pre>{{x}}</code> 时保留原类型</span></span>
            <n-input v-model:value="f.http.body" type="textarea" class="mono" :autosize="{ minRows: 3, maxRows: 10 }"
                     :placeholder="PH.body" /></label>
          <div class="g2">
            <label><span class="lt">只取响应里的字段<span class="sub">如 data.items，省 token</span></span><n-input v-model:value="f.http.pick" size="small" class="mono" /></label>
            <label class="sw"><n-switch v-model:value="f.http.allow_error" size="small" />4xx/5xx 不算失败</label>
          </div>
        </template>

        <template v-else-if="f.kind === 'command'">
          <label><span class="lt">命令行（每行一个参数，不经 shell）<span class="sub">第一行是程序名，需在白名单内；替换后为空的行自动省略</span></span>
            <n-input v-model:value="f.command.argv" type="textarea" class="mono" :autosize="{ minRows: 4, maxRows: 12 }" /></label>
          <div class="hint">白名单：python pip curl ping nslookup ipconfig whoami echo where findstr git node npm tar powershell。
            工作目录为会话工作区。</div>
          <label><span class="lt">标准输入（可选）</span><n-input v-model:value="f.command.stdin" type="textarea" class="mono" :autosize="{ minRows: 2, maxRows: 6 }" /></label>
        </template>

        <template v-else>
          <label><span class="lt">run.py<span class="sub">子进程隔离执行，超时 {{ f.timeout }}s</span></span>
            <n-input v-model:value="f.script" type="textarea" class="mono code" :autosize="{ minRows: 10, maxRows: 30 }"
                     @keydown.tab.prevent="f.script += '    '" /></label>
        </template>
        <label><span class="lt">调用示例（JSON，可选）<span class="sub">进工具卡，模型照抄最不容易错</span></span>
          <n-input v-model:value="f.example" size="small" class="mono" placeholder='{"query": "北京"}' /></label>
      </section>
    </div>

    <!-- 右：预览 / 试运行 -->
    <aside class="side">
      <div class="pv glass">
        <div class="pt2">模型看到的签名</div>
        <code class="sig">{{ sig }}</code>
        <div v-if="problems.length" class="probs">
          <div v-for="p in problems" :key="p">· {{ p }}</div>
        </div>
        <div v-else class="okk">✓ 定义有效</div>
        <div v-if="unused.length" class="soft">声明了但实现里没用到：{{ unused.join("、") }}</div>
        <div class="acts">
          <n-button type="primary" block :loading="saving" :disabled="problems.length > 0" @click="save">
            {{ original ? "保存修改" : "创建工具" }}</n-button>
          <n-popconfirm v-if="original" @positive-click="remove">
            <template #trigger><n-button quaternary type="error" block>删除工具</n-button></template>
            删除 {{ original }}？会同时从所有工具集里移除。
          </n-popconfirm>
        </div>
      </div>

      <div class="pv glass">
        <div class="pt2">试运行 <span v-if="!original" class="sub">保存后可用</span></div>
        <n-input v-model:value="tryParams" type="textarea" class="mono" :autosize="{ minRows: 3, maxRows: 10 }" :disabled="!original" />
        <n-button secondary block :loading="trying" :disabled="!original" @click="runTry">▶ 运行</n-button>
        <Transition name="fade">
          <div v-if="tryResult" class="res" :class="tryResult.status">
            <span class="st">{{ tryResult.status }}</span>
            <span v-if="tryResult.field" class="soft"> 字段 {{ tryResult.field }}：{{ tryResult.detail }}</span>
            <pre>{{ tryText }}</pre>
          </div>
        </Transition>
      </div>

      <details class="pv glass yml">
        <summary>tool.yaml 预览</summary>
        <pre>{{ yamlPreview }}</pre>
      </details>
    </aside>
  </div>
</template>

<style scoped>
.ws { display: grid; grid-template-columns: 210px minmax(0, 1fr) 330px; gap: 18px; max-width: 1400px; align-items: start; }
.mine { display: flex; flex-direction: column; gap: 4px; position: sticky; top: 0; }
.nb { border: 1px solid rgba(143,124,255,0.4); background: linear-gradient(135deg, rgba(143,124,255,0.2), rgba(45,212,191,0.06));
  color: var(--text); border-radius: 12px; padding: 9px; font-size: 12.5px; cursor: pointer; margin-bottom: 10px; }
.nb.on { box-shadow: var(--accent-glow); }
.mt { font-size: 10.5px; color: var(--text-3); font-weight: 600; letter-spacing: 1px; padding: 4px 6px; }
.mi { display: flex; flex-direction: column; gap: 2px; text-align: left; border: 1px solid transparent; background: none;
  padding: 7px 10px; border-radius: 10px; cursor: pointer; color: var(--text); }
.mi:hover { background: var(--glass-hover); }
.mi.on { border-color: rgba(143,124,255,0.45); background: var(--accent-dim); }
.mi code { font: 12px var(--mono); }
.mi span { font-size: 11px; color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.form { display: flex; flex-direction: column; gap: 14px; }
.blk { border-radius: 18px; padding: 18px; border: 1px solid var(--line); background: var(--glass); backdrop-filter: var(--blur);
  display: flex; flex-direction: column; gap: 12px; animation: rise 0.35s var(--ease) both; }
.blk:nth-child(2) { animation-delay: 0.05s; } .blk:nth-child(3) { animation-delay: 0.1s; } .blk:nth-child(4) { animation-delay: 0.15s; }
.bt { display: flex; align-items: center; gap: 9px; font-size: 14px; font-weight: 600; }
.bt .n { font: 600 11px var(--mono); color: var(--accent); background: var(--accent-dim); border-radius: 6px; padding: 1px 7px; }
.sub { font-size: 11px; color: var(--text-3); font-weight: 400; margin-left: 4px; }
.sub code, .refs button { font-family: var(--mono); }
label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-2); }
.lt { display: flex; align-items: baseline; flex-wrap: wrap; gap: 3px; }
label i { color: var(--err); font-style: normal; }
label.sw { flex-direction: row; align-items: center; gap: 8px; align-self: end; padding-bottom: 6px; }
.g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.mono :deep(input), .mono :deep(textarea) { font-family: var(--mono) !important; font-size: 12px !important; }
.code :deep(textarea) { line-height: 1.6 !important; }
.kinds { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.kd { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; text-align: left; padding: 13px; border-radius: 14px;
  border: 1px solid var(--line); background: rgba(255,255,255,0.02); color: var(--text); cursor: pointer; transition: all 0.2s; }
.kd:hover { border-color: var(--line-2); }
.kd.on { border-color: var(--accent); background: var(--accent-dim); box-shadow: 0 0 0 3px rgba(143,124,255,0.14); }
.kd svg { width: 20px; height: 20px; stroke: var(--accent); stroke-width: 1.9; fill: none; stroke-linecap: round; stroke-linejoin: round; margin-bottom: 4px; }
.kd b { font-size: 13px; }
.kd span { font-size: 11px; color: var(--text-3); line-height: 1.5; }
.params { display: flex; flex-direction: column; gap: 8px; }
.pr { display: grid; grid-template-columns: 130px 100px 1fr auto auto; gap: 7px; align-items: center;
  padding: 9px; border-radius: 12px; border: 1px solid var(--line); background: rgba(255,255,255,0.015); }
.pe { grid-column: 1 / 4; }
.pdf { grid-column: 4 / 6; }
.rq { flex-direction: row; align-items: center; gap: 5px; font-size: 11px; white-space: nowrap; }
.del { border: 0; background: none; color: var(--text-3); cursor: pointer; padding: 3px 7px; border-radius: 6px; }
.del:hover { color: var(--err); background: rgba(248,113,113,0.1); }
.add { border: 1px dashed var(--line-2); background: none; color: var(--text-2); border-radius: 10px; padding: 7px; font-size: 12px; cursor: pointer; }
.add:hover { border-color: var(--accent); color: var(--text); }
.add.sm { padding: 2px; width: 34px; }
.refs { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 11px; color: var(--text-3); }
.refs button { border: 1px solid rgba(143,124,255,0.3); background: var(--accent-dim); color: #d6d0ff; border-radius: 6px; padding: 1px 7px; font-size: 11px; cursor: pointer; }
.url { display: flex; gap: 7px; }
.url > :last-child { flex: 1; }
.kvs { display: flex; flex-direction: column; gap: 5px; }
.kt { font-size: 12px; color: var(--text-2); }
.kv { display: grid; grid-template-columns: 1fr 2fr auto; gap: 6px; }
.hint { font-size: 11.5px; color: var(--text-3); line-height: 1.6; }

.side { display: flex; flex-direction: column; gap: 12px; position: sticky; top: 0; }
.pv { border-radius: 18px; padding: 16px; display: flex; flex-direction: column; gap: 10px; background: rgba(12,14,24,0.85); }
.pt2 { font-size: 12.5px; font-weight: 600; }
.sig { font: 12px var(--mono); color: #d6d0ff; background: rgba(143,124,255,0.1); border: 1px solid rgba(143,124,255,0.28);
  border-radius: 10px; padding: 9px 11px; word-break: break-all; }
.probs { font-size: 12px; color: var(--warn); line-height: 1.6; }
.okk { font-size: 12px; color: var(--ok); }
.soft { font-size: 11.5px; color: var(--text-3); }
.acts { display: flex; flex-direction: column; gap: 6px; }
pre { margin: 0; font: 11.5px/1.55 var(--mono); background: rgba(3,4,10,0.6); border: 1px solid var(--line); border-radius: 10px;
  padding: 9px 11px; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow: auto; }
.res { display: flex; flex-direction: column; gap: 6px; }
.st { font: 600 11px var(--mono); align-self: flex-start; padding: 1px 8px; border-radius: 6px; background: rgba(255,255,255,0.06); }
.res.ok .st { color: var(--ok); }
.res:not(.ok) .st { color: var(--err); }
.yml summary { font-size: 12.5px; font-weight: 600; cursor: pointer; }
.row-enter-active, .row-leave-active { transition: all 0.25s var(--ease); }
.row-enter-from, .row-leave-to { opacity: 0; transform: translateY(-6px); }
.fade-enter-active { transition: opacity 0.2s; }
.fade-enter-from { opacity: 0; }
@media (max-width: 1250px) { .ws { grid-template-columns: 1fr 320px; } .mine { grid-column: 1 / -1; position: static; flex-direction: row; flex-wrap: wrap; } .mine .mt, .mine .hint { display: none; } }
@media (max-width: 860px) { .ws { grid-template-columns: 1fr; } .side { position: static; } .kinds, .g2 { grid-template-columns: 1fr; } .pr { grid-template-columns: 1fr 1fr; } .pe, .pdf { grid-column: auto; } }
</style>