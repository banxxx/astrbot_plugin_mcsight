<script setup lang="ts">
import { computed, onMounted } from "vue";
import UiButton from "../components/UiButton.vue";
import UiIcon from "../components/UiIcon.vue";
import UiInput from "../components/UiInput.vue";
import UiNotice from "../components/UiNotice.vue";
import UiSecretInput from "../components/UiSecretInput.vue";
import UiSwitch from "../components/UiSwitch.vue";
import { S, fail, loadConfig, openDialog } from "../store";
import type { ConfigItem } from "../lib/types";

onMounted(() => {
  loadConfig().catch(fail);
});

const LABELS: Record<string, string> = {
  enable_group_owner: "允许群主使用功能",
  enable_group_admin: "允许管理员使用功能",
  super_admins: "超级管理员",
  whitelist_admins: "白名单管理员",
  blacklist: "黑名单",
  default_api_port: "全局默认 API 端口",
  mod_api_token: "模组 API 鉴权 Token",
  use_central_db: "启用中心数据库（D1）",
  db_host: "数据库主机地址",
  db_port: "数据库端口",
  db_user: "数据库用户名",
  db_password: "数据库密码",
  db_name: "数据库名称",
  show_server_version: "在卡片上显示服务器版本",
  show_server_latency: "在卡片上显示服务器延迟",
  default_group_id: "默认群号",
};

/* 画布给每个字段定了不同的输入框宽度，不按内容自适应 */
const WIDTHS: Record<string, number> = {
  default_api_port: 120,
  db_host: 300,
  db_port: 120,
  db_user: 240,
  db_name: 200,
  default_group_id: 200,
};

function label(it: ConfigItem) {
  return LABELS[it.key] || it.desc;
}

function draft(it: ConfigItem): string {
  return it.key in S.cfgDraft ? String(S.cfgDraft[it.key]) : String(it.value ?? "");
}

function boolVal(it: ConfigItem): boolean {
  return it.key in S.cfgDraft ? Boolean(S.cfgDraft[it.key]) : Boolean(it.value);
}

/* 改回原值就不再算待保存，否则按钮会一直亮着 */
function setDraft(it: ConfigItem, v: unknown) {
  const isBool = it.type === "bool";
  const cur = isBool ? Boolean(it.value) : String(it.value ?? "");
  const next = isBool ? Boolean(v) : String(v ?? "");
  if (next === cur) delete S.cfgDraft[it.key];
  else S.cfgDraft[it.key] = v;
}

function listCount(it: ConfigItem): number {
  return Array.isArray(it.value) ? it.value.length : 0;
}

function defaultText(it: ConfigItem): string {
  const d = it.default;
  if (d === null || d === undefined || d === "" || (Array.isArray(d) && !d.length)) return "";
  return typeof d === "boolean" ? (d ? "默认 开" : "默认 关") : `默认 ${d}`;
}

/* 画布的说明行口径：键名 · 默认值 · 提示。
   schema 的 description 基本就是上面那行标题的另一种说法，画布每一行都没用它，这里也不再拼。 */
function meta(it: ConfigItem): string {
  return [it.key, defaultText(it), it.hint].filter(Boolean).join(" · ");
}

function cellStyle(it: ConfigItem) {
  return { width: `${WIDTHS[it.key] ?? 240}px` };
}

const sectionDesc = computed(() => {
  const map: Record<string, string> = {};
  for (const s of S.cfgSections) map[s.title] = s.desc;
  return map;
});

const groups = computed(() => {
  const out: { title: string; items: ConfigItem[] }[] = [];
  for (const it of S.cfgItems) {
    const last = out[out.length - 1];
    if (last && last.title === it.section) last.items.push(it);
    else out.push({ title: it.section, items: [it] });
  }
  return out;
});
</script>

<template>
  <div class="page">
    <section class="ph">
      <div class="ph-t">
        <h1>全局参数</h1>
        <p>_conf_schema.json · {{ S.cfgItems.length }} 个可配置键 · 本页直接改写该文件，保存后需重载插件</p>
      </div>
      <UiButton kind="tool" :disabled="!S.cfgItems.length" @click="openDialog('json-config')">
        <UiIcon name="braces" :size="13" />查看 JSON
      </UiButton>
    </section>

    <UiNotice tone="warn" class="warnbar">
      AstrBot 自带的面板表单与本页面写同一份 config 文件：两边同时打开时，后保存的会覆盖先保存的
    </UiNotice>

    <section v-for="grp in groups" :key="grp.title" class="card">
      <div class="ch">
        <span class="bar" />
        <h2>{{ grp.title }}</h2>
        <span class="hint">{{ [sectionDesc[grp.title], grp.items.length + " 个键"].filter(Boolean).join(" · ") }}</span>
      </div>

      <div v-for="it in grp.items" :key="it.key" class="prow">
        <div class="pl">
          <b>{{ label(it) }}</b>
          <span>{{ meta(it) }}</span>
        </div>

        <div class="pr">
          <UiSwitch v-if="it.type === 'bool'" :on="boolVal(it)" @toggle="setDraft(it, !boolVal(it))" />
          <UiSecretInput
            v-else-if="it.secret"
            :style="cellStyle(it)"
            :model-value="draft(it)"
            :has-value="it.has_value && !!it.key"
            @update:model-value="(v: string) => setDraft(it, v)"
          />
          <template v-else-if="it.type === 'list'">
            <span class="cnt">名单内 {{ listCount(it) }} 个</span>
            <UiButton kind="line" @click="openDialog('list-manage', { item: it, label: label(it) })"><UiIcon name="list" :size="13" />管理名单</UiButton>
          </template>
          <UiInput
            v-else
            class="cell"
            :style="cellStyle(it)"
            :model-value="draft(it)"
            :placeholder="it.default === '' || it.default === null ? '（空）' : String(it.default ?? '')"
            @update:model-value="(v: string) => setDraft(it, v)"
          />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--gap-card);
}

.ph {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.ph-t h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
}

.ph-t p {
  margin: 5px 0 0;
  font-size: 12px;
  color: var(--ink3);
}

.warnbar {
  min-height: 44px;
  padding: 0 12px;
  border-radius: 8px;
}

.ch {
  border-bottom: 1px solid var(--row);
}

.ch h2 {
  border-bottom: none;
}

.ch .bar {
  width: 3px;
  height: 18px;
  border-radius: 1.5px;
  background: var(--ac);
  flex: none;
}

/* 画布上分区说明紧跟标题左排，不像卡片头那样推到右端 */
.ch .hint {
  margin-left: 0;
}

.prow {
  display: flex;
  align-items: center;
  gap: 16px;
  height: var(--h-form);
  padding: 0 var(--pad-card);
  border-bottom: 1px solid var(--row);
}

.prow:last-child {
  border-bottom: none;
}

.pl {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.pl b {
  font-size: 13px;
  font-weight: 500;
  line-height: 16px;
  color: var(--ink);
}

.pl span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  line-height: 14px;
  color: var(--ink3);
}

.pr {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
}

.cnt {
  font-size: 11px;
  color: var(--ink4);
}

@container page (max-width: 900px) {
  .prow {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .pr {
    width: 100%;
  }

  .pr .cell,
  .pr .sec {
    flex: 1 1 auto;
    width: auto;
  }
}
</style>
