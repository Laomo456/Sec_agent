<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { NButton, NInput, NInputNumber, NModal, NSelect, NSwitch, useMessage } from "naive-ui";
import { getSettings, getSkill, saveSettings } from "../api";
import { useSkillsStore } from "../stores/skills";

const data = ref<any>({ model: {}, agent: {}, server: {} });
const dirty = ref(false);
const saving = ref(false);
const loaded = ref(false);
const msg = useMessage();
const skills = useSkillsStore();
skills.load();

type Provider = { label: string; value: string; baseUrl?: string; model?: string };
const providers: Provider[] = [
  { label: "自定义（手动填写）", value: "custom" },
  { label: "DeepSeek", value: "deepseek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { label: "智谱 GLM", value: "zhipu", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-plus" },
  { label: "阿里通义 Qwen", value: "qwen", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-max" },
  { label: "Moonshot Kimi", value: "moonshot", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-32k" },
  { label: "本地 Ollama", value: "ollama", baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:7b" },
];
const surfaces = [
  { label: "全量直连 —— 全部工具直接进模型工具表，一轮直达", value: "native" },
  { label: "两段式检索 —— 先 search_tools 再 run_tool，工具再多上下文也不涨", value: "meta" },
];
const exposed = computed(() => !["127.0.0.1", "localhost"].includes(data.value.server?.host));

onMounted(async () => {
  data.value = await getSettings();
  loaded.value = true;
  // 模型档案的操作全部即时生效，未保存标记只跟踪智能体/网关两卡
  watch(() => [data.value.agent, data.value.server], () => { dirty.value = true; }, { deep: true });
});

/* ── 模型档案（CCswitch 式：切换/保存/删除即时生效） ── */
const profileOpts = computed(() =>
  (data.value.model?.profiles || []).map((p: any) => ({ label: `${p.id} · ${p.model || "未命名模型"}`, value: p.id })));
const failoverOpts = computed(() =>
  profileOpts.value.filter((o: any) => o.value !== data.value.model?.active));

async function patchModel(patch: Record<string, any>) {
  saving.value = true;
  try {
    const resp = await saveSettings({ model: patch });
    data.value.model = resp.model;              // 只回填 model，别冲掉未保存的 agent/server 编辑
    msg.success("模型设置已生效（对新任务与工作流立即生效）");
  } catch (e: any) {
    msg.error(`操作失败：${e.message}`);
  } finally {
    saving.value = false;
  }
}

const editing = ref<any>(null);                  // null = 弹层关闭
const eProvider = ref("custom");
function editProfile(p: any | null) {
  editing.value = p
    ? { ...p, _new: false, api_key: "" }         // 编辑：密钥留空 = 保留原值
    : { _new: true, id: "", base_url: "", api_key: "", model: "", max_tokens: 4000, temperature: 0.6, timeout_s: 600, stream: true };
  eProvider.value = "custom";
}
function applyEProvider(v: string) {
  const pr = providers.find((x) => x.value === v);
  if (!pr?.baseUrl) return;
  editing.value.base_url = pr.baseUrl;
  editing.value.model = pr.model;
}
async function submitProfile() {
  const e = editing.value;
  if (!e.id.trim()) { msg.error("档案名必填"); return; }
  if (!e.base_url?.trim() || !e.model?.trim()) { msg.error("Base URL 与模型名称必填"); return; }
  await patchModel({ save_profile: {
    id: e.id.trim(), base_url: e.base_url.trim(), model: e.model.trim(),
    api_key: e.api_key.trim(), max_tokens: e.max_tokens, temperature: e.temperature,
    timeout_s: e.timeout_s, stream: e.stream,
  } });
  editing.value = null;
}
async function removeProfile(id: string) {
  await patchModel({ delete_profile: id });
}

/* ── 技能库（Skill 栏）：对话里勾选注入，这里增删改 ── */
const skEditing = ref<any>(null);                // null = 弹层关闭
const skSaving = ref(false);
function editSkill(s: any | null) {
  skEditing.value = s ? { name: s.name, description: s.description || "", content: "" , _loaded: false, _desc: s.description || "" }
                      : { name: "", description: "", content: "", _new: true, _loaded: true };
  const editor = skEditing.value;
  if (s) getSkill(s.name).then((d) => {
    if (skEditing.value !== editor) return;
    editor.content = d.content;
    editor._loaded = true;
  }).catch(() => {
    if (skEditing.value !== editor) return;
    skEditing.value = null;
    msg.error("读取技能失败");
  });
}
async function submitSkill() {
  const e = skEditing.value;
  if (!e?._loaded || skSaving.value) return;
  if (!e.name.trim()) { msg.error("技能名必填（字母数字-_）"); return; }
  skSaving.value = true;
  try {
    await skills.create(e.name.trim(), e.description, e.content);
    msg.success(`技能 ${e.name.trim()} 已保存`);
    if (skEditing.value === e) skEditing.value = null;
  } catch (err: any) {
    msg.error(`保存失败：${err.message}`);
  } finally {
    skSaving.value = false;
  }
}
async function removeSkill(name: string) {
  try {
    await skills.remove(name);
    msg.success(`技能 ${name} 已删除`);
  } catch (err: any) {
    msg.error(`删除失败：${err.message}`);
  }
}

/* ── 智能体 / 网关：仍走保存按钮 ── */
async function save() {
  saving.value = true;
  try {
    const resp = await saveSettings({ agent: data.value.agent, server: data.value.server });
    data.value = { ...resp, agent: data.value.agent, server: data.value.server };
    dirty.value = false;
    msg.success("已保存：对新任务立即生效，监听地址/端口需重启服务");
  } catch (e: any) {
    msg.error(`保存失败：${e.message}`);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="settings">
    <header>
      <div>
        <span class="eyebrow">SETTINGS</span>
        <h1>系统设置</h1>
        <p>配置模型档案、智能体行为与网关参数。</p>
      </div>
      <Transition name="pop">
        <div v-if="dirty" class="dirty"><span class="dot" />有未保存的更改</div>
      </Transition>
      <n-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存更改</n-button>
    </header>

    <div v-if="loaded" class="grid">
      <article class="card glass" style="--d: 0s">
        <h2><span class="n">01</span>模型档案</h2>
        <label>当前使用
          <n-select :value="data.model.active" :options="profileOpts" :loading="saving"
                    @update:value="(v: string) => patchModel({ active: v })" />
          <span class="hint">对话与未指定档案的工作流节点都用当前档案；失败时按下方顺序自动切换</span>
        </label>
        <div class="plist">
          <div v-for="p in data.model.profiles || []" :key="p.id" class="prow"
               :class="{ on: p.id === data.model.active }">
            <div class="pmeta">
              <span class="pid">{{ p.id }}<i v-if="p.id === data.model.active">使用中</i></span>
              <span class="pmodel">{{ p.model || "（未设置模型名）" }}</span>
              <span class="purl">{{ p.base_url }}</span>
            </div>
            <div class="pacts">
              <button class="pbtn" @click="editProfile(p)">编辑</button>
              <button class="pbtn" :disabled="p.id === data.model.active"
                      @click="patchModel({ active: p.id })">设为当前</button>
              <button class="pbtn danger" :disabled="(data.model.profiles || []).length <= 1"
                      @click="removeProfile(p.id)">删除</button>
            </div>
          </div>
        </div>
        <button class="add" @click="editProfile(null)">＋ 新增模型档案</button>
        <label>故障切换顺序 <span class="hint">当前档案请求失败时按序尝试</span>
          <n-select :value="data.model.failover || []" multiple :options="failoverOpts"
                    placeholder="不切换（无备胎）" @update:value="(v: string[]) => patchModel({ failover: v })" />
        </label>

        <n-modal :show="!!editing" preset="card" class="pmodal" :title="editing?._new ? '新增模型档案' : `编辑档案 · ${editing?.id}`"
                 :mask-closable="true" @update:show="(s: boolean) => { if (!s) editing = null; }">
          <div v-if="editing" class="eform">
            <label>预设服务<n-select v-model:value="eProvider" :options="providers" size="small"
                      @update:value="applyEProvider" /></label>
            <div class="two">
              <label>档案名<n-input v-model:value="editing.id" size="small" :disabled="!editing._new"
                        placeholder="如 deepseek / glm / local" /></label>
              <label>模型名称<n-input v-model:value="editing.model" size="small" placeholder="deepseek-chat" /></label>
            </div>
            <label>API Base URL<n-input v-model:value="editing.base_url" size="small" placeholder="https://…/v1" /></label>
            <label>API Key
              <n-input v-model:value="editing.api_key" type="password" show-password-on="click" size="small"
                       :placeholder="editing._new ? 'sk-…' : '留空则保留当前密钥'" />
            </label>
            <div class="two">
              <label>最大输出 Token<n-input-number v-model:value="editing.max_tokens" size="small" :min="256" :max="128000" /></label>
              <label>温度<n-input-number v-model:value="editing.temperature" size="small" :min="0" :max="2" :step="0.1" /></label>
            </div>
            <div class="two">
              <label>请求超时（秒）<n-input-number v-model:value="editing.timeout_s" size="small" :min="10" :max="3600" /></label>
              <label class="sw">流式输出<n-switch v-model:value="editing.stream" size="small" /></label>
            </div>
            <div class="eacts">
              <n-button size="small" quaternary @click="editing = null">取消</n-button>
              <n-button size="small" type="primary" :loading="saving" @click="submitProfile">保存档案</n-button>
            </div>
          </div>
        </n-modal>
      </article>

      <article class="card glass" style="--d: 0.08s">
        <h2><span class="n">02</span>智能体行为</h2>
        <div class="two">
          <label>单任务最大轮次<n-input-number v-model:value="data.agent.max_iterations" :min="1" :max="200" /></label>
          <label>记忆预算（估算 token）<n-input-number v-model:value="data.agent.context_budget_est" :min="1000" :max="200000" :step="1000" /></label>
        </div>
        <label>自定义系统指令
          <n-input v-model:value="data.agent.system_prompt" type="textarea" :autosize="{ minRows: 4, maxRows: 10 }"
                   placeholder="追加在内置指令之后（不会覆盖工具使用规则）；留空只用内置默认" />
        </label>
        <label>工具呈现方式<n-select v-model:value="data.agent.tool_surface" :options="surfaces" /></label>
        <label class="sw">兼容正文工具调用<n-switch v-model:value="data.agent.tool_call_compat" /></label>
      </article>

      <article class="card glass" style="--d: 0.16s">
        <h2><span class="n">03</span>网关</h2>
        <div class="two">
          <label>监听地址<n-input v-model:value="data.server.host" /></label>
          <label>端口<n-input-number v-model:value="data.server.port" :min="1" :max="65535" /></label>
        </div>
        <div v-if="exposed" class="warn" role="alert">
          ⚠ 监听非本机地址时，同一网络内任何人都能驱动智能体执行命令、读写文件（网关没有鉴权）。
          仅在可信网络中使用。
        </div>
      </article>

      <article class="card glass wide" style="--d: 0.2s">
        <h2><span class="n">04</span>技能库</h2>
        <p class="hint" style="margin: 0 0 10px">
          技能 = 勾选后随消息注入的作战手册（对话输入框下方勾选，注入为当次任务的附加指令）。
          正文建议写成<strong>索引式</strong>：流程与判断标准写全，长细节放参考文件、只留路径。
          也可以直接把 <code>技能名/SKILL.md</code> 目录丢进 <code>gateway/skills/</code>，刷新即出现。
        </p>
        <div class="plist">
          <div v-for="s in skills.skills" :key="s.name" class="prow">
            <div class="pmeta">
              <span class="pid">{{ s.name }}</span>
              <span class="pmodel">{{ s.description || "（无描述）" }}</span>
              <span class="purl">{{ s.chars }} 字 · {{ s.files }} 个参考文件</span>
            </div>
            <div class="pacts">
              <button class="pbtn" :class="{ 'is-on': skills.selected.includes(s.name) }"
                      @click="skills.toggle(s.name)">
                {{ skills.selected.includes(s.name) ? "已勾选" : "勾选" }}
              </button>
              <button class="pbtn" @click="editSkill(s)">编辑</button>
              <button class="pbtn danger" @click="removeSkill(s.name)">删除</button>
            </div>
          </div>
          <p v-if="skills.loaded && !skills.skills.length" class="hint" style="margin: 6px 0">
            还没有技能。点下方新建，或把技能目录放进 gateway/skills/。
          </p>
        </div>
        <button class="add" @click="editSkill(null)">＋ 新建技能</button>

        <n-modal :show="!!skEditing" preset="card" class="pmodal skmodal"
                 :title="skEditing?._new ? '新建技能' : `编辑技能 · ${skEditing?.name}`"
                 :mask-closable="true" @update:show="(s: boolean) => { if (!s) skEditing = null; }">
          <div v-if="skEditing" class="eform">
            <div class="two">
              <label>技能名（字母数字-_，即目录名）<n-input v-model:value="skEditing.name" size="small"
                        :disabled="!skEditing._new" placeholder="web-attack" /></label>
              <label>描述（勾选条提示用）<n-input v-model:value="skEditing.description" size="small"
                        placeholder="一句话说明这个技能干什么" /></label>
            </div>
            <label>SKILL.md 正文
              <n-input v-model:value="skEditing.content" type="textarea"
                       :autosize="{ minRows: 10, maxRows: 22 }"
                       placeholder="【索引式】写清做什么、按什么流程、判断标准；长细节放参考文件，正文里只留路径" />
            </label>
            <p v-if="!skEditing._new" class="hint">编辑只改写 SKILL.md 正文与描述；目录里的参考文件不受影响。</p>
            <div class="eacts">
              <n-button size="small" quaternary @click="skEditing = null">取消</n-button>
              <n-button size="small" type="primary" :loading="skSaving" :disabled="!skEditing._loaded" @click="submitSkill">保存技能</n-button>
            </div>
          </div>
        </n-modal>
      </article>
    </div>
  </section>
</template>

<style scoped>
.settings { box-sizing: border-box; height: 100%; overflow: auto; padding: 40px 56px 60px; }
header {
  display: flex; align-items: flex-end; gap: 16px; max-width: 1100px;
  border-bottom: 1px solid var(--line); padding-bottom: 24px;
}
header > div:first-child { flex: 1; }
.eyebrow { font: 600 10.5px var(--mono); letter-spacing: 2px; color: var(--accent); }
h1 { margin: 8px 0 6px; font-size: 28px; font-weight: 650; }
header p { color: var(--text-3); font-size: 13px; margin: 0; }
.dirty { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--warn); }
.dirty .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--warn); animation: blink 1.2s infinite; }

.grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px;
  margin-top: 24px; max-width: 1100px;
}
.card {
  border-radius: 18px; padding: 22px 22px 8px; min-width: 0;
  opacity: 0; animation: rise 0.45s var(--ease) forwards; animation-delay: var(--d);
}
.card:nth-child(3) { grid-column: 1 / -1; }
.card.wide { grid-column: 1 / -1; }
.skmodal { width: min(640px, calc(100vw - 32px)); }
.pbtn.is-on { border-color: var(--accent); color: var(--accent); }
h2 { display: flex; align-items: center; gap: 10px; font-size: 15px; margin: 0 0 10px; font-weight: 600; }
h2 .n { font: 600 11px var(--mono); color: var(--accent); padding: 2px 7px; border-radius: 6px; background: var(--accent-dim); }
label {
  display: flex; flex-direction: column; gap: 7px;
  color: var(--text-2); font-size: 12px; margin: 14px 0;
}
label :deep(.n-input), label :deep(.n-input-number), label :deep(.n-select) { width: 100%; }
label.sw { align-items: flex-start; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.hint { color: var(--text-3); font-size: 11px; }
.warn {
  margin: 4px 0 16px; padding: 10px 14px; border-radius: 12px; font-size: 12.5px; line-height: 1.6;
  color: #fde68a; background: rgba(251, 191, 36, 0.08); border: 1px solid rgba(251, 191, 36, 0.28);
}

/* 档案列表（CCswitch 式） */
.plist { display: flex; flex-direction: column; gap: 8px; margin: 6px 0; }
.prow {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  border: 1px solid var(--line); border-radius: 12px; background: rgba(255, 255, 255, 0.02);
  transition: border-color 0.2s;
}
.prow.on { border-color: color-mix(in srgb, var(--accent) 45%, transparent); background: color-mix(in srgb, var(--accent) 7%, transparent); }
.pmeta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.pid { font-size: 13px; font-weight: 600; color: var(--text); display: flex; align-items: center; gap: 7px; }
.pid i {
  font-style: normal; font-size: 10px; color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
  padding: 0 6px; border-radius: 999px;
}
.pmodel { font: 11.5px var(--mono); color: var(--text-2); }
.purl { font-size: 10.5px; color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pacts { display: flex; gap: 4px; flex-shrink: 0; }
.pbtn {
  border: 1px solid var(--line-2); background: none; color: var(--text-2);
  border-radius: 8px; padding: 4px 9px; font-size: 11.5px; cursor: pointer; transition: all 0.15s;
}
.pbtn:hover:not(:disabled) { border-color: var(--accent); color: var(--text); }
.pbtn:disabled { opacity: 0.35; cursor: default; }
.pbtn.danger:hover:not(:disabled) { border-color: rgba(248, 113, 113, 0.6); color: var(--err); }
.add {
  border: 1px dashed var(--line-2); background: none; color: var(--text-2); border-radius: 10px;
  padding: 8px; font-size: 12px; cursor: pointer; transition: all 0.2s; margin: 4px 0 10px;
  width: 100%;
}
.add:hover { border-color: var(--accent); color: var(--text); background: color-mix(in srgb, var(--accent) 8%, transparent); }

/* 档案编辑弹层 */
.pmodal { width: min(480px, calc(100vw - 32px)); }
.eform { display: flex; flex-direction: column; }
.eform label { margin: 8px 0; }
.eform .two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.eacts { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }

.pop-enter-active, .pop-leave-active { transition: all 0.2s var(--ease); }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateY(4px); }
@media (max-width: 768px) {
  .settings { padding: 24px 16px 40px; }
  .grid, .two { grid-template-columns: 1fr; }
  header { flex-wrap: wrap; }
  .prow { flex-wrap: wrap; }
}
</style>
