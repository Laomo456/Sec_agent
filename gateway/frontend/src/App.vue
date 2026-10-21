<script setup lang="ts">
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme } from "naive-ui";
import type { GlobalThemeOverrides } from "naive-ui";
import { defineAsyncComponent, onMounted, ref } from "vue";
import NavRail from "./components/NavRail.vue";
import ChatView from "./components/ChatView.vue";
import SubagentIsland from './components/SubagentIsland.vue';
import SettingsPanel from "./components/SettingsPanel.vue";
// 工作流编辑器（Vue Flow）体积大，按需加载
const WorkflowView = defineAsyncComponent(() => import("./components/workflow/WorkflowView.vue"));
const ToolsView = defineAsyncComponent(() => import("./components/tools/ToolsView.vue"));
const KnowledgeView = defineAsyncComponent(() => import("./components/knowledge/KnowledgeView.vue"));
import { useSessionStore } from "./stores/session";

export type View = "chat" | "workflow" | "tools" | "knowledge" | "settings";

const store = useSessionStore();
const view = ref<View>((localStorage.getItem("agg.view") as View) || "chat");
const setView = (v: View) => { view.value = v; localStorage.setItem("agg.view", v); };

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: "#8f7cff",
    primaryColorHover: "#a597ff",
    primaryColorPressed: "#7b66f5",
    primaryColorSuppl: "#8f7cff",
    infoColor: "#38bdf8",
    successColor: "#34d399",
    warningColor: "#fbbf24",
    errorColor: "#f87171",
    borderRadius: "10px",
    borderRadiusSmall: "8px",
    fontFamily: 'Inter, "HarmonyOS Sans SC", "Segoe UI", "Microsoft YaHei", sans-serif',
    fontFamilyMono: '"JetBrains Mono", "Cascadia Code", Consolas, monospace',
    bodyColor: "transparent",
    cardColor: "rgba(18, 20, 33, 0.9)",
    modalColor: "rgba(16, 18, 30, 0.96)",
    popoverColor: "rgba(20, 22, 36, 0.98)",
    inputColor: "rgba(255, 255, 255, 0.04)",
    borderColor: "rgba(255, 255, 255, 0.1)",
  },
  Input: { border: "1px solid rgba(255,255,255,0.09)", color: "rgba(255,255,255,0.035)" },
  Drawer: { color: "rgba(12, 14, 24, 0.94)" },
};

onMounted(async () => {
  await store.refreshSessions();
  await store.loadPrompts();
  if (store.sessions.length) store.open(store.sessions[0].session_id);
  else await store.newSession();

  // iOS 软键盘补偿:dvh 不跟踪键盘,键盘弹出时输入框会压在键盘下面。
  // 用 visualViewport 与 innerHeight 的差值收缩整个壳(--kb,见 style.css)。
  // 只在聚焦输入类元素期间生效——避免双指缩放 visualViewport 变小误触发;
  // 安卓 layout viewport 随键盘同步缩小、差值为 0,不会二次补偿。
  const vv = window.visualViewport;
  if (vv) {
    let editing = false;
    const sync = () => {
      const kb = editing ? Math.max(0, window.innerHeight - vv.height) : 0;
      document.documentElement.style.setProperty("--kb", `${Math.round(kb)}px`);
    };
    document.addEventListener("focusin", (e) => {
      editing = !!(e.target instanceof Element && e.target.closest("input, textarea, [contenteditable]"));
      sync();
    });
    document.addEventListener("focusout", () => { editing = false; sync(); });
    vv.addEventListener("resize", sync);
  }
});
</script>

<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides" style="height: 100%">
    <n-message-provider placement="top">
      <n-dialog-provider>
        <div class="aurora" aria-hidden="true">
          <div class="blob b1" /><div class="blob b2" /><div class="blob b3" />
          <div class="grid" /><div class="noise" />
        </div>
        <div class="shell">
          <NavRail :view="view" @change="setView" />
          <main class="stage">
            <Transition name="view" mode="out-in">
              <ChatView v-if="view === 'chat'" key="chat" />
              <WorkflowView v-else-if="view === 'workflow'" key="wf" />
              <ToolsView v-else-if="view === 'tools'" key="tools" />
              <KnowledgeView v-else-if="view === 'knowledge'" key="knowledge" />
              <SettingsPanel v-else key="settings" />
            </Transition>
          </main>
        </div>
        <SubagentIsland :view="view" />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.shell {
  position: relative;
  z-index: 1;
  display: flex;
  height: 100%;
}
.stage {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}
.stage > * { flex: 1; min-height: 0; }

@media (max-width: 768px) {
  .shell { flex-direction: column-reverse; }
}
</style>
