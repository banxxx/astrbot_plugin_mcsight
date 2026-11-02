<script setup lang="ts">
/* 弹窗按画布的原型实现：确认 / 表单（含命令 Token）/ 名单管理 / 宽表 JSON。
   规矩：遮罩与 Esc 都不关闭；删除类必须二次确认；弹窗内的「移除」不再叠确认；密钥的清除即时生效。
   注意：具名插槽必须是组件的直接子节点，所以页脚统一放在一个 #foot 里按弹窗分支。 */
import { computed, reactive, ref, watch } from "vue";
import UiButton from "./UiButton.vue";
import UiField from "./UiField.vue";
import UiIcon from "./UiIcon.vue";
import UiInput from "./UiInput.vue";
import UiModal from "./UiModal.vue";
import UiNotice from "./UiNotice.vue";
import UiSecretInput from "./UiSecretInput.vue";
import UiSwitch from "./UiSwitch.vue";
import {
  S,
  api,
  closeDialog,
  dirtyCount,
  discardDrafts,
  loadGroup,
  loadSummary,
  run,
  saveDrafts,
  servers,
  toast,
  type PageKey,
} from "../store";
import { ApiError } from "../lib/bridge";
import type { ConfigItem, ServerEntry } from "../lib/types";

const d = computed(() => S.dialog);
const payload = computed(() => (d.value?.payload || {}) as Record<string, unknown>);
/* 弹窗打开时锁定服名，之后一律从列表里取当前行：清除 Token / 改名 / 移动刷新后标签才跟得上真值 */
const srvKey = ref("");
const srv = computed(() => {
  const snap = payload.value.server as ServerEntry | undefined;
  if (!snap) return undefined;
  return servers.value.find((s) => s.name === srvKey.value) ?? snap;
});
const name = computed(() => d.value?.name || "");

const f = reactive<Record<string, string>>({});
const selected = reactive<Record<string, boolean>>({});
const batchResult = reactive({ message: "", failed: [] as string[] });
const jsonText = reactive({ file: "", text: "", error: "" });
/* 添加服务器时的 MOTD 预取：只回显，不落盘；提交时随创建请求一起写入缓存 */
const motdPreview = reactive({ text: "", error: "", loading: false });
let previewSeq = 0;

const FORMS = ["add-server", "edit-server"];
const CONFIRMS = ["del-server", "del-group", "leave-cfg"];
const LISTS = ["ignore-list", "list-manage"];
const JSONS = ["json-group", "json-config"];
/* 画布上「等价命令 / 文件操作」副标题是等宽主色，说明行是正体灰；只有 JSON 弹窗标题用等宽 */
const NOTE_SUBS = ["add-group", "json-group", "json-config"];

const modalCls = computed(() => {
  /* 删除群配置要多一个「输入确认词」的输入框，桌面固定高度得比普通确认弹窗高一档 */
  if (name.value === "del-group") return "m-confirm-typed";
  if (CONFIRMS.includes(name.value)) return "m-confirm";
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
      return jsonText.file || "插件配置";
    case "ignore-list":
      return `忽略名单 · ${S.gid}`;
    case "list-manage":
      return `${String(payload.value.label ?? "")}名单`;
    case "leave-cfg":
      return "有未保存的更改";
    default:
      return "";
  }
});

