<script setup lang="ts">
/* 14 个弹窗按画布的 5 个原型实现：确认 / 表单 / 单字段密钥 / 列表管理 / 宽表 JSON。
   规矩：遮罩与 Esc 都不关闭；删除类必须二次确认；弹窗内的「移除」不再叠确认。
   注意：具名插槽必须是组件的直接子节点，所以页脚统一放在一个 #foot 里按弹窗分支。 */
import { computed, reactive, watch } from "vue";
import UiButton from "./UiButton.vue";
import UiField from "./UiField.vue";
import UiIcon from "./UiIcon.vue";
import UiInput from "./UiInput.vue";
import UiModal from "./UiModal.vue";
import UiNotice from "./UiNotice.vue";
import { S, api, closeDialog, loadGroup, openDialog, run, servers, toast } from "../store";
import type { ConfigItem, ServerEntry } from "../lib/types";

const d = computed(() => S.dialog);
const payload = computed(() => (d.value?.payload || {}) as Record<string, unknown>);
const srv = computed(() => payload.value.server as ServerEntry | undefined);
const name = computed(() => d.value?.name || "");

const f = reactive<Record<string, string>>({});
const err = reactive<Record<string, string>>({});
const selected = reactive<Record<string, boolean>>({});
const batchResult = reactive({ message: "", failed: [] as string[] });
const jsonText = reactive({ file: "", text: "" });

const FORMS = ["add-server", "edit-server"];
const CONFIRMS = ["clear-token", "del-server", "del-group"];
const LISTS = ["ignore-list", "list-manage"];
const JSONS = ["json-group", "json-config"];
/* 画布上「等价命令 / 文件操作」副标题是等宽主色，说明行是正体灰；只有 JSON 弹窗标题用等宽 */
const NOTE_SUBS = ["add-group", "json-group", "json-config"];

const modalCls = computed(() => {
  if (CONFIRMS.includes(name.value)) return "m-confirm";
  if (name.value === "token") return "m-single";
  if (JSONS.includes(name.value) || name.value === "batch-add") return "m-json";
  if (name.value === "batch-del" || LISTS.includes(name.value)) return "m-list";
  return "m-form";
});

const title = computed(() => {
  switch (name.value) {
    case "add-server":
      return "添加服务器";
    case "edit-server":
      return `编辑「${srv.value?.name ?? ""}」`;
    case "add-group":
      return "新增群";
    case "token":
      return `命令 Token · ${srv.value?.name ?? ""}`;
    case "clear-token":
      return "清除命令 Token";
    case "del-server":
      return "删除服务器";
    case "del-group":
      return "删除群配置";
    case "batch-add":
      return "批量添加";
    case "batch-del":
      return "批量删除";
    case "json-group":
      return `servers_${S.gid}.json`;
    case "json-config":
      return "astrbot_plugin_mcsight_config.json";
    case "ignore-list":
      return `忽略名单 · ${S.gid}`;
    case "list-manage":
      return `${String(payload.value.label ?? "")}名单`;
    default:
      return "";
  }
});

const sub = computed(() => {
  switch (name.value) {
    case "add-server":
      return "等价命令 /mc add <名称> <地址> [端口]";
    case "edit-server":
      return "等价命令 /mc edit 名称 name|host|port|remark";
    case "token":
      return `等价命令 /mc token ${srv.value?.name ?? ""} <Token>`;
    case "clear-token":
      return `等价命令 /mc token ${srv.value?.name ?? ""} 清除`;
    case "del-server":
      return `等价命令 /mc remove ${srv.value?.name ?? ""}`;
    case "del-group":
      return `文件操作 · servers_${S.gid}.json`;
    case "batch-add":
      return "等价命令 /mc batchadd 名:IP[:端口],名:IP…";
    case "batch-del":
      return "等价命令 /mc batchremove 名,名";
    case "ignore-list":
      return "等价命令 /mc nicknamecheck 忽略|取消忽略 <QQ>";
    case "list-manage":
      return `写 _conf_schema.json 的 ${String(payload.value.item ?? "")} · 整表覆盖保存`;
    default:
      return "";
  }
});

