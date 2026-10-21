<script setup lang="ts">
import { NButton, NDrawer, NDrawerContent, NInput } from "naive-ui";
import { ref, watch } from "vue";
import { fetchOutput } from "../api";
import { useSessionStore } from "../stores/session";
import type { OutputPage } from "../types";

const store = useSessionStore();
const grep = ref("");
const page = ref<OutputPage | null>(null);
const loading = ref(false);

async function load(nextOffset = 0) {
  if (!store.drawer.callId) return;
  loading.value = true;
  try {
    page.value = await fetchOutput(store.currentId, store.drawer.callId, {
      offset: nextOffset, limit: 200, grep: grep.value || undefined,
    });
  } finally {
    loading.value = false;
  }
}

watch(() => store.drawer.callId, (id) => { grep.value = ""; if (id) load(0); });
</script>

<template>
  <n-drawer :show="!!store.drawer.callId" :width="720"
            @update:show="(v: boolean) => !v && (store.drawer.callId = '')">
    <n-drawer-content closable>
      <template #header>
        <div class="hd">
          <span class="lab">全量输出</span>
          <span class="id">{{ store.drawer.callId }}</span>
        </div>
      </template>
      <div class="toolbar">
        <n-input v-model:value="grep" size="small" clearable placeholder="正则过滤，回车应用"
                 style="max-width: 300px" @keydown.enter="load(0)" />
        <n-button size="small" secondary :loading="loading" @click="load(0)">应用</n-button>
        <span class="sp" />
        <span v-if="page?.total_lines != null" class="info">
          {{ page.offset }}–{{ (page.offset || 0) + (page.returned || 0) }} / {{ page.total_lines }} 行
        </span>
        <n-button v-if="(page?.offset || 0) > 0" size="small" quaternary
                  @click="load(Math.max(0, (page!.offset || 0) - 200))">↑ 上一页</n-button>
        <n-button v-if="page?.next_offset != null" size="small" quaternary
                  @click="load(page!.next_offset!)">下一页 ↓</n-button>
      </div>
      <pre v-if="page?.lines?.length" class="out">{{ page.lines.join("\n") }}</pre>
      <div v-else-if="page" class="none">（{{ page.status === "not_found" ? "输出不存在" : "无匹配行" }}）</div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.hd { display: flex; align-items: baseline; gap: 10px; }
.lab { font-size: 15px; font-weight: 600; }
.id { font-family: var(--mono); font-size: 11.5px; color: var(--text-3); }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 10px; }
.sp { flex: 1; }
.info { color: var(--text-3); font-size: 11.5px; font-family: var(--mono); }
.out {
  background: rgba(3, 4, 10, 0.7); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px; margin: 0; font-family: var(--mono); font-size: 12px; line-height: 1.65;
  overflow: auto; white-space: pre;
}
.none { color: var(--text-3); padding: 30px; text-align: center; }
</style>
