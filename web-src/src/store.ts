import { computed, reactive, watch } from "vue";
import { api, requireBridge } from "./lib/api";
import { ApiError } from "./lib/bridge";import type {
  ConfigItem,
  ConfigSection,
  ConstRow,
  CommandSection,
  GroupBrief,
  GroupPayload,
  ModState,
  ServerEntry,
} from "./lib/types";

export type PageKey = "srv" | "cfg" | "const" | "map" | "help";

export { api };

export interface Dialog {
  name: string;
  payload?: Record<string, unknown>;
}

interface State {
  ready: boolean;
  noBridge: boolean;
  busy: boolean;
  page: PageKey;
  error: string;
  toast: string;
  pluginVersion: string;
  groups: GroupBrief[];
  gid: string;
  group: GroupPayload | null;
  maxServers: number;
  defaultApiPort: number;
  cfgItems: ConfigItem[];
  cfgSections: ConfigSection[];
  cfgExtraKeys: string[];
  cfgDraft: Record<string, unknown>;
  constRows: ConstRow[];
  cmdSections: CommandSection[];
  cmdTotal: number;
  /** 每台服的「模组登录开关」当前值：只从模组实时读，读不到按 unknown 渲染，不拿默认值猜 */
  modStates: Record<string, ModState>;
  modStateLoading: boolean;
  dialog: Dialog | null;
}

export const S = reactive<State>({
  ready: false,
  noBridge: false,
  busy: false,
  page: "srv",
  error: "",
  toast: "",
  pluginVersion: "",
  groups: [],
  gid: "",
  group: null,
  maxServers: 20,
  defaultApiPort: 25566,
  cfgItems: [],
  cfgSections: [],
  cfgExtraKeys: [],
  cfgDraft: {},
  constRows: [],
  cmdSections: [],
  cmdTotal: 0,
  modStates: {},
  modStateLoading: false,
  dialog: null,
});

export const currentGroup = computed(() => S.group);
export const servers = computed<ServerEntry[]>(() => S.group?.servers ?? []);
export const dirtyCount = computed(() => Object.keys(S.cfgDraft).length);

let toastTimer: number | undefined;

export function toast(text: string) {
  S.toast = text;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    S.toast = "";
  }, 4200);
}

export function fail(err: unknown) {
  const message = err instanceof ApiError ? err.message : String(err);
  S.error = message;
  toast(message);
}

export function openDialog(name: string, payload?: Record<string, unknown>) {
  S.error = "";
  S.dialog = { name, payload };
}

export function closeDialog() {
  S.dialog = null;
}

export async function loadSummary() {
  const r = await api.summary();
  S.groups = r.groups;
  S.maxServers = r.max_servers_per_group;
  S.defaultApiPort = r.default_api_port;
  S.pluginVersion = r.plugin_version;
  if (!S.gid || !r.groups.some((g) => g.gid === S.gid)) {
    S.gid = r.groups[0]?.gid || "";
  }
}

export async function loadGroup() {
  if (!S.gid) {
    S.group = null;
    return;
  }
  S.group = await api.group(S.gid);
}

/* 换群只改 S.gid，重拉交给这里收口，免得每个入口各自记得刷 */
watch(
  () => S.gid,
  () => {
    void loadGroup();
    void loadModState();
  },
);

/** 读各服登录开关的当前值：整群一次并发，失败的那台由调用侧渲染成 unknown */
export async function loadModState() {
  if (!S.gid) {
    S.modStates = {};
    return;
  }
  S.modStateLoading = true;
  try {
    const r = await api.serverModState(S.gid);
    S.modStates = r.states;
  } catch (err) {
    fail(err);
  } finally {
    S.modStateLoading = false;
  }
}

/** 下发登录开关开关：以模组回显的值覆盖本地状态 */
export async function setWhitelist(name: string, enabled: boolean) {
  await run("whitelist", async () => {
    const r = await api.serverWhitelist(S.gid, name, enabled);
    S.modStates = { ...S.modStates, [name]: { reachable: true, enabled: r.enabled } };
    toast(r.message);
  });
}

export async function loadConfig(force = false) {
  if (force) S.cfgItems = [];
  if (!S.cfgItems.length) {
    const r = await api.config();
    S.cfgItems = r.items;
    S.cfgSections = r.sections;
    S.cfgExtraKeys = r.extra_keys;
  }
}

/** 全局参数：草稿逐条写回，密钥走 config/secret，其余走 config/update；写完强制刷新读值。 */
export async function saveDrafts() {
  const keys = Object.keys(S.cfgDraft);
  if (!keys.length) {
    toast("没有待保存的改动");
    return;
  }
  S.busy = true;
  const written: string[] = [];
  let restart = false;
  try {
    for (const key of keys) {
      const item = S.cfgItems.find((it) => it.key === key);
      if (!item) continue;
      const value = S.cfgDraft[key];
      if (item.secret) {
        const r = await api.configSecret(key, String(value ?? ""));
        restart = restart || r.restart_required;
      } else {
        const r = await api.configUpdate(key, value);
        restart = restart || r.item.restart_required;
      }
      written.push(key);
    }
    for (const k of written) delete S.cfgDraft[k];
    await loadConfig(true);
    toast(`已写入 ${written.length} 项${restart ? "；需重载插件才建新连接" : ""}`);
  } catch (err) {
    fail(err);
  } finally {
    S.busy = false;
  }
}

export async function loadConstants() {
  if (!S.constRows.length) {
    S.constRows = (await api.constants()).rows;
  }
}

export async function loadCommands() {
  if (!S.cmdSections.length) {
    const r = await api.commands();
    S.cmdSections = r.sections;
    S.cmdTotal = r.total;
  }
}

/** 统一入口：写操作回来后刷新群数据，避免各弹窗各自维护副本。 */
export async function run<T>(label: string, job: () => Promise<T>, after?: (v: T) => void) {
  S.busy = true;
  S.error = "";
  try {
    const value = await job();
    if (after) after(value);
    return value;
  } catch (err) {
    fail(err);
    return null;
  } finally {
    S.busy = false;
    void label;
  }
}

export async function boot() {
  try {
    requireBridge();
  } catch {
    S.noBridge = true;
    S.ready = true;
    return;
  }
  try {
    await (window as unknown as { AstrBotPluginView?: { ready(): Promise<unknown> } })
      .AstrBotPluginView?.ready();
  } catch {
    /* 桥的 ready 只在面板里 resolve；到这里说明上下文没发过来 */
  }
  try {
    await loadSummary();
    await loadGroup();
  } catch (err) {
    fail(err);
  }
  S.ready = true;
}
