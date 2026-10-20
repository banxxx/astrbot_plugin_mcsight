<script setup lang="ts">
import { computed } from "vue";
import UiIcon from "../components/UiIcon.vue";
import UiButton from "../components/UiButton.vue";
import UiBadge from "../components/UiBadge.vue";
import UiSwitch from "../components/UiSwitch.vue";
import UiNotice from "../components/UiNotice.vue";
import GroupPicker from "../components/GroupPicker.vue";
import { S, api, currentGroup, loadGroup, openDialog, run, toast } from "../store";
import type { ServerEntry } from "../lib/types";

const g = computed(() => currentGroup.value);
const servers = computed(() => g.value?.servers ?? []);
const modCount = computed(() => g.value?.mod_count ?? 0);
const tokenCount = computed(() => g.value?.token_count ?? 0);
const groupCount = computed(() => S.groups.length);
const ignoreCount = computed(() => g.value?.nickname_check_ignore_ids.length ?? 0);
const noPortCount = computed(() => servers.value.filter((s) => s.api_port === null).length);

const sub = computed(() =>
  g.value
    ? `${g.value.gid} · ${servers.value.length} 台 · 已装模组 ${modCount.value} · 未配端口 ${noPortCount.value}`
    : "还没有群配置文件",
);

async function toggleFlag(key: "last_online_enabled" | "nickname_check_enabled", value: boolean) {
  await run("flag", async () => {
    await api.groupFlag(S.gid, key, value);
    await loadGroup();
    if (!!g.value?.[key] !== value) toast(`后端未生效：${key} 读回来还是 ${!!g.value?.[key]}`);
  });
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
    <section class="gbar">
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

    <section class="stats">
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
          <span class="c-op">操作</span>
        </div>
      </div>

      <div v-for="(s, i) in servers" :key="s.name" class="row">
        <div class="cols">
          <span class="c-name"><span class="cell-label">名称</span><b class="nm ell">{{ s.name }}</b></span>
          <span class="c-host"><span class="cell-label">地址</span><span class="ell">{{ s.host }}</span></span>
          <span class="c-port"><span class="cell-label">端口</span><span :class="{ dash: s.api_port === null }">{{ s.api_port ?? "—" }}</span></span>
          <span class="c-rmk"><span class="cell-label">备注</span><span class="ell" :class="{ dash: !s.remark }">{{ s.remark || "—" }}</span></span>
          <span class="c-tk"><UiBadge :tone="s.has_token ? 'neutral' : 'off'">{{ s.has_token ? "已配置" : "未设置" }}</UiBadge></span>
          <span class="c-mod">
            <UiBadge v-if="s.api_port === null" tone="port">未配端口</UiBadge>
            <UiBadge v-else tone="ok">已装模组</UiBadge>
          </span>
          <span class="c-op ops">
            <button class="lnk" type="button" @click="openDialog('edit-server', { server: s, index: i })">编辑</button>
            <button class="lnk" type="button" @click="openDialog('token', { server: s })">换 Token</button>
            <button class="arw" type="button" title="上移" :disabled="i === 0" @click="move(s, -1)"><UiIcon name="arrow-up" :size="12" /></button>
            <button class="arw" type="button" title="下移" :disabled="i === servers.length - 1" @click="move(s, 1)"><UiIcon name="arrow-down" :size="12" /></button>
            <button class="lnk bad" type="button" @click="openDialog('del-server', { server: s })">删除</button>
          </span>
        </div>
      </div>

      <div v-if="g && !servers.length" class="empty">本群还没有服务器条目，点右上「添加服务器」建第一条。</div>

      <div class="tf">
        <span>共 {{ servers.length }} 条 · 每群上限 {{ S.maxServers }} 台（代码常量 MAX_SERVERS_PER_GROUP）</span>
        <span class="tip"><UiIcon name="arrow-up-down" :size="12" />↑↓ 对应 /mc move（位置序号从 0 开始）</span>
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

    <UiNotice v-if="S.error" tone="bad" class="errline">{{ S.error }}</UiNotice>
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
  color: var(--ink4);
  font-family: var(--ff);
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
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

.errline {
  margin-top: 4px;
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
