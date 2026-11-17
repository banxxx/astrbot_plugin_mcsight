<script setup lang="ts">
/* 与 Pen 的表单、名单、确认及 JSON 预览共用同一套弹窗。
   服务器与本群名单即时保存；全局名单只在确认后应用到全局草稿。 */
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import UiBadge from "./UiBadge.vue";
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
  reloadPlugin,
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
const jsonPreview = ref<HTMLElement | null>(null);
const jsonCopied = ref(false);
const jsonCopying = ref(false);
const jsonCopyError = ref("");
let copyTimer: number | undefined;
const busy = ref(false);
const clearTokenConfirm = ref(false);
const listDraft = ref<string[]>([]);
const fieldErrors = reactive<Record<string, string>>({});
/* 添加服务器时的 MOTD 预取：只回显，不落盘；提交时随创建请求一起写入缓存 */
const motdPreview = reactive({ text: "", error: "", loading: false });
let previewSeq = 0;

const FORMS = ["add-server", "edit-server"];
const CONFIRMS = ["del-server", "del-group", "leave-cfg", "reload-plugin"];
const LISTS = ["ignore-list", "list-manage"];
const JSONS = ["json-group", "json-config"];
const modalCls = computed(() => {
  if (clearTokenConfirm.value) return "m-confirm";
  if (name.value === "del-group") return "m-confirm-typed";
  if (CONFIRMS.includes(name.value)) return "m-confirm";
  if (JSONS.includes(name.value) || name.value === "batch-add") return "m-json";
  if (name.value === "batch-del" || LISTS.includes(name.value)) return "m-list";
  return "m-form";
});

const title = computed(() => {
  if (clearTokenConfirm.value) return "清除命令 Token";
  switch (name.value) {
    case "add-server":
      return "添加服务器";
    case "edit-server":
      return "编辑服务器";
    case "add-group":
      return "新增群";
    case "del-server":
      return "删除服务器";
    case "del-group":
      return "删除群配置";
    case "batch-add":
      return "批量添加服务器";
    case "batch-del":
      return "批量删除服务器";
    case "json-group":
    case "json-config":
      return "查看 JSON";
    case "ignore-list":
      return "昵称检测忽略名单";
    case "list-manage": {
      const label = String(payload.value.label ?? "成员");
      return label.endsWith("名单") ? label : `${label}名单`;
    }
    case "leave-cfg":
      return "还有更改未保存";
    case "reload-plugin":
      return "重载插件";
    default:
      return "";
  }
});

const sub = computed(() => {
  if (clearTokenConfirm.value) return srv.value?.name ?? "";
  switch (name.value) {
    case "add-server":
      return `当前群 ${S.gid} / 保存后立即更新本群列表`;
    case "edit-server":
      return `当前群 ${S.gid} / ${srv.value?.name ?? ""}`;
    case "del-server":
      return srv.value?.name ?? "";
    case "del-group":
      return `当前群 ${S.gid}`;
    case "batch-add":
      return "每行一条，或使用逗号分隔；名称与主机之间使用冒号。";
    case "batch-del":
      return "从当前群移除所选服务器条目及其 Token。";
    case "ignore-list":
      return "名单内成员不参与名片校验，添加与移除即时保存。";
    case "list-manage":
      return "本次编辑加入全局草稿，返回全局参数页后统一保存。";
    case "leave-cfg":
      return "离开全局参数页前，请先处理当前草稿。";
    case "add-group":
      return "先建立群配置，再添加这个群的服务器。";
    case "json-group":
      return "当前群配置 / 只读预览";
    case "json-config":
      return "全局配置 / 只读预览";
    case "reload-plugin":
      return "已保存的全局配置将在重载后生效。";
    default:
      return "";
  }
});

