/* 与 web/api.py 的返回结构一一对应：改后端字段时这里必须同步，vue-tsc 会挡住漏改。 */

export type Envelope<T> = { status: "ok"; data: T } | { status: "error"; message: string };

/** 外观主题：经典＝原来的侧栏/表格外观，奶油＝画布里的顶栏/卡片外观。 */
export type UiTheme = "classic" | "cream";

export interface UiPrefs {
  theme: UiTheme;
}

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
  /** MOTD 备注当前生效时的彩色分段；手填/无颜色时为 null */
  remark_runs: RemarkRun[] | null;
  /** 隐藏＝只从 /在线 撤下去（连查询都不发），条目、绑定、Token 都不动 */
  hidden: boolean;
  has_token: boolean;
}

export type RemarkSrc = "manual" | "motd";

/** MOTD 彩色分段：[[文本, 安全色 hex|null], ...]；null 表示默认色 */
export type RemarkRun = [string, string | null];

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
  /** 彩色分段（可能为空数组）；预览只读，也用它显示颜色 */
  runs?: RemarkRun[];
}

/** OneBot 与 QQ 官方共用的群策略；字段名必须与 utils/group_policy.py 一致。 */
export interface GroupPolicy {
  enable_group_owner: boolean;
  enable_group_admin: boolean;
  bind_require_nickname: boolean;
  bind_nickname_rule: BindRule;
  bind_nickname_template: string;
  show_server_version: boolean;
  show_server_latency: boolean;
  show_last_online: boolean;
  nickname_check_enabled: boolean;
  use_central_db: boolean;
}

export interface GroupPayload {
  gid: string;
  file: string;
  /** OneBot 正式库位置，例如 onebot/config.sqlite3 */
  storage?: string;
  /** 群别名（选填）；未设置时为空串 */
  alias: string;
  servers: ServerEntry[];
  mod_count: number;
  token_count: number;
  enabled: boolean;
  policy: GroupPolicy;
  nickname_ignore: string[];
  max_servers_per_group: number;
}

export interface GroupBrief {
  gid: string;
  alias: string;
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

export type GroupPolicyBoolKey =
  | "nickname_check_enabled"
  | "bind_require_nickname"
  | "enable_group_owner"
  | "enable_group_admin"
  | "show_server_version"
  | "show_server_latency"
  | "show_last_online"
  | "use_central_db";

export type GroupFlagKey = "enabled" | GroupPolicyBoolKey;

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
  nickname_ignore: string[];
}

export interface ModState {
  reachable: boolean;
  enabled: boolean | null;
  /** 新模组在官方模式提供 V2 在线写入口；旧模组缺该字段时保持只读。 */
  writable?: boolean;
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

/** OneBot 可编辑备份；不包含命令 Token。 */
export interface OneBotBackupServer {
  server_id?: string;
  name: string;
  host: string;
  api_port: number | null;
  hidden: boolean;
  remark: string;
  remark_src: RemarkSrc;
  remark_motd: string;
  remark_motd_at: number;
  remark_motd_runs?: RemarkRun[] | null;
}

export interface OneBotBackupGroup {
  scope_kind: "group" | "private";
  scope_id: string;
  enabled: boolean;
  alias: string;
  policy: GroupPolicy;
  nickname_ignore: string[];
  servers: OneBotBackupServer[];
}

export interface OneBotBackup {
  format: string;
  format_version: number;
  exported_at: number;
  schema_version: number;
  revision: number;
  groups: OneBotBackupGroup[];
}

export interface OneBotImportPayload {
  summary: { groups: number; servers: number; revision: number };
  groups: GroupBrief[];
}

export type ConfigKind = "bool" | "int" | "list" | "string";

export interface ConfigItem {
  key: string;
  type: ConfigKind;
  section: string;
  desc: string;
  hint: string;
  default: unknown;
  options?: string[] | null;
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
  expected_revision: number;
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
  /** 备注来源：手填或服务端 MOTD；后端按 server_id 独立存放 */
  remark_src?: "manual" | "motd";
  remark_motd?: string;
  remark_motd_at?: number;
  /** 后端算好的最终显示值；列表只渲染它 */
  remark_display?: string;
  /** MOTD 备注当前生效时的彩色分段；手填/无颜色时为 null */
  remark_runs?: RemarkRun[] | null;
}

export interface SetupCodePayload {
  setup_id: string;
  code: string;
  expires_at: number;
  /** 后台当前时间（秒），用来校正前端倒计时。 */
  now: number;
}

export interface SetupStatusPayload {
  status: "pending" | "claimed" | "expired" | "missing";
  expires_at?: number;
  now: number;
  config_id?: string;
  /** 验证码刚被消费时返回最新官方状态，弹窗可直接同步整页。 */
  state?: OfficialState;
}

export type OfficialPolicy = GroupPolicy;

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
  console_token_ids: string[];
  active: boolean;
  binding_ready: boolean | null;
  binding_readiness?: string;
  message_delivery: { ready: boolean; receipts_count: number | null; capacity: number; message: string };
}

export interface OfficialRemark {
  remark_src: "manual" | "motd";
  remark_motd: string;
  remark_motd_at: number;
  remark_motd_runs?: RemarkRun[];
}

/** 后台导出的可编辑备份；不含验证码、控制台 Token 与审计明细。 */
export interface OfficialBackup {
  format: string;
  format_version: number;
  exported_at: number;
  schema_version: number;
  environment_id: string;
  revision: number;
  groups: OfficialGroup[];
  candidates: OfficialCandidate[];
  environment_administrators: string[];
  environment_access: Record<string, string[]>;
  server_remarks: Record<string, OfficialRemark>;
}

export interface OfficialImportPayload {
  summary: { groups: number; servers: number; revision: number };
  state: OfficialState;
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