const isForm = computed(() => FORMS.includes(name.value));
const footInfo = computed(() => {
  if (name.value === "add-server") return `当前 ${servers.value.length} / ${S.maxServers} 台 · 每群上限`;
  if (name.value === "edit-server") return "改地址不影响已有绑定";
  return "";
});
const listNote = computed(() =>
  name.value === "ignore-list"
    ? `名单内 ${listItems.value.length} 个 QQ · 添加与移除即时写入 servers_<群号>.json`
    : `名单内 ${listItems.value.length} 个 · 保存时整表写回 _conf_schema.json`,
);
const confirmWarn = computed(() => {
  if (name.value === "clear-token") return "清除后该服的 /执行 立即不可用，需重新设置才能恢复。";
  if (name.value === "del-server") return `将从 servers_${S.gid}.json 移除「${srv.value?.name ?? ""}」，其命令 Token 一并删除。`;
  return `该群还有 ${servers.value.length} 台服务器条目，删除后 /在线 /绑定 /查询 在这个群全部失效，且不会自动恢复。`;
});
const confirmNote = computed(() => {
  if (name.value === "clear-token") return "现值不会被显示，也无法找回；密钥字段按「留空＝功能关闭」处理。";
  if (name.value === "del-server") return "绑定记录不受影响。";
  return "本群开关与忽略名单一并随文件删除。绑定记录不在此文件内，不受影响。";
});
const confirmVerb = computed(() =>
  name.value === "clear-token" ? "确认清除" : name.value === "del-server" ? "确认删除" : "确认删除文件",
);
const footAction = computed(() => {
  switch (name.value) {
    case "add-server":
      return submitAddServer;
    case "edit-server":
      return submitEditServer;
    case "add-group":
      return submitAddGroup;
    case "token":
      return submitToken;
    case "clear-token":
      return submitClearToken;
    case "del-server":
      return submitDelServer;
    case "del-group":
      return submitDelGroup;
    case "batch-add":
      return submitBatchAdd;
    case "batch-del":
      return submitBatchDel;
    default:
      return null;
  }
});
const footVerb = computed(() => {
  if (CONFIRMS.includes(name.value)) return confirmVerb.value;
  if (name.value === "add-server" || name.value === "batch-add") return "添加";
  if (name.value === "add-group") return "创建";
  if (name.value === "batch-del") return "删除所选";
  return "保存";
});
const footKind = computed(() => (name.value === "del-server" || name.value === "del-group" || name.value === "batch-del" ? "danger" : "pri"));
const footIcon = computed(() => (CONFIRMS.includes(name.value) || name.value === "batch-del" ? null : name.value === "edit-server" || name.value === "token" ? "check" : "plus"));

watch(
  d,
  (cur) => {
    for (const k of Object.keys(f)) delete f[k];
    for (const k of Object.keys(err)) delete err[k];
    batchResult.message = "";
    batchResult.failed = [];
    if (!cur) return;
    if (cur.name === "edit-server") {
      const s = cur.payload?.server as ServerEntry;
      f.name = s.name;
      f.host = s.host;
      f.port = s.api_port == null ? "" : String(s.api_port);
      f.remark = s.remark;
    } else if (cur.name === "token") {
      f.token = "";
    } else if (cur.name === "add-server") {
      f.name = "";
      f.host = "";
      f.port = "";
      f.remark = "";
    } else if (cur.name === "add-group") {
      f.gid = "";
    } else if (cur.name === "batch-add") {
      f.text = "";
    } else if (cur.name === "batch-del") {
      for (const key of Object.keys(selected)) delete selected[key];
    } else if (cur.name === "ignore-list" || cur.name === "list-manage") {
      f.qq = "";
    }
  },
  { immediate: true },
);