const isForm = computed(() => FORMS.includes(name.value));
const listNote = computed(() =>
  name.value === "ignore-list"
    ? `名单内 ${listItems.value.length} 位成员；添加与移除即时保存。`
    : `名单内 ${listItems.value.length} 位成员；修改后需保存全局参数才能生效。`,
);
const confirmWarn = computed(() => {
  if (name.value === "del-server") return `从当前群移除「${srv.value?.name ?? ""}」，其命令 Token 一并删除。`;
  if (name.value === "leave-cfg") {
    return `全局参数还有 ${dirtyCount.value} 项更改未保存。`;
  }
  return "本群的服务器条目、开关与忽略名单将被删除。";
});
const confirmNote = computed(() => {
  if (name.value === "del-server") return "绑定记录不受影响。";
  if (name.value === "leave-cfg") return "保存失败时会保留未保存的草稿，并停留在当前页面。";
  return "本群相关查询与绑定功能将不可用，直到重新创建群配置。已有绑定记录保留。";
});
const confirmVerb = computed(() => (name.value === "del-server" ? "删除服务器" : "删除群配置"));
/* 删除群配置必须手打「确认删除」才放行，防的是弹窗一开随手回车/连点把群配置删掉 */
const delGroupReady = computed(() => (f.confirm ?? "").trim() === "确认删除");
const footerDisabled = computed(
  () =>
    busy.value ||
    (name.value === "batch-del" && !selectedNames.value.length) ||
    (name.value === "del-group" && !delGroupReady.value) ||
    (name.value === "add-server" && servers.value.length >= S.maxServers) ||
    (name.value === "reload-plugin" && dirtyCount.value > 0),
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
    case "reload-plugin":
      return submitReload;
    default:
      return null;
  }
});
const footVerb = computed(() => {
  if (name.value === "reload-plugin") return "重载插件";
  if (CONFIRMS.includes(name.value)) return confirmVerb.value;
  if (name.value === "add-server" || name.value === "batch-add") return "添加服务器";
  if (name.value === "add-group") return "创建群配置";
  if (name.value === "batch-del") return `删除 ${selectedNames.value.length} 台服务器`;
  return "保存服务器";
});
const footKind = computed(() => (name.value === "del-server" || name.value === "del-group" || name.value === "batch-del" ? "danger" : "pri"));
const footIcon = computed(() => name.value === "reload-plugin" ? "refresh" : CONFIRMS.includes(name.value) || name.value === "batch-del" ? "trash" : name.value === "edit-server" ? "check" : "plus");

function requestClose() {
  if (busy.value || leaving.value) return;
  if (clearTokenConfirm.value) clearTokenConfirm.value = false;
  else closeDialog();
}

async function performAction() {
  if (footerDisabled.value || !footAction.value) return;
  busy.value = true;
  try {
    await footAction.value();
  } finally {
    busy.value = false;
  }
}

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
      if (dirtyCount.value === 0) {
        closeDialog();
        S.page = target;
      } else {
        toast("还有新的更改未保存，请再次保存后离开。");
      }
    }
  } finally {
    leaving.value = false;
  }
}
function discardThenLeave() {
  const target = leaveTarget();
  discardDrafts();
  closeDialog();
  S.page = target;
}

watch(
  d,
  (cur) => {
    for (const k of Object.keys(f)) delete f[k];
    for (const k of Object.keys(fieldErrors)) delete fieldErrors[k];
    clearTokenConfirm.value = false;
    listDraft.value = [];
    jsonCopied.value = false;
    jsonCopyError.value = "";
    Object.assign(jsonText, { file: "", text: "", error: "" });
    window.clearTimeout(copyTimer);
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
      if (cur.name === "list-manage") listDraft.value = configList().slice();
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
  if (S.dialog !== cur) return;
  if (r) Object.assign(jsonText, r);
  else jsonText.error = why || "读取失败";
});

