<script setup lang="ts">
import { NSelect, NTooltip, useMessage } from "naive-ui";
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useSessionStore } from "../stores/session";
import { useToolsStore } from "../stores/tools";
import PromptManager from "./PromptManager.vue";
import SkillPicker from "./SkillPicker.vue";

const store = useSessionStore();
const tools = useToolsStore();
tools.load();
/** 工具集下拉：名称 + 可用工具数 */
function renderToolset(opt: { label: string; value: string }) {
  const p = tools.presets.find((x) => x.id === opt.value);
  const n = p ? tools.resolve(p).length : 0;
  return h("span", { style: "display:inline-flex;gap:6px;align-items:center" }, [
    h("span", { style: "opacity:.55;font-size:11px" }, "🧰"), opt.label,
    h("span", { style: "font-family:var(--mono);font-size:10.5px;opacity:.5" }, String(n)),
  ]);
}
const text = ref("");
const ta = ref<HTMLTextAreaElement>();
const fileInput = ref<HTMLInputElement>();
const msg = useMessage();
const contextPercent = computed(() => {
  const cjk = (text.value.match(/[一-鿿぀-ヿ]/g) || []).length;
  const draftEstimate = cjk + (text.value.length - cjk) / 4;
  const usage = store.contextUsage;
  return usage ? Math.round((usage.history_tokens + draftEstimate) / usage.budget * 100) : 0;
});

/** 粘贴纯文本超过该长度自动存为工作区 .md，输入框只留引用（省模型上下文） */
const PASTE_TEXT_LIMIT = 2000;

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

/** 附件选择器：选完必须清 value，否则同一文件选第二次不触发 change */
async function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement;
  try {
    await store.uploadFiles(input.files ?? []);
  } catch (e: any) {
    msg.error(`上传失败：${e.message}`);
  }
  input.value = "";
}

/** 粘贴分流：文件（资源管理器复制/截图）直接上传；超长文本存档成 .md；
 *  短文本不拦截照常进输入框。文件与文本并存时文件优先。 */
async function onPaste(ev: ClipboardEvent) {
  const files = ev.clipboardData?.files;
  if (files?.length) {
    ev.preventDefault();
    try {
      await store.uploadFiles(files);
    } catch (e: any) {
      msg.error(`上传失败：${e.message}`);
    }
    return;
  }
  const pasted = ev.clipboardData?.getData("text/plain") ?? "";
  if (pasted.trim().length <= PASTE_TEXT_LIMIT) return;
  ev.preventDefault();
  try {
    const info = await store.uploadText(pasted, pasteName(pasted));
    insertAtCursor(`[已附上 ${info.name}]\n`);
    autosize();
  } catch (e: any) {
    msg.error(`存档失败：${e.message}`);
  }
}

