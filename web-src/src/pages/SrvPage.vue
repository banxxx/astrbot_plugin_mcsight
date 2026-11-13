<script setup lang="ts">
import { computed, ref, watch } from "vue";
import UiIcon from "../components/UiIcon.vue";
import UiButton from "../components/UiButton.vue";
import UiBadge from "../components/UiBadge.vue";
import UiSwitch from "../components/UiSwitch.vue";
import UiInput from "../components/UiInput.vue";
import UiModal from "../components/UiModal.vue";
import UiNotice from "../components/UiNotice.vue";
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
const noPortCount = computed(() => servers.value.filter((s) => s.api_port === null).length);

const sub = computed(() => {
  /* 读还没回来时别说「还没有群配置文件」——那会把「不知道」说成「没有」 */
  if (loading.value && !g.value) return "读取中…";
  return g.value
    ? `${g.value.gid} · ${servers.value.length} 台 · 已装模组 ${modCount.value} · 未配端口 ${noPortCount.value}`
    : "还没有群配置文件";
});

/** 该服的模组状态读数：undefined＝这一台还没回；reachable=false＝连不上；enabled=null＝旧版模组没这字段 */
function modState(s: ServerEntry): ModState | undefined {
  return S.modStates[s.name];
}

function modBadge(s: ServerEntry): { tone: "port" | "off" | "ok" | "warn"; text: string; title: string } {
  if (s.api_port === null) return { tone: "port", text: "未配端口", title: "没写 api_port：该服按未安装模组处理" };
  const st = modState(s);
  if (!st) return { tone: "off", text: S.modStateLoading ? "读取中…" : "未读取", title: modTitle(s) };
  if (!st.reachable) return { tone: "warn", text: "不可达", title: st.reason || "连不上该服的模组 API" };
  if (st.enabled === null) return { tone: "warn", text: "已装·旧版", title: st.reason || "模组版本没有登录开关字段" };
  return { tone: "ok", text: "已装模组", title: "模组 API 已连通" };
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

function tokenNote(s: ServerEntry): string {
  if (!s.has_token) return "/执行 对该服不可用";
  if (s.api_port === null) return "该服未配模组端口，/执行 发不出去";
  return "";
}

function modShort(s: ServerEntry): string {
  if (s.api_port === null) return "不可用";
  const state = S.modStates[s.name];
  if (!state) return S.modStateLoading ? "读取中…" : "不可用";
  if (state.enabled === null) return "不可用";
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
  <div class="page">
    <section v-if="isMobile" class="m-groupbar">
      <div class="m-groupbar-top">
        <label>当前群</label>
        <GroupPicker />
      </div>
      <div class="m-groupbar-acts">
        <UiButton kind="ghost" @click="openDialog('add-group')"><UiIcon name="plus" :size="14" />新增群</UiButton>
        <UiButton kind="danger-light" :disabled="!g" @click="openDialog('del-group')">删除群配置</UiButton>
      </div>
    </section>
    <section v-else class="gbar">
      <span class="bar" />
      <label>当前群</label>
      <GroupPicker />
      <div class="right">
        <UiButton kind="ghost" @click="openDialog('add-group')"><UiIcon name="plus" :size="13" />新增群</UiButton>
        <UiButton kind="danger-light" :disabled="!g" @click="openDialog('del-group')">{{ "删除群配置" }}</UiButton>
      </div>
    </section>

    <section class="ph">
      <div class="ph-t">
        <h1>群与服务器</h1>
        <p>{{ sub }}</p>
      </div>
    </section>

    <section v-if="isMobile" class="m-stats">
      <div class="m-stat"><label>群组配置文件</label><b>{{ groupCount }}</b><em>servers_&lt;群号&gt;.json</em></div>
      <div class="m-stat"><label>服务器条目</label><b>{{ servers.length }} / {{ S.maxServers }}</b></div>
      <div class="m-stat">
        <label>已装模组</label><b>{{ modCount }} / {{ servers.length }}</b>
        <span class="cap"><i :style="{ width: servers.length ? (modCount / servers.length) * 100 + '%' : '0%' }" /></span>
      </div>
      <div class="m-stat">
        <label>已配命令 Token</label><b>{{ tokenCount }} / {{ servers.length }}</b>
        <span class="cap warn"><i :style="{ width: servers.length ? (tokenCount / servers.length) * 100 + '%' : '0%' }" /></span>
      </div>
    </section>
    <section v-else class="stats">
      <div class="st"><label>群组配置文件</label><b>{{ groupCount }}</b><em>servers_&lt;群号&gt;.json</em></div>
      <span class="sp" />
      <div class="st"><label>服务器条目 / 本群</label><b>{{ servers.length }} / {{ S.maxServers }}</b></div>
      <span class="sp" />
      <div class="st">
        <label>已装模组</label><b>{{ modCount }} / {{ servers.length }}</b>
        <span class="cap"><i :style="{ width: servers.length ? (modCount / servers.length) * 100 + '%' : '0%' }" /></span>
      </div>
      <span class="sp" />
      <div class="st">
        <label>已配命令 Token</label><b>{{ tokenCount }} / {{ servers.length }}</b>
        <span class="cap warn"><i :style="{ width: servers.length ? (tokenCount / servers.length) * 100 + '%' : '0%' }" /></span>
      </div>
    </section>

    <template v-if="isMobile">
      <section class="m-sec">
        <div class="m-sec-h">
          <span class="bar" />
          <h2>服务器条目</h2>
          <span class="m-count">{{ servers.length }} / {{ S.maxServers }}</span>
        </div>
        <div class="m-tools">
          <UiButton kind="tool" :disabled="!g" @click="openDialog('json-group')">查看 JSON</UiButton>
          <UiButton kind="tool" :disabled="servers.length === 0" @click="openDialog('batch-del')">批量删除</UiButton>
          <UiButton kind="tool" :disabled="!g" @click="openDialog('batch-add')">批量添加</UiButton>
          <UiButton kind="pri" :disabled="!g" @click="openDialog('add-server')"><UiIcon name="plus" :size="15" />添加服务器</UiButton>
        </div>
      </section>

      <div v-if="servers.length" class="m-list">
        <article v-for="(s, i) in servers" :key="s.name" class="srv-card" :class="{ hid: s.hidden }">
          <div class="srv-h">
            <b class="srv-name">{{ s.name }}</b>
            <UiBadge v-if="s.hidden" tone="off">已隐藏</UiBadge>
          </div>
          <dl class="srv-kv">
            <div><dt>地址</dt><dd>{{ s.host }}</dd></div>
            <div><dt>API 端口</dt><dd :class="{ dash: s.api_port === null }">{{ s.api_port ?? "—" }}</dd></div>
            <div>
              <dt>备注</dt>
              <dd>
                <UiBadge v-if="s.remark_src === 'motd' && s.remark_motd" tone="neutral">MOTD</UiBadge>
                <span :class="{ dash: !s.remark_display }">{{ s.remark_display || "—" }}</span>
              </dd>
            </div>
            <div>
              <dt>命令 Token</dt>
              <dd>
                <UiBadge :tone="s.has_token ? 'neutral' : 'off'">{{ s.has_token ? "已配置" : "未设置" }}</UiBadge>
                <span v-if="tokenNote(s)" class="mnote">{{ tokenNote(s) }}</span>
              </dd>
            </div>
            <div>
              <dt>模组状态</dt>
              <dd>
                <UiBadge :tone="modBadge(s).tone">{{ modBadge(s).text }}</UiBadge>
                <span v-if="modNote(s)" class="mnote">{{ modNote(s) }}</span>
              </dd>
            </div>
            <div class="srv-sw">
              <dt>登录开关</dt>
              <dd>
                <UiSwitch
                  :on="modOn(s)"
                  :disabled="!modKnown(s)"
                  :title="modTitle(s)"
                  @toggle="setWhitelist(s.name, !modOn(s))"
                />
                <span class="mnote">{{ modShort(s) }}</span>
              </dd>
            </div>
          </dl>
          <div class="srv-acts">
            <button class="mact" type="button" @click="openDialog('edit-server', { server: s, index: i })">编辑</button>
            <button class="mact" type="button" @click="setHidden(s.name, !s.hidden)">{{ s.hidden ? "显示" : "隐藏" }}</button>
            <button class="mact" type="button" title="上移" aria-label="上移" :disabled="i === 0" @click="move(s, -1)"><UiIcon name="arrow-up" :size="15" /></button>
            <button class="mact" type="button" title="下移" aria-label="下移" :disabled="i === servers.length - 1" @click="move(s, 1)"><UiIcon name="arrow-down" :size="15" /></button>
            <button class="mact danger" type="button" @click="openDialog('del-server', { server: s })">删除</button>
          </div>
        </article>
      </div>
      <div v-else-if="loading && !servers.length" class="m-empty">读取中…</div>
      <div v-else-if="g" class="m-empty">本群还没有服务器条目，点上方「添加服务器」建第一条。</div>
      <div v-else class="m-empty">还没有群配置文件</div>

      <div class="m-foot">共 {{ servers.length }} 条 · 每群上限 {{ S.maxServers }} 台（代码常量 MAX_SERVERS_PER_GROUP）</div>

      <section class="m-card">
        <div class="m-card-h">
          <span class="bar" />
          <h2>本群开关</h2>
          <span class="m-count">servers_&lt;群号&gt;.json</span>
        </div>
        <div class="m-swrow">
          <div class="m-swrow-t">
            <b>上次在线显示</b>
            <span>开启后 /在线 会显示服务器最后活动时间</span>
            <em>/上次在线 开|关</em>
          </div>
          <UiSwitch :on="!!g?.last_online_enabled" :disabled="!g" @toggle="toggleFlag('last_online_enabled', !g?.last_online_enabled)" />
        </div>
        <div class="m-swrow">
          <div class="m-swrow-t">
            <b>绑定校验群名片</b>
            <span>开启后，/绑定 按下面的规则核对群昵称</span>
            <em>默认开启</em>
          </div>
          <UiSwitch :on="!!g?.bind_require_nickname" :disabled="!g" @toggle="toggleFlag('bind_require_nickname', !g?.bind_require_nickname)" />
        </div>
        <div class="m-rule-row" :class="{ off: !g?.bind_require_nickname }">
          <button class="help-btn" type="button" title="规则说明" aria-label="规则说明" @click="showBindHelp = true">?</button>
          <div class="m-rule-line">
            <span class="rl">匹配方式</span>
            <div class="sel-wrap">
              <select v-model="bindRule" class="sel" :disabled="!g?.bind_require_nickname" @change="saveBindRule">
                <option value="paren">括号内（默认）</option>
                <option value="equals">完全等于游戏ID</option>
                <option value="contains">包含游戏ID</option>
                <option value="template">自定义模板</option>
              </select>
            </div>
          </div>
          <div class="m-rule-line">
            <span class="rl">模板</span>
            <div class="rule-inp" @focusout="saveBindRule">
              <UiInput
                v-model="bindTemplate"
                placeholder="如 MC:{gameId}"
                mono
                :disabled="!g?.bind_require_nickname || bindRule !== 'template'"
                @enter="saveBindRule"
              />
            </div>
          </div>
          <div class="rule-ex" :class="{ bad: bindTemplateInvalid }">
            <template v-if="bindTemplateInvalid">模板里要有一个 {gameId}（只能用一次）</template>
            <template v-else>昵称示例：{{ bindExample }}</template>
          </div>
        </div>
        <div class="m-swrow">
          <div class="m-swrow-t">
            <b>昵称检测</b>
            <span>开启后本群可使用 /检测昵称</span>
            <em>/mc nicknamecheck 开|关</em>
          </div>
          <UiSwitch :on="!!g?.nickname_check_enabled" :disabled="!g" @toggle="toggleFlag('nickname_check_enabled', !g?.nickname_check_enabled)" />
        </div>
        <div class="m-swrow">
          <div class="m-swrow-t">
            <b>昵称检测忽略名单</b>
            <span>名单内的 QQ 不参与名片校验</span>
            <em>/mc nicknamecheck 忽略|取消忽略 &lt;QQ&gt;</em>
          </div>
          <div class="m-swrow-r">
            <span class="m-count">名单内 {{ ignoreCount }} 个 QQ</span>
            <UiButton kind="line" :disabled="!g" @click="openDialog('ignore-list')"><UiIcon name="list" :size="14" />管理名单</UiButton>
          </div>
        </div>
      </section>
    </template>

    <template v-else>
      <section class="card">
        <div class="ch">
          <span class="bar" />
          <h2>服务器条目</h2>
          <div class="acts">
            <UiButton kind="tool" :disabled="!g" @click="openDialog('json-group')">查看 JSON</UiButton>
            <UiButton kind="tool" :disabled="servers.length === 0" @click="openDialog('batch-del')">批量删除</UiButton>
            <UiButton kind="tool" :disabled="!g" @click="openDialog('batch-add')">批量添加</UiButton>
            <UiButton kind="pri" :disabled="!g" @click="openDialog('add-server')"><UiIcon name="plus" :size="14" />添加服务器</UiButton>
          </div>
        </div>

        <div class="th">
          <div class="cols">
            <span class="c-name">名称</span><span class="c-host">主机地址</span><span class="c-port">API 端口</span>
            <span class="c-rmk">备注</span><span class="c-tk">命令 Token</span><span class="c-mod">模组状态</span>
            <span class="c-wl">登录开关</span><span class="c-op">操作</span>
          </div>
        </div>

        <div v-for="(s, i) in servers" :key="s.name" class="row" :class="{ hid: s.hidden }">
          <div class="cols">
            <span class="c-name"><span class="cell-label">名称</span><b class="nm ell">{{ s.name }}</b><UiBadge v-if="s.hidden" tone="off">已隐藏</UiBadge></span>
            <span class="c-host"><span class="cell-label">地址</span><span class="ell">{{ s.host }}</span></span>
            <span class="c-port"><span class="cell-label">端口</span><span :class="{ dash: s.api_port === null }">{{ s.api_port ?? "—" }}</span></span>
            <span class="c-rmk"><span class="cell-label">备注</span><UiBadge v-if="s.remark_src === 'motd' && s.remark_motd" tone="neutral" title="该值来自服务端 motd">MOTD</UiBadge><span class="ell" :class="{ dash: !s.remark_display }" :title="remarkTitle(s)">{{ s.remark_display || "—" }}</span></span>
            <span class="c-tk"><UiBadge :tone="s.has_token ? 'neutral' : 'off'" :title="tokTitle(s)">{{ s.has_token ? "已配置" : "未设置" }}</UiBadge></span>
            <span class="c-mod">
              <UiBadge :tone="modBadge(s).tone" :title="modBadge(s).title">{{ modBadge(s).text }}</UiBadge>
            </span>
            <span class="c-wl">
              <span class="cell-label">登录开关</span>
              <UiSwitch
                :on="modOn(s)"
                :disabled="!modKnown(s)"
                :title="modTitle(s)"
                @toggle="setWhitelist(s.name, !modOn(s))"
              />
            </span>
            <span class="c-op ops">
              <button class="lnk" type="button" @click="openDialog('edit-server', { server: s, index: i })">编辑</button>
              <button class="lnk" type="button" :title="s.hidden ? '取消隐藏：/在线 会重新采集并出这张卡' : '隐藏：/在线 不再采集这台，也不出卡片'" @click="setHidden(s.name, !s.hidden)">{{ s.hidden ? "显示" : "隐藏" }}</button>
              <button class="arw" type="button" title="上移" :disabled="i === 0" @click="move(s, -1)"><UiIcon name="arrow-up" :size="12" /></button>
              <button class="arw" type="button" title="下移" :disabled="i === servers.length - 1" @click="move(s, 1)"><UiIcon name="arrow-down" :size="12" /></button>
              <button class="lnk bad" type="button" @click="openDialog('del-server', { server: s })">删除</button>
            </span>
          </div>
        </div>

        <div v-if="g && !servers.length" class="empty">本群还没有服务器条目，点右上「添加服务器」建第一条。</div>

        <div class="tf">
          <span v-if="loading && !servers.length">读取中…</span>
          <span v-else>共 {{ servers.length }} 条 · 每群上限 {{ S.maxServers }} 台（代码常量 MAX_SERVERS_PER_GROUP）</span>
          <span class="tip"><UiIcon name="arrow-up-down" :size="12" />对应 /mc move（位置序号从 0 开始）</span>
        </div>
      </section>

      <section class="card swc">
        <div class="ch">
          <span class="bar" />
          <h2>本群开关</h2>
          <span class="hint">与服务器条目同文件（servers_&lt;群号&gt;.json）</span>
        </div>

        <div class="swrow">
          <div class="sl">
            <b>上次在线显示</b>
            <span>开启后 /在线 会显示服务器最后活动时间</span>
            <em>/上次在线 开|关</em>
          </div>
          <UiSwitch :on="!!g?.last_online_enabled" :disabled="!g" @toggle="toggleFlag('last_online_enabled', !g?.last_online_enabled)" />
        </div>

        <div class="swrow">
          <div class="sl">
            <b>绑定校验群名片</b>
            <span>开启后，/绑定 按下面的规则核对群昵称</span>
            <em>默认开启</em>
          </div>
          <UiSwitch :on="!!g?.bind_require_nickname" :disabled="!g" @toggle="toggleFlag('bind_require_nickname', !g?.bind_require_nickname)" />
        </div>

        <div class="rule-row" :class="{ off: !g?.bind_require_nickname }">
          <button class="help-btn" type="button" title="规则说明" aria-label="规则说明" @click="showBindHelp = true">?</button>
          <div class="rule-line">
            <span class="rl">匹配方式</span>
            <div class="sel-wrap">
              <select v-model="bindRule" class="sel" :disabled="!g?.bind_require_nickname" @change="saveBindRule">
                <option value="paren">括号内（默认）</option>
                <option value="equals">完全等于游戏ID</option>
                <option value="contains">包含游戏ID</option>
                <option value="template">自定义模板</option>
              </select>
            </div>
          </div>
          <div class="rule-line">
            <span class="rl">模板</span>
            <div class="rule-inp" @focusout="saveBindRule">
              <UiInput
                v-model="bindTemplate"
                placeholder="如 MC:{gameId}"
                mono
                :disabled="!g?.bind_require_nickname || bindRule !== 'template'"
                @enter="saveBindRule"
              />
            </div>
          </div>
          <div class="rule-ex" :class="{ bad: bindTemplateInvalid }">
            <template v-if="bindTemplateInvalid">模板里要有一个 {gameId}（只能用一次）</template>
            <template v-else>昵称示例：{{ bindExample }}</template>
          </div>
        </div>

        <div class="swrow">
          <div class="sl">
            <b>昵称检测</b>
            <span>开启后本群可使用 /检测昵称</span>
            <em>/mc nicknamecheck 开|关</em>
          </div>
          <UiSwitch :on="!!g?.nickname_check_enabled" :disabled="!g" @toggle="toggleFlag('nickname_check_enabled', !g?.nickname_check_enabled)" />
        </div>

        <div class="swrow">
          <div class="sl">
            <b>昵称检测忽略名单</b>
            <span>名单内的 QQ 不参与名片校验</span>
            <em>/mc nicknamecheck 忽略|取消忽略 &lt;QQ&gt;</em>
          </div>
          <div class="sr">
            <span class="cnt">名单内 {{ ignoreCount }} 个 QQ</span>
            <UiButton kind="line" :disabled="!g" @click="openDialog('ignore-list')"><UiIcon name="list" :size="13" />管理名单</UiButton>
          </div>
        </div>
      </section>
    </template>

    <UiNotice v-if="S.error" tone="bad" class="errline">{{ S.error }}</UiNotice>

    <UiModal
      v-if="showBindHelp"
      cls="m-help"
      title="绑定名片规则说明"
      sub="仅在「绑定校验群名片」开启时生效；游戏ID 始终以游戏里登录的为准"
      subNote
      @close="showBindHelp = false"
    >
      <p class="help-lead">这里决定 /绑定 时，你的群昵称要写成什么样才算通过。游戏ID 以你在游戏里登录的名字为准，下面统一用 <b>Steve</b> 举例。</p>
      <div class="help-sec">
        <h4>四种写法</h4>
        <div class="help-item">
          <div class="help-name">括号内（默认）</div>
          <p>昵称里用括号写上游戏ID，括号里的字要和游戏ID一模一样；括号前面写什么都可以。</p>
          <p class="help-eg"><span class="ok">能过</span>玩家（Steve）、Steve(Steve)、大佬（Steve）</p>
          <p class="help-eg"><span class="no">过不了</span>Steve（没括号）、玩家（Steve2）、玩家（steve）</p>
        </div>
        <div class="help-item">
          <div class="help-name">完全等于游戏ID</div>
          <p>群昵称只写游戏ID，不加任何字；前后多几个空格没关系。</p>
          <p class="help-eg"><span class="ok">能过</span>Steve</p>
          <p class="help-eg"><span class="no">过不了</span>玩家Steve、Steve大佬</p>
        </div>
        <div class="help-item">
          <div class="help-name">包含游戏ID</div>
          <p>昵称里只要有游戏ID就行，但它不能是另一个更长ID的一部分。</p>
          <p class="help-eg"><span class="ok">能过</span>玩家Steve、Steve大佬、大Steve</p>
          <p class="help-eg"><span class="no">过不了</span>Steve2（那是另一个ID）、My_Steve（下划线连着的也算另一个ID）、steve（大小写不同）</p>
        </div>
        <div class="help-item">
          <div class="help-name">自定义模板</div>
          <p>你自己定格式，用 <code>{gameId}</code> 代表游戏ID，填进去以后要一字不差。</p>
          <p class="help-eg"><span class="ok">能过</span>模板写 <code>MC:{gameId}</code>，昵称就写 MC:Steve</p>
          <p class="help-eg"><span class="no">过不了</span>MC:steve（大小写不同）、XMC:Steve（前面多了字）</p>
        </div>
      </div>
      <div class="help-sec">
        <h4>怎么选</h4>
        <ul>
          <li>大多数群用默认的「括号内」就行。</li>
          <li>群里习惯直接拿游戏ID当昵称，选「完全等于游戏ID」。</li>
          <li>昵称里还带称呼或头衔，选「包含游戏ID」。</li>
          <li>群里有一套固定格式（比如 MC:Steve），选「自定义模板」。</li>
        </ul>
      </div>
      <div class="help-sec">
        <h4>还要注意</h4>
        <ul>
          <li>大小写要一模一样：Steve 和 steve 不算同一个。</li>
          <li>自定义模板里必须有一个 <code>{gameId}</code>，只能用一次，最多 64 个字。</li>
          <li>模板写错时不会保存；配置文件里的规则坏了会自动按「括号内」处理，不会把绑定卡死。</li>
          <li>关掉「绑定校验群名片」后，这些规则都不生效，绑定只看绑定码。</li>
          <li>改上面的选择时，下面的「昵称示例」会跟着变，照着写就行。</li>
        </ul>
      </div>
      <template #foot>
        <UiButton kind="pri" @click="showBindHelp = false">知道了</UiButton>
      </template>
    </UiModal>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--gap-card);
}