watch(d, async (cur) => {
  if (!cur) return;
  if (cur.name === "json-group") {
    const r = await run("json", () => api.jsonGroup(S.gid));
    if (r) Object.assign(jsonText, r);
  } else if (cur.name === "json-config") {
    const r = await run("json", () => api.configJson());
    if (r) Object.assign(jsonText, r);
  }
});

const listItems = computed<string[]>(() => {
  if (name.value === "ignore-list") return S.group?.nickname_check_ignore_ids ?? [];
  const it = payload.value.item as ConfigItem | undefined;
  return Array.isArray(it?.value) ? (it?.value as string[]) : [];
});

const selectedNames = computed(() => servers.value.filter((s) => selected[s.name]).map((s) => s.name));

async function submitAddServer() {
  await run("add", async () => {
    const r = await api.serverCreate(S.gid, { name: f.name, host: f.host, api_port: f.port, remark: f.remark });
    S.group = r;
    closeDialog();
    toast(`已添加「${f.name}」`);
  });
}

async function submitEditServer() {
  await run("edit", async () => {
    const s = srv.value;
    if (!s) return;
    const r = await api.serverUpdate(S.gid, { name: s.name, new_name: f.name, host: f.host, api_port: f.port, remark: f.remark });
    S.group = r;
    closeDialog();
    toast("已保存");
  });
}

async function submitAddGroup() {
  await run("create", async () => {
    const r = await api.groupCreate(f.gid);
    S.gid = r.gid;
    await loadGroup();
    closeDialog();
    toast(`已创建 servers_${r.gid}.json`);
  });
}

async function submitToken() {
  await run("token", async () => {
    const s = srv.value;
    if (!s) return;
    const r = await api.serverToken(S.gid, s.name, f.token);
    toast(r.message);
    await loadGroup();
    closeDialog();
  });
}

async function submitClearToken() {
  await run("clear", async () => {
    const s = srv.value;
    if (!s) return;
    const r = await api.serverToken(S.gid, s.name, "");
    toast(r.message);
    await loadGroup();
    closeDialog();
  });
}

async function submitDelServer() {
  await run("del", async () => {
    const s = srv.value;
    if (!s) return;
    const r = await api.serverDelete(S.gid, s.name);
    S.group = r;
    closeDialog();
    toast(`已删除「${s.name}」`);
  });
}

async function submitDelGroup() {
  await run("delgroup", async () => {
    await api.groupDelete(S.gid);
    closeDialog();
    await loadGroup();
    toast("已删除该群配置文件");
  });
}

async function submitBatchAdd() {
  await run("batchadd", async () => {
    const r = await api.serverBatchAdd(S.gid, f.text);
    batchResult.message = r.message;
    batchResult.failed = r.failed;
    S.group = r.group;
  });
}

async function submitBatchDel() {
  await run("batchdel", async () => {
    const r = await api.serverBatchRemove(S.gid, selectedNames.value);
    S.group = r.group;
    closeDialog();
    toast(r.message);
  });
}

async function addIgnore() {
  await run("ignore", async () => {
    const r = await api.ignoreAdd(S.gid, f.qq);
    if (S.group) S.group.nickname_check_ignore_ids = r.nickname_check_ignore_ids;
    f.qq = "";
  });
}

async function removeIgnore(qq: string) {
  await run("ignore", async () => {
    const r = await api.ignoreRemove(S.gid, qq);
    if (S.group) S.group.nickname_check_ignore_ids = r.nickname_check_ignore_ids;
  });
}
</script>

