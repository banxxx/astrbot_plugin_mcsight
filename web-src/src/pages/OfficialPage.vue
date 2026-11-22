<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, officialBusy, officialDraftPending, S, toast } from "../store";
import type { BindRule, OfficialCandidate, OfficialGroup, OfficialState } from "../lib/types";
import UiButton from "../components/UiButton.vue";
import UiInput from "../components/UiInput.vue";
import UiNotice from "../components/UiNotice.vue";
import UiSelect from "../components/UiSelect.vue";
import UiSwitch from "../components/UiSwitch.vue";
import UiSecretInput from "../components/UiSecretInput.vue";
import OfficialOperations from "../components/OfficialOperations.vue";

const environment = ref("");
const state = ref<OfficialState | null>(null);
const groupId = ref("");
const draft = ref<OfficialGroup | null>(null);
const busy = ref(false);
const operationBusy = ref(false);
const working = computed(() => busy.value || operationBusy.value);
const error = ref("");
const token = ref("");
const alias = ref("");
const display = ref("");
const candidate = ref("");
const role = ref("group_admin");
const confirmed = ref(false);
const baseline = ref("");
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value) !== baseline.value);
const unsaved = computed(() => dirty.value || !!token.value);
watch([unsaved, busy, operationBusy], ([pending, running, recovering]) => {
  officialDraftPending.value = pending;
  officialBusy.value = running || recovering;
}, { flush: "sync" });
onBeforeUnmount(() => { officialDraftPending.value = false; officialBusy.value = false; });
const environments = computed(() => (S.activation?.environments ?? []).filter(e => e.platform === "qq_official")
  .map(e => ({ value: e.environment_id, label: e.application_ref, description: e.environment_id })));
const groups = computed(() => (state.value?.groups ?? []).map(g => ({ value: g.config_id, label: g.alias })));
const candidateOptions = computed(() => (state.value?.candidates ?? []).filter(c => !draft.value || c.group_openid === draft.value.group_openid)
  .map(c => ({ value: c.candidate_id, label: c.nickname || "昵称不可用", description: `${c.note || '本人登记'} · ${c.candidate_id}` })));
const selectedCandidate = computed(() => state.value?.candidates.find(c => c.candidate_id === candidate.value));
const roles = [{ value: "group_admin", label: "本群管理员" }, { value: "environment_admin", label: "环境超管（从本群身份授权）" },
  { value: "blacklist", label: "本群聊天黑名单" }, { value: "nickname_ignore", label: "昵称检测忽略" }, { value: "member", label: "撤销本群管理员（黑名单另行管理）" }];
const nicknameRules = [{ value: "paren", label: "括号内为游戏名" }, { value: "equals", label: "昵称等于游戏名" },
  { value: "contains", label: "昵称包含游戏名" }, { value: "template", label: "自定义模板" }];
