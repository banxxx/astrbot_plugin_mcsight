import { call, getBridge, type RawBridge } from "./bridge";
import type {
  ActivationSave,
  ActivationStatus,
  OfficialState,
  BatchPayload,
  BindRule,
  BindRulePayload,
  CommandsPayload,
  ConfigPayload,
  ConfigUpdatePayload,
  ConstantsPayload,
  DeleteGroupPayload,
  FlagPayload,
  GroupFlagKey,
  GroupPayload,
  HidePayload,
  IgnorePayload,
  JsonPayload,
  ModStatePayload,
  MotdPreviewPayload,
  ReloadPayload,
  SecretPayload,
  SetupCodePayload,
  SetupStatusPayload,
  SummaryPayload,
  RemarkSyncPayload,
  WhitelistPayload,
  TokenPayload,
} from "./types";

let bridge: RawBridge | null = null;

export function requireBridge(): RawBridge {
  if (!bridge) {
    bridge = getBridge();
  }
  if (!bridge) {
    throw new Error("本页面必须在 AstrBot WebUI 的插件页面里打开");
  }
  return bridge;
}

/* 面板桥用 postMessage 传参，Vue 的 reactive/ref 代理不能被结构化克隆。
   统一在这里转成普通 JSON 快照，避免各页面漏转后报 “could not be cloned”。 */
function plain<T>(value: T): T {
  if (value === undefined) return value;
  return JSON.parse(JSON.stringify(value)) as T;
}

function get<T>(endpoint: string, params?: Record<string, string | number>) {
  return call<T>(requireBridge().apiGet<T>(`view/${endpoint}`, params === undefined ? undefined : plain(params)));
}

function post<T>(endpoint: string, body?: unknown) {
  return call<T>(requireBridge().apiPost<T>(`view/${endpoint}`, plain(body)));
}

export const api = {
  activation: () => get<ActivationStatus>("activation"),
  activationUpdate: (value: ActivationSave) => post<ActivationStatus>("activation/update", value),
  officialState: (environment_id: string) => get<OfficialState>("official/state", { environment_id }),
  officialModState: (environment_id: string, config_id: string) =>
    get<ModStatePayload>("official/mod_state", { environment_id, config_id }),
  officialWhitelist: (environment_id: string, config_id: string, expected_revision: number,
    server_id: string, enabled: boolean) =>
    post<WhitelistPayload>("official/whitelist", { environment_id, config_id, expected_revision, server_id, enabled }),
  officialMotdPreview: (environment_id: string, host: string, game_port: number, api_port: string) =>
    post<MotdPreviewPayload>("official/motd_preview", { environment_id, host, game_port, api_port }),
  officialRemarkSync: (environment_id: string, config_id: string, expected_revision: number, server_id: string) =>
    post<OfficialState>("official/remark_sync", { environment_id, config_id, expected_revision, server_id }),
  officialRemarkSrc: (environment_id: string, config_id: string, expected_revision: number,
    server_id: string, remark_src: "manual" | "motd") =>
    post<OfficialState>("official/remark_src", { environment_id, config_id, expected_revision, server_id, remark_src }),
  officialSetupCreate: (environment_id: string, alias: string, display_group: string) =>
    post<SetupCodePayload>("official/setup_create", { environment_id, alias, display_group }),
  officialSetupStatus: (environment_id: string, setup_id: string) =>
    get<SetupStatusPayload>("official/setup_status", { environment_id, setup_id }),
  officialSetupCancel: (environment_id: string, setup_id: string) =>
    post<Record<string, never>>("official/setup_cancel", { environment_id, setup_id }),
  officialUpdate: (body: Record<string, unknown>) => post<OfficialState>("official/update", body),
  summary: (platform?: "onebot" | "qq_official") =>
    get<SummaryPayload>("summary", platform ? { platform } : undefined),
  group: (gid: string) => get<GroupPayload>("group", { gid }),
  groupCreate: (gid: string, alias?: string) => post<GroupPayload>("group/create", { gid, alias: alias ?? "" }),
  groupUpdate: (gid: string, alias: string) => post<GroupPayload>("group/update", { gid, alias }),
  groupDelete: (gid: string) => post<DeleteGroupPayload>("group/delete", { gid }),
  groupFlag: (gid: string, key: GroupFlagKey, value: boolean) =>
    post<FlagPayload>("group/flag", { gid, key, value }),
  bindRule: (gid: string, rule: BindRule, template: string) =>
    post<BindRulePayload>("group/bind_rule", { gid, rule, template }),
  ignoreAdd: (gid: string, qq: string) => post<IgnorePayload>("group/ignore_add", { gid, qq }),
  ignoreRemove: (gid: string, qq: string) => post<IgnorePayload>("group/ignore_remove", { gid, qq }),

  serverCreate: (
    gid: string,
    f: {
      name: string;
      host: string;
      api_port: string;
      remark: string;
      remark_src: string;
      remark_motd: string;
      token: string;
    },
  ) => post<GroupPayload>("server/create", { gid, ...f }),
  serverMotdPreview: (host: string, api_port: string) =>
    post<MotdPreviewPayload>("server/motd_preview", { host, api_port }),
  serverUpdate: (
    gid: string,
    f: { name: string; new_name: string; host: string; api_port: string; remark: string; remark_src?: string },
  ) => post<GroupPayload>("server/update", { gid, ...f }),
  serverRemarkSync: (gid: string, name?: string) =>
    post<RemarkSyncPayload>("server/remark_sync", { gid, name: name ?? "" }),
  serverDelete: (gid: string, name: string) => post<GroupPayload>("server/delete", { gid, name }),
  serverMove: (gid: string, name: string, index: number) =>
    post<GroupPayload>("server/move", { gid, name, index }),
  serverHide: (gid: string, name: string, hidden: boolean) =>
    post<HidePayload>("server/hide", { gid, name, hidden }),
  serverToken: (gid: string, name: string, token: string) =>
    post<TokenPayload>("server/token", { gid, name, token }),
  serverWhitelist: (gid: string, name: string, enabled: boolean) =>
    post<WhitelistPayload>("server/whitelist", { gid, name, enabled }),
  serverModState: (gid: string) => get<ModStatePayload>("server/mod_state", { gid }),
  serverBatchAdd: (gid: string, text: string) => post<BatchPayload>("server/batch_add", { gid, text }),
  serverBatchRemove: (gid: string, names: string[]) =>
    post<BatchPayload>("server/batch_remove", { gid, names }),

  jsonGroup: (gid: string) => get<JsonPayload>("json/group", { gid }),
  config: () => get<ConfigPayload>("config"),
  configUpdate: (key: string, value: unknown) => post<ConfigUpdatePayload>("config/update", { key, value }),
  configSecret: (key: string, value: string) => post<SecretPayload>("config/secret", { key, value }),
  configJson: () => get<JsonPayload>("config/json"),
  constants: () => get<ConstantsPayload>("constants"),
  commands: () => get<CommandsPayload>("commands"),
  pluginReload: () => post<ReloadPayload>("plugin/reload", {}),
};
