<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { api, officialBusy, officialDraftPending, openDialog, S, toast } from "../store";
import type { BindRule, ModState, OfficialGroup, OfficialServer, OfficialState } from "../lib/types";
import UiBadge from "../components/UiBadge.vue";
import UiButton from "../components/UiButton.vue";
import UiIcon from "../components/UiIcon.vue";
import UiInput from "../components/UiInput.vue";
import UiMenu from "../components/UiMenu.vue";
import UiModal from "../components/UiModal.vue";
import UiNotice from "../components/UiNotice.vue";
import UiSelect from "../components/UiSelect.vue";
import UiSwitch from "../components/UiSwitch.vue";
import OfficialServerModal from "../components/OfficialServerModal.vue";
import RemarkText from "../components/RemarkText.vue";
import { aliasUnits, clipAlias, clipDigits } from "../lib/groupFields";
import { useIsMobile } from "../lib/useMediaQuery";

const props = defineProps<{ embedded?: boolean }>();
const isMobile = useIsMobile();

const state = ref<OfficialState | null>(null);
const initializing = ref(true);
const groupId = ref("");
const busy = ref(false);
const working = computed(() => busy.value);
const error = ref("");
const bindTemplateValue = ref("");
const metaAlias = ref("");
const metaDisplay = ref("");
const metaAliasInput = computed({
  get: () => metaAlias.value,
  set: value => { metaAlias.value = clipAlias(value); },
});
const metaDisplayInput = computed({
  get: () => metaDisplay.value,
  set: value => { metaDisplay.value = clipDigits(value); },
});
const showBindHelp = ref(false);
const serverEditor = ref<{ server: OfficialServer | null } | null>(null);
const deleteTarget = ref<{ server: OfficialServer; index: number } | null>(null);
const deleteGroupTarget = ref<OfficialGroup | null>(null);
const deleteGroupConfirm = ref("");
const showJson = ref(false);
const jsonCopied = ref(false);
let jsonCopyTimer: number | undefined;
const showBatchAdd = ref(false);
const batchText = ref("");
const batchError = ref("");
const showBatchDelete = ref(false);
const showBatchDeleteConfirm = ref(false);
const batchDeleteSelected = reactive<Record<string, boolean>>({});
const batchDeleteError = ref("");
const showGroupMeta = ref(false);

/* 官方配置改为和 OneBot 一样逐项即时保存；保留 busy 给切页/切群做防抖。 */
watch(busy, saving => {
  officialBusy.value = saving;
  officialDraftPending.value = false;
}, { flush: "sync", immediate: true });
onBeforeUnmount(() => {
  officialBusy.value = false;
  officialDraftPending.value = false;
});

const officialEnvironments = computed(() => (S.activation?.environments ?? [])
  .filter(e => e.platform === "qq_official"));
/* 环境由“生效机器人”决定：优先当前运行选择，其次下次启动选择，最后取首个登记环境。
   页面不再提供切换下拉；需要更换环境请到“全局参数 → 生效机器人”。 */
const environmentEntry = computed(() => {
  for (const selection of [S.activation?.selection, S.activation?.saved_selection]) {
    if (selection?.platform !== "qq_official") continue;
    const match = officialEnvironments.value.find(e => e.environment_id === selection.environment_id);
    if (match) return match;
  }
  return officialEnvironments.value[0] ?? null;
});
const environment = computed(() => environmentEntry.value?.environment_id ?? "");
const groupOptions = computed(() => (state.value?.groups ?? []).map(g => ({
  value: g.config_id,
  label: g.display_group || g.alias || g.group_openid,
  sub: g.alias,
  description: `${g.servers.length} 台服务器`,
})));
const group = computed<OfficialGroup | null>(() =>
  state.value?.groups.find(g => g.config_id === groupId.value) ?? null);
const servers = computed(() => group.value?.servers ?? []);
const modCount = computed(() => servers.value.filter(s => s.api_port !== null).length);
const consoleTokenIds = computed(() => new Set(state.value?.console_token_ids ?? []));
const tokenCount = computed(() => servers.value.filter(s => consoleTokenIds.value.has(s.server_id)).length);
const modStates = ref<Record<string, ModState>>({});
const modStateLoading = ref(false);
const whitelistBusy = reactive<Record<string, boolean>>({});
function hasConsoleToken(server: OfficialServer): boolean {
  return consoleTokenIds.value.has(server.server_id);
}
function serverAddress(server: OfficialServer): string {
  return server.game_port === 25565 ? server.host : `${server.host}:${server.game_port}`;
}
function modState(server: OfficialServer): ModState | undefined {
  return modStates.value[server.server_id];
}
function modBadge(server: OfficialServer): { tone: "port" | "off" | "ok" | "warn"; text: string; title: string } {
  if (server.api_port === null) return { tone: "port", text: "未配端口", title: "没写 api_port：该服按未安装模组处理" };
  const st = modState(server);
  if (!st) return { tone: "off", text: modStateLoading.value ? "读取中…" : "未读取", title: modTitle(server) };
  if (!st.reachable) return { tone: "warn", text: "连接失败", title: st.reason || "连不上该服的模组 API" };
  if (st.enabled === null) return { tone: "warn", text: "旧版不支持", title: st.reason || "模组版本没有登录开关字段" };
  return { tone: "ok", text: "已连通", title: "模组 API 已连通" };
}
function modEnabled(server: OfficialServer): boolean | null {
  return modState(server)?.enabled ?? null;
}
function modKnown(server: OfficialServer): boolean {
  return server.api_port !== null && modEnabled(server) !== null;
}
function modOn(server: OfficialServer): boolean {
  return modEnabled(server) === true;
}
/** 新模组会声明官方 V2 写入口；旧模组没有该字段时保持只读，按钮不可点。 */
function modWritable(server: OfficialServer): boolean {
  return modKnown(server) && modState(server)?.writable === true;
}
function modTitle(server: OfficialServer): string {
  if (server.api_port === null) return "未配置模组 API 端口，该服视为未安装模组，开关不可用";
  const state = modState(server);
  if (!state) return modStateLoading.value ? "正在读取模组状态…" : "尚未读到该服状态，开关暂不可用";
  if (state.enabled === null) return state.reason || "未连通，登录开关状态未知";
  const stateText = state.enabled
    ? "已开启：未绑定玩家进服会被限制"
    : "已关闭：所有玩家不受限制，在线受限玩家已放开";
  return state.writable
    ? `${stateText}；点击可在线切换，立即生效并写回模组配置`
    : `${stateText}；该服模组版本不支持在线修改，需更新模组后重启服务器`;
}
function modNote(server: OfficialServer): string {
  if (server.api_port === null) return "没写 api_port：该服按未安装模组处理";
  const st = modState(server);
  if (!st) return modStateLoading.value ? "" : "尚未读到该服状态";
  if (!st.reachable) return st.reason || "连不上该服的模组 API";
  if (st.enabled === null) return st.reason || "模组版本没有登录开关字段";
  return "";
}
function modShort(server: OfficialServer): string {
  if (server.api_port === null) return "未知";
  const state = modState(server);
  if (!state) return modStateLoading.value ? "读取中…" : "未知";
  if (state.enabled === null) return "未知";
  return state.enabled ? "已开启" : "已关闭";
}
function tokTitle(server: OfficialServer): string {
  if (!hasConsoleToken(server)) return "未设置：/执行 对该服未启用";
  if (server.api_port === null) return "口令已存，但该服未配模组 API 端口，/执行 发不出去";
  return "口令已存：需与该服模组的 consoleToken 逐字一致";
}
function motdTime(epoch: number): string {
  const d = new Date(epoch * 1000);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}
