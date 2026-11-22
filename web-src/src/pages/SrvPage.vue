<script setup lang="ts">
import { computed, ref, watch } from "vue";
import UiIcon from "../components/UiIcon.vue";
import UiButton from "../components/UiButton.vue";
import UiBadge from "../components/UiBadge.vue";
import UiSwitch from "../components/UiSwitch.vue";
import UiInput from "../components/UiInput.vue";
import UiModal from "../components/UiModal.vue";
import UiNotice from "../components/UiNotice.vue";
import UiSelect from "../components/UiSelect.vue";
import UiMenu from "../components/UiMenu.vue";
import GroupPicker from "../components/GroupPicker.vue";
import { S, api, currentGroup, loadGroup, loading, openDialog, run, setWhitelist, setHidden, toast } from "../store";
import { useIsMobile } from "../lib/useMediaQuery";
import type { BindRule, ModState, ServerEntry } from "../lib/types";

const isMobile = useIsMobile();
const g = computed(() => currentGroup.value);
const servers = computed(() => g.value?.servers ?? []);
const modCount = computed(() => g.value?.mod_count ?? 0);
const tokenCount = computed(() => g.value?.token_count ?? 0);
const groupCount = computed(() => S.groups.length);
const ignoreCount = computed(() => g.value?.nickname_check_ignore_ids.length ?? 0);
const atServerLimit = computed(() => servers.value.length >= S.maxServers);
const batchItems = computed(() => [
  { value: "batch-add", label: "批量添加", icon: "plus", disabled: !g.value || atServerLimit.value },
  { value: "batch-del", label: "批量删除", icon: "trash", danger: true, disabled: !servers.value.length },
]);
const groupItems = computed(() => [
  ...(isMobile.value ? [
    { value: "add-group", label: "新增群", icon: "plus" },
    { value: "json-group", label: "查看 JSON", icon: "braces", disabled: !g.value },
    ...batchItems.value,
  ] : []),
  { value: "del-group", label: "删除群配置", icon: "trash", danger: true, disabled: !g.value },
]);
function groupAction(value: string) { openDialog(value); }
function serverItems(index: number) {
  return [
    { value: "up", label: "上移", icon: "arrow-up", disabled: index === 0 },
    { value: "down", label: "下移", icon: "arrow-down", disabled: index === servers.value.length - 1 },
    { value: "token", label: "命令 Token", icon: "key" },
    { value: "delete", label: "删除服务器", icon: "trash", danger: true },
  ];
}
function serverAction(value: string, server: ServerEntry, index: number) {
  if (value === "up" || value === "down") void move(server, value === "up" ? -1 : 1);
  else if (value === "delete") openDialog("del-server", { server });
  else if (value === "token") openDialog("edit-server", { server, index, focus: "token" });
}

/** 该服的模组状态读数：undefined＝这一台还没回；reachable=false＝连不上；enabled=null＝旧版模组没这字段 */
function modState(s: ServerEntry): ModState | undefined {
  return S.modStates[s.name];
}

function modBadge(s: ServerEntry): { tone: "port" | "off" | "ok" | "warn"; text: string; title: string } {
  if (s.api_port === null) return { tone: "port", text: "未配端口", title: "没写 api_port：该服按未安装模组处理" };
  const st = modState(s);
  if (!st) return { tone: "off", text: S.modStateLoading ? "读取中…" : "未读取", title: modTitle(s) };
  if (!st.reachable) return { tone: "warn", text: "不可达", title: st.reason || "连不上该服的模组 API" };
  if (st.enabled === null) return { tone: "warn", text: "旧版不支持", title: st.reason || "模组版本没有登录开关字段" };
  return { tone: "ok", text: "已连通", title: "模组 API 已连通" };
}

async function toggleFlag(key: "last_online_enabled" | "nickname_check_enabled" | "bind_require_nickname", value: boolean) {
  await run("flag", async () => {
    await api.groupFlag(S.gid, key, value);
    await loadGroup();
    if (!!g.value?.[key] !== value) toast(`后端未生效：${key} 读回来还是 ${!!g.value?.[key]}`);
  });
}

