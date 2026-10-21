<script setup lang="ts">
/** 知识库(RAG)管理面:collection 浏览、试搜、上传/本机路径入库、文档预览/删除、配置。
 *  数据全部来自 /api/knowledge/*(gateway 直连 rag_store,和模型侧 rag 工具同一索引)。 */
import { onMounted, ref } from "vue";
import {
  NButton, NEmpty, NInput, NInputNumber, NModal, NSelect, NSpin, NSwitch, NTag, useDialog, useMessage,
} from "naive-ui";
import {
  kbDeleteDoc, kbGetConfig, kbGetDoc, kbIngestPath, kbListDocs, kbSaveConfig,
  kbSearch, kbStatus, kbUpload,
} from "../../api";
import type { KnowledgeDoc, KnowledgeHit, KnowledgeStatus } from "../../types";

const message = useMessage();
const dialog = useDialog();

const status = ref<KnowledgeStatus | null>(null);
const docs = ref<KnowledgeDoc[]>([]);
const total = ref(0);
const loading = ref(true);
const activeCollection = ref("");
const docQuery = ref("");

const searchQuery = ref("");
const searchMode = ref("");
const searching = ref(false);
const hits = ref<KnowledgeHit[] | null>(null);
const hitsMode = ref("");
const hitsWarning = ref("");

const showPreview = ref(false);
const previewDoc = ref<any>(null);
const showConfig = ref(false);
const cfgDraft = ref<any>(null);
const savingCfg = ref(false);

const uploading = ref(false);
const ingestPath = ref("");
const ingestingPath = ref(false);
const dragDepth = ref(0);
const fileInput = ref<HTMLInputElement>();

const modeOptions = [
  { label: "默认模式", value: "" },
  { label: "direct 关键词", value: "direct" },
  { label: "embedding 向量", value: "embedding" },
  { label: "hybrid 混合", value: "hybrid" },
];
const providerOptions = [
  { label: "关闭", value: "none" },
  { label: "local_http(Ollama 等)", value: "local_http" },
  { label: "sentence_transformers", value: "sentence_transformers" },
];

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
function fmtTime(s: string): string {
  return (s || "").slice(0, 19).replace("T", " ");
}

async function loadStatus() {
  status.value = await kbStatus();
}
async function loadDocs() {
  loading.value = true;
  try {
    const r = await kbListDocs(activeCollection.value, docQuery.value.trim());
    docs.value = r.documents;
    total.value = r.total;
  } finally {
    loading.value = false;
  }
}
async function reload() {
  await Promise.all([loadStatus(), loadDocs()]);
}
function selectCollection(c: string) {
  activeCollection.value = c;
  hits.value = null;
  loadDocs();
}

async function doSearch() {
  const q = searchQuery.value.trim();
  if (!q) return;
  searching.value = true;
  try {
    const r = await kbSearch({
      query: q,
      collection: activeCollection.value || undefined,
      mode: searchMode.value || undefined,
    });
    hits.value = r.results;
    hitsMode.value = r.mode;
    hitsWarning.value = r.warning || "";
    if (r.warning) message.warning(`向量检索不可用,已回退关键词:${r.warning}`);
  } catch (e: any) {
    message.error(String(e?.message || e));
  } finally {
    searching.value = false;
  }
}

async function openPreview(doc: KnowledgeDoc) {
  previewDoc.value = await kbGetDoc(doc.id);
  showPreview.value = true;
}

function removeDoc(doc: KnowledgeDoc) {
  dialog.warning({
    title: "删除文档",
    content: `确定删除「${doc.title}」?分块与索引一并删除,不可恢复。`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      await kbDeleteDoc(doc.id);
      message.success("已删除");
      reload();
    },
  });
}

async function uploadFiles(files: FileList | File[]) {
  const list = Array.from(files);
  if (!list.length) return;
  uploading.value = true;
  const target = activeCollection.value || "general";
  let ok = 0;
  try {
    for (const f of list) {
      try {
        const r = await kbUpload(f, target);
        ok += r.status === "exists" ? 0 : 1;
        if (r.status === "exists") message.info(`「${f.name}」内容未变,已跳过`);
      } catch (e: any) {
        message.error(`「${f.name}」:${e?.message || e}`);
      }
    }
    if (ok) message.success(`已入库 ${ok} 篇 → ${target}`);
  } finally {
    uploading.value = false;
    reload();
  }
}
function onDrop(e: DragEvent) {
  dragDepth.value = 0;
  if (e.dataTransfer?.files?.length) uploadFiles(e.dataTransfer.files);
}
function onPick(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.length) uploadFiles(input.files);
  input.value = "";
}