function remarkTitle(server: OfficialServer): string {
  if (server.remark_src === "motd") {
    return server.remark_motd
      ? `来自服务端 motd · 采集于 ${motdTime(server.remark_motd_at ?? 0)}`
      : "已选「用 MOTD」但还没采到值，当前显示手填的值";
  }
  return server.remark_motd ? `手填的值 · 另存着上次采到的 motd：${server.remark_motd}` : "手填的值";
}
async function loadModStates() {
  const current = group.value;
  if (!current || !environment.value) {
    modStates.value = {};
    modStateLoading.value = false;
    return;
  }
  const configId = current.config_id;
  modStateLoading.value = true;
  try {
    const result = await api.officialModState(environment.value, configId);
    if (group.value?.config_id === configId) modStates.value = result.states;
  } catch (e) {
    if (group.value?.config_id === configId) modStates.value = {};
    error.value = e instanceof Error ? e.message : "读取模组状态失败";
  } finally {
    if (group.value?.config_id === configId) modStateLoading.value = false;
  }
}
async function toggleOfficialWhitelist(server: OfficialServer) {
  const current = group.value;
  const env = environment.value;
  if (!current || !env || !modWritable(server) || whitelistBusy[server.server_id]) return;
  whitelistBusy[server.server_id] = true;
  try {
    const result = await api.officialWhitelist(
      env, current.config_id, current.revision, server.server_id, !modOn(server));
    if (group.value?.config_id === current.config_id) {
      const previous = modStates.value[server.server_id];
      modStates.value = {
        ...modStates.value,
        [server.server_id]: {
          reachable: true,
          enabled: result.enabled,
          writable: previous?.writable ?? true,
          reason: "",
        },
      };
    }
    toast(result.message);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "下发登录开关失败";
    // 失败可能来自旧模组、Token 不一致或配置已被修改；重新读一次真实状态。
    await loadModStates();
  } finally {
    whitelistBusy[server.server_id] = false;
  }
}
const policy = computed(() => group.value?.policy ?? null);
const atServerLimit = computed(() => servers.value.length >= 20);
const batchItems = computed(() => [
  { value: "batch-add", label: "批量添加", icon: "plus", disabled: working.value || !group.value || atServerLimit.value },
  { value: "batch-del", label: "批量删除", icon: "trash", danger: true, disabled: working.value || !group.value || !servers.value.length },
]);
const batchDeleteCount = computed(() => servers.value.filter(s => batchDeleteSelected[s.server_id]).length);
const allBatchSelected = computed(() => servers.value.length > 0 && batchDeleteCount.value === servers.value.length);
const jsonText = computed(() => {
  const current = group.value;
  if (!current) return "";
  return JSON.stringify({
    environment_id: environment.value,
    config_id: current.config_id,
    group_openid: current.group_openid,
    revision: current.revision,
    alias: current.alias,
    display_group: current.display_group,
    enabled: current.enabled,
    servers: canonicalServers(current.servers),
    policy: current.policy,
    administrators: current.administrators,
    blacklist: current.blacklist,
    nickname_ignore: current.nickname_ignore,
  }, null, 2);
});
const groupItems = computed(() => [
  ...(isMobile.value ? [
    { value: "add-group", label: "新增群", icon: "plus", disabled: working.value || !state.value },
    { value: "json-group", label: "查看 JSON", icon: "braces", disabled: !group.value },
    ...batchItems.value,
  ] : []),
  { value: "edit-group", label: "编辑群信息", icon: "pencil", disabled: !group.value || working.value },
  { value: "copy-openid", label: "复制群 OpenID", icon: "copy", disabled: !group.value },
  { value: "delete-group", label: "删除群配置", icon: "trash", danger: true, disabled: !group.value || working.value },
]);
async function groupAction(value: string) {
  if (value === "add-group") {
    openAddGroup();
    return;
  }
  if (value === "json-group") {
    if (group.value) showJson.value = true;
    return;
  }
  if (value === "batch-add") {
    openBatchAdd();
    return;
  }
  if (value === "batch-del") {
    openBatchDelete();
    return;
  }
  if (value === "delete-group") {
    if (group.value && !working.value) {
      deleteGroupTarget.value = group.value;
      deleteGroupConfirm.value = "";
    }
    return;
  }
  if (value === "edit-group") {
    openGroupMeta();
    return;
  }
  if (value === "copy-openid" && group.value) {
    try {
      await navigator.clipboard.writeText(group.value.group_openid);
      toast("已复制群 OpenID");
    } catch {
      toast("复制失败，请在“编辑群信息”中手动复制");
    }
  }
}
function openBatchAdd() {
  if (working.value || !group.value || atServerLimit.value) return;
  batchText.value = "";
  batchError.value = "";
  showBatchAdd.value = true;
}
function openBatchDelete() {
  if (working.value || !group.value || !servers.value.length) return;
  for (const key of Object.keys(batchDeleteSelected)) delete batchDeleteSelected[key];
  batchDeleteError.value = "";
  showBatchDelete.value = true;
}
function batchAction(value: string) {
  if (value === "batch-add") openBatchAdd();
  else if (value === "batch-del") openBatchDelete();
}
function toggleAllBatch() {
  const next = !allBatchSelected.value;
  for (const server of servers.value) batchDeleteSelected[server.server_id] = next;
}
type BatchServer = { name: string; host: string; game_port: number; api_port: number | null };
function parseBatchRows(text: string): BatchServer[] {
  const rows = text.split(/[\n,，]+/).map(row => row.trim()).filter(Boolean);
  if (!rows.length) throw new Error("请至少输入一台服务器。");
  const names = new Set(servers.value.map(server => server.name));
  const result: BatchServer[] = [];
  rows.forEach((row, index) => {
    const parts = row.split(":").map(part => part.trim());
    if (parts.length < 2 || parts.length > 4) {
      throw new Error(`第 ${index + 1} 行格式应为 名称:主机[:游戏端口[:模组API端口]]`);
    }
    const [name, host, gameRaw, apiRaw] = parts;
    if (!name || Array.from(name).length > 20 || /\s|[\[\]【】]/.test(name)) {
      throw new Error(`第 ${index + 1} 行服务器名称无效：最多 20 个字符，不能含空格或方括号。`);
    }
    if (names.has(name)) throw new Error(`服务器名称「${name}」重复。`);
    names.add(name);
    if (!host || host.length > 253 || host.includes(":") || !/^[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(host)) {
      throw new Error(`第 ${index + 1} 行主机地址无效：只填域名或标准 IPv4。`);
    }
    const gamePort = gameRaw ? Number(gameRaw) : 25565;
    if (!Number.isInteger(gamePort) || gamePort < 1 || gamePort > 65535) {
      throw new Error(`第 ${index + 1} 行游戏端口必须是 1–65535 的整数。`);
    }
    let apiPort: number | null = null;
    if (apiRaw) {
      apiPort = Number(apiRaw);
      if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) {
        throw new Error(`第 ${index + 1} 行模组 API 端口必须是 1–65535，留空表示未安装模组。`);
      }
    }
    result.push({ name, host, game_port: gamePort, api_port: apiPort });
  });
  if (servers.value.length + result.length > 20) throw new Error("每群最多 20 台服务器。");
  return result;
}
async function saveBatchAdd() {
  if (working.value) return;
  batchError.value = "";
  let rows: BatchServer[];
  try {
    rows = parseBatchRows(batchText.value);
  } catch (e) {
    batchError.value = e instanceof Error ? e.message : "批量内容无效";
    return;
  }
  const next = [
    ...servers.value.map(server => ({ ...server })),
    ...rows.map(row => ({ server_id: "", ...row, hidden: false, remark: "" })),
  ];
  if (await saveGroup({ servers: next })) {
    showBatchAdd.value = false;
    toast(`已添加 ${rows.length} 台服务器`);
  } else {
    batchError.value = error.value || "保存失败";
  }
}
function openBatchDeleteConfirm() {
  if (working.value || !batchDeleteCount.value) return;
  batchDeleteError.value = "";
  showBatchDelete.value = false;
  showBatchDeleteConfirm.value = true;
}