/* ---- 绑定群名片的匹配规则 ---- */
const bindRule = ref<BindRule>("paren");
const bindTemplate = ref("");
const showBindHelp = ref(false);
const bindOptions = [
  { value: "paren", label: "括号内的游戏 ID", description: "玩家（Steve）" },
  { value: "equals", label: "完全等于游戏 ID", description: "Steve" },
  { value: "contains", label: "包含游戏 ID", description: "玩家Steve" },
  { value: "template", label: "自定义模板", description: "MC:{gameId}" },
];
function changeBindRule(value: string) {
  if (!bindOptions.some((option) => option.value === value)) return;
  bindRule.value = value as BindRule;
  void saveBindRule();
}
watch(
  () => g.value,
  (v) => {
    bindRule.value = v?.bind_nickname_rule ?? "paren";
    bindTemplate.value = v?.bind_nickname_template ?? "";
  },
  { immediate: true },
);

const RULE_SAMPLE = "Steve";
const bindTemplateInvalid = computed(() => {
  if (bindRule.value !== "template") return false;
  const tpl = bindTemplate.value;
  return !tpl.trim() || tpl.length > 64 || tpl.split("{gameId}").length - 1 !== 1;
});
const bindExample = computed(() => {
  if (bindRule.value === "equals") return RULE_SAMPLE;
  if (bindRule.value === "contains") return `玩家${RULE_SAMPLE}`;
  if (bindRule.value === "template") return bindTemplate.value.replace("{gameId}", RULE_SAMPLE);
  return `玩家（${RULE_SAMPLE}）`;
});

let bindSaving = false;
async function saveBindRule() {
  // 选了自定义模板但还没填：先给一个可用的默认，免得留下「选了却没存上」的空档
  if (bindRule.value === "template" && !bindTemplate.value.trim()) {
    bindTemplate.value = "MC:{gameId}";
  }
  if (bindTemplateInvalid.value) {
    toast("模板必须包含且只包含一个 {gameId}");
    return;
  }
  if (bindSaving) return;
  bindSaving = true;
  try {
    await run("bind-rule", async () => {
      await api.bindRule(S.gid, bindRule.value, bindTemplate.value);
      await loadGroup();
    });
  } finally {
    bindSaving = false;
  }
}

/* 登录开关是模组侧的配置，只能实时读：读不到就禁用开关，不拿默认值冒充已知 */
function modEnabled(s: ServerEntry): boolean | null {
  return S.modStates[s.name]?.enabled ?? null;
}

function modKnown(s: ServerEntry): boolean {
  return s.api_port !== null && modEnabled(s) !== null;
}

function modOn(s: ServerEntry): boolean {
  return modEnabled(s) === true;
}

function modTitle(s: ServerEntry): string {
  if (s.api_port === null) return "未配置模组 API 端口，该服视为未安装模组，开关不可用";
  const state = S.modStates[s.name];
  if (!state) return S.modStateLoading ? "正在读取模组状态…" : "尚未读到该服状态，开关暂不可用";
  if (state.enabled === null) return state.reason || "未连通，登录开关状态未知";
  return state.enabled
    ? "已开启：未绑定玩家进服会被限制"
    : "已关闭：所有玩家不受限制，在线受限玩家已放开";
}

/* 标签只答「机器人这一侧存没存口令」，模组侧认不认要靠 /执行 才能验，这里把边界写在悬停里 */
function tokTitle(s: ServerEntry): string {
  if (!s.has_token) return "未设置：/执行 对该服未启用";
  if (s.api_port === null) return "口令已存，但该服未配模组 API 端口，/执行 发不出去";
  return "口令已存：需与该服模组的 consoleToken 逐字一致";
}