function selectJsonText() {
  const el = jsonPreview.value;
  if (!el) return;
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

function copyWithSelection(text: string): boolean {
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const input = document.createElement("textarea");
  input.value = text;
  input.readOnly = true;
  input.setAttribute("aria-hidden", "true");
  Object.assign(input.style, { position: "fixed", top: "0", left: "0", width: "1px", height: "1px", opacity: "0" });
  document.body.appendChild(input);
  try {
    input.focus();
    input.select();
    input.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } finally {
    input.remove();
    previousFocus?.focus();
  }
}

async function copyJson() {
  if (!jsonText.text || jsonCopying.value || jsonText.error) return;
  const dialog = S.dialog;
  // Copy exactly the visible preview; the backend supplies redacted JSON.
  const visibleText = jsonText.text;
  jsonCopying.value = true;
  jsonCopyError.value = "";
  let copied = false;
  try {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(visibleText);
        copied = true;
      }
    } catch {
      // The AstrBot iframe may disallow Clipboard API access.
    }
    if (!copied) copied = copyWithSelection(visibleText);
    if (S.dialog !== dialog) return;
    if (!copied) throw new Error("copy-not-available");
    jsonCopied.value = true;
    window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => { jsonCopied.value = false; }, 2000);
  } catch {
    if (S.dialog !== dialog) return;
    jsonCopyError.value = "复制失败，请选择文本手动复制。";
    selectJsonText();
  } finally {
    jsonCopying.value = false;
  }
}

onBeforeUnmount(() => window.clearTimeout(copyTimer));

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
  const next = listDraft.value.slice();
  for (const id of ids) if (!next.includes(id)) next.push(id);
  listDraft.value = next;
  f.qq = "";
}

function removeConfigList(id: string) {
  listDraft.value = listDraft.value.filter((v) => v !== id);
}

function applyConfigList() {
  commitConfigList(listDraft.value.slice());
  closeDialog();
}

