<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import ProcessBlock from "./ProcessBlock.vue";
import FinalAnswer from "./blocks/FinalAnswer.vue";
import SkillBlock from "./blocks/SkillBlock.vue";
import UserMessage from "./blocks/UserMessage.vue";
import { useSessionStore } from "../stores/session";
import type { GatewayEvent } from "../types";

const store = useSessionStore();
const box = ref<HTMLElement>();
const stick = ref(true);        // 用户往上翻时不强制滚到底
const far = ref(false);         // 距底部足够远才显示"回到底部"圆钮（迟滞，流式追加时不闪）

const EXAMPLES = [
  { icon: "🧰", t: "列出你有哪些可用的工具" },
  { icon: "📝", t: "把 hello world 写进 test.txt，再读出来确认" },
  { icon: "🌐", t: "搜索今天的科技新闻，总结三条" },
  { icon: "🧩", t: "把这段需求拆成可执行的步骤清单" },
];

let farTimer: number | undefined;
function onScroll() {
  const el = box.value;
  if (!el) return;
  const d = el.scrollHeight - el.scrollTop - el.clientHeight;
  stick.value = d < 120;
  clearTimeout(farTimer);
  if (d > 280) {
    // 出现阈值远大于恢复阈值 + 延时复核：流式追加造成的滚动滞后不会闪出按钮
    farTimer = window.setTimeout(() => {
      const el2 = box.value;
      if (el2 && el2.scrollHeight - el2.scrollTop - el2.clientHeight > 280) far.value = true;
    }, 350);
  } else {
    far.value = false;
  }
}
async function toBottom(smooth = true) {
  await nextTick();
  box.value?.scrollTo({ top: box.value.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}
watch(() => [store.events.length, store.live.content.length, store.pending], () => {
  if (stick.value) toBottom();
});
watch(() => store.currentId, () => { stick.value = true; toBottom(false); });
onMounted(() => toBottom(false));

const time = (ts: string) => ts.slice(11, 16);

interface Group {
  user: GatewayEvent;
  skills: GatewayEvent[];
  inner: GatewayEvent[];
  interjects: GatewayEvent[];
  done: GatewayEvent | null;
  error: GatewayEvent | null;
}

/** 渲染行：上传提示独立成行（不进任务组）——空闲时的上传发生在首个
 *  user_message 之前/任务组之外，塞进组里会被 ProcessBlock 忽略。 */
type Row = { kind: "group"; g: Group } | { kind: "upload"; e: GatewayEvent };
const rows = computed<Row[]>(() => {
  const out: Row[] = [];
  let g: Group | null = null;
  let pendSkills: GatewayEvent[] = [];   // skill_injected 先于 user_message 到达
  for (const e of store.events) {
    if (e.type === "file_upload") {
      out.push({ kind: "upload", e });
    } else if (e.type === "skill_injected") {
      if (g) g.skills.push(e);           // 已在组内（异常序）：就近挂到当前组
      else pendSkills.push(e);
    } else if (e.type === "user_message") {
      g = { user: e, skills: pendSkills, inner: [], interjects: [], done: null, error: null };
      pendSkills = [];
      out.push({ kind: "group", g });
    } else if (g) {
      if (e.type === 'agent_resumed') {
        g = { user: { ...g.user, seq: e.seq, content: '子任务完成 · 继续处理', ts: e.ts },
              skills: [], inner: [], interjects: [], done: null, error: null };
        out.push({ kind: 'group', g });
      }
      else if (e.type === "done") g.done = e;
      else if (e.type === "error") g.error = e;
      else if (e.type === "interject") { g.interjects.push(e); g.inner.push(e); }
      else g.inner.push(e);
    }
  }
  return out;
});
const lastGroupIdx = computed(() => {
  let last = -1;
  rows.value.forEach((r, i) => { if (r.kind === "group") last = i; });
  return last;
});

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
</script>

<template>
  <div ref="box" class="stream" @scroll.passive="onScroll">
    <!-- 空状态 -->
    <Transition name="fade">
      <div v-if="!store.events.length && !store.pending" class="hero">
        <div class="orb"><span /><span /><span /></div>
        <h1>有什么可以<span class="grad-text">帮你</span>的？</h1>
        <p>独立大脑 · 工具可插拔 · 执行中可随时插话调整方向</p>
        <div class="chips">
          <button v-for="(ex, i) in EXAMPLES" :key="ex.t" class="chip"
                  :style="{ animationDelay: `${0.08 * i + 0.15}s` }" @click="store.setDraft(ex.t)">
            <span class="ic">{{ ex.icon }}</span>{{ ex.t }}
          </button>
        </div>
      </div>
    </Transition>

    <template v-for="(r, ri) in rows" :key="r.kind === 'group' ? `g${r.g.user.seq}` : `u${r.e.seq}`">
      <div v-if="r.kind === 'upload'" class="upload-row">
        <svg viewBox="0 0 24 24"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
        <span class="un">{{ r.e.name }}</span>
        <span>（{{ fmtSize(r.e.size || 0) }}）已存入工作区</span>
      </div>
      <div v-else class="group">
        <SkillBlock v-for="s in r.g.skills" :key="s.seq" :name="s.name || ''" :content="s.content || ''" />
        <UserMessage :text="r.g.user.content || ''" :time="time(r.g.user.ts)" :prompt="r.g.user.prompt || ''" />
        <ProcessBlock :events="r.g.inner" :running="!r.g.done && !r.g.error" :error="r.g.error?.detail"
                      :live="ri === lastGroupIdx && !r.g.done && !r.g.error" />
        <FinalAnswer v-if="r.g.done" :text="r.g.done.content || ''" />
        <div v-if="r.g.error" class="error" role="alert">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.5" /></svg>
          {{ r.g.error.detail }}
        </div>
      </div>
    </template>

    <!-- 乐观显示：刚发出、服务端还没回显 -->
    <div v-if="store.pending" class="group">
      <UserMessage :text="store.pending" time="" sending />
    </div>

    <Transition name="fade">
      <button v-if="far" class="jump" aria-label="回到底部" @click="stick = true; far = false; toBottom()">
        <svg viewBox="0 0 24 24"><path d="M12 5v14M6 13l6 6 6-6" /></svg>
      </button>
    </Transition>
  </div>
</template>

<style scoped>
.stream {
  flex: 1;
  overflow-y: auto;
  padding: 32px 28px 20px;
  position: relative;
  scroll-behavior: auto;
}
.group {
  max-width: var(--conversation-width, 980px);
  margin: 0 auto 30px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  animation: rise 0.35s var(--ease);
}

/* 上传提示行：与任务组同节奏的居中小胶囊 */
.upload-row {
  max-width: var(--conversation-width, 980px);
  width: 100%;
  box-sizing: border-box;
  margin: 0 auto 30px;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--text-3);
  border: 1px dashed var(--line-2);
  border-radius: 12px;
  padding: 8px 14px;
  background: var(--glass);
  animation: rise 0.35s var(--ease);
}
.upload-row svg { width: 14px; height: 14px; flex-shrink: 0; stroke: var(--accent-2); stroke-width: 1.9; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.upload-row .un { color: var(--text-2); max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.hero { max-width: 640px; margin: 11vh auto 0; text-align: center; }
.orb {
  position: relative; width: 84px; height: 84px; margin: 0 auto 26px;
}
.orb span {
  position: absolute; inset: 0; border-radius: 50%;
  background: var(--accent-grad); filter: blur(1px);
  animation: orb 5s var(--ease) infinite;
}
.orb span:nth-child(2) { inset: 10px; filter: blur(8px); opacity: 0.8; animation-delay: -1.6s; }
.orb span:nth-child(3) {
  inset: 22px; background: #0b0d16; filter: none;
  box-shadow: inset 0 0 18px rgba(143, 124, 255, 0.6);
  animation: none;
}
@keyframes orb {
  0%, 100% { transform: scale(1) rotate(0); border-radius: 50%; }
  33% { transform: scale(1.06) rotate(60deg); border-radius: 46% 54% 52% 48%; }
  66% { transform: scale(0.96) rotate(140deg); border-radius: 54% 46% 48% 52%; }
}
.hero h1 { font-size: 30px; font-weight: 650; margin: 0 0 10px; letter-spacing: 0.5px; }
.hero p { color: var(--text-3); margin: 0 0 34px; font-size: 13.5px; }
.chips { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.chip {
  display: flex; align-items: center; gap: 10px; text-align: left;
  border: 1px solid var(--line); background: var(--glass);
  backdrop-filter: var(--blur);
  color: var(--text-2); padding: 13px 15px; border-radius: 14px;
  font-size: 13px; cursor: pointer; line-height: 1.45;
  opacity: 0; animation: rise 0.5s var(--ease) forwards;
  transition: border-color 0.2s, color 0.2s, transform 0.2s var(--ease), background 0.2s;
}
.chip .ic { font-size: 17px; }
.chip:hover {
  border-color: rgba(143, 124, 255, 0.45); color: var(--text);
  background: var(--accent-dim); transform: translateY(-2px);
}

.error {
  display: flex; gap: 10px; align-items: flex-start;
  color: #fecaca; background: rgba(248, 113, 113, 0.08);
  border: 1px solid rgba(248, 113, 113, 0.28);
  border-radius: 14px; padding: 11px 15px; font-size: 13px; line-height: 1.6;
}
.error svg { width: 18px; height: 18px; flex-shrink: 0; stroke: var(--err); stroke-width: 2; fill: none; margin-top: 1px; }

.jump {
  position: sticky; bottom: 8px; left: 100%;
  display: grid; place-items: center;
  width: 38px; height: 38px; margin-left: auto; border-radius: 50%;
  border: 1px solid var(--line-2); background: var(--glass-strong);
  backdrop-filter: var(--blur); cursor: pointer; box-shadow: var(--shadow);
}
.jump svg { width: 18px; height: 18px; stroke: var(--text); stroke-width: 2; fill: none; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.25s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

@media (max-width: 768px) {
  .stream { padding: 16px 12px 10px; }
  .hero { margin-top: 5vh; }
  .hero h1 { font-size: 23px; }
  .chips { grid-template-columns: 1fr; }
}
</style>
