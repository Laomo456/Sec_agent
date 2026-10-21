<script setup lang="ts">
import { NButton, NInput, NModal } from "naive-ui";
import { ref, watch } from "vue";
import { useSessionStore } from "../stores/session";
import type { PromptPreset } from "../types";

const store = useSessionStore();
const show = ref(false);
const list = ref<PromptPreset[]>([]);

watch(show, (v) => { if (v) list.value = JSON.parse(JSON.stringify(store.prompts)); });

const add = () => list.value.push({ id: "", name: "新预设", content: "" });
const remove = (i: number) => list.value.splice(i, 1);
const saving = ref(false);
const err = ref("");
watch(show, () => { err.value = ""; });
async function save() {
  saving.value = true;
  err.value = "";
  try {
    await store.savePromptList(list.value.filter((p) => p.name.trim()));
    show.value = false;           // 只有真正存上才关窗
  } catch (e: any) {
    err.value = e?.message || String(e);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <button class="mg" aria-label="管理提示词预设" @click="show = true">
    <svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4" /></svg>
  </button>

  <n-modal v-model:show="show" transform-origin="center">
    <div class="panel glass">
      <div class="head">
        <div>
          <div class="t">提示词预设</div>
          <div class="sub">发送时作为附加指令注入，仅作用于该次任务</div>
        </div>
        <n-button size="small" secondary @click="add">＋ 新增</n-button>
      </div>
      <TransitionGroup name="row" tag="div" class="rows">
        <div v-for="(p, i) in list" :key="p.id || i" class="row">
          <div class="top">
            <n-input v-model:value="p.name" size="small" placeholder="名称" class="name" />
            <button class="del" aria-label="删除预设" @click="remove(i)">✕</button>
          </div>
          <n-input v-model:value="p.content" type="textarea" size="small"
                   :autosize="{ minRows: 2, maxRows: 8 }" placeholder="提示词内容" />
        </div>
      </TransitionGroup>
      <div v-if="!list.length" class="empty">还没有预设，点「新增」创建一个</div>
      <div v-if="err" class="err" role="alert">{{ err }}</div>
      <div class="foot">
        <n-button size="small" @click="show = false">取消</n-button>
        <n-button size="small" type="primary" :loading="saving" @click="save">保存全部</n-button>
      </div>
    </div>
  </n-modal>
</template>

<style scoped>
.mg {
  width: 30px; height: 30px; border-radius: 9px; cursor: pointer;
  border: 1px solid var(--line); background: rgba(255, 255, 255, 0.03);
  display: grid; place-items: center;
}
.mg svg { width: 14px; height: 14px; stroke: var(--text-2); stroke-width: 1.8; fill: none; stroke-linejoin: round; }
.mg:hover { border-color: var(--line-2); }
.mg:hover svg { stroke: var(--text); }

.panel {
  background: var(--glass-strong);
  border-radius: 20px; padding: 22px; width: min(640px, 92vw); max-height: 80vh;
  display: flex; flex-direction: column; box-shadow: var(--shadow);
}
.head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.t { font-size: 16px; font-weight: 600; }
.sub { color: var(--text-3); font-size: 12px; margin-top: 3px; }
.rows { overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.row { padding: 12px; border-radius: 14px; border: 1px solid var(--line); background: rgba(255, 255, 255, 0.02); }
.top { display: flex; gap: 8px; margin-bottom: 8px; }
.name { max-width: 240px; }
.del { margin-left: auto; border: 0; background: none; color: var(--text-3); cursor: pointer; padding: 2px 8px; border-radius: 6px; }
.del:hover { color: var(--err); background: rgba(248, 113, 113, 0.1); }
.empty { color: var(--text-3); text-align: center; padding: 30px 0; font-size: 13px; }
.err {
  margin-top: 12px; padding: 8px 12px; border-radius: 10px; font-size: 12.5px;
  color: #fecaca; background: rgba(248, 113, 113, 0.08); border: 1px solid rgba(248, 113, 113, 0.28);
}
.foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.row-enter-active, .row-leave-active { transition: all 0.25s var(--ease); }
.row-enter-from, .row-leave-to { opacity: 0; transform: translateY(-6px); }
</style>
