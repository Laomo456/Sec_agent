<script setup lang="ts">
/** 工具页：工具库（启停 + 说明）/ 工具集（预设）/ 工坊（自建工具）三个标签。 */
import { NButton, useMessage } from "naive-ui";
import { onMounted, ref } from "vue";
import { useToolsStore } from "../../stores/tools";
import ToolLibrary from "./ToolLibrary.vue";
import ToolPresets from "./ToolPresets.vue";
import ToolWorkshop from "./ToolWorkshop.vue";

const store = useToolsStore();
const msg = useMessage();
const tab = ref<"library" | "presets" | "workshop">("library");
const editing = ref<string | null>(null);   // 工坊里正在编辑的工具（null = 新建）

onMounted(() => store.load(true));

async function save() {
  try { await store.save(); msg.success("已保存，对新任务生效"); }
  catch (e: any) { msg.error(`保存失败：${e.message}`); }
}
function openWorkshop(name: string | null) {
  editing.value = name;
  tab.value = "workshop";
}
async function onWorkshopSaved() {
  await store.load(true);
}
const tabs = [
  { key: "library", label: "工具库", icon: "M4 6h16M4 12h16M4 18h10" },
  { key: "presets", label: "工具集", icon: "M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v5H4zM13 14h7v5h-7z" },
  { key: "workshop", label: "工坊", icon: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z" },
] as const;
</script>

<template>
  <section class="tv">
    <header>
      <div class="ttl">
        <span class="eyebrow">TOOLS</span>
        <h1>工具</h1>
        <p>管理智能体能用的工具：启停、组合成工具集、查看说明，或者自己造一个。</p>
      </div>
      <Transition name="pop">
        <div v-if="store.dirty && tab !== 'workshop'" class="dirty"><span class="dot" />有未保存的更改</div>
      </Transition>
      <n-button v-if="tab !== 'workshop'" type="primary" :disabled="!store.dirty" @click="save">保存配置</n-button>
    </header>

    <nav class="tabs" role="tablist">
      <button v-for="t in tabs" :key="t.key" role="tab" :aria-selected="tab === t.key"
              :class="{ on: tab === t.key }" @click="tab = t.key">
        <svg viewBox="0 0 24 24"><path :d="t.icon" /></svg>{{ t.label }}
        <span v-if="t.key === 'library'" class="n">{{ store.tools.length }}</span>
        <span v-if="t.key === 'presets'" class="n">{{ store.presets.length }}</span>
      </button>
      <span class="ink" :style="{ transform: `translateX(${tabs.findIndex((t) => t.key === tab) * 100}%)` }" />
    </nav>

    <div v-if="store.error" class="err">加载工具失败：{{ store.error }}（检查 config.yaml 的 tools 段与 toolbox 是否可启动）</div>

    <Transition name="view" mode="out-in">
      <ToolLibrary v-if="tab === 'library'" key="lib" @edit="openWorkshop" @create="openWorkshop(null)" />
      <ToolPresets v-else-if="tab === 'presets'" key="pre" />
      <ToolWorkshop v-else key="ws" :name="editing" @saved="onWorkshopSaved" @switch="(n: string | null) => editing = n" />
    </Transition>
  </section>
</template>

<style scoped>
.tv { box-sizing: border-box; height: 100%; overflow: auto; padding: 40px 56px 60px; display: flex; flex-direction: column; }
header { display: flex; align-items: flex-end; gap: 16px; max-width: 1240px; width: 100%; }
.ttl { flex: 1; }
.eyebrow { font: 600 10.5px var(--mono); letter-spacing: 2px; color: var(--accent); }
h1 { margin: 8px 0 6px; font-size: 28px; font-weight: 650; }
.ttl p { color: var(--text-3); font-size: 13px; margin: 0; }
.dirty { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--warn); }
.dirty .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--warn); animation: blink 1.2s infinite; }

.tabs {
  position: relative; display: grid; grid-template-columns: repeat(3, 1fr); width: 390px;
  margin: 24px 0 20px; padding: 4px; border-radius: 14px; background: var(--glass); border: 1px solid var(--line);
}
.tabs button {
  position: relative; z-index: 1; display: flex; align-items: center; justify-content: center; gap: 7px;
  border: 0; background: none; color: var(--text-3); font-size: 13px; padding: 8px 0; cursor: pointer;
  transition: color 0.2s;
}
.tabs button.on { color: var(--text); }
.tabs button:focus-visible { outline: 2px solid var(--accent); border-radius: 10px; }
.tabs svg { width: 15px; height: 15px; stroke: currentColor; stroke-width: 1.9; fill: none; stroke-linecap: round; stroke-linejoin: round; }
.n { font: 10.5px var(--mono); color: var(--text-3); background: rgba(255,255,255,0.06); border-radius: 999px; padding: 0 6px; }
.ink {
  position: absolute; left: 4px; top: 4px; bottom: 4px; width: calc((100% - 8px) / 3); border-radius: 10px;
  background: linear-gradient(135deg, rgba(143, 124, 255, 0.28), rgba(45, 212, 191, 0.12));
  box-shadow: inset 0 0 0 1px rgba(143, 124, 255, 0.35); transition: transform 0.35s var(--ease);
}
.err { max-width: 1240px; margin-bottom: 14px; font-size: 12.5px; color: #fecaca; background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.28); border-radius: 12px; padding: 10px 14px; }
.pop-enter-active, .pop-leave-active { transition: all 0.2s var(--ease); }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateY(4px); }
@media (max-width: 768px) { .tv { padding: 22px 14px 40px; } header { flex-wrap: wrap; } .tabs { width: 100%; } }
</style>
