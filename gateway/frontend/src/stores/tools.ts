/** 工具配置状态：工具目录、全局禁用、工具集预设（草稿 + 保存）。 */
import { defineStore } from "pinia";
import { getTools, saveToolsets } from "../api";
import type { ToolCard, ToolPreset } from "../types";

export const useToolsStore = defineStore("tools", {
  state: () => ({
    tools: [] as ToolCard[],
    disabled: [] as string[],
    defaultPreset: "all",
    presets: [] as ToolPreset[],
    loaded: false,
    loading: false,
    error: "",
    savedSnap: "",
    /** 对话里当前选中的工具集（每次发送随消息下发） */
    activePreset: localStorage.getItem("agg.toolset") || "",
  }),

  getters: {
    dirty(state): boolean {
      return state.loaded && JSON.stringify([state.disabled, state.defaultPreset, state.presets]) !== state.savedSnap;
    },
    byName(state): Map<string, ToolCard> { return new Map(state.tools.map((t) => [t.name, t])); },
    categories(state): string[] { return [...new Set(state.tools.map((t) => t.category || "misc"))].sort(); },
    presetOptions(state): { label: string; value: string }[] {
      return state.presets.map((p) => ({ label: p.name, value: p.id }));
    },
    /** 当前实际生效的预设 id（未手选就用默认） */
    effectivePreset(state): string {
      return state.presets.some((p) => p.id === state.activePreset) ? state.activePreset : state.defaultPreset;
    },
  },

  actions: {
    async load(force = false) {
      if (this.loaded && !force) return;
      this.loading = true;
      this.error = "";
      try {
        const s = await getTools();
        this.tools = s.tools;
        this.disabled = s.disabled;
        this.defaultPreset = s.default;
        this.presets = s.presets;
        this.snap();
        this.loaded = true;
      } catch (e: any) {
        this.error = e.message || String(e);
      } finally {
        this.loading = false;
      }
    },
    snap() { this.savedSnap = JSON.stringify([this.disabled, this.defaultPreset, this.presets]); },

    async save() {
      const r = await saveToolsets({
        disabled: this.disabled, default: this.defaultPreset,
        presets: this.presets.filter((p) => !p.builtin),
      });
      this.disabled = r.disabled;
      this.defaultPreset = r.default;
      this.presets = r.presets;
      for (const t of this.tools) t.enabled = !this.disabled.includes(t.name);
      this.snap();
    },

    toggle(name: string, on: boolean) {
      this.disabled = on ? this.disabled.filter((x) => x !== name) : [...new Set([...this.disabled, name])];
      const t = this.tools.find((x) => x.name === name);
      if (t) t.enabled = on;
    },

    setActive(id: string) {
      this.activePreset = id;
      localStorage.setItem("agg.toolset", id);
    },

    /** 某预设最终可用的工具（去掉全局禁用） */
    resolve(p: ToolPreset): ToolCard[] {
      const all = p.tools.includes("*");
      return this.tools.filter((t) => !this.disabled.includes(t.name) && (all || p.tools.includes(t.name)));
    },
  },
});