async function saveBatchDelete() {
  if (working.value || !batchDeleteCount.value) return;
  const count = batchDeleteCount.value;
  batchDeleteError.value = "";
  const next = servers.value.filter(server => !batchDeleteSelected[server.server_id])
    .map(server => ({ ...server }));
  if (await saveGroup({ servers: next })) {
    showBatchDelete.value = false;
    showBatchDeleteConfirm.value = false;
    toast(`已删除 ${count} 台服务器`);
  } else {
    batchDeleteError.value = error.value || "保存失败";
  }
}
async function copyJson() {
  try {
    await navigator.clipboard.writeText(jsonText.value);
    jsonCopied.value = true;
    window.clearTimeout(jsonCopyTimer);
    jsonCopyTimer = window.setTimeout(() => { jsonCopied.value = false; }, 2000);
  } catch {
    toast("复制失败，请手动选择文本");
  }
}
const bindOptions = [
  { value: "paren", label: "括号内的游戏 ID", description: "玩家（Steve）" },
  { value: "equals", label: "完全等于游戏 ID", description: "Steve" },
  { value: "contains", label: "包含游戏 ID", description: "玩家Steve" },
  { value: "template", label: "自定义模板", description: "MC:{gameId}" },
];
const RULE_SAMPLE = "Steve";
const bindRule = computed<BindRule>(() => policy.value?.bind_nickname_rule ?? "paren");
const bindTemplateInvalid = computed(() => {
  if (bindRule.value !== "template") return false;
  const template = bindTemplateValue.value;
  return !template.trim() || template.length > 64 || template.split("{gameId}").length - 1 !== 1;
});
const bindExample = computed(() => {
  if (bindRule.value === "equals") return RULE_SAMPLE;
  if (bindRule.value === "contains") return `玩家${RULE_SAMPLE}`;
  if (bindRule.value === "template") return bindTemplateValue.value.replace("{gameId}", RULE_SAMPLE);
  return `玩家（${RULE_SAMPLE}）`;
});

watch(group, (value) => {
  metaAlias.value = clipAlias(value?.alias ?? "");
  metaDisplay.value = clipDigits(value?.display_group ?? "");
  bindTemplateValue.value = value?.policy.bind_nickname_template ?? "";
}, { immediate: true });

function selectGroup(value: string) {
  groupId.value = value;
  deleteTarget.value = null;
  serverEditor.value = null;
}
function chooseGroup(value: string) {
  if (working.value) return;
  selectGroup(value);
}
function accept(data: OfficialState) {
  const previous = new Set((state.value?.groups ?? []).map(g => g.config_id));
  state.value = data;
  S.officialState = data;
  S.officialEnvironment = environment.value;
  const added = data.groups.find(g => !previous.has(g.config_id));
  selectGroup(added?.config_id
    ?? (data.groups.some(g => g.config_id === groupId.value) ? groupId.value : data.groups[0]?.config_id ?? ""));
}
async function load() {
  if (!environment.value || working.value) return;
  busy.value = true;
  error.value = "";
  S.officialEnvironment = environment.value;
  try {
    accept(await api.officialState(environment.value));
    S.error = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "读取失败";
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  try {
    S.activation = await api.activation();
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "读取实例失败";
  } finally {
    initializing.value = false;
  }
});

/* 与 OneBot 一样，模组状态按当前群实时读取；配置修订变化后重读。 */
watch([() => environment.value, () => group.value?.config_id, () => group.value?.revision],
  () => { void loadModStates(); }, { immediate: true });

/* 新增群弹窗保存后会更新共享状态；这里同步并自动选中新群。 */
watch(() => S.officialState, (data) => {
  if (applyingOfficialState.value || !data || data === state.value) return;
  const previous = new Set((state.value?.groups ?? []).map(g => g.config_id));
  state.value = data;
  const added = data.groups.find(g => !previous.has(g.config_id));
  selectGroup(added?.config_id
    ?? (data.groups.some(g => g.config_id === groupId.value) ? groupId.value : data.groups[0]?.config_id ?? ""));
}, { flush: "sync" });

/* 后端只接受 7 个规范字段；列表里还有 remark_src 等展示字段，提交前必须剥掉。 */
function canonicalServers(list: OfficialServer[]): OfficialServer[] {
  return list.map(server => ({
    server_id: server.server_id,
    name: server.name,
    host: server.host,
    game_port: server.game_port,
    api_port: server.api_port,
    hidden: server.hidden,
    remark: server.remark,
  }));
}
/** 备注/Token 这类局部保存不能打断当前弹窗；只换数据，不重置编辑目标。 */
const applyingOfficialState = ref(false);
function applyOfficialState(data: OfficialState) {
  applyingOfficialState.value = true;
  state.value = data;
  S.officialState = data;
  S.officialEnvironment = environment.value;
  const current = data.groups.find(g => g.config_id === groupId.value) ?? data.groups[0];
  if (!data.groups.some(g => g.config_id === groupId.value)) {
    groupId.value = current?.config_id ?? "";
  }
  const editing = serverEditor.value?.server;
  if (editing && current) {
    const updated = current.servers.find(item => item.server_id === editing.server_id);
    if (updated) serverEditor.value = { server: { ...updated } };
  }
  applyingOfficialState.value = false;
}
type GroupPatch = Partial<Pick<OfficialGroup, "alias" | "display_group" | "enabled" | "servers" | "policy" | "nickname_ignore">>;
async function saveGroup(patch: GroupPatch): Promise<boolean> {
  const current = group.value;
  if (!current || working.value) return false;
  busy.value = true;
  error.value = "";
  try {
    const r = await api.officialUpdate({
      environment_id: environment.value,
      action: "save",
      config_id: current.config_id,
      expected_revision: current.revision,
      changes: {
        alias: patch.alias ?? current.alias,
        display_group: patch.display_group ?? current.display_group,
        enabled: patch.enabled ?? current.enabled,
        servers: canonicalServers(patch.servers ?? current.servers),
        policy: patch.policy ? { ...patch.policy } : current.policy,
        nickname_ignore: patch.nickname_ignore ?? current.nickname_ignore,
      },
    });
    accept(r);
    return true;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败，请刷新确认";
    toast(error.value);
    return false;
  } finally {
    busy.value = false;
  }
}
function toggleGroupEnabled(value: boolean) {
  void saveGroup({ enabled: value });
}
type PolicyBoolKey = "show_server_version" | "show_server_latency" | "show_last_online"
  | "enable_group_owner" | "enable_group_admin" | "nickname_check_enabled" | "bind_require_nickname"
  | "use_central_db";
