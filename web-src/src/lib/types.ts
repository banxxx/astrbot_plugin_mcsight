/* 与 web/api.py 的返回结构一一对应：改后端字段时这里必须同步，vue-tsc 会挡住漏改。 */

export type Envelope<T> = { status: "ok"; data: T } | { status: "error"; message: string };

export interface ServerEntry {
  name: string;
  host: string;
  api_port: number | null;
  remark: string;
  has_token: boolean;
}

export interface GroupPayload {
  gid: string;
  file: string;
  servers: ServerEntry[];
  mod_count: number;
  token_count: number;
  last_online_enabled: boolean;
  nickname_check_enabled: boolean;
  nickname_check_ignore_ids: string[];
  max_servers_per_group: number;
  default_api_port: number;
}

export interface GroupBrief {
  gid: string;
  server_count: number;
  mod_count: number;
  token_count: number;
}

export interface SummaryPayload {
  groups: GroupBrief[];
  max_servers_per_group: number;
  default_api_port: number;
  plugin_version: string;
  plugin_name: string;
}

export type GroupFlagKey = "last_online_enabled" | "nickname_check_enabled";

export interface FlagPayload {
  key: GroupFlagKey;
  value: boolean;
}

export interface IgnorePayload {
  nickname_check_ignore_ids: string[];
}

export interface ModState {
  reachable: boolean;
  enabled: boolean | null;
  reason?: string;
}

export interface ModStatePayload {
  states: Record<string, ModState>;
}

export interface WhitelistPayload {
  enabled: boolean;
  released: number;
  persisted: boolean | null;
  message: string;
}

export interface TokenPayload {
  has_token: boolean;
  char_count?: number;
  message: string;
}

export interface BatchPayload {
  success: number;
  failed: string[];
  message: string;
  group: GroupPayload;
}

export interface JsonPayload {
  file: string;
  text: string;
}

export type ConfigKind = "bool" | "int" | "list" | "string";

export interface ConfigItem {
  key: string;
  type: ConfigKind;
  section: string;
  desc: string;
  hint: string;
  default: unknown;
  secret: boolean;
  value: unknown;
  has_value: boolean;
  restart_required: boolean;
}

export interface ConfigSection {
  title: string;
  desc: string;
}

export interface ConfigPayload {
  items: ConfigItem[];
  extra_keys: string[];
  sections: ConfigSection[];
}

export interface ConfigUpdatePayload {
  item: ConfigItem;
}

export interface SecretPayload {
  key: string;
  has_value: boolean;
  char_count: number;
  restart_required: boolean;
}

export interface ConstRow {
  title: string;
  name: string;
  value: string;
  unit: string;
  source: string;
}

export interface ConstantsPayload {
  rows: ConstRow[];
}

export interface CommandRow {
  cmd: string;
  desc: string;
  aliases: string;
  tags: string;
}

export interface CommandSection {
  title: string;
  hint: string;
  rows: CommandRow[];
}

export interface CommandsPayload {
  sections: CommandSection[];
  total: number;
}

export interface DeleteGroupPayload {
  gid: string;
}