.gbar,
.stats {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 var(--pad-card);
  border: 1px solid var(--bd);
  border-radius: var(--r-card);
  background: var(--surface);
}

.gbar .bar,
.ch .bar {
  width: 3px;
  height: 18px;
  border-radius: 1.5px;
  background: var(--ac);
  flex: none;
}

.gbar label {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink2);
}

.gbar .right {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
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

.stats {
  gap: 16px;
}

.st {
  display: flex;
  flex: 1 1 0;
  align-items: center;
  gap: 6px;
  min-width: 0;
  white-space: nowrap;
}

.st label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ink3);
}

.st b {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.st em {
  font-size: 10px;
  font-style: normal;
  color: var(--ink4);
}

.st .cap {
  width: 60px;
  height: 4px;
  border-radius: 2px;
  background: var(--row);
  overflow: hidden;
}

.st .cap i {
  display: block;
  height: 4px;
  border-radius: 2px;
  background: var(--ac);
}

.st .cap.warn i {
  background: var(--warn);
}

.sp {
  width: 1px;
  height: 18px;
  background: var(--row);
  flex: none;
}

.ch {
  border-bottom: 1px solid var(--row);
}

.ch h2 {
  border-bottom: none;
}

.ch .acts {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.nm {
  font-weight: 600;
  color: var(--ink);
}

/* 隐藏中的条目：铺一层 1px 斜线纹，不铺纯色——整块底色会把「暂时不上 /在线」
   说成「这条废了」；名字转灰＋名字后挂「已隐藏」牌（牌给竖排卡片档兜底）。
   线的深浅只由 --hatch 一个令牌控制。 */
.row.hid {
  background-image: repeating-linear-gradient(135deg, var(--hatch) 0 1px, transparent 1px 7px);
}

.row.hid .nm {
  color: var(--ink3);
  font-weight: 500;
}

.c-host,
.c-port {
  color: var(--ink2);
}

.c-rmk {
  font-size: 12px;
  color: var(--ink3);
}

.dash {
  color: var(--ink5);
}

.ops {
  display: flex;
  align-items: center;
  gap: 14px;
}

.lnk {
  padding: 0;
  border: none;
  background: none;
  color: var(--ac);
  font-family: var(--ff);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.lnk.bad {
  color: var(--bad);
}

.lnk:hover {
  text-decoration: underline;
}

.arw {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: none;
  color: var(--ink2);
  font-family: var(--ff);
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
}

.arw:hover:not(:disabled) {
  color: var(--ac);
}

.arw:disabled {
  color: var(--ink5);
  cursor: not-allowed;
}

.swc .ch {
  border-bottom: 1px solid var(--row);
}

/* 画布 sc-h 里说明文字紧跟标题左排，不像参数页那样推到右端 */
.swc .ch .hint {
  margin-left: 0;
}

.swrow {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 64px;
  padding: 0 var(--pad-card);
  border-bottom: 1px solid var(--row);
}

.swrow:last-child {
  border-bottom: none;
}

.sl {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.sl b {
  font-size: 13px;
  font-weight: 500;
  line-height: 16px;
  color: var(--ink);
}

.sl span {
  font-size: 11px;
  line-height: 14px;
  color: var(--ink3);
}

.sl em {
  font-size: 11px;
  font-style: normal;
  font-weight: 500;
  line-height: 14px;
  color: var(--ac);
}

/* 绑定匹配规则：常驻一行，主开关关闭时整行置灰 */
.rule-row {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px var(--pad-card) 14px;
  border-bottom: 1px solid var(--row);
}

.rule-row.off .rule-line,
.rule-row.off .rule-ex {
  opacity: 0.55;
}

.help-btn {
  position: absolute;
  top: 9px;
  right: var(--pad-card);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid var(--bd);
  border-radius: 50%;
  background: var(--surface);
  color: var(--ink3);
  font-family: var(--ff);
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
}

.help-btn:hover {
  border-color: var(--ac);
  color: var(--ac);
}

.rule-line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.rule-line .rl {
  width: 64px;
  flex: none;
  font-size: 11px;
  color: var(--ink3);
}

.sel-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.sel-wrap::after {
  content: "";
  position: absolute;
  right: 11px;
  top: 50%;
  width: 6px;
  height: 6px;
  border-right: 1.5px solid var(--ink3);
  border-bottom: 1.5px solid var(--ink3);
  transform: translateY(-70%) rotate(45deg);
  pointer-events: none;
}

.sel {
  appearance: none;
  color-scheme: dark;
  height: 32px;
  min-width: 180px;
  padding: 0 30px 0 10px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--surface);
  color: var(--ink);
  font-family: var(--ff);
  font-size: 13px;
}

.sel:disabled {
  cursor: not-allowed;
}

.rule-inp {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 360px;
}

.rule-ex {
  padding-left: 74px;
  font-size: 11px;
  color: var(--ink4);
}

.rule-ex.bad {
  color: var(--bad);
}

.m-rule-row {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--row);
}

.m-rule-row.off .m-rule-line,
.m-rule-row.off .rule-ex {
  opacity: 0.55;
}

.m-rule-line {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.m-rule-line .rl {
  font-size: 11px;
  color: var(--ink3);
}

.m-rule-row .sel {
  width: 100%;
  min-width: 0;
}

.m-rule-row .rule-inp {
  max-width: none;
}

.m-rule-row .rule-ex {
  padding-left: 0;
}

/* 规则说明弹窗内容 */
.help-lead {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink3);
}

.help-lead b {
  color: var(--ink);
  font-weight: 600;
}

.help-sec {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.help-sec h4 {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink2);
}

.help-sec p {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink3);
}

.help-sec ul {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 5px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink3);
}

