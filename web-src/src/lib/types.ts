/* 与 web/api.py 的返回结构一一对应：改后端字段时这里必须同步，vue-tsc 会挡住漏改。 */

export type Envelope<T> = { status: "ok"; data: T } | { status: "error"; message: string };

export interface ServerEntry {
  name: string;
  host: string;
  api_port: number | null;
  /** 用户手填的备注值，切到 MOTD 也不会被覆盖 */
  remark: string;
  /** 备注取值来源：manual＝用手填的值（默认），motd＝用采集到的 MOTD */
  remark_src: RemarkSrc;
  /** 采集到的 MOTD（已清洗）；空串＝还没采到过 */
  remark_motd: string;
  /** 这份 MOTD 的采集时间（epoch 秒），0＝从没采到 */
  remark_motd_at: number;
  /** 后端算好的最终显示值——/在线 卡片名字下方用的就是它 */
  remark_display: string;
  /** 隐藏＝只从 /在线 撤下去（连查询都不发），条目、绑定、Token 都不动 */
  hidden: boolean;
  has_token: boolean;
}

export type RemarkSrc = "manual" | "motd";

export interface HidePayload {
  hidden: boolean;
  message: string;
  group: GroupPayload;
}

export interface RemarkSyncPayload {
  synced: string[];
  failed: { name: string; reason: string }[];
  message: string;
  group: GroupPayload;
}

export interface MotdPreviewPayload {
  motd: string;
}

export interface GroupPayload {
  gid: string;
  file: string;
  servers: ServerEntry[];
  mod_count: number;
  token_count: number;
  last_online_enabled: boolean;
  nickname_check_enabled: boolean;
  bind_require_nickname: boolean;
  bind_nickname_rule: BindRule;
  bind_nickname_template: string;
  nickname_check_ignore_ids: string[];
  max_servers_per_group: number;
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
  plugin_version: string;
  plugin_name: string;
}

export type GroupFlagKey = "last_online_enabled" | "nickname_check_enabled" | "bind_require_nickname";

export type BindRule = "paren" | "equals" | "contains" | "template";

export interface BindRulePayload {
  rule: BindRule;
  template: string;
}

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
  activation: ActivationStatus;
}

export type ActivationPlatform = "onebot" | "qq_official";

export interface ActivationEnvironment {
  environment_id: string;
  platform: ActivationPlatform;
  application_ref: string;
}

export interface ActivationSelection extends ActivationEnvironment {
  instance_id: string;
  config_revision: number;
}

export interface ActivationChoice {
  platform: ActivationPlatform;
  instance_id: string;
  adapter: string;
  adapter_status: string;
}

export interface ActivationStatus {
  state: string;
  message: string;
  selection: ActivationSelection | null;
  saved_selection: ActivationSelection | null;
  config_revision: number;
  environments: ActivationEnvironment[];
  choices: ActivationChoice[];
  registered: boolean;
  online: "unknown";
  adapter_status: string;
  pending_restart: boolean;
  restart_required: boolean;
}

export interface ActivationSave {
  platform: ActivationPlatform;
  instance_id: string;
  application_ref: string;
  environment_id: string;
  expected_revision: number;
  new_environment: boolean;
  confirm_application: boolean;
}

export interface OfficialCandidate {
  candidate_id: string;
  group_openid: string;
  member_openid: string;
  nickname: string;
  note: string;
  created_at: number;
  expires_at: number;
}

export interface OfficialServer {
  server_id: string;
  name: string;
  host: string;
  game_port: number;
  api_port: number | null;
  hidden: boolean;
  remark: string;
}

export interface OfficialPolicy {
  enable_group_owner: boolean;
  enable_group_admin: boolean;
  bind_require_nickname: boolean;
  bind_nickname_rule: BindRule;
  bind_nickname_template: string;
  show_server_version: boolean;
  show_server_latency: boolean;
  show_last_online: boolean;
  nickname_check_enabled?: boolean;
}

export interface OfficialGroup {
  config_id: string;
  group_openid: string;
  revision: number;
  alias: string;
  display_group: string;
  enabled: boolean;
  servers: OfficialServer[];
  policy: OfficialPolicy;
  administrators: string[];
  blacklist: string[];
  nickname_ignore: string[];
}

export interface OfficialState {
  schema_version: number;
  environment_id: string;
  revision: number;
  groups: OfficialGroup[];
  candidates: OfficialCandidate[];
  environment_administrators: string[];
  authorization_history_count: number;
  has_mod_token: boolean;
  active: boolean;
  binding_ready: boolean | null;
  binding_readiness?: string;
  message_delivery: { ready: boolean; receipts_count: number | null; capacity: number; message: string };
}

export type OfficialOperationState = "PREPARED" | "RESERVED" | "COMMITTED" | "REJECTED" | "ABORTED";
export interface OfficialOperationScope { environment_id: string; config_id: string; expected_revision: number; server_id?: string; }
export interface OfficialOperationSummary {
  operation_id: string; kind: string; state: OfficialOperationState; result_code: string | null;
  policy_revision: string; created_at: string; updated_at: string;
}
export interface OfficialOperationPage {
  operations: OfficialOperationSummary[]; next_cursor: string | null; total: number; truncated: boolean;
}
export interface OfficialOperationDetail {
  operation_id: string; kind: string; state: OfficialOperationState; result_code: string | null;
  payload_hash: string; policy_revision: string; pending: boolean; token_state: string | null;
  effects: { game_id: string; game_key: string; state: string; revision: string }[];
  delivery: { server_id: string; name: string; mirror: string; whitelist: string; online: string }[];
}
export interface OfficialOperationAction {
  code: string; operation: OfficialOperationDetail;
  recovery: { token_confirmed: number; delivered: number; failed: number; budget_exhausted: boolean } | null;
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

export interface ReloadPayload {
  reloading: boolean;
  message: string;
}