const members = computed(() => draft.value ? [
  ...draft.value.administrators.map(id => ({ id, role: "群管理员" })),
  ...(state.value?.environment_administrators ?? []).map(id => ({ id, role: "环境超管" })),
  ...draft.value.blacklist.map(id => ({ id, role: "聊天黑名单" })),
  ...draft.value.nickname_ignore.map(id => ({ id, role: "昵称忽略" })),
] : []);
function chooseGroup(value: string) {
  if (operationBusy.value) return;
  groupId.value = value;
  draft.value = JSON.parse(JSON.stringify(state.value?.groups.find(g => g.config_id === value) ?? null));
  if (draft.value) draft.value.policy.nickname_check_enabled ??= true;
  baseline.value = JSON.stringify(draft.value);
  candidate.value = "";
  confirmed.value = false;
}
function accept(data: OfficialState) {
  state.value = data;
  chooseGroup(data.groups.some(g => g.config_id === groupId.value) ? groupId.value : data.groups[0]?.config_id ?? "");
}
async function load() {
  if (!environment.value || working.value) return;
  busy.value = true;
  error.value = "";
  try { accept(await api.officialState(environment.value)); token.value = ""; S.error = ""; }
  catch (e) { error.value = e instanceof Error ? e.message : "读取失败"; }
  finally { busy.value = false; }
}
async function changeEnvironment(value: string) {
  if (working.value) return;
  environment.value = value;
  state.value = null;
  draft.value = null;
  baseline.value = "";
  groupId.value = "";
  token.value = "";
  await load();
}
onMounted(async () => {
  try {
    S.activation = await api.activation();
    environment.value = environments.value.find(e => e.value === S.activation?.selection?.environment_id)?.value ?? environments.value[0]?.value ?? "";
    await load();
  } catch (e) { error.value = e instanceof Error ? e.message : "读取实例失败"; }
});
async function mutate(body: Record<string, unknown>) {
  if (working.value || !state.value) return;
  busy.value = true;
  error.value = "";
  try { accept(await api.officialUpdate({ environment_id: environment.value, ...body })); toast("官方配置已保存"); }
  catch (e) { error.value = e instanceof Error ? e.message : "保存失败，请刷新确认"; }
  finally { busy.value = false; }
}
async function approve(value: OfficialCandidate) {
  await mutate({ action: "approve", candidate_id: value.candidate_id, expected_revision: state.value?.revision,
    alias: alias.value, display_group: display.value });
}
async function save() {
  const g = draft.value;
  if (!g) return;
  await mutate({ action: "save", config_id: g.config_id, expected_revision: g.revision,
    changes: { alias: g.alias, display_group: g.display_group, enabled: g.enabled, servers: JSON.parse(JSON.stringify(g.servers)), policy: { ...g.policy } } });
}
async function authorize() {
  if (!confirmed.value || !draft.value || !candidate.value) return;
  await mutate({ action: "authorize", config_id: draft.value.config_id, candidate_id: candidate.value,
    role: role.value, expected_revision: role.value === "environment_admin" ? state.value?.revision : draft.value.revision });
}
async function revoke(id: string, role: string) {
  if (!draft.value) return;
  const envRole = role === "environment_admin";
  await mutate({ action: "revoke", config_id: draft.value.config_id, user_id: id, role,
    expected_revision: envRole ? state.value?.revision : draft.value.revision });
}
async function saveToken(clear = false) {
  const value = clear ? "" : token.value;
  token.value = "";
  await mutate({ action: "token", token: value });
}
function addServer() {
  if (draft.value && draft.value.servers.length < 20) draft.value.servers.push({ server_id: "", name: "", host: "", game_port: 25565, api_port: null, hidden: false, remark: "" });
}
function chooseNicknameRule(value: string) {
  if (draft.value && nicknameRules.some(rule => rule.value === value)) draft.value.policy.bind_nickname_rule = value as BindRule;
}
</script>