async function doIngestPath() {
  const p = ingestPath.value.trim();
  if (!p) return;
  ingestingPath.value = true;
  try {
    await kbIngestPath({ path: p, collection: activeCollection.value || "general" });
    message.success("已入库");
    ingestPath.value = "";
    reload();
  } catch (e: any) {
    message.error(String(e?.message || e));
  } finally {
    ingestingPath.value = false;
  }
}

async function openConfig() {
  cfgDraft.value = await kbGetConfig();
  showConfig.value = true;
}
async function saveConfig() {
  savingCfg.value = true;
  try {
    const d = cfgDraft.value;
    await kbSaveConfig({ search: d.search, embedding: d.embedding, ingest: d.ingest });
    message.success("配置已保存");
    showConfig.value = false;
    loadStatus();
  } catch (e: any) {
    message.error(String(e?.message || e));
  } finally {
    savingCfg.value = false;
  }
}

onMounted(reload);
</script>

<template>
  <div class="kb">
    <aside class="kb-side glass">
      <div class="kb-side-title">知识库</div>
      <button class="kb-col" :class="{ on: activeCollection === '' }" @click="selectCollection('')">
        <span>全部</span><em>{{ status?.documents ?? 0 }}</em>
      </button>
      <button v-for="c in status?.collections ?? []" :key="c.collection" class="kb-col"
              :class="{ on: activeCollection === c.collection }" @click="selectCollection(c.collection)">
        <span>{{ c.collection }}</span><em>{{ c.documents }}</em>
      </button>

      <div class="kb-drop" :class="{ over: dragDepth > 0, busy: uploading }"
           @click="fileInput?.click()"
           @dragenter.prevent="dragDepth++" @dragleave.prevent="dragDepth--"
           @dragover.prevent @drop.prevent="onDrop">
        <template v-if="uploading">上传中…</template>
        <template v-else>拖拽文件到这里入库<br><span>或点击选择 · 进 {{ activeCollection || "general" }}</span></template>
      </div>
      <input ref="fileInput" type="file" multiple hidden @change="onPick">

      <div class="kb-path">
        <n-input v-model:value="ingestPath" size="small" placeholder="本机绝对路径导入…"
                 @keyup.enter="doIngestPath" />
        <n-button size="small" :loading="ingestingPath" @click="doIngestPath">导入</n-button>
      </div>
    </aside>

    <section class="kb-main">
      <div class="kb-status glass">
        <span>文档 <b>{{ status?.documents ?? "…" }}</b></span>
        <span>分块 <b>{{ status?.chunks ?? "…" }}</b></span>
        <span>FTS <b>{{ status?.fts_tokenizer || "关" }}</b></span>
        <span>embedding <b>{{ status?.config?.embedding?.enabled ? "开" : "关" }}</b></span>
        <span class="kb-flex" />
        <n-button size="small" quaternary @click="openConfig">配置</n-button>
      </div>

      <div class="kb-search glass">
        <n-input v-model:value="searchQuery" placeholder="试搜:和模型看到的检索结果一致"
                 clearable @keyup.enter="doSearch" />
        <n-select v-model:value="searchMode" :options="modeOptions" class="kb-mode" />
        <n-button type="primary" :loading="searching" @click="doSearch">试搜</n-button>
      </div>

      <div v-if="hits" class="kb-hits glass">
        <div class="kb-hits-head">
          <span>命中 {{ hits.length }} 条 · {{ hitsMode }}</span>
          <span v-if="hitsWarning" class="kb-warn">已回退关键词检索</span>
          <span class="kb-flex" />
          <n-button size="tiny" quaternary @click="hits = null">关闭</n-button>
        </div>
        <div v-for="h in hits" :key="h.chunk_id" class="kb-hit">
          <div class="kb-hit-head">
            <b>{{ h.title }}</b>
            <n-tag size="tiny" :bordered="false">{{ h.collection }}</n-tag>
            <span class="kb-score">{{ h.similarity != null ? h.similarity.toFixed(3) : h.score.toFixed(3) }}</span>
          </div>
          <div class="kb-hit-body">{{ h.content }}</div>
          <div v-if="h.source" class="kb-hit-src">{{ h.source }}</div>
        </div>
        <n-empty v-if="!hits.length" description="没有命中——库里可能还没收录相关资料" />
      </div>

      <div class="kb-docs glass">
        <div class="kb-docs-head">
          <span>文档 {{ total }}</span>
          <span class="kb-flex" />
          <n-input v-model:value="docQuery" size="small" placeholder="过滤标题/来源…"
                   clearable class="kb-filter" @keyup.enter="loadDocs" />
        </div>
        <n-spin :show="loading">
          <div v-for="d in docs" :key="d.id" class="kb-doc">
            <div class="kb-doc-main">
              <b>{{ d.title }}</b>
              <n-tag size="tiny" :bordered="false">{{ d.collection }}</n-tag>
            </div>
            <div class="kb-doc-meta">
              {{ d.chunks }} 块 · {{ fmtSize(d.size) }} · {{ fmtTime(d.updated_at) }}
              <template v-if="d.source"> · {{ d.source }}</template>
            </div>
            <div class="kb-doc-ops">
              <n-button size="tiny" quaternary @click="openPreview(d)">预览</n-button>
              <n-button size="tiny" quaternary type="error" @click="removeDoc(d)">删除</n-button>
            </div>
          </div>
          <n-empty v-if="!loading && !docs.length"
                   description="还没有文档——拖文件到左侧,或让模型用 rag 工具收录结论" />
        </n-spin>
      </div>
    </section>

    <n-modal v-model:show="showPreview" preset="card" :title="previewDoc?.title"
             class="kb-preview" :bordered="false">
      <div class="kb-preview-meta">
        {{ previewDoc?.collection }} · {{ previewDoc?.chunks }} 块
        <template v-if="previewDoc?.truncated"> · 内容过长已截断</template>
      </div>
      <pre class="kb-preview-body">{{ previewDoc?.content }}</pre>
    </n-modal>

    <n-modal v-model:show="showConfig" preset="card" title="知识库配置"
             class="kb-config" :bordered="false">
      <template v-if="cfgDraft">
        <div class="kb-cfg-row">
          <span>检索模式</span>
          <n-select v-model:value="cfgDraft.search.mode" :options="modeOptions.slice(1)" />
        </div>
        <div class="kb-cfg-row">
          <span>top_k</span>
          <n-input-number v-model:value="cfgDraft.search.top_k" :min="1" :max="50" />
        </div>
        <div class="kb-cfg-row">
          <span>分块大小 / 重叠</span>
          <div class="kb-cfg-pair">
            <n-input-number v-model:value="cfgDraft.search.chunk_size" :min="128" />
            <n-input-number v-model:value="cfgDraft.search.chunk_overlap" :min="0" />
          </div>
        </div>
        <div class="kb-cfg-row">
          <span>启用 embedding</span>
          <n-switch v-model:value="cfgDraft.embedding.enabled" />
        </div>
        <template v-if="cfgDraft.embedding.enabled">
          <div class="kb-cfg-row">
            <span>provider</span>
            <n-select v-model:value="cfgDraft.embedding.provider" :options="providerOptions" />
          </div>
          <div class="kb-cfg-row">
            <span>endpoint</span>
            <n-input v-model:value="cfgDraft.embedding.endpoint"
                     placeholder="http://127.0.0.1:11434/v1/embeddings" />
          </div>
          <div class="kb-cfg-row">
            <span>model</span>
            <n-input v-model:value="cfgDraft.embedding.model" placeholder="bge-m3" />
          </div>
        </template>
        <div class="kb-cfg-note">保存即写入 toolbox/rag_config.yaml;分块参数只影响之后入库的文档。</div>
      </template>
      <template #footer>
        <n-button type="primary" :loading="savingCfg" @click="saveConfig">保存</n-button>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.kb { display: flex; gap: 14px; padding: 18px; height: 100%; min-height: 0; }

