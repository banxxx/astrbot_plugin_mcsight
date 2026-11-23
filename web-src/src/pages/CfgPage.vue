<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import UiBadge from "../components/UiBadge.vue";
import UiButton from "../components/UiButton.vue";
import UiIcon from "../components/UiIcon.vue";
import UiInput from "../components/UiInput.vue";
import UiModal from "../components/UiModal.vue";
import UiNotice from "../components/UiNotice.vue";
import UiSecretInput from "../components/UiSecretInput.vue";
import UiSelect from "../components/UiSelect.vue";
import UiSwitch from "../components/UiSwitch.vue";
import ActivationPanel from "../components/ActivationPanel.vue";
import { S, configSaving, dirtyCount, discardDrafts, fail, loadConfig, loading, openDialog, saveDrafts } from "../store";
import { useIsMobile } from "../lib/useMediaQuery";
import type { ConfigItem } from "../lib/types";

const isMobile = useIsMobile();
const saving = ref(false);
const secretEditing = reactive<Record<string, boolean>>({});
const secretToClear = ref<ConfigItem | null>(null);
const COLLAPSIBLE: string[] = [];
const collapsed = reactive<Record<string, boolean>>({});
onMounted(() => { loadConfig().catch(fail); });
async function retryConfig() {
  S.error = "";
  try { await loadConfig(true); } catch (error) { fail(error); }
}

const LABELS: Record<string, string> = {
  super_admins: "超级管理员", whitelist_admins: "白名单管理员", blacklist: "黑名单",
  mod_api_token: "模组 API 鉴权 Token",
  db_host: "数据库主机地址", db_port: "数据库端口", db_user: "数据库用户名",
  db_password: "数据库密码", db_name: "数据库名称",
};
const HINTS: Record<string, string> = {
  super_admins: "两个平台共用；QQ 号为 5–12 位数字，openid 为 16–64 位英文或数字。QQ 官方 openid 通常按群区分，跨群需逐群添加。",
  whitelist_admins: "两个平台共用；QQ 号为 5–12 位数字，openid 为 16–64 位英文或数字。QQ 官方 openid 通常按群区分，跨群需逐群添加。",
  blacklist: "两个平台共用；QQ 号为 5–12 位数字，openid 为 16–64 位英文或数字。QQ 官方 openid 通常按群区分，跨群封禁需逐群添加。",
  mod_api_token: "OneBot 与 QQ 官方共用；与服务器模组配置一致，保存的值不回显。",
  db_host: "OneBot 与 QQ 官方共用；环境级 center_settings.json 可覆盖。",
  db_port: "OneBot 与 QQ 官方共用。", db_user: "OneBot 与 QQ 官方共用；完整用户名含集群前缀。",
  db_password: "OneBot 与 QQ 官方共用；留空保留原值，使用清除按钮移除。", db_name: "两个平台使用不同数据表，可以放在同一个库。",
};
const DESCRIPTIONS: Record<string, string> = {
  "公共参数": "两个平台共用的权限名单、Token 与中心数据库连接",
  中心数据库: "管理绑定数据的保存位置",
  其他: "兜底参数",
};
const PLACEHOLDERS: Record<string, string> = {
  db_host: "请输入数据库主机地址", db_port: "端口号", db_user: "请输入用户名", db_name: "请输入数据库名称", default_group_id: "请输入默认群号",
};
function selectOptions(it: ConfigItem) {
  return (it.options ?? []).map(value => ({ value, label: value }));
}
function label(it: ConfigItem) { return LABELS[it.key] || it.desc || it.key; }
function hint(it: ConfigItem) { return HINTS[it.key] || it.hint || ""; }
function draft(it: ConfigItem): string { return String(it.key in S.cfgDraft ? S.cfgDraft[it.key] ?? "" : it.value ?? ""); }
function boolVal(it: ConfigItem) { return it.key in S.cfgDraft ? Boolean(S.cfgDraft[it.key]) : Boolean(it.value); }
function setDraft(it: ConfigItem, value: unknown) {
  if (configSaving.value) return;
  const next = it.type === "bool" ? Boolean(value) : String(value ?? "");
  const original = it.type === "bool" ? Boolean(it.value) : String(it.value ?? "");
  if (next === original) delete S.cfgDraft[it.key]; else S.cfgDraft[it.key] = value;
}
function count(it: ConfigItem) {
  const value = it.key in S.cfgDraft ? S.cfgDraft[it.key] : it.value;
  return Array.isArray(value) ? value.length : 0;
}
function secretStatus(it: ConfigItem) {
  if (it.key in S.cfgDraft) return S.cfgDraft[it.key] ? "待更新" : "待清除";
  return it.has_value ? "已设置" : "未设置";
}
function clearSecret() {
  const it = secretToClear.value;
  if (!it) return;
  if (it.has_value) S.cfgDraft[it.key] = ""; else delete S.cfgDraft[it.key];
  secretEditing[it.key] = false;
  secretToClear.value = null;
}
async function save() {
  if (saving.value) return;
  saving.value = true;
  try { await saveDrafts(); } finally { saving.value = false; }
}
const groups = computed(() => {
  const out: { title: string; items: ConfigItem[] }[] = [];
  for (const it of S.cfgItems) {
    let group = out.find(g => g.title === it.section);
    if (!group) { group = { title: it.section, items: [] }; out.push(group); }
    group.items.push(it);
  }
  return out;
});
function isCollapsed(title: string) { return isMobile.value && !!collapsed[title]; }
const restartRequired = computed(() => S.cfgItems.some(it => it.key in S.cfgDraft && it.restart_required));
</script>