function togglePolicy(key: PolicyBoolKey, value: boolean) {
  if (!policy.value) return;
  void saveGroup({ policy: { ...policy.value, [key]: value } });
}
function chooseBindRule(value: string) {
  if (!policy.value || !bindOptions.some(option => option.value === value)) return;
  let template = bindTemplateValue.value;
  if (value === "template" && !template.trim()) {
    template = "MC:{gameId}";
    bindTemplateValue.value = template;
  }
  void saveGroup({ policy: { ...policy.value, bind_nickname_rule: value as BindRule, bind_nickname_template: template } });
}
function saveBindTemplate() {
  if (!policy.value) return;
  if (bindTemplateInvalid.value) {
    toast("模板必须包含且只包含一个 {gameId}");
    return;
  }
  if (bindTemplateValue.value !== policy.value.bind_nickname_template) {
    void saveGroup({ policy: { ...policy.value, bind_nickname_template: bindTemplateValue.value } });
  }
}
function openGroupMeta() {
  const current = group.value;
  if (!current || working.value) return;
  metaAlias.value = clipAlias(current.alias);
  metaDisplay.value = clipDigits(current.display_group);
  showGroupMeta.value = true;
}
async function saveGroupMeta() {
  const current = group.value;
  if (!current || working.value) return;
  const alias = metaAlias.value.trim();
  const display = metaDisplay.value.trim();
  if (!display) {
    toast("请填写 QQ 群号。");
    return;
  }
  if (!/^[0-9]+$/.test(display)) {
    toast("QQ 群号只能填写数字。");
    return;
  }
  if (aliasUnits(alias) > 16) {
    toast("群别名最多 8 个中文字符（或 16 个英文字符）。");
    return;
  }
  if (await saveGroup({ alias, display_group: display })) {
    showGroupMeta.value = false;
    toast("群信息已保存");
  }
}

