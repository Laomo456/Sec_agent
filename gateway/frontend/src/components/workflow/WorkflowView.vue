<script setup lang="ts">
/** 工作流页：无选中时显示工作流列表 + 模板；打开后是编辑器（画布 + 配置面板 + 运行面板）。 */
import { NButton, NInput, NModal, NPopconfirm, NTooltip, useMessage } from "naive-ui";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useWorkflowStore } from "../../stores/workflow";
import NodeInspector from "./NodeInspector.vue";
import RunPanel from "./RunPanel.vue";
import WorkflowCanvas from "./WorkflowCanvas.vue";
import { TEMPLATES } from "./templates";

const store = useWorkflowStore();
const msg = useMessage();
const showNew = ref(false);
const newName = ref("");
const tplKey = ref("blank");
const importing = ref<HTMLInputElement>();

onMounted(async () => {
  store.loadTools(true);   // 后台刷新工具卡：从工具页切过来时，启用/禁用立即生效（视图是 v-if 切换，每次进入都会走这里）
  await store.refresh();
  const last = localStorage.getItem("agg.wf");
  if (!store.wf && last && store.list.some((w) => w.id === last)) await store.open(last);
});
onBeforeUnmount(() => store.closeRun());

async function create() {
  const tpl = TEMPLATES.find((t) => t.key === tplKey.value)!;
  await store.create(newName.value.trim() || tpl.name, tpl.wf as any);
  showNew.value = false;
  newName.value = "";
}
async function save() {
  try {
    await store.save();
    if (store.problems.length) msg.warning(`已保存，但有 ${store.problems.length} 个问题待处理`);
    else msg.success("已保存");
  } catch (e: any) { msg.error(`保存失败：${e.message}`); }
}
function back() {
  store.closeRun();
  store.wf = null;
  store.selectedId = "";
  localStorage.removeItem("agg.wf");
  store.refresh();
}
function exportJson() {
  if (!store.wf) return;
  const blob = new Blob([JSON.stringify(store.wf, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${store.wf.name || "workflow"}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
async function importJson(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0];
  if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    if (!Array.isArray(data.nodes)) throw new Error("不是有效的工作流文件");
    await store.create(data.name || f.name.replace(/\.json$/, ""), data);
    msg.success("已导入");
  } catch (e: any) { msg.error(`导入失败：${e.message}`); }
  (ev.target as HTMLInputElement).value = "";
}
const fmtTime = (s: string) => (s || "").replace("T", " ").slice(0, 16);
const panelOpen = computed(() => store.panel === "run");
</script>

<template>
  <div class="wfv">
    <!-- ═══ 列表页 ═══ -->
    <section v-if="!store.wf" class="gallery">
      <header>
        <div>
          <span class="eyebrow">WORKFLOW STUDIO</span>
          <h1>工作流</h1>
          <p>把 LLM、分类器、条件分支、循环、代码、HTTP 和工具编排成可复用的自动化流程。</p>
        </div>
        <div class="hacts">
          <n-button secondary @click="importing?.click()">导入 JSON</n-button>
          <n-button type="primary" @click="showNew = true">＋ 新建工作流</n-button>
          <input ref="importing" type="file" accept="application/json" hidden @change="importJson" />
        </div>
      </header>

      <div class="grid">
        <button class="card new" @click="showNew = true">
          <span class="plus">＋</span><span>新建工作流</span><span class="sub">空白或从模板开始</span>
        </button>
        <div v-for="(w, i) in store.list" :key="w.id" class="card" role="button" tabindex="0"
             :style="{ animationDelay: `${i * 0.04}s` }" @click="store.open(w.id)" @keydown.enter="store.open(w.id)">
          <div class="ct">
            <span class="ci">⌬</span>
            <span class="cn">{{ w.name }}</span>
            <n-popconfirm @positive-click="store.remove(w.id)">
              <template #trigger><button class="cdel" aria-label="删除" @click.stop>✕</button></template>
              删除工作流「{{ w.name }}」？
            </n-popconfirm>
          </div>
          <div class="cd">{{ w.description || "暂无描述" }}</div>
          <div class="cm">
            <span>{{ w.nodes }} 个节点</span><span>{{ fmtTime(w.updated) }}</span>
            <button class="cdup" @click.stop="store.duplicate(w.id)">复制</button>
          </div>
        </div>
      </div>
    </section>

    <!-- ═══ 编辑器 ═══ -->
    <section v-else class="editor">
      <header class="bar glass">
        <button class="back" aria-label="返回列表" @click="back">
          <svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6" /></svg>
        </button>
        <div class="names">
          <input v-model="store.wf.name" class="nm" aria-label="工作流名称" />
          <input v-model="store.wf.description" class="ds" placeholder="添加描述…" aria-label="工作流描述" />
        </div>
        <n-tooltip v-if="store.problems.length">
          <template #trigger><span class="prob">⚠ {{ store.problems.length }} 个问题</span></template>
          <div v-for="p in store.problems" :key="p">· {{ p }}</div>
        </n-tooltip>
        <span class="saved" :class="{ dirty: store.dirty }">{{ store.dirty ? "未保存" : "已保存" }}</span>
        <n-button size="small" quaternary @click="exportJson">导出</n-button>
        <n-button size="small" secondary :loading="store.saving" @click="save">保存 <span class="k">Ctrl+S</span></n-button>
        <n-button size="small" type="primary" :class="{ glow: !panelOpen }"
                  :loading="!!store.pendingCommand || store.stopping"
                  @click="store.panel = panelOpen ? '' : 'run'">
          {{ store.stopping ? "停止中…" : store.running ? "运行中…" : "▶ 运行" }}
        </n-button>
      </header>
      <div class="body">
        <WorkflowCanvas />
        <Transition name="side"><RunPanel v-if="panelOpen" /></Transition>
        <Transition name="side"><NodeInspector v-if="!panelOpen && store.selected" :key="store.selectedId" /></Transition>
      </div>
    </section>

    <!-- 新建弹窗 -->
    <n-modal v-model:show="showNew">
      <div class="modal glass">
        <div class="mt">新建工作流</div>
        <n-input v-model:value="newName" placeholder="名称（可选，默认用模板名）" @keydown.enter="create" />
        <div class="tpls">
          <button v-for="t in TEMPLATES" :key="t.key" class="tpl" :class="{ on: tplKey === t.key }" @click="tplKey = t.key">
            <span class="ti">{{ t.icon }}</span>
            <span class="tn">{{ t.name }}</span>
            <span class="td">{{ t.desc }}</span>
          </button>
        </div>
        <div class="mf">
          <n-button @click="showNew = false">取消</n-button>
          <n-button type="primary" @click="create">创建</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.wfv { height: 100%; min-height: 0; }
.gallery { height: 100%; overflow: auto; padding: 40px 56px 60px; box-sizing: border-box; }
.gallery header { display: flex; align-items: flex-end; gap: 16px; border-bottom: 1px solid var(--line); padding-bottom: 24px; }
.gallery header > div:first-child { flex: 1; }
.eyebrow { font: 600 10.5px var(--mono); letter-spacing: 2px; color: var(--accent); }
h1 { margin: 8px 0 6px; font-size: 28px; font-weight: 650; }
.gallery p { color: var(--text-3); font-size: 13px; margin: 0; }
.hacts { display: flex; gap: 8px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 16px; margin-top: 26px; }
.card {
  text-align: left; border-radius: 18px; padding: 18px; cursor: pointer; color: var(--text);
  border: 1px solid var(--line); background: var(--glass); backdrop-filter: var(--blur);
  display: flex; flex-direction: column; gap: 10px; min-height: 130px; box-sizing: border-box;
  transition: transform 0.25s var(--ease), border-color 0.25s, box-shadow 0.25s;
  opacity: 0; animation: rise 0.45s var(--ease) forwards;
}
.card:hover { transform: translateY(-3px); border-color: rgba(143, 124, 255, 0.45); box-shadow: 0 14px 40px rgba(91, 80, 220, 0.18); }
.card:focus-visible { outline: 2px solid var(--accent); }
.card.new {
  align-items: center; justify-content: center; border-style: dashed; border-color: var(--line-2);
  background: rgba(143, 124, 255, 0.04); opacity: 1; animation: none; font-size: 14px;
}
.plus { font-size: 26px; width: 44px; height: 44px; border-radius: 14px; display: grid; place-items: center; background: var(--accent-grad); color: #fff; box-shadow: 0 6px 20px rgba(143,124,255,0.4); }
.card .sub { font-size: 11.5px; color: var(--text-3); }
.ct { display: flex; align-items: center; gap: 10px; }
.ci { width: 32px; height: 32px; border-radius: 10px; display: grid; place-items: center; background: var(--accent-dim); color: var(--accent); font-size: 17px; }
.cn { flex: 1; font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cdel { border: 0; background: none; color: var(--text-3); opacity: 0; cursor: pointer; padding: 3px 7px; border-radius: 6px; }
.card:hover .cdel { opacity: 1; }
.cdel:hover { color: var(--err); background: rgba(248,113,113,0.12); }
.cd { flex: 1; font-size: 12.5px; color: var(--text-3); line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.cm { display: flex; align-items: center; gap: 12px; font-size: 11px; color: var(--text-3); font-family: var(--mono); }
.cdup { margin-left: auto; border: 1px solid var(--line); background: none; color: var(--text-2); font-size: 11px; border-radius: 7px; padding: 2px 9px; cursor: pointer; font-family: var(--sans); }
.cdup:hover { border-color: var(--accent); color: var(--text); }

.editor { height: 100%; display: flex; flex-direction: column; }
.bar {
  display: flex; align-items: center; gap: 10px; padding: 10px 16px; border-width: 0 0 1px 0;
  background: rgba(8, 9, 16, 0.6); z-index: 20;
}
.back { width: 32px; height: 32px; border-radius: 10px; border: 1px solid var(--line); background: none; cursor: pointer; display: grid; place-items: center; }
.back svg { width: 16px; height: 16px; stroke: var(--text-2); stroke-width: 2; fill: none; }
.back:hover { background: var(--glass-hover); }
.names { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.names input { border: 1px solid transparent; background: none; outline: 0; color: var(--text); border-radius: 6px; padding: 0 6px; margin-left: -6px; }
.names input:hover, .names input:focus { border-color: var(--line-2); }
.nm { font-size: 14.5px; font-weight: 600; }
.ds { font-size: 11.5px; color: var(--text-3) !important; }
.prob { font-size: 12px; color: var(--warn); cursor: help; }
.saved { font-size: 11.5px; color: var(--text-3); }
.saved.dirty { color: var(--warn); }
.k { font: 10px var(--mono); opacity: 0.55; margin-left: 6px; }
.glow { box-shadow: 0 4px 18px rgba(143, 124, 255, 0.4); }
.body { flex: 1; min-height: 0; position: relative; }
.side-enter-active, .side-leave-active { transition: opacity 0.2s, transform 0.25s var(--ease); }
.side-enter-from, .side-leave-to { opacity: 0; transform: translateX(16px); }

.modal { width: min(680px, 92vw); padding: 22px; border-radius: 20px; background: var(--glass-strong); box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 14px; }
.mt { font-size: 16px; font-weight: 600; }
.tpls { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; }
.tpl {
  display: flex; flex-direction: column; gap: 4px; text-align: left; padding: 13px; border-radius: 14px; cursor: pointer;
  border: 1px solid var(--line); background: rgba(255,255,255,0.02); color: var(--text); transition: all 0.2s;
}
.tpl:hover { border-color: var(--line-2); }
.tpl.on { border-color: var(--accent); background: var(--accent-dim); box-shadow: 0 0 0 3px rgba(143,124,255,0.15); }
.ti { font-size: 20px; }
.tn { font-size: 13px; font-weight: 600; }
.td { font-size: 11px; color: var(--text-3); }
.mf { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 768px) { .gallery { padding: 22px 14px; } .gallery header { flex-wrap: wrap; } .bar .k, .saved { display: none; } }
</style>