/** 长文本存档文件名：首行去掉 markdown 前缀符截 24 字，回退 paste-HHmmss */
function pasteName(textBody: string): string {
  const first = textBody.trim().split("\n")[0].replace(/^[#>\-*`\s]+/, "").trim();
  const stem = first.replace(/[\\/:*?"<>|]/g, "").slice(0, 24).trim();
  const t = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
  return `${stem || `paste-${t}`}.md`;
}

function insertAtCursor(insert: string) {
  const el = ta.value;
  if (!el) {
    text.value += insert;
    return;
  }
  const { selectionStart: s, selectionEnd: e } = el;
  text.value = text.value.slice(0, s) + insert + text.value.slice(e);
  nextTick(() => {
    el.focus();
    el.selectionStart = el.selectionEnd = s + insert.length;
  });
}

/** 运行中 = 插话模式：Enter 发送的消息排队，下一轮模型调用前注入 */
const interjectMode = computed(() => store.running && !store.cancelling);
const queued = computed(() => store.interjections.filter((i) => i.state === "queued"));
const placeholder = computed(() => store.compacting ? '正在停止当前请求并压缩对话…' : interjectMode.value
  ? "任务执行中，输入即插话 —— 会在下一步前送达智能体，Esc 停止"
  : "给智能体布置任务… Enter 发送 · Shift+Enter 换行");

watch(() => store.draft, (v) => {
  if (v) {
    text.value = v;
    store.draft = "";
    nextTick(() => { ta.value?.focus(); autosize(); });
  }
});

function autosize() {
  const el = ta.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
}

function submit() {
  if (!text.value.trim()) return;
  if (store.send(text.value)) {
    text.value = "";
    nextTick(autosize);
  }
}

/** Enter 发送，但输入法组合中的 Enter 是"选词确认"，必须放行 */
function onKey(ev: KeyboardEvent) {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    submit();
  } else if (ev.key === "Escape" && store.running && !ev.isComposing) {
    // 输入法组合中的 Esc 是"关闭候选框"，不能当成停止任务
    ev.preventDefault();
    store.cancel();
  }
}

/** 全局快捷键：/ 聚焦输入框，N 新建对话（不在输入框内时） */
function onGlobalKey(ev: KeyboardEvent) {
  const t = ev.target as HTMLElement;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === "/") { ev.preventDefault(); ta.value?.focus(); }
  else if (ev.key.toLowerCase() === "n") { ev.preventDefault(); store.newSession(); }
}
onMounted(() => window.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onGlobalKey));
</script>

<template>
  <div class="wrap">
    <div v-if="contextPercent >= 80 || store.compacting || store.contextNote" class="context-notice" role="status">
      <span>{{ store.compacting ? '正在停止当前请求并压缩对话…'
        : store.contextNote || `对话内容接近预算（约 ${contextPercent}%），建议压缩后继续。` }}</span>
      <button :disabled="store.compacting || !store.connected" @click="store.compressContext()">{{ store.compacting ? '正在压缩…' : '压缩对话' }}</button>
    </div>
    <!-- 插话队列：排队中的可撤回 -->
    <TransitionGroup name="q" tag="div" class="queue">
      <div v-for="q in queued" :key="q.id" class="qi">
        <span class="qdot" />
        <span class="qt">{{ q.content }}</span>
        <span class="qs">等待送达</span>
        <button class="qx" aria-label="撤回插话" @click="store.withdraw(q.id)">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </TransitionGroup>

    <div class="card" :class="{ interject: interjectMode }">
      <div class="border" aria-hidden="true" />
      <div v-if="interjectMode" class="mode">
        <span class="spark" />插话模式 · 智能体正在执行，你的消息会在下一步前送达
      </div>
      <div v-if="store.attachments.length || store.uploading" class="atts">
        <span v-for="(a, i) in store.attachments" :key="a.path" class="att-chip">
          <svg viewBox="0 0 24 24"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
          <span class="an">{{ a.name }}</span>
          <span class="as">{{ fmtSize(a.size) }}</span>
          <button class="ax" aria-label="移除附件" @click="store.removeAttachment(i)">
            <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
          </button>
        </span>
        <span v-if="store.uploading" class="att-chip wait"><span class="dot" />上传中…</span>
      </div>
      <textarea ref="ta" v-model="text" rows="1" :placeholder="placeholder" :disabled="store.compacting"
                aria-label="消息输入" @input="autosize" @keydown="onKey" @paste="onPaste" />
      <div class="row">
        <n-select v-model:value="store.promptId" :options="store.promptOptions" size="small"
                  class="prompt-sel" :consistent-menu-width="false" />
        <PromptManager />
        <n-select :value="tools.effectivePreset" :options="tools.presetOptions" size="small" class="ts-sel"
                  :consistent-menu-width="false" :loading="!tools.loaded" aria-label="工具集"
                  :render-label="renderToolset" @update:value="tools.setActive" />
        <SkillPicker />
        <span class="sp" />
        <button class="compact" :disabled="store.compacting || !store.connected"
                title="保留历史摘录和最近消息，原文归档；系统提示词继续保留。预算为估算值，系统和工具另计。"
                aria-label="压缩对话" @click="store.compressContext()">{{ store.compacting ? '正在压缩…' : '压缩对话' }}</button>
        <input ref="fileInput" type="file" multiple hidden @change="onPick" />
        <button class="att" :disabled="!store.currentId" title="添加附件（可多选）"
                aria-label="添加附件" @click="fileInput?.click()">
          <svg viewBox="0 0 24 24"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
        </button>
        <Transition name="pop">
          <button v-if="store.running || store.cancelling" class="stop"
                  :disabled="store.cancelling" aria-label="停止" @click="store.cancel()">
            <span class="sq" />{{ store.cancelling ? "停止中" : "停止" }}
          </button>
        </Transition>
        <n-tooltip :disabled="!interjectMode">
          <template #trigger>
            <button class="send" :disabled="store.compacting || !text.trim() || !store.connected"
                    :aria-label="interjectMode ? '插话' : '发送'" @click="submit">
              <svg v-if="!interjectMode" viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
              <svg v-else viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4zM9 10h6" /></svg>
            </button>
          </template>
          插话（Enter）
        </n-tooltip>
      </div>
    </div>
    <div class="hint">
      <span><kbd>/</kbd> 聚焦</span>
      <span><kbd>Esc</kbd> 停止</span>
      <span>执行中可随时插话</span>
    </div>
  </div>
</template>

<style scoped>
.context-notice { display: flex; align-items: center; gap: 12px; padding: 10px 12px; margin-bottom: 8px; border: 1px solid #b8874055; border-radius: 10px; background: #b8874010; font-size: 12px; color: var(--text); }
.context-notice span { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.context-notice button, .compact { flex-shrink: 0; background: transparent; color: inherit; border: 1px solid #b8874066; border-radius: 7px; padding: 5px 8px; cursor: pointer; font-size: 12px; }
.context-notice button:disabled, .compact:disabled { opacity: .5; cursor: default; }
.wrap { padding: 8px 28px 16px; max-width: calc(var(--conversation-width, 980px) + 56px); margin: 0 auto; width: 100%; box-sizing: border-box; }

.queue { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.qi {
  display: flex; align-items: center; gap: 9px;
  padding: 7px 8px 7px 12px; border-radius: 12px;
  background: rgba(45, 212, 191, 0.08); border: 1px solid rgba(45, 212, 191, 0.25);
  backdrop-filter: var(--blur); font-size: 12.5px;
}
.qdot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-2); animation: blink 1.2s infinite; }
.qt { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qs { font-size: 10.5px; color: var(--text-3); }
.qx { border: 0; background: none; padding: 4px; border-radius: 6px; cursor: pointer; }
.qx svg { width: 13px; height: 13px; stroke: var(--text-3); stroke-width: 2; }
.qx:hover { background: rgba(248, 113, 113, 0.12); }
.qx:hover svg { stroke: var(--err); }
.q-enter-active, .q-leave-active { transition: all 0.25s var(--ease); }
.q-enter-from, .q-leave-to { opacity: 0; transform: translateY(8px); }

.card {
  position: relative;
  border-radius: 20px;
  background: var(--glass-strong);
  backdrop-filter: var(--blur);
  padding: 12px 12px 10px 16px;
  box-shadow: var(--shadow);
  transition: box-shadow 0.3s;
}
/* 渐变描边：聚焦时亮起，插话模式时流动 */
.border {
  position: absolute; inset: 0; border-radius: inherit; padding: 1px; pointer-events: none;
  background: linear-gradient(135deg, var(--line-2), var(--line));
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);
  transition: opacity 0.3s;
}
.card:focus-within .border { background: var(--accent-grad); }
.card:focus-within { box-shadow: var(--shadow), 0 0 40px rgba(143, 124, 255, 0.15); }
.card.interject .border {
  background: linear-gradient(90deg, #2dd4bf, #8f7cff, #38bdf8, #2dd4bf);
  background-size: 300% 100%;
  animation: shimmer 4s linear infinite;
}
.mode {
  display: flex; align-items: center; gap: 7px;
  font-size: 11.5px; color: var(--accent-2); margin-bottom: 6px;
}
.spark { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-2); box-shadow: 0 0 10px var(--accent-2); animation: blink 1.2s infinite; }

textarea {
  width: 100%; box-sizing: border-box; resize: none; border: 0; outline: 0;
  background: none; color: var(--text); font-family: inherit;
  font-size: 14.5px; line-height: 1.6; padding: 2px 0; max-height: 220px;
}
textarea::placeholder { color: var(--text-3); }

/* 附件 chip 区：已上传到工作区的文件（发送与上传解耦，chip 只是可见性） */
.atts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.att-chip {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 5px 8px 5px 10px; border-radius: 10px; font-size: 12px;
  background: var(--glass); border: 1px solid var(--line); color: var(--text-2);
}
.att-chip svg { width: 12px; height: 12px; flex-shrink: 0; stroke: var(--text-3); stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.att-chip .an { max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.att-chip .as { font-family: var(--mono); font-size: 10.5px; color: var(--text-3); }
.att-chip .ax { border: 0; background: none; padding: 2px 4px; border-radius: 5px; cursor: pointer; display: grid; place-items: center; }
.att-chip .ax svg { width: 11px; height: 11px; }
.att-chip .ax:hover { background: rgba(248, 113, 113, 0.12); }
.att-chip .ax:hover svg { stroke: var(--err); }
.att-chip.wait { color: var(--text-3); }
.att-chip.wait .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-2); animation: blink 1.2s infinite; }

.row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
.sp { flex: 1; }
.prompt-sel { width: 170px; flex: 0 1 170px; min-width: 110px; }
.ts-sel { width: 150px; flex: 0 1 150px; min-width: 110px; }
.ts-sel :deep(.n-base-selection) { border-radius: 9px; }
.prompt-sel :deep(.n-base-selection) { border-radius: 9px; }

.send {
  width: 36px; height: 36px; border-radius: 12px; border: 0; cursor: pointer;
  display: grid; place-items: center;
  background: var(--accent-grad); box-shadow: 0 4px 18px rgba(143, 124, 255, 0.4);
  transition: transform 0.2s var(--ease), opacity 0.2s, box-shadow 0.2s;
}
.send svg { width: 18px; height: 18px; stroke: #fff; stroke-width: 2.3; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.send:hover:not(:disabled) { transform: translateY(-1px) scale(1.04); box-shadow: 0 6px 24px rgba(143, 124, 255, 0.55); }
.send:active:not(:disabled) { transform: scale(0.94); }
.send:disabled { opacity: 0.3; cursor: not-allowed; box-shadow: none; }
.send:focus-visible, .stop:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.stop {
  display: flex; align-items: center; gap: 7px;
  height: 34px; padding: 0 13px; border-radius: 11px; cursor: pointer;
  border: 1px solid rgba(248, 113, 113, 0.35); background: rgba(248, 113, 113, 0.1);
  color: #fecaca; font-size: 12.5px;
}
.stop .sq { width: 9px; height: 9px; border-radius: 2px; background: var(--err); }
.stop:hover:not(:disabled) { background: rgba(248, 113, 113, 0.18); }
.stop:disabled { opacity: 0.6; }

/* 附件按钮：与停止按钮同排的 ghost 圆角钮 */
.att {
  display: grid; place-items: center; flex-shrink: 0;
  width: 34px; height: 34px; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--line); background: var(--glass);
  transition: background 0.18s, border-color 0.18s;
}
.att svg { width: 16px; height: 16px; stroke: var(--text-2); stroke-width: 1.9; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.att:hover:not(:disabled) { background: var(--glass-hover); border-color: var(--line-2); }
.att:disabled { opacity: 0.4; cursor: not-allowed; }
.att:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.pop-enter-active, .pop-leave-active { transition: all 0.2s var(--ease); }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: scale(0.9); }

.hint {
  display: flex; justify-content: center; gap: 16px;
  font-size: 10.5px; color: var(--text-3); margin-top: 10px;
}
kbd {
  font-family: var(--mono); font-size: 10px; border: 1px solid var(--line-2);
  border-bottom-width: 2px; border-radius: 5px; padding: 0 5px; margin-right: 3px;
}

@media (max-width: 768px) {
  .wrap { padding: 6px 10px 10px; }
  /* 窄屏选择器自行换行，发送操作独占末行，技能不再占输入框上方空间。 */
  .row { flex-wrap: wrap; gap: 8px 6px; }
  .prompt-sel, .ts-sel { width: auto; flex: 1 1 110px; min-width: 0; }
  .sp { flex: 1 1 0; min-width: 0; }
  .att, .stop, .send { flex-shrink: 0; }
  .send { margin-left: auto; }
  .hint { display: none; }
}
</style>