function openAddGroup() {
  if (working.value || !environment.value || !state.value) return;
  openDialog("add-group", { platform: "qq_official" });
}
function openAddServer() {
  if (!group.value || working.value || atServerLimit.value) return;
  serverEditor.value = { server: null };
}
function openEditServer(server: OfficialServer) {
  if (working.value) return;
  serverEditor.value = { server: { ...server } };
}
async function saveServer(server: OfficialServer, tokenValue = "", remarkSrc: "manual" | "motd" = "manual") {
  const editing = serverEditor.value?.server;
  const beforeIds = new Set(servers.value.map(item => item.server_id));
  const next = servers.value.map(item => ({ ...item }));
  if (editing) {
    const index = next.findIndex(item => item.server_id === editing.server_id);
    if (index >= 0) next[index] = server;
    else next.push(server);
  } else {
    next.push(server);
  }
  if (!(await saveGroup({ servers: next }))) return;
  const current = group.value;
  const target = editing
    ? current?.servers.find(item => item.server_id === editing.server_id)
    : current?.servers.find(item => !beforeIds.has(item.server_id));
  if (!target || !current) {
    serverEditor.value = null;
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    if (tokenValue) {
      const r = await api.officialUpdate({
        environment_id: environment.value,
        action: "server_token",
        config_id: current.config_id,
        server_id: target.server_id,
        expected_revision: current.revision,
        token: tokenValue,
      });
      applyOfficialState(r);
      toast(`已保存「${target.name}」命令 Token`);
    }
    if (remarkSrc !== (target.remark_src ?? "manual")) {
      const groupNow = group.value;
      if (groupNow) {
        const r = await api.officialRemarkSrc(environment.value, groupNow.config_id,
          groupNow.revision, target.server_id, remarkSrc);
        applyOfficialState(r);
      }
    }
    if (remarkSrc === "motd" && !target.remark_motd) {
      const groupNow = group.value;
      if (groupNow) {
        try {
          const r = await api.officialRemarkSync(environment.value, groupNow.config_id,
            groupNow.revision, target.server_id);
          applyOfficialState(r);
          toast("MOTD 已同步");
        } catch (e) {
          toast(`MOTD 同步失败：${e instanceof Error ? e.message : String(e)}`);
        }
      }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存服务器附加配置失败";
    toast(error.value);
  } finally {
    busy.value = false;
    serverEditor.value = null;
  }
}
async function clearServerToken(server: OfficialServer) {
  const current = group.value;
  if (!current || working.value) return;
  busy.value = true;
  error.value = "";
  try {
    const r = await api.officialUpdate({
      environment_id: environment.value,
      action: "server_token",
      config_id: current.config_id,
      server_id: server.server_id,
      expected_revision: current.revision,
      token: "",
    });
    applyOfficialState(r);
    toast(`已清除「${server.name}」命令 Token`);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "清除命令 Token 失败";
    toast(error.value);
  } finally {
    busy.value = false;
  }
}
async function confirmDeleteServer() {
  if (!deleteTarget.value) return;
  const index = deleteTarget.value.index;
  const next = servers.value.filter((_, i) => i !== index);
  if (await saveGroup({ servers: next })) deleteTarget.value = null;
}
async function confirmDeleteGroup() {
  const target = deleteGroupTarget.value;
  if (!target || working.value) return;
  busy.value = true;
  error.value = "";
  try {
    const r = await api.officialUpdate({
      environment_id: environment.value,
      action: "delete",
      config_id: target.config_id,
      expected_revision: target.revision,
    });
    deleteGroupTarget.value = null;
    accept(r);
    toast(`已删除官方群「${target.alias || target.display_group || target.group_openid}」配置`);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "删除群配置失败";
  } finally {
    busy.value = false;
  }
}
function toggleHidden(server: OfficialServer) {
  const next = servers.value.map(item => ({ ...item, hidden: item.server_id === server.server_id ? !item.hidden : item.hidden }));
  void saveGroup({ servers: next });
}
function moveServer(server: OfficialServer, delta: number) {
  const next = servers.value.map(item => ({ ...item }));
  const index = next.findIndex(item => item.server_id === server.server_id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= next.length) return;
  [next[index], next[target]] = [next[target], next[index]];
  void saveGroup({ servers: next });
}
function serverItems(index: number) {
  return [
    { value: "up", label: "上移", icon: "arrow-up", disabled: index === 0 },
    { value: "down", label: "下移", icon: "arrow-down", disabled: index === servers.value.length - 1 },
    { value: "token", label: "命令 Token", icon: "key" },
    { value: "delete", label: "删除服务器", icon: "trash", danger: true },
  ];
}
function serverAction(value: string, server: OfficialServer, index: number) {
  if (value === "up" || value === "down") moveServer(server, value === "up" ? -1 : 1);
  else if (value === "token") openEditServer(server);
  else if (value === "delete") deleteTarget.value = { server, index };
}
</script>

<template>
  <div class="srv-page official-page" :class="{ 'is-mobile': isMobile }">
    <header v-if="!props.embedded" class="srv-heading">
      <div>
        <h1>群与服务器</h1>
        <p>官方群配置按 OneBot 相同布局即时保存。</p>
      </div>
      <UiBadge v-if="!isMobile" tone="ok">即时保存</UiBadge>
    </header>

    <section class="srv-groupbar" aria-label="当前群配置">
      <template v-if="!isMobile">
        <UiIcon name="users" :size="18" /><span class="srv-group-label">当前群</span>
      </template>
      <div class="srv-group-picker">
        <UiSelect :model-value="groupId" :options="groupOptions" :disabled="working || !groupOptions.length"
          placeholder="尚无群配置" aria-label="选择群" @update:model-value="chooseGroup" />
      </div>
      <div class="srv-group-actions">
        <UiButton v-if="!isMobile" kind="sec" :disabled="working || !environment || !state" @click="openAddGroup()">
          <UiIcon name="plus" :size="16" />新增群
        </UiButton>
        <UiMenu :items="groupItems" :label="isMobile ? undefined : '更多群操作'" @select="groupAction" />
      </div>
    </section>

    <div v-if="!isMobile" class="srv-summary" aria-label="本群概况">
      <span>已配置 {{ groupOptions.length }} 个群</span>
      <span>本群服务器 {{ servers.length }} / 20</span>
      <span>已配模组端口 {{ modCount }} 台</span>
      <span>命令 Token 已设置 {{ tokenCount }} 台</span>
    </div>

    <UiNotice v-if="!officialEnvironments.length" tone="warn">请先到「全局参数 → 生效机器人」保存 QQ 官方实例。</UiNotice>
    <UiNotice v-if="state && !state.active" tone="warn">当前官方环境尚未生效，仅可准备本地配置；查询需在完整重启并启用该环境后使用。</UiNotice>
    <UiNotice v-if="state && state.active && !state.has_mod_token" tone="warn">
      全局参数 → 公共参数尚未配置「模组 API 鉴权 Token」，模组状态、登录限制与 MOTD 不可用。
    </UiNotice>
    <UiNotice v-if="state && !state.message_delivery?.ready" tone="warn">{{ state.message_delivery?.message ??
      '发送防重状态待核验'
    }}</UiNotice>

    <section class="srv-list" :class="{ 'srv-list--mobile': isMobile }" aria-label="服务器列表">
      <div class="srv-list-toolbar">
        <h2 v-if="!isMobile">服务器列表</h2><span v-else class="srv-mobile-count">{{ servers.length }} / 20 台服务器</span>
        <div class="srv-toolbar-actions">
          <template v-if="!isMobile">
            <UiButton kind="sec" :disabled="!group || working" @click="showJson = true">
              <UiIcon name="braces" :size="16" />查看 JSON
            </UiButton>
            <UiMenu :items="batchItems" label="批量管理" icon="list-checks" @select="batchAction" />
          </template>
          <UiButton kind="pri" :disabled="!group || working || atServerLimit"
            :title="atServerLimit ? '每群最多 20 台服务器' : undefined" @click="openAddServer">
            <UiIcon name="plus" :size="16" />{{ isMobile ? "添加" : "添加服务器" }}
          </UiButton>
        </div>
      </div>

      <div v-if="!isMobile" class="srv-table-scroll">
        <div class="srv-table" role="table" aria-label="当前群服务器">
          <div class="srv-table-head srv-grid" role="row">
            <span role="columnheader" aria-label="序号">#</span>
            <span class="srv-cell--name" role="columnheader">服务器 / 备注</span>
            <span class="srv-cell--address" role="columnheader">主机地址 / 模组端口</span>
            <span role="columnheader">模组状态</span>
            <span role="columnheader">登录限制</span>
            <span role="columnheader">命令 Token</span>
            <span class="srv-cell--actions" role="columnheader">操作</span>
          </div>
          <div v-for="(server, index) in servers" :key="server.server_id || 'new-' + index"
            class="srv-table-row srv-grid" :class="{ 'is-hidden': server.hidden }" role="row">
            <span class="srv-index" role="cell">{{ String(index + 1).padStart(2, "0") }}</span>
            <div class="srv-name-cell srv-cell--name" role="cell">
              <b class="srv-server-name">{{ server.name }}</b>
              <div class="srv-remark-line">
                <UiBadge v-if="server.hidden" tone="off">隐藏</UiBadge>
                <UiBadge v-if="server.remark_src === 'motd' && server.remark_motd" tone="neutral">MOTD</UiBadge>
                <span :title="server.remark_display ? remarkTitle(server) : undefined"><RemarkText :runs="server.remark_runs" :fallback="server.remark_display || '-'" /></span>
              </div>
            </div>
            <div class="srv-address-cell srv-cell--address" role="cell">
              <span class="srv-host">{{ serverAddress(server) }}</span>
              <span class="srv-secondary">API {{ server.api_port ?? "未配置" }}</span>
            </div>
            <div class="srv-state-cell" role="cell">
              <UiBadge :tone="modBadge(server).tone" :title="modBadge(server).title">{{ modBadge(server).text }}</UiBadge>
            </div>
            <div class="srv-login-cell" role="cell" :title="modTitle(server)">
              <UiSwitch :on="modOn(server)" :disabled="!modWritable(server) || whitelistBusy[server.server_id]" :aria-label="server.name + '登录限制'" :title="modTitle(server)" @toggle="toggleOfficialWhitelist(server)" />
              <span>{{ modShort(server) }}</span>
            </div>
            <div role="cell">
              <UiBadge :tone="hasConsoleToken(server) ? 'neutral' : 'off'" :title="tokTitle(server)">{{ hasConsoleToken(server) ? "已设置" : "未设置" }}</UiBadge>
            </div>
            <div class="srv-row-actions" role="cell">
              <button class="srv-action srv-action--edit" type="button" @click="openEditServer(server)">编辑</button>
              <button class="srv-action" type="button" :title="server.hidden ? '恢复官方群展示与查询' : '从官方群撤下并停止查询'"
                @click="toggleHidden(server)">{{ server.hidden ? "显示" : "隐藏" }}</button>
              <UiMenu :items="serverItems(index)" @select="(value) => serverAction(value, server, index)">
                <template #trigger="{ attrs, toggle }"><button v-bind="attrs" class="srv-action srv-action--more"
                    type="button" :aria-label="server.name + '更多操作'" @click="toggle">
                    <UiIcon name="more-horizontal" :size="16" />
                  </button></template>
              </UiMenu>
            </div>
          </div>
        </div>
      </div>

      <div v-else-if="servers.length" class="srv-mobile-list">
        <article v-for="(server, index) in servers" :key="server.server_id || 'new-' + index" class="srv-mobile-card"
          :class="{ 'is-hidden': server.hidden }">
          <div class="srv-mobile-card-title"><span class="srv-index">{{ String(index + 1).padStart(2, "0") }}</span><b class="srv-server-name">{{ server.name }}</b>
            <UiBadge :tone="modBadge(server).tone" :title="modBadge(server).title">{{ modBadge(server).text }}</UiBadge>
          </div>
          <div class="srv-host">{{ serverAddress(server) }}</div>
          <div class="srv-remark-line">
            <UiBadge v-if="server.hidden" tone="off">隐藏</UiBadge>
            <UiBadge v-if="server.remark_src === 'motd' && server.remark_motd" tone="neutral">MOTD</UiBadge>
            <span :title="server.remark_display ? remarkTitle(server) : undefined"><RemarkText :runs="server.remark_runs" :fallback="server.remark_display || '-'" /></span>
          </div>
          <dl class="srv-mobile-details">
            <div>
              <dt>模组 API 端口</dt>
              <dd>{{ server.api_port ?? "未配置" }}</dd>
            </div>
            <div>
              <dt>命令 Token</dt>
              <dd>
                <UiBadge :tone="hasConsoleToken(server) ? 'neutral' : 'off'" :title="tokTitle(server)">{{ hasConsoleToken(server) ? "已设置" : "未设置"
                }}
                </UiBadge>
              </dd>
            </div>
            <div class="srv-mobile-login">
              <dt>登录限制</dt>
              <dd :title="modTitle(server)"><span>{{ modShort(server) }}</span><UiSwitch :on="modOn(server)" :disabled="!modWritable(server) || whitelistBusy[server.server_id]" :aria-label="server.name + '登录限制'" :title="modTitle(server)" @toggle="toggleOfficialWhitelist(server)" /></dd>
            </div>
          </dl>
          <p v-if="modNote(server)" class="srv-mobile-status-note">{{ modNote(server) }}</p>
          <div class="srv-mobile-actions">
            <UiButton kind="sec" @click="openEditServer(server)">
              <UiIcon name="pencil" :size="16" />编辑
            </UiButton>
            <UiButton kind="sec" @click="toggleHidden(server)">
              <UiIcon :name="server.hidden ? 'eye' : 'eye-off'" :size="16" />{{ server.hidden ? "显示" : "隐藏" }}
            </UiButton>
            <UiMenu :items="serverItems(index)" label="更多" @select="(value) => serverAction(value, server, index)" />
          </div>
        </article>
      </div>

      <div v-if="!servers.length" class="srv-empty" role="status">
        <template v-if="initializing || (working && !state)">
          <UiIcon name="refresh" :size="24" /><b>正在读取配置</b><span>加载完成后展示服务器状态。</span>
        </template>
        <template v-else-if="group">
          <UiIcon name="server" :size="24" /><b>本群还没有服务器</b><span>添加第一台服务器，开始配置状态查询。</span>
          <UiButton kind="pri" :disabled="working" @click="openAddServer">
            <UiIcon name="plus" :size="16" />添加服务器
          </UiButton>
        </template>
        <template v-else>
          <UiIcon name="users" :size="24" /><b>还没有群配置</b><span>先新增一个群，再添加服务器。</span>
          <UiButton kind="pri" :disabled="working || !state" @click="openAddGroup()">
            <UiIcon name="plus" :size="16" />新增群
          </UiButton>
        </template>
      </div>
      <div v-if="!isMobile" class="srv-table-foot">
        <UiIcon name="info" :size="15" /><span>隐藏后从 /在线 撤下并停止采集；绑定与 Token 保留。更多菜单可调整顺序、管理 Token 或删除。</span>
      </div>
    </section>

    <section class="srv-rules" aria-label="本群规则">
      <header class="srv-section-heading">
        <h2>本群规则</h2><span>仅应用于当前群</span>
      </header>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>启用本群功能</b><span>仅作用于当前群；关闭后查询、绑定与管理命令不再响应，其他群和数据不受影响。</span></div>
        <UiSwitch :on="!!group?.enabled" :disabled="!group || working" aria-label="启用本群功能"
          @toggle="toggleGroupEnabled(!group?.enabled)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>启用中心数据库（本群）</b><span>{{ isMobile ? "使用公共库连接。" : "使用「全局参数 → 公共参数」里的数据库连接；只影响本群官方绑定。切换模式不会自动迁移已有绑定。" }}</span></div>
        <UiSwitch :on="!!policy?.use_central_db" :disabled="!group || working" aria-label="启用中心数据库"
          @toggle="togglePolicy('use_central_db', !policy?.use_central_db)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>显示服务器版本</b><span>在官方群状态卡片中显示每台服务器的版本。</span></div>
        <UiSwitch :on="!!policy?.show_server_version" :disabled="!group || working" aria-label="显示服务器版本"
          @toggle="togglePolicy('show_server_version', !policy?.show_server_version)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>显示服务器延迟</b><span>在官方群状态卡片中显示每台服务器的连接延迟。</span></div>
        <UiSwitch :on="!!policy?.show_server_latency" :disabled="!group || working" aria-label="显示服务器延迟"
          @toggle="togglePolicy('show_server_latency', !policy?.show_server_latency)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>显示上次在线时间</b><span>在官方群状态卡片中显示服务器最后活动时间。</span></div>
        <UiSwitch :on="!!policy?.show_last_online" :disabled="!group || working" aria-label="显示上次在线时间"
          @toggle="togglePolicy('show_last_online', !policy?.show_last_online)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>允许群主使用管理功能</b><span>平台确认的群主可在本群使用添加服务器、执行命令等管理功能。</span></div>
        <UiSwitch :on="!!policy?.enable_group_owner" :disabled="!group || working" aria-label="允许群主使用管理功能"
          @toggle="togglePolicy('enable_group_owner', !policy?.enable_group_owner)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>允许群管理员使用管理功能</b><span>平台确认的群管理员可在本群使用管理功能。</span></div>
        <UiSwitch :on="!!policy?.enable_group_admin" :disabled="!group || working" aria-label="允许群管理员使用管理功能"
          @toggle="togglePolicy('enable_group_admin', !policy?.enable_group_admin)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>绑定时校验群昵称</b><span>玩家绑定前，按下面的匹配规则核对官方群昵称。</span></div>
        <UiSwitch :on="!!policy?.bind_require_nickname" :disabled="!group || working" aria-label="绑定时校验群昵称"
          @toggle="togglePolicy('bind_require_nickname', !policy?.bind_require_nickname)" />
      </div>
      <div class="srv-bind-rule" :class="{ 'is-disabled': !policy?.bind_require_nickname }">
        <div class="srv-rule-select"><label v-if="!isMobile">匹配方式</label>
          <UiSelect :model-value="bindRule" :options="bindOptions" :disabled="!policy?.bind_require_nickname || working"
            aria-label="官方群昵称匹配方式" @change="chooseBindRule" />
        </div>
        <div class="srv-rule-preview"><span v-if="!isMobile">符合规则的昵称</span>
          <div class="srv-preview-value" :class="{ 'is-invalid': bindTemplateInvalid }">
            <UiIcon v-if="!isMobile" name="check" :size="15" /><span>{{ isMobile ? "昵称示例：" : "" }}{{ bindExample
            }}</span>
          </div>
        </div>
        <button class="srv-help-link" type="button" @click="showBindHelp = true">规则说明</button>
        <div v-if="bindRule === 'template'" class="srv-template-field" @focusout="saveBindTemplate"><label>昵称模板</label>
          <UiInput v-model="bindTemplateValue" placeholder="MC:{gameId}"
            :disabled="!policy?.bind_require_nickname || working" mono @enter="saveBindTemplate" /><span
            :class="{ 'is-invalid': bindTemplateInvalid }">必须且只能包含一个 {gameId}，最多 64 个字符。</span>
        </div>
      </div>
      <!-- QQ 官方平台无法在成员未发言时获取群昵称；先隐藏昵称检测开关与忽略名单，后端字段保留。 -->
    </section>

    <div class="srv-save-note">
      <UiIcon :name="working ? 'loader-circle' : 'circle-check'" :size="16" :class="{ 'official-spin': working }" />
      <span>{{
        working ? "保存中…" : "本群更改即时保存" }}</span>
    </div>
    <UiNotice v-if="error" tone="bad" class="srv-error">{{ error }}</UiNotice>
    <UiNotice v-if="S.error" tone="warn" class="srv-error">{{ S.error }}</UiNotice>

    <OfficialServerModal v-if="serverEditor" :server="serverEditor.server" :saving="working"
      :has-token="serverEditor.server ? hasConsoleToken(serverEditor.server) : false"
      :environment="environment" :config-id="group?.config_id ?? ''" :revision="group?.revision ?? 0"
      :group-label="group?.group_openid ?? ''"
      @close="serverEditor = null" @save="saveServer" @clear-token="clearServerToken"
      @state="applyOfficialState" />

    <UiModal v-if="deleteTarget" cls="m-confirm" title="删除服务器" :sub="deleteTarget.server.name"
      @close="deleteTarget = null">
      <UiNotice tone="warn">从当前官方群移除「{{ deleteTarget.server.name }}」，其命令 Token 一并删除。已有绑定记录不受影响。</UiNotice>
      <template #foot>
        <UiButton @click="deleteTarget = null">取消</UiButton>
        <UiButton kind="danger" :disabled="working" @click="confirmDeleteServer">
          <UiIcon name="trash" :size="16" />删除服务器
        </UiButton>
      </template>
    </UiModal>

    <UiModal v-if="showGroupMeta && group" title="编辑群信息" :sub="group.display_group || group.alias || group.group_openid"
      @close="showGroupMeta = false">
      <div class="official-modal-form">
        <div class="official-grid">
          <label class="official-field">QQ 群号（必填）
            <UiInput v-model="metaDisplayInput" :disabled="working" :maxlength="20" inputmode="numeric"
              placeholder="只用于玩家提示，只能填写数字" />
          </label>
          <label class="official-field">群别名（选填）
            <UiInput v-model="metaAliasInput" :disabled="working" :maxlength="16" placeholder="例如：生存服玩家群" />
          </label>
        </div>
        <p class="official-json-help">QQ 群号只用于玩家提示，只能填写数字；群别名选填，最多 8 个中文字符（或 16 个英文字符）。</p>
        <p class="official-id">模组绑定群键：<code>{{ group.group_openid }}</code></p>
      </div>
      <template #foot>
        <UiButton :disabled="working" @click="showGroupMeta = false">取消</UiButton>
        <UiButton kind="pri" :disabled="working || !metaDisplay.trim()" @click="saveGroupMeta">
          {{ working ? "保存中…" : "保存" }}
        </UiButton>
      </template>
    </UiModal>

    <UiModal v-if="showJson" cls="m-json" title="查看 JSON" sub="当前官方群配置 / 只读预览" @close="showJson = false">
      <div class="json-preview">
        <div class="json-toolbar">
          <span class="json-format">JSON</span>
          <button class="json-copy" type="button" :disabled="!jsonText" @click="copyJson">
            <UiIcon :name="jsonCopied ? 'check' : 'copy'" :size="16" />
            <span aria-live="polite">{{ jsonCopied ? "已复制" : "复制" }}</span>
          </button>
        </div>
        <pre class="json-code" tabindex="0" aria-label="只读 JSON 预览">{{ jsonText || "暂无群配置" }}</pre>
      </div>
      <p class="official-json-help">只读预览；不包含模组 API Token 或命令 Token。</p>
      <template #foot>
        <UiButton @click="showJson = false">关闭</UiButton>
      </template>
    </UiModal>

    <UiModal v-if="showBatchAdd" title="批量添加服务器" sub="每行一条，格式：名称:主机[:游戏端口[:模组API端口]]" @close="showBatchAdd = false">
      <UiInput v-model="batchText" class="official-batch-text" :rows="6" mono
        placeholder="生存主服:survival.example.com:25565:8090&#10;创造世界:creative.example.com" />
      <p class="official-json-help">游戏端口省略时默认 25565；模组 API 端口留空表示未安装模组。</p>
      <UiNotice v-if="batchError" tone="bad">{{ batchError }}</UiNotice>
      <UiNotice tone="warn">逐条校验，全部合法后一次性保存；任一条不合法则不会添加。</UiNotice>
      <template #foot>
        <UiButton :disabled="working" @click="showBatchAdd = false">取消</UiButton>
        <UiButton kind="pri" :disabled="working || !batchText.trim()" @click="saveBatchAdd">
          <UiIcon name="plus" :size="16" />添加服务器
        </UiButton>
      </template>
    </UiModal>

    <UiModal v-if="showBatchDelete" title="批量删除服务器" sub="从当前官方群移除所选服务器及其命令 Token。" @close="showBatchDelete = false">
      <div class="pick-summary">
        <button class="pick-all" type="button" role="checkbox" :aria-checked="allBatchSelected" @click="toggleAllBatch">
          <span class="checkbox" :class="{ checked: allBatchSelected }"><UiIcon v-if="allBatchSelected" name="check" :size="13" /></span>
          全选
        </button>
        <span>已选 {{ batchDeleteCount }} / {{ servers.length }}</span>
      </div>
      <div class="picks">
        <button
          v-for="server in servers"
          :key="server.server_id"
          class="pick"
          :class="{ selected: batchDeleteSelected[server.server_id] }"
          type="button"
          role="checkbox"
          :aria-checked="!!batchDeleteSelected[server.server_id]"
          @click="batchDeleteSelected[server.server_id] = !batchDeleteSelected[server.server_id]"
        >
          <span class="checkbox" :class="{ checked: batchDeleteSelected[server.server_id] }"><UiIcon v-if="batchDeleteSelected[server.server_id]" name="check" :size="13" /></span>
          <span class="pick-name">{{ server.name }}</span>
          <span class="pick-state">{{ server.hidden ? "已隐藏" : "显示中" }}</span>
        </button>
        <p v-if="!servers.length" class="empty-list">本群还没有服务器。</p>
      </div>
      <UiNotice tone="warn">绑定记录会保留；此操作保存后无法直接撤销。</UiNotice>
      <template #foot>
        <UiButton :disabled="working" @click="showBatchDelete = false">取消</UiButton>
        <UiButton kind="danger" :disabled="working || !batchDeleteCount" @click="openBatchDeleteConfirm">
          <UiIcon name="trash" :size="16" />删除 {{ batchDeleteCount }} 台服务器
        </UiButton>
      </template>
    </UiModal>

    <UiModal v-if="showBatchDeleteConfirm" cls="m-confirm" title="批量删除服务器" sub="从当前官方群移除所选服务器及其命令 Token。" @close="showBatchDeleteConfirm = false">
      <UiNotice tone="warn">确定删除所选 {{ batchDeleteCount }} 台服务器？其命令 Token 一并删除。</UiNotice>
      <p class="dialog-copy">绑定记录会保留；此操作保存后无法直接撤销。</p>
      <UiNotice v-if="batchDeleteError" tone="bad">{{ batchDeleteError }}</UiNotice>
      <template #foot>
        <UiButton :disabled="working" @click="showBatchDeleteConfirm = false">取消</UiButton>
        <UiButton kind="danger" :disabled="working || !batchDeleteCount" @click="saveBatchDelete">
          <UiIcon name="trash" :size="16" />删除 {{ batchDeleteCount }} 台服务器
        </UiButton>
      </template>
    </UiModal>

    <UiModal v-if="deleteGroupTarget" cls="m-confirm-typed" title="删除群配置"
      :sub="deleteGroupTarget.display_group || deleteGroupTarget.alias || deleteGroupTarget.group_openid"
      @close="deleteGroupTarget = null">
      <UiNotice tone="warn">从当前官方环境移除该群的服务器、显示规则与授权记录。</UiNotice>
      <p class="dialog-copy">本群相关查询与绑定功能将不可用，直到重新登记；模组内已有绑定记录保留。</p>
      <UiNotice v-if="error" tone="bad">{{ error }}</UiNotice>
      <label class="official-field">输入「确认删除」继续
        <UiInput v-model="deleteGroupConfirm" placeholder="确认删除"
          :error="!!deleteGroupConfirm && deleteGroupConfirm.trim() !== '确认删除'" />
      </label>
      <template #foot>
        <UiButton :disabled="working" @click="deleteGroupTarget = null">取消</UiButton>
        <UiButton kind="danger" :disabled="working || deleteGroupConfirm.trim() !== '确认删除'" @click="confirmDeleteGroup">
          <UiIcon name="trash" :size="16" />删除群配置
        </UiButton>
      </template>
    </UiModal>

    <UiModal v-if="showBindHelp" cls="m-help" title="官方群昵称匹配规则" sub="绑定前校验群昵称；修改后即时保存。" @close="showBindHelp = false">
      <div class="srv-help-field"><label>匹配方式</label>
        <UiSelect :model-value="bindRule" :options="bindOptions" :disabled="!policy?.bind_require_nickname || working"
          aria-label="官方群昵称匹配方式" @change="chooseBindRule" />
      </div>
      <UiNotice :tone="bindTemplateInvalid ? 'bad' : 'info'">{{ bindTemplateInvalid ? "模板必须且只能包含一个 {gameId}，最多 64 个字符。"
        :
        "昵称示例：" + bindExample }}</UiNotice>
      <div class="srv-rule-examples">
        <div class="srv-rule-examples-heading"><span>匹配方式</span><span>昵称示例</span></div>
        <div><b>括号内（默认）</b><span>玩家（Steve）</span></div>
        <div><b>完全等于游戏 ID</b><span>Steve</span></div>
        <div><b>包含游戏 ID</b><span>玩家Steve</span></div>
        <div><b>自定义模板</b><span>MC:Steve</span></div>
      </div>
      <template #foot>
        <UiButton kind="sec" @click="showBindHelp = false">关闭说明</UiButton>
      </template>
    </UiModal>
  </div>
</template>

<style scoped src="../styles/group-server.css"></style>

<style scoped>
.official-page .srv-groupbar {
  flex-wrap: wrap;
}

.dialog-copy {
  margin: 0;
  color: var(--ink3);
  font-size: 13px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

/* 与 OneBot「查看 JSON」同款的深色 JSON 面板 */
.json-preview {
  display: flex;
  min-height: 0;
  flex-direction: column;
  gap: 12px;
  padding: 12px 16px 16px;
  border-radius: 10px;
  background: var(--json-bg, #252e43);
}
.json-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: none;
  gap: 12px;
  height: 34px;
}
.json-format {
  color: var(--json-label, #aebbd2);
  font-family: var(--fm);
  font-size: 11px;
  font-weight: 500;
}
.json-copy {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--json-button-border, #4a5874);
  border-radius: 8px;
  background: var(--json-button-bg, #35425c);
  color: var(--json-button-text, #edf2ff);
  font-family: var(--ff);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.json-copy:hover:not(:disabled) {
  background: var(--json-button-hover, #41506d);
}
.json-copy:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.json-code {
  min-width: 0;
  min-height: 0;
  max-height: 50vh;
  margin: 0;
  overflow: auto;
  color: var(--json-text, #dde4f1);
  font-family: var(--fm);
  font-size: 12px;
  line-height: 1.5;
  tab-size: 2;
  white-space: pre;
}
.json-copy:focus-visible,
.json-code:focus-visible {
  outline: 3px solid #6a79df;
  outline-offset: 2px;
}

.official-json-help {
  margin: 0;
  color: var(--ink3);
  font-size: 12px;
  line-height: 1.6;
}

.official-batch-text {
  min-height: 132px;
}

.pick-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--ink3);
  font-size: 12px;
}

.pick-all {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: 13px/1.5 var(--ff);
  cursor: pointer;
}

.checkbox {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 1px solid var(--bd-soft);
  border-radius: 5px;
  background: var(--surface);
  color: #fff;
}

.checkbox.checked {
  border-color: var(--ac);
  background: var(--ac);
}

.picks {
  display: flex;
  min-height: 0;
  flex-direction: column;
  gap: 8px;
}

.pick {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 48px;
  padding: 10px 14px;
  border: 1px solid var(--row);
  border-radius: 8px;
  background: var(--thead);
  color: var(--ink);
  font-family: var(--ff);
  text-align: left;
  cursor: pointer;
}

.pick.selected {
  border-color: var(--ac-line);
  background: var(--ac-soft);
}

.pick-name {
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.pick-state {
  flex: none;
  margin-left: auto;
  color: var(--ink3);
  font-size: 12px;
}

.empty-list {
  margin: 0;
  padding: 18px 12px;
  color: var(--ink3);
  font-size: 12px;
  line-height: 1.5;
}

.pick-all:focus-visible,
.pick:focus-visible {
  outline: 2px solid var(--ac-line);
  outline-offset: 2px;
}

.official-badge-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.official-badge-row :deep(.bdg) {
  flex: none;
  height: 18px;
  padding: 0 7px;
  border-radius: 9px;
  font-size: 11px;
  line-height: 18px;
}

.official-card {
  display: grid;
  gap: 14px;
  min-width: 0;
  padding: 16px 18px;
  border: 1px solid var(--bd);
  border-radius: 12px;
  background: var(--surface);
}

.official-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.official-card-head>div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.official-card h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
}

.official-card p {
  margin: 0;
  color: var(--ink3);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.official-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.official-modal-form {
  display: grid;
  gap: 14px;
}

.official-modal-form p {
  margin: 0;
}

.official-field {
  display: grid;
  gap: 7px;
  min-width: 0;
  color: var(--ink3);
  font-size: 12px;
}

.official-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 13px;
}

.official-actions span {
  color: var(--ink3);
  overflow-wrap: anywhere;
}

.official-candidate {
  display: flex;
  gap: 16px;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-top: 1px solid var(--row);
}

.official-candidate>div {
  min-width: 0;
}

.official-candidate b {
  font-size: 13px;
}

.official-id,
.official-candidate code {
  font-size: 12px;
  color: var(--ink3);
  overflow-wrap: anywhere;
}

.official-confirm {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink3);
  font-size: 13px;
}

.official-confirm input {
  width: 16px;
  height: 16px;
  accent-color: var(--ac);
}

.official-spin {
  animation: official-spin 1s linear infinite;
}

@keyframes official-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .official-spin {
    animation: none;
  }
}

@media (max-width: 760px) {
  .official-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .official-candidate {
    align-items: flex-start;
    flex-direction: column;
  }

  .official-card-head {
    flex-direction: column;
    gap: 8px;
  }
}
</style>