const listItems = computed<string[]>(() => {
  if (name.value === "ignore-list") return S.group?.nickname_check_ignore_ids ?? [];
  return listDraft.value;
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
const allSelected = computed(() => servers.value.length > 0 && selectedNames.value.length === servers.value.length);

function toggleAll() {
  const next = !allSelected.value;
  for (const server of servers.value) selected[server.name] = next;
}

function validateServer(): boolean {
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key];
  const serverName = f.name?.trim() ?? "";
  if (!serverName) fieldErrors.name = "请输入服务器名称。";
  else if (Array.from(serverName).length > 20 || /[\[\]]/.test(serverName)) fieldErrors.name = "名称最多 20 个字符，不能包含方括号。";
  if (!f.host?.trim()) fieldErrors.host = "请输入主机地址。";
  if (f.port?.trim() && (!/^\d+$/.test(f.port.trim()) || Number(f.port) < 1 || Number(f.port) > 65535)) {
    fieldErrors.port = "请输入 1–65535 之间的端口号。";
  }
  if (Array.from(f.remark ?? "").length > 50) fieldErrors.remark = "备注最多 50 个字符。";
  if (f.token && (f.token.length < 16 || f.token.length > 128)) fieldErrors.token = "Token 长度应为 16–128 个字符。";
  return !Object.keys(fieldErrors).length;
}

async function submitAddServer() {
  if (!validateServer()) return;
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
  if (!validateServer()) return;
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

/* 清除需要单独确认；确认后即时保存，并返回原有编辑表单。 */
async function clearToken() {
  if (busy.value) return;
  const s = srv.value;
  if (!s) return;
  busy.value = true;
  try {
    const cleared = await run("clear", async () => {
      const r = await api.serverToken(S.gid, s.name, "");
      await loadGroup();
      toast(r.message);
      return true;
    });
    if (cleared) clearTokenConfirm.value = false;
  } finally {
    busy.value = false;
  }
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
  if (!/^\d{5,12}$/.test(f.gid?.trim() ?? "")) {
    fieldErrors.gid = "请输入 5–12 位数字群号。";
    return;
  }
  delete fieldErrors.gid;
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

async function submitReload() {
  const dialog = S.dialog;
  await reloadPlugin();
  if (S.dialog === dialog) closeDialog();
}
</script>

<template>
  <UiModal
    v-if="d"
    :cls="modalCls"
    :title="title"
    :sub="sub"
    @close="requestClose"
  >
    <template v-if="clearTokenConfirm">
      <p class="dialog-copy">清除后，/执行 对这台服务器不可用。其他服务器不受影响。</p>
      <UiNotice tone="warn">此操作会立即保存，关闭编辑弹窗不会撤销。</UiNotice>
    </template>

    <div v-else-if="isForm" class="server-form">
      <UiField for="server-name" label="服务器名称" required help="最多 20 个字符，不含方括号。" :err="fieldErrors.name">
        <UiInput id="server-name" v-model="f.name" placeholder="请输入服务器名称" :error="!!fieldErrors.name" />
      </UiField>

      <div class="connection-fields">
        <UiField for="server-host" label="主机地址" required help="可包含 Minecraft 游戏端口。" :err="fieldErrors.host">
          <UiInput
            id="server-host"
            v-model="f.host"
            placeholder="survival.example.com:25565"
            :error="!!fieldErrors.host"
            @blur="maybeFetchMotdOnBlur"
          />
        </UiField>
        <UiField for="server-api-port" label="模组 API 端口" help="与游戏端口不同。" :err="fieldErrors.port">
          <UiInput
            id="server-api-port"
            v-model="f.port"
            placeholder="留空不启用"
            inputmode="numeric"
            :error="!!fieldErrors.port"
            @blur="maybeFetchMotdOnBlur"
          />
        </UiField>
      </div>

      <UiField for="server-remark" label="手填备注" help="最多 50 个字符，切换来源不会丢失。" :err="fieldErrors.remark">
        <UiInput id="server-remark" v-model="f.remark" placeholder="请输入服务器备注" :error="!!fieldErrors.remark" />
      </UiField>

      <section class="motd-field" aria-label="备注来源">
        <div class="switch-row">
          <div class="switch-copy">
            <label class="field-label">使用服务端 MOTD</label>
            <p class="field-help">开启后，在线卡片使用服务端介绍作为备注。</p>
          </div>
          <UiSwitch :on="f.remark_src === 'motd'" aria-label="使用服务端 MOTD" @toggle="setRemarkSrc(f.remark_src !== 'motd')" />
        </div>
        <div class="motd-result">
          <div class="motd-copy">
            <p class="motd-value" :class="{ muted: !hasMotdValue || f.remark_src !== 'motd', error: !!motdPreview.error }">{{ motdValueText() }}</p>
            <p class="field-help">{{ motdTimeText() }}</p>
          </div>
          <UiButton v-if="name === 'edit-server'" :disabled="syncing || busy" @click="syncRemarkMotd">
            <UiIcon name="refresh" :size="16" :class="{ spinning: syncing }" />{{ syncing ? "同步中…" : "立即同步" }}
          </UiButton>
          <UiButton v-else :disabled="motdPreview.loading || !f.host.trim() || busy" @click="fetchMotdPreview">
            <UiIcon name="refresh" :size="16" :class="{ spinning: motdPreview.loading }" />{{ motdPreview.loading ? "获取中…" : "获取 MOTD" }}
          </UiButton>
        </div>
      </section>

      <div class="token-field">
        <div class="token-label-row">
          <label class="field-label" for="server-token">命令 Token</label>
          <UiBadge v-if="name === 'edit-server' && srv?.has_token">已设置</UiBadge>
          <button
            v-if="name === 'edit-server' && srv?.has_token"
            class="clear-token"
            type="button"
            :disabled="!!f.token || busy"
            :title="f.token ? '清空本次输入后，可清除已保存的 Token' : '清除现有 Token'"
            @click="clearTokenConfirm = true"
          >清除现有 Token</button>
        </div>
        <UiSecretInput
          id="server-token"
          v-model="f.token"
          :has-value="name === 'edit-server' && !!srv?.has_token"
          :placeholder="name === 'edit-server' ? '留空保留原 Token' : '留空不启用命令执行'"
          :error="!!fieldErrors.token"
        />
        <p class="field-help">16–128 个字符。已保存的值不回显；{{ name === "edit-server" ? "清除操作需单独确认。" : "需与模组的 consoleToken 一致。" }}</p>
        <p v-if="fieldErrors.token" class="field-error" role="alert">{{ fieldErrors.token }}</p>
      </div>

      <UiNotice v-if="name === 'edit-server' && srv && srv.api_port === null" tone="warn">
        该服未配模组 API 端口，Token 写入后 /执行 仍不可用。
      </UiNotice>
      <UiNotice v-if="name === 'add-server' && servers.length >= S.maxServers" tone="warn">
        本群已达到 {{ S.maxServers }} 台服务器上限，请先移除不再使用的条目。
      </UiNotice>
    </div>

    <template v-else-if="name === 'add-group'">
      <UiField for="group-id" label="群号" required help="只创建配置，不会自动加入或发送群消息。" :err="fieldErrors.gid">
        <UiInput id="group-id" v-model="f.gid" inputmode="numeric" placeholder="请输入 5–12 位数字群号" :error="!!fieldErrors.gid" />
      </UiField>
    </template>

    <template v-else-if="name === 'reload-plugin'">
      <p class="dialog-copy">重载会重新建立插件连接。完成后自动刷新页面数据；期间请稍候。</p>
      <UiNotice v-if="dirtyCount" tone="warn">还有 {{ dirtyCount }} 项全局更改未保存，请先保存再重载。</UiNotice>
      <UiNotice v-else tone="ok">当前配置已保存。</UiNotice>
    </template>

    <template v-else-if="CONFIRMS.includes(name)">
      <UiNotice v-if="name !== 'leave-cfg'" tone="warn">{{ confirmWarn }}</UiNotice>
      <p v-else class="dialog-copy">{{ confirmWarn }}</p>
      <p class="dialog-copy">{{ confirmNote }}</p>
      <UiField v-if="name === 'del-group'" for="delete-group-confirm" label="输入「确认删除」继续">
        <UiInput
          id="delete-group-confirm"
          v-model="f.confirm"
          placeholder="确认删除"
          :error="!!f.confirm && !delGroupReady"
        />
      </UiField>
    </template>

    <template v-else-if="name === 'batch-add'">
      <div class="batch-input">
        <label class="field-help" for="batch-server-input">格式：名称:主机[:模组API端口]</label>
        <UiInput id="batch-server-input" v-model="f.text" class="batch-text" :rows="4" mono placeholder="生存主服:survival.example.com:8090&#10;创造世界:creative.example.com:8091" />
      </div>
      <UiNotice v-if="batchResult.message" :tone="batchResult.failed.length ? 'warn' : 'ok'">
        {{ batchResult.message }}
      </UiNotice>
      <div v-if="batchResult.failed.length" class="batch-failures" role="status">
        <div v-for="(message, i) in batchResult.failed" :key="i" class="batch-failure">
          <span class="failure-badge">失败</span>
          <p>{{ message }}</p>
        </div>
      </div>
      <p class="field-help">逐条校验，成功条目会保存。未填写第三段时，模组功能不启用。</p>
    </template>

    <template v-else-if="name === 'batch-del'">
      <div class="pick-summary">
        <button class="pick-all" type="button" role="checkbox" :aria-checked="allSelected" @click="toggleAll">
          <span class="checkbox" :class="{ checked: allSelected }"><UiIcon v-if="allSelected" name="check" :size="13" /></span>
          全选
        </button>
        <span>已选 {{ selectedNames.length }} / {{ servers.length }}</span>
      </div>
      <div class="picks">
        <button
          v-for="s in servers"
          :key="s.name"
          class="pick"
          :class="{ selected: selected[s.name] }"
          type="button"
          role="checkbox"
          :aria-checked="!!selected[s.name]"
          @click="selected[s.name] = !selected[s.name]"
        >
          <span class="checkbox" :class="{ checked: selected[s.name] }"><UiIcon v-if="selected[s.name]" name="check" :size="13" /></span>
          <span class="pick-name">{{ s.name }}</span>
          <span class="pick-state">{{ s.hidden ? "已隐藏" : "显示中" }}</span>
        </button>
        <p v-if="!servers.length" class="empty-list">本群还没有服务器。</p>
      </div>
      <UiNotice tone="warn">绑定记录会保留。此操作保存后无法直接撤销。</UiNotice>
    </template>

    <template v-else-if="JSONS.includes(name)">
      <UiNotice v-if="jsonText.error" tone="bad">读取失败：{{ jsonText.error }}</UiNotice>
      <div v-else class="json-preview">
        <div class="json-toolbar">
          <span class="json-format">JSON</span>
          <button class="json-copy" type="button" :disabled="!jsonText.text || jsonCopying" @click="copyJson">
            <UiIcon :name="jsonCopied ? 'check' : 'copy'" :size="16" />
            <span aria-live="polite">{{ jsonCopied ? "已复制" : "复制" }}</span>
          </button>
        </div>
        <pre ref="jsonPreview" class="json-code" tabindex="0" aria-label="只读 JSON 预览">{{ jsonText.text || "读取中…" }}</pre>
      </div>
      <p v-if="jsonCopyError" class="field-error" role="status">{{ jsonCopyError }}</p>
      <p class="field-help">所有敏感字段均以占位符呈现。</p>
    </template>

    <template v-else-if="LISTS.includes(name)">
      <div class="addrow">
        <UiInput
          id="list-member-input"
          v-model="f.qq"
          :placeholder="name === 'ignore-list' ? '输入 QQ 号' : '输入 QQ 号，逗号可一次加多个'"
          class="grow"
          @enter="addEntry"
        />
        <UiButton :disabled="!f.qq?.trim() || busy" @click="addEntry"><UiIcon name="plus" :size="16" />添加</UiButton>
      </div>
      <div class="member-list" role="list">
        <div v-for="q in listItems" :key="q" class="member-row" role="listitem">
          <UiIcon name="user" :size="16" />
          <span class="member-id mono">{{ q }}</span>
          <button class="remove-member" type="button" :aria-label="'移除 ' + q" title="移除成员" @click="removeEntry(q)">
            <UiIcon name="x" :size="18" />
          </button>
        </div>
        <p v-if="!listItems.length" class="empty-list">名单为空，可在上方添加成员。</p>
      </div>
      <p class="field-help">{{ listNote }}</p>
    </template>

    <UiNotice v-if="S.error && !jsonText.error" tone="bad">{{ S.error }}</UiNotice>

    <template #foot>
      <template v-if="clearTokenConfirm">
        <UiButton :disabled="busy" @click="clearTokenConfirm = false">取消</UiButton>
        <UiButton kind="danger" :disabled="busy" @click="clearToken">
          <UiIcon :name="busy ? 'loader-circle' : 'trash'" :size="16" :class="{ spinning: busy }" />{{ busy ? "清除中…" : "确认清除" }}
        </UiButton>
      </template>
      <template v-else-if="name === 'leave-cfg'">
        <UiButton :disabled="leaving" @click="requestClose">继续编辑</UiButton>
        <UiButton :disabled="leaving" @click="discardThenLeave">放弃更改</UiButton>
        <UiButton kind="pri" :disabled="leaving" @click="saveThenLeave">
          <UiIcon :name="leaving ? 'loader-circle' : 'check'" :size="16" :class="{ spinning: leaving }" />{{ leaving ? "保存中…" : "保存并离开" }}
        </UiButton>
      </template>
      <template v-else-if="name === 'list-manage'">
        <UiButton @click="requestClose">取消</UiButton>
        <UiButton kind="pri" @click="applyConfigList"><UiIcon name="check" :size="16" />应用到草稿</UiButton>
      </template>
      <template v-else-if="name === 'ignore-list'">
        <UiButton kind="pri" @click="requestClose"><UiIcon name="check" :size="16" />完成</UiButton>
      </template>
      <template v-else>
        <UiButton :disabled="busy" @click="requestClose">{{ JSONS.includes(name) || name === "batch-add" ? "关闭" : name === "reload-plugin" ? "稍后重载" : "取消" }}</UiButton>
        <UiButton
          v-if="footAction"
          :kind="footKind"
          :disabled="footerDisabled || (name === 'reload-plugin' && !!dirtyCount)"
          @click="performAction"
        >
          <UiIcon :name="busy ? 'loader-circle' : footIcon" :size="16" :class="{ spinning: busy }" />{{ busy ? "处理中…" : footVerb }}
        </UiButton>
      </template>
    </template>
  </UiModal>
</template>

<style scoped>
.server-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.connection-fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 180px;
  align-items: start;
  gap: 16px;
}
.field-label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
}
.field-help,
.dialog-copy {
  margin: 0;
  color: var(--ink3);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.dialog-copy {
  font-size: 13px;
}
.field-error {
  margin: 0;
  color: var(--bad);
  font-size: 12px;
  line-height: 1.5;
}
.switch-row,
.token-label-row {
  display: flex;
  align-items: center;
  gap: 16px;
}
.switch-copy {
  flex: 1 1 auto;
  min-width: 0;
}
.switch-copy .field-help {
  margin-top: 4px;
}
.motd-field {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.motd-result {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 5px 12px;
  padding: 14px;
  border-radius: 6px;
  background: var(--thead);
}
.motd-copy {
  display: contents;
}
.motd-value {
  grid-column: 1;
  grid-row: 1;
  margin: 0;
  color: var(--ink);
  font-size: 13px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.motd-value.muted {
  color: var(--ink3);
}
.motd-value.error {
  color: var(--bad);
}
.motd-result > .btn {
  grid-column: 2;
  grid-row: 1 / span 2;
}
.motd-copy .field-help {
  grid-column: 1;
  grid-row: 2;
}
.token-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.token-label-row {
  gap: 8px;
}
.clear-token {
  margin-left: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--bad);
  font-family: var(--ff);
  font-size: 12px;
  line-height: 24px;
  cursor: pointer;
}
.clear-token:disabled {
  color: var(--ink4);
  cursor: not-allowed;
}
.batch-input {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.batch-text {
  min-height: 112px;
  padding: 14px;
  font-size: 12px;
}
.batch-failures {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.batch-failure {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border-radius: 6px;
  background: var(--thead);
}
.batch-failure p {
  margin: 0;
  color: var(--ink2);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.failure-badge {
  flex: none;
  padding: 3px 8px;
  border-radius: 12px;
  background: var(--bad-bg);
  color: var(--bad);
  font-size: 12px;
  line-height: 18px;
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
.picks,
.member-list {
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
.addrow {
  display: flex;
  gap: 8px;
}
.addrow .grow {
  flex: 1 1 auto;
  width: auto;
}
.member-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 6px 12px;
  border-radius: 6px;
  background: var(--thead);
  color: var(--ink3);
}
.member-id {
  min-width: 0;
  color: var(--ink);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.remove-member {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 32px;
  height: 32px;
  margin-left: auto;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 8px;
  background: var(--icon-button-bg, #f6f8fc);
  color: var(--ink3);
  cursor: pointer;
}
.remove-member:hover {
  color: var(--bad);
  background: var(--bad-bg);
}
.empty-list {
  margin: 0;
  padding: 18px 12px;
  color: var(--ink3);
  font-size: 12px;
  line-height: 1.5;
}
.clear-token:focus-visible,
.pick-all:focus-visible,
.pick:focus-visible,
.remove-member:focus-visible,
.json-copy:focus-visible,
.json-code:focus-visible {
  outline: 2px solid var(--ac-line);
  outline-offset: 2px;
}
.spinning {
  animation: dialog-spin 1s linear infinite;
}
@keyframes dialog-spin {
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  .spinning { animation: none; }
}
@media (max-width: 760px) {
  .connection-fields {
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
  }
  .motd-result {
    padding: 12px;
    gap: 8px;
  }
  .motd-value {
    grid-column: 1 / -1;
  }
  .motd-result > .btn {
    grid-row: 2;
  }
  .remove-member,
  .json-copy {
    min-width: 44px;
    height: 44px;
  }
  .json-toolbar {
    height: 44px;
  }
  .json-code {
    max-height: none;
  }
  .clear-token {
    min-height: 44px;
  }
}
</style>