<template>
  <div class="cfg-page">
    <section class="page-heading">
      <div><h1>全局参数</h1><p>{{ isMobile ? "保存后按提示重载插件" : "这里只保留两个平台共用的权限名单、Token 与中心数据库连接；群规则都在「群与服务器」。" }}</p></div>
      <UiButton v-if="!isMobile" kind="line" :disabled="!S.cfgItems.length" @click="openDialog('json-config')"><UiIcon name="braces" :size="16" />查看 JSON</UiButton>
    </section>
    <ActivationPanel />
    <p class="cfg-guidance"><UiIcon name="info" :size="15" /><span>修改后统一保存，再按提示重载插件生效。请避免同时在 AstrBot 原生表单中编辑。</span></p>
    <div v-if="loading && !S.cfgItems.length" class="cfg-loading" role="status"><UiIcon name="loader-circle" :size="18" />正在读取配置…</div>
    <section v-else-if="!S.cfgItems.length && S.error" class="cfg-loading" role="alert"><UiIcon name="circle-alert" :size="18" /><span>暂时无法读取配置</span><UiButton kind="line" @click="retryConfig">重新加载</UiButton></section>
    <section v-for="group in groups" :key="group.title" class="cfg-section">
      <button v-if="isMobile && COLLAPSIBLE.includes(group.title)" type="button" class="cfg-section-heading cfg-collapse" :aria-expanded="!isCollapsed(group.title)" @click="collapsed[group.title] = !collapsed[group.title]">
        <span><b>{{ group.title }}</b><small v-if="isCollapsed(group.title)">{{ group.items.length }} 项设置 · 点击展开</small></span>
        <UiIcon :name="isCollapsed(group.title) ? 'chevron-down' : 'chevron-up'" :size="18" />
      </button>
      <div v-else class="cfg-section-heading"><h2>{{ group.title }}</h2><span v-if="!isMobile">{{ DESCRIPTIONS[group.title] || S.cfgSections.find(s => s.title === group.title)?.desc }}</span></div>
      <div v-if="!isCollapsed(group.title)" class="cfg-fields">
        <div v-for="it in group.items" :key="it.key" class="cfg-field-wrap">
          <div class="cfg-field" :class="{ 'is-text': !it.secret && !['bool', 'list'].includes(it.type), 'is-secret': it.secret }">
            <div class="cfg-label"><label :for="`cfg-${it.key}`">{{ label(it) }}</label><p v-if="!isMobile || !['bool','list'].includes(it.type)">{{ hint(it) }}</p><span v-if="isMobile && it.type === 'list'" class="cfg-mobile-count">{{ count(it) }} 位成员</span></div>
            <div class="cfg-control">
              <UiSwitch v-if="it.type === 'bool'" :id="`cfg-${it.key}`" :on="boolVal(it)" :disabled="configSaving" :aria-label="label(it)" @toggle="setDraft(it, !boolVal(it))" />
              <template v-else-if="it.type === 'list'">
                <UiBadge v-if="!isMobile" tone="neutral">{{ count(it) }} 位成员</UiBadge>
                <UiButton kind="line" :disabled="configSaving" @click="openDialog('list-manage', { item: it, label: label(it) })"><UiIcon v-if="!isMobile" name="users" :size="16" />{{ isMobile ? "管理" : "管理名单" }}</UiButton>
              </template>
              <template v-else-if="it.secret">
                <UiBadge tone="neutral">{{ secretStatus(it) }}</UiBadge>
                <UiButton kind="line" :disabled="configSaving" :aria-expanded="!!secretEditing[it.key]" @click="secretEditing[it.key] = !secretEditing[it.key]">{{ secretEditing[it.key] ? "收起" : (it.has_value ? "重新设置" : "设置") }}</UiButton>
                <UiButton kind="line" :disabled="configSaving || (!it.has_value && !(it.key in S.cfgDraft))" @click="secretToClear = it">清除</UiButton>
              </template>
              <UiSelect v-else-if="it.options?.length" :id="`cfg-${it.key}`" :model-value="draft(it)" :options="selectOptions(it)" :disabled="configSaving" :aria-label="label(it)" @update:model-value="v => setDraft(it, v)" />
              <UiInput v-else :id="`cfg-${it.key}`" :model-value="draft(it)" :disabled="configSaving" :placeholder="PLACEHOLDERS[it.key] || String(it.default ?? '')" :inputmode="it.type === 'int' ? 'numeric' : undefined" @update:model-value="v => setDraft(it, v)" />
            </div>
          </div>
          <div v-if="it.secret && secretEditing[it.key]" class="cfg-secret-editor">
            <UiSecretInput :id="`cfg-${it.key}`" :model-value="draft(it)" :has-value="it.has_value" :disabled="configSaving" @update:model-value="v => setDraft(it, v)" />
            <p>修改将加入草稿，点击本页“保存更改”后生效。</p>
          </div>
        </div>
      </div>
    </section>
    <UiButton v-if="isMobile" kind="line" :disabled="!S.cfgItems.length" @click="openDialog('json-config')"><UiIcon name="braces" :size="16" />查看 JSON</UiButton>
    <UiNotice v-if="S.error" tone="bad">{{ S.error }}</UiNotice>
    <section v-if="!isMobile && S.cfgItems.length" class="cfg-savebar" aria-live="polite">
      <UiIcon :name="dirtyCount ? 'alert-circle' : 'check-circle'" :size="18" :class="dirtyCount ? 'pending-icon' : 'saved-icon'" />
      <div class="cfg-save-status"><b>{{ dirtyCount ? "有未保存的更改" : "所有更改已保存" }}</b><span>{{ dirtyCount ? (restartRequired ? "保存成功后需重载插件" : "保存后按提示生效") : "修改全局参数后，在这里统一保存" }}</span></div>
      <div class="cfg-save-actions"><UiButton kind="line" :disabled="!dirtyCount || saving" @click="discardDrafts">放弃更改</UiButton><UiButton kind="pri" :loading="saving" :disabled="!dirtyCount || saving" @click="save"><UiIcon name="check" :size="16" />保存更改</UiButton></div>
    </section>
    <UiModal v-if="secretToClear" :title="`清除${label(secretToClear)}`" subtitle="此操作加入全局草稿，保存后生效。" @close="secretToClear = null">
      <p class="cfg-clear-description">清除后，对应连接或鉴权可能不可用。</p>
      <template #footer><UiButton kind="line" @click="secretToClear = null">取消</UiButton><UiButton kind="danger" :disabled="configSaving" @click="clearSecret">确认清除</UiButton></template>
    </UiModal>
  </div>