<template>
  <div class="official-page">
    <section class="page-heading"><div><h1>QQ 官方群</h1><p>先在实际官方群发送 /mc setup，再审核登记并配置本群功能。</p></div><UiButton kind="line" :disabled="working || !environment" @click="load">{{ unsaved ? '放弃草稿并刷新' : '刷新配置' }}</UiButton></section>
    <UiNotice>绑定按每次模组能力握手开放；hybrid、remote 和 local 均需单独配置权威。广播与控制台须管理员权限，控制台另需本服独立令牌。群管理授权不等于游戏绑定，聊天黑名单不会自动解绑。实际 QQ/MC 兼容仍需部署验收。</UiNotice>
    <UiNotice>全群绑定检测和昵称检测需要 QQ 平台内邀的成员接口权限。未获权时会明确提示；成员接口的用户名只用于显示，昵称检测仅核验近 5 分钟插件收到的群命令事件中的可靠群昵称，其余成员标为“无法核验”。</UiNotice>
    <UiNotice v-if="!environments.length" tone="warn">请先到“全局参数 → 生效机器人”保存官方实例及应用环境。</UiNotice>
    <label class="official-field">官方应用环境<UiSelect :model-value="environment" :options="environments" :disabled="working || dirty" aria-label="官方应用环境" @update:model-value="changeEnvironment" /></label>
    <UiNotice v-if="state && !state.active" tone="warn">当前环境尚未生效，可以准备本地配置；MC 查询须在完整重启并选中该环境后使用。</UiNotice>
    <UiNotice v-if="state && !state.message_delivery?.ready" tone="warn">{{ state.message_delivery?.message ?? '发送防重状态待核验' }}</UiNotice>
    <UiNotice v-if="error" tone="bad">{{ error }}</UiNotice>
    <UiNotice v-if="S.error" tone="warn">{{ S.error }}</UiNotice>
    <section v-if="state" class="official-card">
      <h2>待配置群</h2><p>登记有效期 24 小时。对照 QQ 客户端中的登记回执确认来源，昵称仅供显示。</p>
      <div class="official-grid"><label>新群别名<UiInput v-model="alias" :disabled="working" placeholder="例如：生存服玩家群" /></label><label>展示群号<UiInput v-model="display" :disabled="working" placeholder="只用于玩家提示，不作为身份键" /></label></div>
      <div v-for="c in state.candidates.filter(c => !state?.groups.some(g => g.group_openid === c.group_openid))" :key="c.candidate_id" class="official-candidate">
        <div><b>{{ c.nickname || '昵称不可用' }}</b><p>{{ c.note || '未填写备注' }}</p><code>{{ c.candidate_id }}</code></div>
        <UiButton kind="line" :disabled="working || dirty || !alias.trim()" @click="approve(c)">确认该群并创建配置</UiButton>
      </div>
      <p v-if="!state.candidates.length">尚无待审核登记。普通成员登记不会自动获得管理权限。</p>
    </section>
    <section v-if="state" class="official-card">
      <h2>模组连接</h2><p>该环境使用独立模组令牌，不会自动使用 OneBot 的旧令牌。</p>
      <UiSecretInput v-model="token" :has-value="state.has_mod_token" :disabled="working" />
      <div class="official-actions"><UiButton kind="line" :disabled="working || dirty || !token" @click="saveToken()">保存新令牌</UiButton><UiButton kind="line" :disabled="working || dirty || !state.has_mod_token" @click="saveToken(true)">清除令牌</UiButton><span>{{ state.has_mod_token ? '已配置（不回显）' : '未配置；无模组服务器仍可查询在线状态' }}</span></div>
    </section>
    <UiNotice v-if="dirty" tone="warn">本群有未保存的更改，请先保存再切换群或修改授权。刷新会放弃本页草稿。</UiNotice>
    <label v-if="groups.length" class="official-field">已确认官方群<UiSelect :model-value="groupId" :options="groups" :disabled="working || dirty" aria-label="选择官方群" @update:model-value="chooseGroup" /></label>
    <section v-if="draft" class="official-card">
      <div class="official-actions"><h2>群配置</h2><UiSwitch :on="draft.enabled" :disabled="working" aria-label="启用本群功能" @toggle="draft.enabled = !draft.enabled" /><span>启用本群功能</span></div>
      <div class="official-grid"><label>群别名<UiInput v-model="draft.alias" :disabled="working" /></label><label>展示群号<UiInput v-model="draft.display_group" :disabled="working" /></label></div>
      <p>模组绑定群键：<code>{{ draft.group_openid }}</code></p>
      <div class="official-actions"><label><input v-model="draft.policy.show_server_version" type="checkbox" :disabled="working" />显示版本</label><label><input v-model="draft.policy.show_server_latency" type="checkbox" :disabled="working" />显示可用延迟</label><label><input v-model="draft.policy.show_last_online" type="checkbox" :disabled="working" />显示最后在线</label><label><input v-model="draft.policy.enable_group_owner" type="checkbox" :disabled="working" />接受可靠群主角色</label><label><input v-model="draft.policy.enable_group_admin" type="checkbox" :disabled="working" />接受可靠管理员角色</label><label><input v-model="draft.policy.nickname_check_enabled" type="checkbox" :disabled="working" />开启昵称检测</label></div>
      <h3>绑定昵称规则</h3>
      <label><input v-model="draft.policy.bind_require_nickname" type="checkbox" :disabled="working" />绑定时校验可靠群昵称</label>
      <div class="official-grid"><label>匹配规则<UiSelect :model-value="draft.policy.bind_nickname_rule" :options="nicknameRules" :disabled="working || !draft.policy.bind_require_nickname" aria-label="绑定昵称匹配规则" @update:model-value="chooseNicknameRule" /></label><label v-if="draft.policy.bind_nickname_rule === 'template'">昵称模板<UiInput v-model="draft.policy.bind_nickname_template" :disabled="working || !draft.policy.bind_require_nickname" placeholder="例如：玩家（{gameId}）" /></label></div>
      <p>模板须包含一个 {gameId}。首绑匹配待绑账号，后续可匹配本人当前有效账号或待绑账号；每个新增账号仍须独立取码。开启校验时，缺少可靠群昵称会拒绝绑定，不使用用户名代替。全群昵称检测仍按括号格式提示。</p>
      <h3>服务器</h3>
      <div v-for="(server, index) in draft.servers" :key="server.server_id || `new-${index}`" class="official-server">
        <div class="official-grid"><label>名称<UiInput v-model="server.name" :disabled="working" /></label><label>主机<UiInput v-model="server.host" :disabled="working" placeholder="域名或 IPv4，不含端口" /></label><label>游戏端口<UiInput :model-value="String(server.game_port)" :disabled="working" inputmode="numeric" @update:model-value="value => server.game_port = Number(value)" /></label><label>模组 API 端口<UiInput :model-value="server.api_port === null ? '' : String(server.api_port)" :disabled="working" inputmode="numeric" placeholder="未装模组留空" @update:model-value="value => server.api_port = value ? Number(value) : null" /></label><label>备注<UiInput v-model="server.remark" :disabled="working" /></label></div>
        <div class="official-actions"><label><input v-model="server.hidden" type="checkbox" :disabled="working" />隐藏并停止查询</label><UiButton kind="line" :disabled="working" @click="draft.servers.splice(index, 1)">移除条目</UiButton><small>{{ server.server_id || '保存时生成稳定标识' }}</small></div>
      </div>
      <div class="official-actions"><UiButton kind="line" :disabled="working || draft.servers.length >= 20" @click="addServer">添加服务器</UiButton><UiButton kind="pri" :disabled="working" @click="save">保存本群配置</UiButton><span>保存启用后可在官方群使用 /mc status。</span></div>
    </section>
    <section v-if="draft" class="official-card">
      <h2>身份核对与授权</h2><p>请让目标成员发送 /mc setup，在 QQ 客户端核对该成员的回执；无法确认来源时保持待审核。已保存 {{ state?.authorization_history_count ?? 0 }} 条授权记录。</p>
      <div class="official-grid"><label>本群身份候选<UiSelect :model-value="candidate" :options="candidateOptions" :disabled="working" aria-label="身份候选" @update:model-value="value => { candidate = value; confirmed = false; }" /></label><label>授权角色<UiSelect v-model="role" :options="roles" :disabled="working" aria-label="授权角色" /></label></div>
      <p v-if="selectedCandidate">回执：<code>{{ selectedCandidate.candidate_id }}</code></p>
      <label><input v-model="confirmed" type="checkbox" :disabled="working" />我已对照实际发言核对登记回执和身份</label>
      <div class="official-actions"><UiButton kind="pri" :disabled="working || dirty || !confirmed || !candidate" @click="authorize">保存该身份授权</UiButton></div>
      <div v-for="member in members" :key="member.role + member.id" class="official-candidate"><div><b>{{ member.role }}</b><p class="official-id">{{ member.id }}</p></div><UiButton kind="line" :disabled="working || dirty" @click="revoke(member.id, member.role === '群管理员' ? 'group_admin' : member.role === '环境超管' ? 'environment_admin' : member.role === '聊天黑名单' ? 'blacklist' : 'nickname_ignore')">撤销 {{ member.role }}</UiButton></div>
    </section>
    <OfficialOperations v-if="draft && state" :environment="environment" :config-id="draft.config_id" :revision="draft.revision" :enabled="draft.enabled" :servers="draft.servers.filter(s => s.api_port !== null)" :blocked="busy || unsaved || !state.active" @busy="value => operationBusy = value" />
  </div>
</template>

<style scoped>
.official-page { display: grid; gap: 18px; }
.official-card { padding: 22px; border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); display: grid; gap: 14px; min-width: 0; }
.official-card h2 { font-size: 16px; margin: 0; }
.official-card h3 { font-size: 14px; margin: 6px 0 0; }
.official-card p { margin: 0; font-size: 13px; line-height: 1.6; color: var(--ink3); overflow-wrap: anywhere; }
.official-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.official-grid label, .official-field { display: grid; gap: 8px; font-size: 13px; min-width: 0; }
.official-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; font-size: 13px; }
.official-actions span, .official-actions small { color: var(--ink3); overflow-wrap: anywhere; }
.official-candidate { display: flex; gap: 16px; justify-content: space-between; align-items: center; padding: 12px 0; border-top: 1px solid var(--row); }
.official-candidate > div { min-width: 0; }
code, .official-id { overflow-wrap: anywhere; font-size: 12px; }
.official-server { padding: 16px; border: 1px solid var(--bd); border-radius: 8px; display: grid; gap: 14px; }
@media (max-width: 640px) { .official-card { padding: 16px; } .official-grid { grid-template-columns: minmax(0,1fr); } .official-candidate { align-items: flex-start; flex-direction: column; } }
</style>