.kb-side {
  width: 220px; flex-shrink: 0; border-radius: 16px; padding: 14px;
  display: flex; flex-direction: column; gap: 6px; overflow-y: auto;
}
.kb-side-title { font-size: 13px; font-weight: 600; color: var(--text-2); margin-bottom: 6px; }
.kb-col {
  display: flex; justify-content: space-between; align-items: center;
  padding: 7px 10px; border: 0; border-radius: 10px; background: transparent;
  color: var(--text-3); cursor: pointer; font-size: 13px; transition: background 0.2s, color 0.2s;
}
.kb-col em { font-style: normal; font-size: 11px; opacity: 0.7; }
.kb-col:hover { background: var(--glass-hover); color: var(--text); }
.kb-col.on { background: linear-gradient(160deg, rgba(143, 124, 255, 0.22), rgba(45, 212, 191, 0.1)); color: #fff; }

.kb-drop {
  margin-top: auto; border: 1.5px dashed rgba(255, 255, 255, 0.18); border-radius: 12px;
  padding: 16px 10px; text-align: center; font-size: 12px; color: var(--text-3);
  cursor: pointer; transition: border-color 0.2s, background 0.2s;
}
.kb-drop span { opacity: 0.65; font-size: 11px; }
.kb-drop.over { border-color: var(--accent); background: rgba(143, 124, 255, 0.1); }
.kb-drop.busy { opacity: 0.6; pointer-events: none; }
.kb-path { display: flex; gap: 6px; margin-top: 8px; }

.kb-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.kb-status {
  display: flex; gap: 18px; align-items: center; border-radius: 14px;
  padding: 10px 16px; font-size: 12.5px; color: var(--text-3);
}
.kb-status b { color: var(--text); font-weight: 600; }
.kb-flex { flex: 1; }

.kb-search { display: flex; gap: 10px; border-radius: 14px; padding: 10px 12px; }
.kb-mode { width: 150px; flex-shrink: 0; }

.kb-hits { border-radius: 14px; padding: 12px 14px; max-height: 45%; overflow-y: auto; }
.kb-hits-head { display: flex; align-items: center; gap: 10px; font-size: 12.5px; color: var(--text-2); margin-bottom: 8px; }
.kb-warn { color: var(--warn, #fbbf24); font-size: 11.5px; }
.kb-hit { border-top: 1px solid rgba(255, 255, 255, 0.06); padding: 10px 2px; }
.kb-hit:first-of-type { border-top: 0; }
.kb-hit-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.kb-score { margin-left: auto; font-size: 11px; color: var(--text-3); font-family: var(--mono, monospace); }
.kb-hit-body {
  margin-top: 5px; font-size: 12.5px; color: var(--text-2); line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
}
.kb-hit-src { margin-top: 4px; font-size: 11px; color: var(--text-3); }

.kb-docs { flex: 1; min-height: 0; border-radius: 14px; padding: 12px 14px; overflow-y: auto; }
.kb-docs-head { display: flex; align-items: center; gap: 10px; font-size: 12.5px; color: var(--text-2); margin-bottom: 8px; }
.kb-filter { width: 200px; }
.kb-doc {
  display: flex; align-items: center; gap: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06); padding: 9px 2px;
}
.kb-doc:first-of-type { border-top: 0; }
.kb-doc-main { display: flex; align-items: center; gap: 8px; min-width: 0; }
.kb-doc-main b { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
.kb-doc-meta { flex: 1; font-size: 11.5px; color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-doc-ops { display: flex; gap: 2px; flex-shrink: 0; }

.kb-preview { width: min(760px, 92vw); }
.kb-preview-meta { font-size: 12px; color: var(--text-3); margin-bottom: 8px; }
.kb-preview-body {
  max-height: 60vh; overflow: auto; font-size: 12.5px; line-height: 1.65;
  white-space: pre-wrap; word-break: break-word; font-family: var(--mono, monospace);
}
.kb-config { width: min(560px, 92vw); }
.kb-cfg-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.kb-cfg-row > span { width: 110px; flex-shrink: 0; font-size: 12.5px; color: var(--text-2); }
.kb-cfg-pair { display: flex; gap: 8px; flex: 1; }
.kb-cfg-note { font-size: 11.5px; color: var(--text-3); margin-top: 4px; }

@media (max-width: 768px) {
  .kb { flex-direction: column; overflow-y: auto; }
  .kb-side { width: 100%; }
  .kb-drop { margin-top: 8px; }
}
</style>