<template>
  <UiModal
    v-if="d"
    :cls="modalCls"
    :title="title"
    :title-mono="JSONS.includes(name)"
    :sub="sub"
    :sub-mono="!NOTE_SUBS.includes(name)"
    :sub-note="NOTE_SUBS.includes(name)"
    :info="footInfo"
  >
    <template v-if="isForm">
      <UiField label="名称" required help="不能含方括号：/执行 用 [服务器名] 定界；最多20个字符">
        <UiInput v-model="f.name" placeholder="一区" />
      </UiField>
      <UiField
        label="主机地址"
        required
        :help="name === 'add-server' ? '一条地址字段；不写端口则按 25565 探测。内网 / 回环地址仅超管可加' : '一条字段；可带 :游戏端口（探测层已按冒号拆分）'"
      >
        <UiInput
          v-model="f.host"
          :placeholder="name === 'add-server' ? 'mc-a1.example.net 或 …net:25565' : undefined"
        />
      </UiField>
      <UiField
        label="模组 API 端口"
        :help="name === 'add-server' ? '填了才会走模组 HTTP；留空时 /执行 与 /广播 不可用' : '清空并保存＝删掉 api_port，退回「未装模组」'"
      >
        <UiInput v-model="f.port" placeholder="留空则视为未装模组" />
      </UiField>
      <UiField label="备注" help="显示在 /在线 卡片服务器名下方；最多50个字符">
        <UiInput v-model="f.remark" placeholder="整合包 A 3.4.0" />
      </UiField>
    </template>

    <template v-else-if="name === 'add-group'">
      <UiField label="QQ 群号" required help="只接受 5-12 位数字。落点：servers_<群号>.json">
        <UiInput v-model="f.gid" placeholder="例如 100000001" />
      </UiField>
    </template>

    <template v-else-if="name === 'token'">
      <UiNotice tone="info" lock>该字段是密钥，只在首次生成时显示，设置后只能重置或者本地文件查看</UiNotice>
      <UiField label="新 Token" required help="需与模组端 qqbind-config.json 的 CONSOLE_TOKEN 一致">
        <UiInput v-model="f.token" class="tok" :rows="2" :error="!!err.token" placeholder="16-128 个不含空格的可见字符" mono />
      </UiField>
      <div v-if="err.token" class="err">{{ err.token }}</div>
      <UiNotice v-if="srv && srv.api_port === null" tone="bad">
        该服未配模组 API 端口，Token 写入后 /执行 仍不可用。
      </UiNotice>
    </template>

    <template v-else-if="CONFIRMS.includes(name)">
      <UiNotice tone="bad">{{ confirmWarn }}</UiNotice>
      <div class="cnote">{{ confirmNote }}</div>
    </template>

    <template v-else-if="name === 'batch-add'">
      <UiInput v-model="f.text" class="btext" :rows="4" mono placeholder="起床服:mc7.example.net:25566" />
      <div class="bhelp">逗号或换行分隔；第三段端口留空则继承全局 default_api_port</div>
      <div class="rlbl">回显与命令输出同格式</div>
      <div v-if="batchResult.message" class="rbox mono">
        <div>{{ batchResult.message }}</div>
        <div v-for="(x, i) in batchResult.failed" :key="i">{{ x }}</div>
      </div>
      <div class="bhelp">逐条校验：名称含方括号、非法/内网 host、端口越界、重名、超上限——各自计入失败列表，其余照常写入。</div>
    </template>

    <template v-else-if="name === 'batch-del'">
      <div class="picks">
        <button
          v-for="s in servers"
          :key="s.name"
          class="pick"
          :class="{ on: selected[s.name] }"
          type="button"
          @click="selected[s.name] = !selected[s.name]"
        >
          <span class="cb"><UiIcon v-if="selected[s.name]" name="check" :size="11" /></span>
          <b>{{ s.name }}</b><span class="mono">{{ s.host }}</span>
        </button>
      </div>
      <UiNotice tone="neutral">
        已选 {{ selectedNames.length }} / {{ servers.length }} · 勾选即选中，未勾选的保持不动；确认后回显与命令输出同格式
      </UiNotice>
    </template>

    <pre v-else-if="JSONS.includes(name)" class="json mono">{{ jsonText.text || "读取中…" }}</pre>

    <template v-else-if="LISTS.includes(name)">
      <div class="addrow">
        <UiInput v-model="f.qq" placeholder="输入 QQ 号" class="grow" />
        <UiButton v-if="name === 'ignore-list'" kind="pri" class="add" @click="addIgnore"><UiIcon name="plus" :size="13" />添加</UiButton>
      </div>
      <div class="rows">
        <div v-for="q in listItems" :key="q" class="li">
          <span class="mono">{{ q }}</span>
          <button v-if="name === 'ignore-list'" class="rm" type="button" @click="removeIgnore(q)">移除</button>
        </div>
        <div v-if="!listItems.length" class="li empty">名单为空 · 添加后即时生效</div>
      </div>
      <UiNotice tone="neutral">{{ listNote }}</UiNotice>
    </template>

    <template #foot>
      <UiButton @click="closeDialog">{{ JSONS.includes(name) || LISTS.includes(name) ? "关闭" : "取消" }}</UiButton>
      <UiButton
        v-if="name === 'token' && srv?.has_token"
        kind="danger-light"
        @click="openDialog('clear-token', { server: srv })"
      >清除</UiButton>
      <UiButton
        v-if="footAction"
        :kind="footKind"
        :disabled="name === 'batch-del' && !selectedNames.length"
        @click="footAction()"
      >
        <UiIcon v-if="footIcon" :name="footIcon" :size="14" />{{ footVerb }}
      </UiButton>
    </template>
  </UiModal>