.help-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 9px 10px;
  border: 1px solid var(--row);
  border-radius: 6px;
}

.help-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}

.help-sec .help-eg {
  font-size: 11px;
  line-height: 1.6;
  color: var(--ink4);
}

.help-eg .ok {
  margin-right: 4px;
  color: var(--ok);
  font-weight: 600;
}

.help-eg .no {
  margin: 0 6px;
  color: var(--bad);
  font-weight: 600;
}

.help-sec code {
  font-family: var(--fm);
  color: var(--ac);
}

.sr {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

.cnt {
  font-size: 11px;
  color: var(--ink4);
}

@container page (max-width: 1040px) {
  .stats {
    height: auto;
    flex-wrap: wrap;
    gap: 10px 16px;
    padding: 10px var(--pad-card);
  }

  .sp {
    display: none;
  }

  .st {
    flex: 0 0 auto;
  }
}

@container page (max-width: 980px) {
  .gbar {
    height: auto;
    flex-wrap: wrap;
    padding: 8px var(--pad-card);
  }

  .gbar .right {
    margin-left: 0;
    flex-wrap: wrap;
  }

  .ch {
    height: auto;
    flex-wrap: wrap;
    padding: 8px var(--pad-card);
  }

  .ch .acts {
    margin-left: 0;
    flex-wrap: wrap;
  }
}
</style>
