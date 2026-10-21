import { call, getBridge, type RawBridge } from "./bridge";
import type {
  BatchPayload,
  CommandsPayload,
  ConfigPayload,
  ConfigUpdatePayload,
  ConstantsPayload,
  DeleteGroupPayload,
  FlagPayload,
  GroupFlagKey,
  GroupPayload,
  IgnorePayload,
  JsonPayload,
  ModStatePayload,
  ReloadPayload,
  SecretPayload,
  SummaryPayload,
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

function get<T>(endpoint: string, params?: Record<string, string | number>) {
  return call<T>(requireBridge().apiGet<T>(`view/${endpoint}`, params));
}

function post<T>(endpoint: string, body?: unknown) {
  return call<T>(requireBridge().apiPost<T>(`view/${endpoint}`, body));
}

export const api = {
  summary: () => get<SummaryPayload>("summary"),
  group: (gid: string) => get<GroupPayload>("group", { gid }),
  groupCreate: (gid: string) => post<GroupPayload>("group/create", { gid }),
  groupDelete: (gid: string) => post<DeleteGroupPayload>("group/delete", { gid }),
  groupFlag: (gid: string, key: GroupFlagKey, value: boolean) =>
    post<FlagPayload>("group/flag", { gid, key, value }),
  ignoreAdd: (gid: string, qq: string) => post<IgnorePayload>("group/ignore_add", { gid, qq }),
  ignoreRemove: (gid: string, qq: string) => post<IgnorePayload>("group/ignore_remove", { gid, qq }),

  serverCreate: (gid: string, f: { name: string; host: string; api_port: string; remark: string; token: string }) =>
    post<GroupPayload>("server/create", { gid, ...f }),
  serverUpdate: (
    gid: string,
    f: { name: string; new_name: string; host: string; api_port: string; remark: string },
  ) => post<GroupPayload>("server/update", { gid, ...f }),
  serverDelete: (gid: string, name: string) => post<GroupPayload>("server/delete", { gid, name }),
  serverMove: (gid: string, name: string, index: number) =>
    post<GroupPayload>("server/move", { gid, name, index }),
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