const sub = computed(() => {
  switch (name.value) {
    case "add-server":
      return "等价命令 /mc add <名称> <地址> [端口] · /mc token 名称 <口令>";
    case "edit-server":
      return "等价命令 /mc edit 名称 name|host|port|remark · /mc token 名称 <口令>|清除";
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
      return `写 _conf_schema.json 的 ${(payload.value.item as ConfigItem | undefined)?.key ?? ""} · 增删只改草稿`;
    case "leave-cfg":
      return `全局参数 · ${dirtyCount.value} 项待保存`;
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
    : `名单内 ${listItems.value.length} 个 · 增删只改草稿，回页面点「保存更改」才落盘`,
);
const confirmWarn = computed(() => {
  if (name.value === "del-server") return `将从 servers_${S.gid}.json 移除「${srv.value?.name ?? ""}」，其命令 Token 一并删除。`;
  if (name.value === "leave-cfg") {
    return `「全局参数」里还有 ${dirtyCount.value} 项改动没有保存，切走之后这些改动会丢掉。`;
  }
  return `该群还有 ${servers.value.length} 台服务器条目，删除后 /在线 /绑定 /查询 在这个群全部失效，且不会自动恢复。`;
});
const confirmNote = computed(() => {
  if (name.value === "del-server") return "绑定记录不受影响。";
  if (name.value === "leave-cfg") return "可以先「保存并离开」，或回去点右上角的「保存更改」。";
  return "本群开关与忽略名单一并随文件删除。绑定记录不在此文件内，不受影响。";
});
const confirmVerb = computed(() => (name.value === "del-server" ? "确认删除" : "确认删除文件"));
/* 删除群配置必须手打「确认删除」才放行，防的是弹窗一开随手回车/连点把群配置删掉 */
const delGroupReady = computed(() => (f.confirm ?? "").trim() === "确认删除");
const footerDisabled = computed(
  () =>
    (name.value === "batch-del" && !selectedNames.value.length) ||
    (name.value === "del-group" && !delGroupReady.value),
);
const footAction = computed(() => {
  switch (name.value) {
    case "add-server":
      return submitAddServer;
    case "edit-server":
      return submitEditServer;
    case "add-group":
      return submitAddGroup;
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
const footIcon = computed(() => (CONFIRMS.includes(name.value) || name.value === "batch-del" ? null : name.value === "edit-server" ? "check" : "plus"));

/* 切页确认：只有保存成功才真的跳走；保存失败就留在弹窗里，草稿还在 */
const leaving = ref(false);
function leaveTarget(): PageKey {
  return String(payload.value.to ?? "srv") as PageKey;
}
async function saveThenLeave() {
  if (leaving.value) return;
  /* 保存是逐项 await 的：先把目标页和本次弹窗记下来。期间用户点 X 关掉、
     或打开了别的弹窗时，保存完成后不能替他跳页/关掉新弹窗。 */
  const target = leaveTarget();
  const dlg = S.dialog;
  leaving.value = true;
  try {
    if (await saveDrafts() && S.dialog === dlg) {
      closeDialog();
      S.page = target;
    }
  } finally {
    leaving.value = false;
  }
}
function discardThenLeave() {
  discardDrafts();
  closeDialog();
  S.page = leaveTarget();
}

watch(
  d,
  (cur) => {
    for (const k of Object.keys(f)) delete f[k];
    batchResult.message = "";
    batchResult.failed = [];
    previewSeq += 1;
    motdPreview.text = "";
    motdPreview.error = "";
    motdPreview.loading = false;
    if (!cur) return;
    srvKey.value = (cur.payload?.server as ServerEntry | undefined)?.name ?? "";
    if (cur.name === "edit-server") {
      const s = cur.payload?.server as ServerEntry;
      f.name = s.name;
      f.host = s.host;
      f.port = s.api_port == null ? "" : String(s.api_port);
      f.remark = s.remark;
      f.remark_src = s.remark_src;
      f.token = "";
    } else if (cur.name === "add-server") {
      f.name = "";
      f.host = "";
      f.port = "";
      f.remark = "";
      f.remark_src = "manual";
      f.token = "";
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
  if (cur.name !== "json-group" && cur.name !== "json-config") return;
  /* 打开即清空：读失败时绝不留着上一份文件的内容，那会让群 A 的文件被当成群 B 的看 */
  Object.assign(jsonText, { file: "", text: "", error: "" });
  let why = "";
  const r = await run("json", async () => {
    try {
      return cur.name === "json-group" ? await api.jsonGroup(S.gid) : await api.configJson();
    } catch (err) {
      why = err instanceof ApiError ? err.message : String(err);
      throw err;
    }
  });
  if (r) Object.assign(jsonText, r);
  else jsonText.error = why || "读取失败";
});

/* 地址/端口一改，之前取到的 MOTD 就作废：别让提交时把旧地址的值写进新服务器 */
watch(
  () => [f.host, f.port],
  () => {
    if (name.value !== "add-server") return;
    previewSeq += 1;
    motdPreview.text = "";
    motdPreview.error = "";
    motdPreview.loading = false;
  },
);

/* 全局参数的名单（超管 / 白名单管理员 / 黑名单）：改的是草稿，回页面点「保存更改」才落盘。
   原先这里只给群忽略名单做了增删，配置类名单只能看不能改——配超管却加不进去。 */
function configListItem(): ConfigItem | undefined {
  return name.value === "list-manage" ? (payload.value.item as ConfigItem | undefined) : undefined;
}

/** 名单条目的值可能是数组，也可能是手写配置里的「111,222」逗号串：统一摊平 */
function asRows(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter(Boolean);
  if (typeof v === "string") {
    return v.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean);
  }
  return [];
}

function configList(): string[] {
  const it = configListItem();
  if (!it) return [];
  const key = it.key;
  return key in S.cfgDraft ? asRows(S.cfgDraft[key]) : asRows(it.value);
}

function commitConfigList(next: string[]) {
  const it = configListItem();
  if (!it) return;
  const orig = asRows(it.value);
  // 改回原样就从草稿里删掉，免得「保存更改」一直亮着却没有任何变化
  const same = next.length === orig.length && next.every((v, i) => v === orig[i]);
  if (same) delete S.cfgDraft[it.key];
  else S.cfgDraft[it.key] = next;
}

function addConfigList() {
  const ids = f.qq.split(/[,，\s]+/).map((v) => v.trim()).filter(Boolean);
  if (!ids.length) return;
  const next = configList().slice();
  for (const id of ids) if (!next.includes(id)) next.push(id);
  commitConfigList(next);
  f.qq = "";
}

function removeConfigList(id: string) {
  commitConfigList(configList().filter((v) => v !== id));
}

const listItems = computed<string[]>(() => {
  if (name.value === "ignore-list") return S.group?.nickname_check_ignore_ids ?? [];
  return configList();
});

function addEntry() {
  if (name.value === "ignore-list") void addIgnore();
  else addConfigList();
}

function removeEntry(id: string) {
  if (name.value === "ignore-list") void removeIgnore(id);
  else removeConfigList(id);
}

const selectedNames = computed(() => servers.value.filter((s) => selected[s.name]).map((s) => s.name));

async function submitAddServer() {
  await run("add", async () => {
    const previewMotd = f.remark_src === "motd" ? motdPreview.text : "";
    const r = await api.serverCreate(S.gid, {
      name: f.name,
      host: f.host,
      api_port: f.port,
      remark: f.remark,
      remark_src: f.remark_src,
      remark_motd: previewMotd,
      token: f.token,
    });
    S.group = r;
    /* 添加时选了「用 MOTD」：
       - 弹窗里已经「获取」过：创建请求已把值写进缓存，不再重复查一次；
       - 没获取过：提交成功后补采一次，失败不回滚服务器，只把原因带进提示。 */
    let motdMsg = "";
    if (f.remark_src === "motd") {
      if (previewMotd) {
        motdMsg = "；已写入获取到的 MOTD";
      } else {
        try {
          const sync = await api.serverRemarkSync(S.gid, f.name.trim());
          S.group = sync.group;
          motdMsg = `；${sync.message}`;
        } catch (err) {
          motdMsg = `；MOTD 同步失败：${err instanceof ApiError ? err.message : String(err)}`;
        }
      }
    }
    closeDialog();
    toast(`已添加「${f.name}」${f.token ? "，命令 Token 已写入" : ""}${motdMsg}`);
  });
}

async function submitEditServer() {
  await run("edit", async () => {
    const s = srv.value;
    if (!s) return;
    const finalName = f.name || s.name;
    const r = await api.serverUpdate(S.gid, {
      name: s.name,
      new_name: finalName,
      host: f.host,
      api_port: f.port,
      remark: f.remark,
      remark_src: f.remark_src,
    });
    S.group = r;
    srvKey.value = finalName;
    let tokenMsg = "";
    if (f.token) {
      const t = await api.serverToken(S.gid, finalName, f.token);
      tokenMsg = `；${t.message}`;
      await loadGroup();
    }
    closeDialog();
    toast(`已保存「${finalName}」${tokenMsg}`);
  });
}

/* 行内垃圾桶：即时清掉该服存量口令，不叠二次确认；弹窗留在原地，口令行自己退回「未设置」 */
async function clearToken() {
  const s = srv.value;
  if (!s) return;
  await run("clear", async () => {
    const r = await api.serverToken(S.gid, s.name, "");
    await loadGroup();
    toast(r.message);
  });
}

/* 备注的 MOTD 值只走「立即同步」这条显式动作：它只写 remark_motd 缓存、不动手填值，
   所以用户随后点取消，多出来的也只是那份缓存，不会改到他的意图。 */
const syncing = ref(false);

async function syncRemarkMotd() {
  const s = srv.value;
  if (!s || syncing.value) return;
  syncing.value = true;
  try {
    await run("sync", async () => {
      const r = await api.serverRemarkSync(S.gid, s.name);
      S.group = r.group;
      toast(r.message);
    });
  } finally {
    syncing.value = false;
  }
}

/* 添加弹窗里的预取：按当前表单的 host/模组端口先查一次 MOTD。
   只要地址填对就能拿到值（没配 api_port 走 SLP，配了先问模组再回落 SLP）。 */
async function fetchMotdPreview() {
  const host = f.host.trim();
  if (!host) {
    motdPreview.text = "";
    motdPreview.error = "先填主机地址";
    return;
  }
  const seq = ++previewSeq;
  motdPreview.loading = true;
  motdPreview.error = "";
  try {
    const r = await api.serverMotdPreview(host, f.port);
    if (seq !== previewSeq) return;
    motdPreview.text = r.motd;
  } catch (err) {
    if (seq !== previewSeq) return;
    motdPreview.text = "";
    motdPreview.error = err instanceof ApiError ? err.message : String(err);
  } finally {
    if (seq === previewSeq) motdPreview.loading = false;
  }
}

/* 开关已经打开、地址也填完了：失焦就自动取一次，不用再特意点按钮。
   只在这种明确意图下自动请求，避免用户只是路过地址框就发网络查询。 */
function maybeFetchMotdOnBlur() {
  if (name.value !== "add-server" || f.remark_src !== "motd") return;
  if (!f.host.trim() || motdPreview.text || motdPreview.loading) return;
  void fetchMotdPreview();
}

function setRemarkSrc(value: boolean) {
  f.remark_src = value ? "motd" : "manual";
  // 编辑时刚选上 MOTD 但还没采到过值：当场替他取一次，省得对着空备注猜下一步。
  if (value && name.value === "edit-server" && !srv.value?.remark_motd) void syncRemarkMotd();
  // 添加时地址已经填好：打开开关就顺手取一次，让用户提交前能看到真实 MOTD。
  if (value && name.value === "add-server" && f.host.trim() && !motdPreview.text && !motdPreview.loading) {
    void fetchMotdPreview();
  }
}

/* 真正取到 MOTD 时才用主题色突出；提示语、获取中、失败原因仍走灰阶 */
const hasMotdValue = computed(() => {
  if (name.value === "add-server") return !motdPreview.loading && !motdPreview.error && !!motdPreview.text;
  return !!srv.value?.remark_motd;
});

function motdTimeText(): string {
  if (name.value === "add-server") {
    if (motdPreview.loading) return "正在获取…";
    if (motdPreview.error) return "获取失败";
    if (motdPreview.text) return "已获取，添加时写入缓存";
    return "填好地址后可先获取";
  }
  const at = Number(srv.value?.remark_motd_at || 0);
  if (!at) return "还没采到过值";
  const d = new Date(at * 1000);
  const two = (n: number) => String(n).padStart(2, "0");
  return `采集于 ${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/* 采到的值要在弹窗里当场看得见：只显示时间等于让用户信不过那次同步的结果 */
function motdValueText(): string {
  if (name.value === "add-server") {
    if (motdPreview.loading) return "正在向服务器查询 MOTD…";
    if (motdPreview.error) return `获取失败：${motdPreview.error}`;
    return motdPreview.text || "填好主机地址后点「获取 MOTD」，或提交后自动采集";
  }
  return srv.value?.remark_motd || "还没有值：点「立即同步」或在群里跑一次 /在线";
}

async function submitAddGroup() {
  await run("create", async () => {
    const r = await api.groupCreate(f.gid);
    /* 群列表来自 summary：不重读的话「当前群」下拉里看不到刚建的群，
       要等下次刷新页面才出现。先刷新列表，再把选中项落到新群。 */
    await loadSummary();
    S.gid = r.gid;
    S.group = r;  // 立即切到新群，别让面板停在旧群等请求回来
    closeDialog();
    toast(`已创建 servers_${r.gid}.json`);
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
    const gone = S.gid;
    await api.groupDelete(gone);
    /* 先把面板从已删除的群上摘下来：loadSummary 会把 S.gid 换成剩下的第一个群
       （没有了就置空），watcher 再按新群拉数据。原先这里还对着已删的群号调
       loadGroup，只会拿回 404，列表里那一条也一直留着。 */
    S.group = null;
    S.modStates = {};
    closeDialog();
    await loadSummary();
    toast(`已删除 servers_${gone}.json`);
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
    @close="closeDialog"
  >
    <template v-if="isForm">
      <UiField label="名称" required help="不能含方括号：/执行 用 [服务器名] 定界；最多20个字符">
        <UiInput v-model="f.name" placeholder="一区" />
      </UiField>
      <UiField
        label="主机地址"
        required
        :help="name === 'add-server' ? '一条地址字段；只写域名会自动跟随 SRV 记录，不写端口时回落 25565。冒号前后的空格和全角「：」会自动忽略；内网 / 回环地址仅超管可加' : '一条字段；可带 :游戏端口（写了端口就不再查 SRV）。冒号前后的空格和全角「：」会自动忽略'"
      >
        <UiInput
          v-model="f.host"
          :placeholder="name === 'add-server' ? 'mc-a1.example.net（推荐）或 play.example.net:25501' : undefined"
          @blur="maybeFetchMotdOnBlur"
        />
      </UiField>
      <UiField
        label="模组 API 端口"
        :help="name === 'add-server' ? '这是模组 API 端口，不是游戏端口；填了才会走模组 HTTP，留空时 /执行 与 /广播 不可用' : '这是模组 API 端口；清空并保存＝删掉 api_port，退回「未装模组」'"
      >
        <UiInput v-model="f.port" placeholder="留空则视为未装模组" @blur="maybeFetchMotdOnBlur" />
      </UiField>
      <UiField label="备注" help="手填的值；显示在 /在线 卡片服务器名下方；最多50个字符">
        <UiInput v-model="f.remark" placeholder="整合包 A 3.4.0" />
      </UiField>
      <UiField label="备注用 MOTD" help="卡片改显示服务端 motd，手填的值仍留着">
        <div class="rk">
          <UiSwitch :on="f.remark_src === 'motd'" @toggle="setRemarkSrc(f.remark_src !== 'motd')" />
          <UiButton v-if="name === 'edit-server'" kind="ghost" :disabled="syncing" @click="syncRemarkMotd">立即同步</UiButton>
          <UiButton
            v-else
            kind="ghost"
            :disabled="motdPreview.loading || !f.host.trim()"
            @click="fetchMotdPreview"
          >
            <UiIcon v-if="!motdPreview.loading" name="refresh" :size="13" />{{ motdPreview.loading ? "获取中…" : "获取 MOTD" }}
          </UiButton>
          <span class="rk-t">{{ motdTimeText() }}</span>
        </div>
        <div
          class="rk-v"
          :class="{ off: f.remark_src !== 'motd', wrap: name === 'add-server', motd: hasMotdValue }"
        >{{ motdValueText() }}</div>
      </UiField>

      <UiField
        label="命令 Token"
        :help="
          name === 'add-server'
            ? '该服 /执行 的口令，与模组端 consoleToken 逐字一致；留空＝不启用'
            : '该服 /执行 的口令，与模组端 consoleToken 逐字一致；留空＝不改动'
        "
      >
        <UiSecretInput
          v-model="f.token"
          :has-value="name === 'edit-server' && !!srv?.has_token"
          :placeholder="name === 'edit-server' && srv?.has_token ? '留空则保持不变' : undefined"
          :clearable="name === 'edit-server'"
          clear-title="清除该服命令 Token"
          @clear="clearToken"
        />
      </UiField>

      <UiNotice v-if="name === 'edit-server' && srv && srv.api_port === null" tone="bad">
        该服未配模组 API 端口，Token 写入后 /执行 仍不可用。
      </UiNotice>
    </template>

    <template v-else-if="name === 'add-group'">
      <UiField label="QQ 群号" required help="只接受 5-12 位数字。落点：servers_<群号>.json">
        <UiInput v-model="f.gid" placeholder="例如 100000001" />
      </UiField>
    </template>

    <template v-else-if="CONFIRMS.includes(name)">
      <UiNotice tone="bad">{{ confirmWarn }}</UiNotice>
      <div class="cnote">{{ confirmNote }}</div>
      <UiField
        v-if="name === 'del-group'"
        label="输入「确认删除」以继续"
        help="内容必须与上面四个字完全一致，下方删除按钮才会可用"
      >
        <UiInput
          v-model="f.confirm"
          placeholder="确认删除"
          :error="!!f.confirm && !delGroupReady"
        />
      </UiField>
    </template>

    <template v-else-if="name === 'batch-add'">
      <UiInput v-model="f.text" class="btext" :rows="4" mono placeholder="起床服:mc7.example.net:25566" />
      <div class="bhelp">逗号或换行分隔；第三段不写（不要留空冒号）则该服视为未装模组（/执行、/广播 不可用）</div>
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

    <template v-else-if="JSONS.includes(name)">
      <UiNotice v-if="jsonText.error" tone="bad">读取失败：{{ jsonText.error }}</UiNotice>
      <pre v-else class="json mono">{{ jsonText.text || "读取中…" }}</pre>
    </template>

    <template v-else-if="LISTS.includes(name)">
      <div class="addrow">
        <UiInput
          v-model="f.qq"
          :placeholder="name === 'ignore-list' ? '输入 QQ 号' : '输入 QQ 号，逗号可一次加多个'"
          class="grow"
          @enter="addEntry"
        />
        <UiButton kind="pri" class="add" @click="addEntry"><UiIcon name="plus" :size="13" />添加</UiButton>
      </div>
      <div class="rows">
        <div v-for="q in listItems" :key="q" class="li">
          <span class="mono">{{ q }}</span>
          <button class="rm" type="button" @click="removeEntry(q)">移除</button>
        </div>
        <div v-if="!listItems.length" class="li empty">
          {{ name === "ignore-list" ? "名单为空 · 添加后即时生效" : "名单为空 · 添加后回页面点「保存更改」" }}
        </div>
      </div>
      <UiNotice tone="neutral">{{ listNote }}</UiNotice>
    </template>

    <template #foot>
      <template v-if="name === 'leave-cfg'">
        <UiButton :disabled="leaving" @click="closeDialog">继续编辑</UiButton>
        <UiButton kind="danger-light" :disabled="leaving" @click="discardThenLeave">放弃更改</UiButton>
        <UiButton kind="pri" :disabled="leaving" @click="saveThenLeave">
          <UiIcon v-if="!leaving" name="check" :size="14" />{{ leaving ? "保存中…" : "保存并离开" }}
        </UiButton>
      </template>
      <template v-else>
        <UiButton @click="closeDialog">{{ JSONS.includes(name) || LISTS.includes(name) ? "关闭" : "取消" }}</UiButton>
        <UiButton
          v-if="footAction"
          :kind="footKind"
          :disabled="footerDisabled"
          @click="footAction()"
        >
          <UiIcon v-if="footIcon" :name="footIcon" :size="14" />{{ footVerb }}
        </UiButton>
      </template>
    </template>
  </UiModal>
</template>

<style scoped>
.cnote {
  font-size: 11px;
  line-height: 1.5;
  color: var(--ink3);
}

/* 备注来源这一行：开关 + 同步 + 采集时间共用一行，别把它拆成两个字段撑破弹窗定高 */
.rk {
  display: flex;
  align-items: center;
  gap: 10px;
}

.rk-t {
  font-size: 11px;
  color: var(--ink3);
}

/* 采到的值单独一行；开关关着＝这份值当前不上卡片，用灰阶和生效态区分 */
.rk-v {
  margin-top: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  line-height: 18px;
  color: var(--ink);
  white-space: nowrap;
}

/* 添加弹窗里的预取值/失败原因可能比一行长，允许换行；编辑弹窗保持单行省略 */
.rk-v.wrap {
  overflow: visible;
  white-space: normal;
  overflow-wrap: anywhere;
}

.rk-v.off {
  color: var(--ink4);
}

/* 真正取到的 MOTD 用主题蓝突出；提示语、获取中、失败原因仍走灰阶。
   开关没开时退回淡灰蓝：能看出已经取到，但不会和开启态抢注意力。 */
.rk-v.motd {
  color: var(--ac);
  font-weight: 500;
}

.rk-v.motd.off {
  color: var(--motd-idle);
}

.err {
  font-size: 12px;
  font-weight: 600;
  color: var(--bad);
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