</template>

<style scoped>
.cnote {
  font-size: 11px;
  line-height: 1.5;
  color: var(--ink3);
}

.err {
  font-size: 12px;
  font-weight: 600;
  color: var(--bad);
}

/* 画布上 Token 输入是 60 高的两行文本域，字号 12 */
.tok {
  min-height: 60px;
  font-size: 12px;
}

/* 批量添加的条目文本域画布定 96 高 */
.btext {
  min-height: 96px;
  font-size: 12px;
}

.bhelp {
  font-size: 11px;
  line-height: 1.5;
  color: var(--ink3);
}

.rlbl {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}

.rbox {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--bad-bd);
  border-radius: var(--r-ctl);
  background: var(--bad-bg);
  color: var(--bad-deep);
  font-size: 11px;
  line-height: 1.5;
}

.picks {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
  overflow-y: auto;
}

.pick {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
  height: 40px;
  padding: 0 6px;
  border: none;
  border-bottom: 1px solid var(--row);
  border-radius: var(--r-ctl);
  background: none;
  color: var(--ink);
  font-family: var(--ff);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.pick b {
  font-weight: 600;
}

.pick .mono {
  font-size: 12px;
  color: var(--ink2);
}

.pick .cb {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 16px;
  height: 16px;
  border: 1px solid var(--ac-line);
  border-radius: 4px;
  background: var(--surface);
  color: #fff;
}

.pick.on {
  background: var(--bad-bg);
}

.pick.on .cb {
  border-color: var(--bad);
  background: var(--bad);
}

.json {
  flex: 1 1 auto;
  min-height: 0;
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--row);
  border-radius: var(--r-ctl);
  background: var(--thead);
  color: var(--ink);
  font-size: 11px;
  line-height: 1.6;
  overflow: auto;
  white-space: pre;
}

.addrow {
  display: flex;
  gap: 8px;
}

.addrow .grow {
  flex: 1 1 auto;
  width: auto;
}

.addrow .add {
  height: 34px;
}

.rows {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
  overflow-y: auto;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
}

.li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 36px;
  padding: 0 12px;
  border-bottom: 1px solid var(--row);
  font-size: 12px;
  color: var(--ink);
}

.li:last-child {
  border-bottom: none;
}

.li.empty {
  justify-content: flex-start;
  color: var(--ink4);
}

.rm {
  padding: 0;
  border: none;
  background: none;
  color: var(--bad);
  font-family: var(--ff);
  font-size: 12px;
  cursor: pointer;
}
</style>