</template>

<style scoped>
.cfg-page { display:flex; flex-direction:column; gap:20px; min-width:0; }
.cfg-guidance { display:flex; align-items:flex-start; gap:8px; margin:0; color:var(--ink3); font-size:12px; line-height:1.6; }
.cfg-guidance svg { flex:none; margin-top:1px; color:var(--ink4); }
.cfg-section { background:var(--surface); border:1px solid var(--bd); border-radius:12px; overflow:hidden; }
.cfg-section-heading { display:flex; align-items:center; gap:12px; min-height:56px; padding:16px 22px; border-bottom:1px solid var(--row); }
.cfg-section-heading h2,.cfg-section-heading b { margin:0; color:var(--ink); font-size:16px; font-weight:600; line-height:24px; }
.cfg-section-heading>span { font-size:12px; color:var(--ink3); }
.cfg-field { display:flex; align-items:center; padding:15px 22px; gap:20px; min-height:64px; }
.cfg-field-wrap+.cfg-field-wrap { border-top:1px solid var(--row); }
.cfg-label { flex:1 1 auto; min-width:0; display:flex; flex-direction:column; gap:4px; }
.cfg-label label { font-size:14px; color:var(--ink); font-weight:500; line-height:21px; }
.cfg-label p { margin:0; color:var(--ink3); font-size:12px; line-height:1.6; max-width:620px; }
.cfg-control { display:flex; flex:none; align-items:center; gap:16px; }
.cfg-control>.inp { width:300px; }
.cfg-mobile-count { color:var(--ink3); font-size:12px; }
.cfg-secret-editor { padding:0 20px 16px; display:flex; flex-direction:column; align-items:flex-end; gap:8px; }
.cfg-secret-editor>.sec { width:min(100%,420px); }
.cfg-secret-editor p { margin:0; font-size:12px; color:var(--ink3); }
.cfg-collapse { width:100%; border:0; background:var(--surface); cursor:pointer; justify-content:space-between; font-family:var(--ff); text-align:left; }
.cfg-collapse>span { display:flex; flex-direction:column; gap:4px; }
.cfg-collapse small { color:var(--ink3); font-size:12px; font-weight:400; }
.cfg-savebar { display:flex; align-items:center; gap:12px; min-height:72px; padding:14px 20px; background:var(--surface); border:1px solid var(--bd); border-radius:12px; }
.cfg-save-status { display:flex; flex-direction:column; gap:3px; }
.cfg-save-status b { font-size:14px; font-weight:500; }
.cfg-save-status span { font-size:12px; color:var(--ink3); }
.cfg-save-actions { display:flex; gap:12px; margin-left:auto; }
.pending-icon { color:var(--warn); }.saved-icon { color:var(--ok); }
.cfg-loading { display:flex; align-items:center; justify-content:center; gap:10px; min-height:120px; color:var(--ink3); background:var(--surface); border:1px solid var(--bd); border-radius:12px; }
.cfg-clear-description { margin:0; color:var(--ink3); font-size:13px; }
@media (max-width:1100px) and (min-width:901px) { .cfg-control>.inp { width:240px; } }
@media (max-width:900px),(max-height:520px) {
  .cfg-page { gap:14px; }
  .cfg-section-heading { padding:16px; }
  .cfg-field { padding:14px 16px; gap:12px; min-height:58px; }
  .cfg-field.is-text,.cfg-field.is-secret { align-items:flex-start; flex-direction:column; }
  .cfg-field.is-text .cfg-control { width:100%; }
  .cfg-control { gap:8px; }
  .cfg-control>.inp { width:100%; }
  .cfg-secret-editor { padding:0 16px 16px; align-items:stretch; }
  .cfg-secret-editor>.sec { width:100%; }
  .cfg-collapse { min-height:52px; }
}
</style>
