/** 技能库状态：目录列表 + 对话勾选。注入走 session.send() 的 skills 字段。 */
import { defineStore } from "pinia";
import { deleteSkill, listSkills, saveSkill } from "../api";
import type { SkillInfo } from "../types";

export const useSkillsStore = defineStore("skills", {
  state: () => ({
    skills: [] as SkillInfo[],
    loaded: false,
    /** 勾选的技能名：随每条消息发送（system 通道注入，仅当次任务生效） */
    selected: [] as string[],
  }),

  actions: {
    async load() {
      this.skills = await listSkills();
      // 已被删除的技能从勾选里剔除
      const names = new Set(this.skills.map((s) => s.name));
      this.selected = this.selected.filter((n) => names.has(n));
      this.loaded = true;
    },

    toggle(name: string) {
      const i = this.selected.indexOf(name);
      if (i >= 0) this.selected.splice(i, 1);
      else this.selected.push(name);
    },

    /** 供 session.send() 取值：勾选且仍存在的技能 */
    effective(): string[] {
      if (!this.selected.length) return [];
      const names = new Set(this.skills.map((s) => s.name));
      return this.selected.filter((n) => names.has(n));
    },

    async create(name: string, description: string, content: string) {
      await saveSkill({ name, description, content });
      await this.load();
    },

    async remove(name: string) {
      await deleteSkill(name);
      await this.load();
    },
  },
});