/* 备注有两个仓（手填值 / MOTD 缓存），列表只画后端算好的最终值，来源与采集时间进悬停 */
function motdTime(epoch: number): string {
  const d = new Date(epoch * 1000);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

function remarkTitle(s: ServerEntry): string {
  if (s.remark_src === "motd") {
    return s.remark_motd ? `来自服务端 motd · 采集于 ${motdTime(s.remark_motd_at)}` : "已选「用 MOTD」但还没采到值，当前显示手填的值";
  }
  return s.remark_motd ? `手填的值 · 另存着上次采到的 motd：${s.remark_motd}` : "手填的值";
}

/* 手机卡片空间小：同一句解释不在「模组状态」和「登录开关」里重复两遍 */
function modNote(s: ServerEntry): string {
  if (s.api_port === null) return "没写 api_port：该服按未安装模组处理";
  const st = modState(s);
  if (!st) return S.modStateLoading ? "" : "尚未读到该服状态";
  if (!st.reachable) return st.reason || "连不上该服的模组 API";
  if (st.enabled === null) return st.reason || "模组版本没有登录开关字段";
  return "";
}

function modShort(s: ServerEntry): string {
  if (s.api_port === null) return "未知";
  const state = S.modStates[s.name];
  if (!state) return S.modStateLoading ? "读取中…" : "未知";
  if (state.enabled === null) return "未知";
  return state.enabled ? "已开启" : "已关闭";
}

async function move(srv: ServerEntry, dir: -1 | 1) {
  const at = servers.value.findIndex((s) => s.name === srv.name);
  const index = at + dir;
  await run("move", async () => {
    const r = await api.serverMove(S.gid, srv.name, index);
    S.group = r;
    const to = (r.servers || []).findIndex((s) => s.name === srv.name);
    if (to !== index) toast(`后端未生效：「${srv.name}」请求移到 ${index}，读回来在 ${to}`);
  });
}
</script>

<template>
  <div class="srv-page" :class="{ 'is-mobile': isMobile }">
    <header class="srv-heading">
      <div><h1>群与服务器</h1><p>{{ isMobile ? "本群操作即时保存" : "按群管理服务器、显示方式与绑定规则。" }}</p></div>
      <UiBadge v-if="!isMobile" tone="ok">即时保存</UiBadge>
    </header>

    <section class="srv-groupbar" aria-label="当前群配置">
      <template v-if="!isMobile"><UiIcon name="users" :size="18" /><span class="srv-group-label">当前群</span></template>
      <GroupPicker class="srv-group-picker" />
      <div class="srv-group-actions">
        <UiButton v-if="!isMobile" kind="sec" @click="openDialog('add-group')"><UiIcon name="plus" :size="16" />新增群</UiButton>
        <UiMenu :items="groupItems" :label="isMobile ? undefined : '更多群操作'" @select="groupAction" />
      </div>
    </section>

    <div v-if="!isMobile" class="srv-summary" aria-label="本群概况">
      <span>已配置 {{ groupCount }} 个群</span><span>本群服务器 {{ servers.length }} / {{ S.maxServers }}</span>
      <span>已配模组端口 {{ modCount }} 台</span><span>命令 Token 已设置 {{ tokenCount }} 台</span>
    </div>

    <section class="srv-list" :class="{ 'srv-list--mobile': isMobile }" aria-label="服务器列表">
      <div class="srv-list-toolbar">
        <h2 v-if="!isMobile">服务器列表</h2><span v-else class="srv-mobile-count">{{ servers.length }} / {{ S.maxServers }} 台服务器</span>
        <div class="srv-toolbar-actions">
          <template v-if="!isMobile">
            <UiButton kind="sec" :disabled="!g" @click="openDialog('json-group')"><UiIcon name="braces" :size="16" />查看 JSON</UiButton>
            <UiMenu :items="batchItems" label="批量管理" icon="list-checks" @select="groupAction" />
          </template>
          <UiButton kind="pri" :disabled="!g || atServerLimit" :title="atServerLimit ? '每群最多 ' + S.maxServers + ' 台服务器' : undefined" @click="openDialog('add-server')"><UiIcon name="plus" :size="16" />{{ isMobile ? "添加" : "添加服务器" }}</UiButton>
        </div>
      </div>

      <div v-if="!isMobile" class="srv-table-scroll">
        <div class="srv-table" role="table" aria-label="当前群服务器">
          <div class="srv-table-head srv-grid" role="row">
            <span class="srv-cell--name" role="columnheader">服务器 / 备注</span>
            <span class="srv-cell--address" role="columnheader">主机地址 / 模组端口</span>
            <span role="columnheader">模组状态</span><span role="columnheader">登录限制</span>
            <span role="columnheader">命令 Token</span><span class="srv-cell--actions" role="columnheader">操作</span>
          </div>
          <div v-for="(server, index) in servers" :key="server.name" class="srv-table-row srv-grid" :class="{ 'is-hidden': server.hidden }" role="row">
            <div class="srv-name-cell srv-cell--name" role="cell">
              <b class="srv-server-name">{{ server.name }}</b>
              <div class="srv-remark-line">
                <UiBadge v-if="server.hidden" tone="off">隐藏</UiBadge>
                <UiBadge v-if="server.remark_src === 'motd' && server.remark_motd" tone="neutral">MOTD</UiBadge>
                <span :title="remarkTitle(server)">{{ server.remark_display || "暂无备注" }}</span>
              </div>
            </div>
            <div class="srv-address-cell srv-cell--address" role="cell"><span class="srv-host">{{ server.host }}</span><span class="srv-secondary">API {{ server.api_port ?? "未配置" }}</span></div>
            <div class="srv-state-cell" role="cell">
              <UiBadge :tone="modBadge(server).tone" :title="modBadge(server).title">{{ modBadge(server).text }}</UiBadge>
              <span v-if="server.api_port !== null && modState(server) && !modState(server)?.reachable" class="srv-mod-error" :title="modNote(server)">连接失败</span>
            </div>
            <div class="srv-login-cell" role="cell" :title="modTitle(server)"><UiSwitch :on="modOn(server)" :disabled="!modKnown(server)" :aria-label="server.name + '登录限制'" @toggle="setWhitelist(server.name, !modOn(server))" /><span>{{ modShort(server) }}</span></div>
            <div role="cell"><UiBadge :tone="server.has_token ? 'neutral' : 'off'" :title="tokTitle(server)">{{ server.has_token ? "已设置" : "未设置" }}</UiBadge></div>
            <div class="srv-row-actions" role="cell">
              <button class="srv-action srv-action--edit" type="button" @click="openDialog('edit-server', { server, index })">编辑</button>
              <button class="srv-action" type="button" :title="server.hidden ? '恢复在 /在线 中展示与采集' : '从 /在线 撤下并停止采集，保留绑定与 Token'" @click="setHidden(server.name, !server.hidden)">{{ server.hidden ? "显示" : "隐藏" }}</button>
              <UiMenu :items="serverItems(index)" @select="(value) => serverAction(value, server, index)">
                <template #trigger="{ attrs, toggle }"><button v-bind="attrs" class="srv-action srv-action--more" type="button" :aria-label="server.name + '更多操作'" @click="toggle"><UiIcon name="more-horizontal" :size="16" /></button></template>
              </UiMenu>
            </div>
          </div>
        </div>
      </div>

      <div v-else-if="servers.length" class="srv-mobile-list">
        <article v-for="(server, index) in servers" :key="server.name" class="srv-mobile-card" :class="{ 'is-hidden': server.hidden }">
          <div class="srv-mobile-card-title"><b class="srv-server-name">{{ server.name }}</b><UiBadge :tone="modBadge(server).tone" :title="modBadge(server).title">{{ modBadge(server).text }}</UiBadge></div>
          <div class="srv-host">{{ server.host }}</div>
          <div class="srv-remark-line"><UiBadge v-if="server.hidden" tone="off">隐藏</UiBadge><UiBadge v-if="server.remark_src === 'motd' && server.remark_motd" tone="neutral">MOTD</UiBadge><span :title="remarkTitle(server)">{{ server.remark_display || "暂无备注" }}</span></div>
          <dl class="srv-mobile-details">
            <div><dt>模组 API 端口</dt><dd>{{ server.api_port ?? "未配置" }}</dd></div>
            <div><dt>命令 Token</dt><dd><UiBadge :tone="server.has_token ? 'neutral' : 'off'" :title="tokTitle(server)">{{ server.has_token ? "已设置" : "未设置" }}</UiBadge></dd></div>
            <div class="srv-mobile-login"><dt>登录限制</dt><dd :title="modTitle(server)"><span>{{ modShort(server) }}</span><UiSwitch :on="modOn(server)" :disabled="!modKnown(server)" :aria-label="server.name + '登录限制'" @toggle="setWhitelist(server.name, !modOn(server))" /></dd></div>
          </dl>
          <p v-if="modNote(server)" class="srv-mobile-status-note">{{ modNote(server) }}</p>
          <div class="srv-mobile-actions">
            <UiButton kind="sec" @click="openDialog('edit-server', { server, index })"><UiIcon name="pencil" :size="16" />编辑</UiButton>
            <UiButton kind="sec" @click="setHidden(server.name, !server.hidden)"><UiIcon :name="server.hidden ? 'eye' : 'eye-off'" :size="16" />{{ server.hidden ? "显示" : "隐藏" }}</UiButton>
            <UiMenu :items="serverItems(index)" label="更多" @select="(value) => serverAction(value, server, index)" />
          </div>
        </article>
      </div>

      <div v-if="!servers.length" class="srv-empty" role="status">
        <template v-if="loading"><UiIcon name="refresh" :size="24" /><b>正在读取配置</b><span>加载完成后展示服务器状态。</span></template>
        <template v-else-if="g"><UiIcon name="server" :size="24" /><b>本群还没有服务器</b><span>添加第一台服务器，开始配置状态查询。</span><UiButton kind="pri" @click="openDialog('add-server')"><UiIcon name="plus" :size="16" />添加服务器</UiButton></template>
        <template v-else><UiIcon name="users" :size="24" /><b>还没有群配置</b><span>先新增一个群，再添加服务器。</span><UiButton kind="pri" @click="openDialog('add-group')"><UiIcon name="plus" :size="16" />新增群</UiButton></template>
      </div>
      <div v-if="!isMobile" class="srv-table-foot"><UiIcon name="info" :size="15" /><span>隐藏后从 /在线 撤下并停止采集；绑定与 Token 保留。更多菜单可调整顺序、管理 Token 或删除。</span></div>
    </section>

    <section class="srv-rules" aria-label="本群规则">
      <header class="srv-section-heading"><h2>本群规则</h2><span v-if="!isMobile">仅应用于当前群</span></header>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>显示上次在线时间</b><span>{{ isMobile ? "展示最后活动时间。" : "在 /在线 卡片中显示服务器最后活动时间。" }}</span></div>
        <UiSwitch :on="!!g?.last_online_enabled" :disabled="!g" aria-label="显示上次在线时间" @toggle="toggleFlag('last_online_enabled', !g?.last_online_enabled)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>绑定时校验群名片</b><span>{{ isMobile ? "按匹配规则检查名片。" : "玩家绑定前，按下面的匹配规则核对群名片。" }}</span></div>
        <UiSwitch :on="!!g?.bind_require_nickname" :disabled="!g" aria-label="绑定时校验群名片" @toggle="toggleFlag('bind_require_nickname', !g?.bind_require_nickname)" />
      </div>
      <div class="srv-bind-rule" :class="{ 'is-disabled': !g?.bind_require_nickname }">
        <div class="srv-rule-select"><label v-if="!isMobile">匹配方式</label><UiSelect :model-value="bindRule" :options="bindOptions" :disabled="!g?.bind_require_nickname" aria-label="群名片匹配方式" @change="changeBindRule" /></div>
        <div class="srv-rule-preview"><span v-if="!isMobile">符合规则的名片</span><div class="srv-preview-value" :class="{ 'is-invalid': bindTemplateInvalid }"><UiIcon v-if="!isMobile" name="check" :size="15" /><span>{{ isMobile ? "昵称示例：" : "" }}{{ bindExample }}</span></div></div>
        <button class="srv-help-link" type="button" @click="showBindHelp = true">规则说明</button>
        <div v-if="bindRule === 'template'" class="srv-template-field" @focusout="saveBindRule"><label>名片模板</label><UiInput v-model="bindTemplate" placeholder="MC:{gameId}" :disabled="!g?.bind_require_nickname" mono @enter="saveBindRule" /><span :class="{ 'is-invalid': bindTemplateInvalid }">必须且只能包含一个 {gameId}，最多 64 个字符。</span></div>
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>启用昵称检测</b><span>{{ isMobile ? "允许检测成员群名片。" : "允许本群使用 /检测昵称 检查成员群名片。" }}</span></div>
        <UiSwitch :on="!!g?.nickname_check_enabled" :disabled="!g" aria-label="启用昵称检测" @toggle="toggleFlag('nickname_check_enabled', !g?.nickname_check_enabled)" />
      </div>
      <div class="srv-rule-row">
        <div class="srv-rule-copy"><b>{{ isMobile ? "忽略名单" : "昵称检测忽略名单" }}</b><span>{{ isMobile ? ignoreCount + " 位成员" : "名单内成员不参与名片校验。" }}</span></div>
        <div class="srv-ignore-actions"><UiBadge v-if="!isMobile" tone="neutral">{{ ignoreCount }} 位成员</UiBadge><UiButton kind="sec" :disabled="!g" @click="openDialog('ignore-list')"><UiIcon v-if="!isMobile" name="users" :size="16" />{{ isMobile ? "管理" : "管理名单" }}</UiButton></div>
      </div>
    </section>

    <div class="srv-save-note"><UiIcon name="circle-check" :size="16" /><span>本群更改即时保存</span></div>
    <UiNotice v-if="S.error" tone="bad" class="srv-error">{{ S.error }}</UiNotice>

    <UiModal v-if="showBindHelp" cls="m-help" title="群名片匹配规则" sub="绑定前校验群名片；修改后即时保存。" subNote @close="showBindHelp = false">
      <div class="srv-help-field"><label>匹配方式</label><UiSelect :model-value="bindRule" :options="bindOptions" :disabled="!g?.bind_require_nickname" aria-label="群名片匹配方式" @change="changeBindRule" /></div>
      <div v-if="bindRule === 'template'" class="srv-help-field" @focusout="saveBindRule"><label>名片模板</label><UiInput v-model="bindTemplate" placeholder="MC:{gameId}" :disabled="!g?.bind_require_nickname" mono @enter="saveBindRule" /><span :class="{ 'is-invalid': bindTemplateInvalid }">必须且只能包含一个 {gameId}，最多 64 个字符。</span></div>
      <UiNotice :tone="bindTemplateInvalid ? 'bad' : 'info'">{{ bindTemplateInvalid ? "模板必须且只能包含一个 {gameId}，最多 64 个字符。" : "昵称示例：" + bindExample }}</UiNotice>
      <div class="srv-rule-examples"><div class="srv-rule-examples-heading"><span>匹配方式</span><span>名片示例</span></div><div><b>括号内（默认）</b><span>玩家（Steve）</span></div><div><b>完全等于游戏 ID</b><span>Steve</span></div><div><b>包含游戏 ID</b><span>玩家Steve</span></div><div><b>自定义模板</b><span>MC:Steve</span></div></div>
      <details class="srv-rule-details"><summary>匹配细节与注意事项</summary><ul><li>游戏 ID 以游戏中登录的名字为准，大小写必须一致。</li><li>括号内：括号里的游戏 ID 必须完全一致，括号前的称呼可以不同。</li><li>完全等于：群名片只写游戏 ID，允许前后空格。</li><li>包含游戏 ID：不能是另一个更长 ID 的一部分，例如 Steve2、My_Steve 都不匹配 Steve。</li><li>自定义模板：替换 {gameId} 后，名片必须与结果完全一致。</li><li>关闭绑定校验后，上述规则不生效，绑定仍需使用绑定码。</li><li>模板无效时不会保存；配置中的规则损坏时会退回括号规则。</li></ul></details>
      <template #foot><UiButton kind="sec" @click="showBindHelp = false">关闭说明</UiButton></template>
    </UiModal>
  </div>
</template>

<style scoped>
.srv-page { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.srv-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; order: 0; }
.srv-heading h1 { margin: 0; color: var(--ink); font-size: 24px; font-weight: 600; line-height: 1.5; }
.srv-heading p { margin: 4px 0 0; color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-groupbar { order: 1; display: flex; align-items: center; gap: 12px; min-width: 0; padding: 12px 16px; border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); color: var(--ink3); }
.srv-group-label { flex: none; font-size: 12px; }
.srv-group-picker { flex: 0 1 260px; min-width: 0; width: 260px; }
.srv-group-actions, .srv-toolbar-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.srv-summary { order: 2; display: flex; align-items: center; flex-wrap: wrap; gap: 8px 20px; color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-list { order: 3; min-width: 0; border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); }
.srv-list-toolbar { display: flex; align-items: center; gap: 8px; padding: 13px 18px; }
.srv-list-toolbar h2, .srv-section-heading h2 { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.5; }
.srv-table-scroll { overflow-x: auto; }
.srv-table { min-width: 1020px; }
/* Canvas inner columns: 304 / 292 / 128 / 136 / 116 / 160px at 1136px. */
.srv-grid { display: grid; grid-template-columns: minmax(0, 304fr) minmax(0, 292fr) minmax(0, 128fr) minmax(0, 136fr) minmax(0, 116fr) 160px; align-items: center; padding: 0 18px; }
.srv-grid > * { min-width: 0; }
.srv-table-head { min-height: 40px; background: var(--thead); color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-table-row { padding-top: 14px; padding-bottom: 14px; border-bottom: 1px solid var(--row); }
.srv-table-row.is-hidden { background-color: var(--hidden-bg, #f8f9fb); background-image: repeating-linear-gradient(-45deg, transparent 0 7px, rgba(104, 120, 138, 0.06) 7px 8px); }
.srv-table-row:hover { background-color: var(--row-hover, #f8f9ff); }
.srv-table-row.is-hidden:hover { background-color: var(--row-hover, #f8f9ff); background-image: repeating-linear-gradient(-45deg, transparent 0 7px, rgba(104, 120, 138, 0.09) 7px 8px); }
.srv-cell--name { padding-right: 16px; }
.srv-cell--address { padding-right: 14px; }
.srv-cell--actions { text-align: right; }
.srv-name-cell, .srv-address-cell, .srv-state-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; }
.srv-name-cell { align-items: stretch; }
.srv-server-name { display: block; min-width: 0; width: 100%; color: var(--ink); font-size: 14px; font-weight: 600; line-height: 1.5; overflow-wrap: anywhere; }
.srv-remark-line { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-remark-line > span:last-child { min-width: 0; overflow-wrap: anywhere; }
/* 行内小标签压到与 12px 文字同高，隐藏状态不会把整行撑高 */
.srv-remark-line :deep(.bdg) { flex: none; height: 18px; padding: 0 7px; border-radius: 9px; font-size: 11px; line-height: 18px; }
.srv-host { max-width: 100%; font-family: var(--fm); font-size: 12px; line-height: 1.5; color: var(--ink); overflow-wrap: anywhere; }
.srv-address-cell { gap: 4px; }
.srv-secondary { color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-mod-error { color: var(--warn); font-size: 11px; line-height: 1.5; }
.srv-login-cell { display: flex; align-items: center; gap: 8px; color: var(--ink3); font-size: 12px; white-space: nowrap; }
.srv-row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 6px; }
.srv-action { display: inline-flex; align-items: center; justify-content: center; flex: none; gap: 6px; height: 30px; padding: 0 9px; border: 0; border-radius: 7px; background: var(--quiet-bg, #f6f8fb); color: var(--ink3); font: 500 12px/1.5 var(--ff); cursor: pointer; transition: background-color 140ms ease, color 140ms ease; }
.srv-action--edit { background: var(--quiet-accent-bg, #f1f3fd); color: var(--ac); }
.srv-action--more { width: 30px; padding: 0 7px; }
.srv-action:hover { background: var(--ac-soft); color: var(--ac); }
.srv-action:focus-visible, .srv-help-link:focus-visible { outline: 3px solid var(--focus-ring, #bbc3f7); outline-offset: 2px; }
.srv-table-foot { display: flex; align-items: center; gap: 8px; padding: 10px 18px; color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-table-foot > svg { flex: none; }
.srv-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; padding: 36px 24px; color: var(--ink3); text-align: center; }
.srv-empty b { color: var(--ink); font-size: 15px; font-weight: 600; }
.srv-empty span { font-size: 13px; }
.srv-rules { order: 4; border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); }
.srv-section-heading { display: flex; align-items: center; gap: 12px; padding: 13px 18px; border-bottom: 1px solid var(--row); }
.srv-section-heading > span { color: var(--ink3); font-size: 12px; }
.srv-rule-row { display: flex; align-items: center; gap: 16px; padding: 11px 18px; border-bottom: 1px solid var(--row); }
.srv-rule-row:last-child { border-bottom: 0; }
.srv-rule-copy { display: flex; flex: 1 1 auto; flex-direction: column; gap: 3px; min-width: 0; }
.srv-rule-copy b { color: var(--ink); font-size: 13px; font-weight: 500; line-height: 1.5; }
.srv-rule-copy > span { color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-bind-rule { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 16px; padding: 13px 18px; background: var(--subtle-bg, #fafbfd); border-bottom: 1px solid var(--row); }
.srv-rule-select { display: flex; flex: 0 0 260px; flex-direction: column; gap: 6px; min-width: 0; }
.srv-rule-select > label, .srv-rule-preview > span, .srv-template-field > label { color: var(--ink3); font-size: 12px; line-height: 1.5; }
.srv-rule-preview { display: flex; flex: 1 1 180px; flex-direction: column; gap: 6px; min-width: 0; }
.srv-preview-value { display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 6px 10px; border-radius: 6px; background: var(--ac-soft); color: var(--ac); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.srv-preview-value > svg { flex: none; }
.srv-help-link { flex: 0 0 100px; margin-top: 26px; padding: 0; border: 0; background: transparent; color: var(--ac); font: 400 13px/1.5 var(--ff); text-align: left; cursor: pointer; }
.srv-help-link:hover { text-decoration: underline; text-underline-offset: 3px; }
.srv-template-field { display: flex; flex: 0 0 100%; flex-direction: column; gap: 7px; min-width: 0; }
.srv-template-field > span { font-size: 12px; color: var(--ink3); }
.srv-bind-rule.is-disabled .srv-rule-preview { opacity: 0.6; }
.srv-ignore-actions { display: flex; flex: none; align-items: center; gap: 16px; }
.srv-save-note { order: 5; display: flex; align-items: center; gap: 8px; color: var(--ok); font-size: 12px; line-height: 1.5; }
.srv-error { order: 6; }
.srv-help-field { display: flex; flex-direction: column; gap: 7px; }
.srv-help-field > label { font-size: 13px; font-weight: 500; color: var(--ink); }
.srv-help-field > span { color: var(--ink3); font-size: 12px; }
.srv-rule-examples { display: flex; flex-direction: column; }
.srv-rule-examples > div { display: flex; justify-content: space-between; gap: 20px; padding: 10px 0; border-bottom: 1px solid var(--row); color: var(--ink3); font-size: 13px; }
.srv-rule-examples > div > b { color: var(--ink); font-weight: 400; }
.srv-rule-examples .srv-rule-examples-heading { font-size: 12px; }
.srv-rule-details { font-size: 12px; line-height: 1.7; color: var(--ink3); }
.srv-rule-details summary { color: var(--ac); cursor: pointer; }
.srv-rule-details ul { display: grid; gap: 5px; margin: 12px 0 0; padding-left: 20px; }
.is-invalid { color: var(--bad) !important; }

.srv-page.is-mobile { gap: 14px; }
.srv-page.is-mobile .srv-groupbar { order: -1; padding: 0; border: 0; border-radius: 0; background: transparent; }
.srv-page.is-mobile .srv-group-picker { flex: 1 1 auto; width: auto; }
.srv-page.is-mobile .srv-group-actions { margin-left: 0; }
.srv-page.is-mobile .srv-heading h1 { font-size: 22px; }
.srv-page.is-mobile .srv-heading p { font-size: 12px; }
.srv-list--mobile { border: 0; background: transparent; }
.srv-list--mobile .srv-list-toolbar { padding: 0 0 12px; }
.srv-mobile-count { font-size: 12px; color: var(--ink3); }
.srv-mobile-list { display: flex; flex-direction: column; gap: 12px; }
.srv-mobile-card { display: flex; flex-direction: column; gap: 10px; padding: 14px; border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); }
.srv-mobile-card.is-hidden { background-color: var(--hidden-bg, #f8f9fb); background-image: repeating-linear-gradient(-45deg, transparent 0 7px, rgba(104, 120, 138, 0.06) 7px 8px); }
.srv-mobile-card-title { display: flex; align-items: center; gap: 8px; }
.srv-mobile-card-title .srv-server-name { flex: 1 1 auto; width: auto; font-size: 16px; }
.srv-mobile-card-title :deep(.bdg) { flex: none; }
.srv-mobile-details { display: flex; flex-direction: column; gap: 3px; margin: 0; }
.srv-mobile-details > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 28px; }
.srv-mobile-details dt { font-size: 12px; color: var(--ink3); }
.srv-mobile-details dd { display: flex; align-items: center; gap: 8px; margin: 0; font-size: 13px; color: var(--ink); }
.srv-mobile-details .srv-mobile-login { min-height: 32px; }
.srv-mobile-login dt { font-size: 13px; color: var(--ink); }
.srv-mobile-login dd { color: var(--ink3); font-size: 12px; }
.srv-mobile-status-note { margin: 0; font-size: 12px; line-height: 1.5; color: var(--ink3); overflow-wrap: anywhere; }
.srv-mobile-actions { display: flex; align-items: center; gap: 8px; }
.srv-page.is-mobile .srv-section-heading { padding: 14px 16px; }
.srv-page.is-mobile .srv-rule-row { padding: 12px 14px; gap: 12px; }
.srv-page.is-mobile .srv-rule-copy { gap: 4px; }
.srv-page.is-mobile .srv-bind-rule { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; padding: 10px 14px; }
.srv-page.is-mobile .srv-rule-select { grid-column: 1 / -1; }
.srv-page.is-mobile .srv-rule-preview { min-width: 0; }
.srv-page.is-mobile .srv-preview-value { min-height: 0; padding: 0; background: transparent; font-size: 12px; }
.srv-page.is-mobile .srv-help-link { width: auto; margin: 0; font-size: 12px; }
.srv-page.is-mobile .srv-template-field { grid-column: 1 / -1; }
.srv-page.is-mobile .srv-ignore-actions { gap: 8px; }
.srv-page.is-mobile .srv-empty { border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); }

@media (prefers-reduced-motion: reduce) {
  .srv-action { transition: none; }
}
</style>
