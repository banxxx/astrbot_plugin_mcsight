<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { api, toast } from "../store";
import { copyText } from "../lib/clipboard";
import UiBadge from "./UiBadge.vue";
import UiButton from "./UiButton.vue";
import UiField from "./UiField.vue";
import UiIcon from "./UiIcon.vue";
import UiInput from "./UiInput.vue";
import UiModal from "./UiModal.vue";
import UiNotice from "./UiNotice.vue";
import UiSecretInput from "./UiSecretInput.vue";
import UiSwitch from "./UiSwitch.vue";
import type { OfficialServer, OfficialState, RemarkRun } from "../lib/types";
import RemarkText from "./RemarkText.vue";

const props = defineProps<{
  server?: OfficialServer | null;
  saving?: boolean;
  hasToken?: boolean;
  environment: string;
  configId: string;
  revision: number;
  groupLabel: string;
}>();
const emit = defineEmits<{
  (e: "close"): void;
  (e: "save", server: OfficialServer, token: string, remarkSrc: "manual" | "motd"): void;
  (e: "clear-token", server: OfficialServer): void;
  (e: "state", state: OfficialState): void;
}>();

const isEdit = computed(() => !!props.server);
const clearTokenConfirm = ref(false);
const modalCls = computed(() => clearTokenConfirm.value ? "m-confirm" : "m-form");
const modalTitle = computed(() => clearTokenConfirm.value
  ? "清除命令 Token"
  : isEdit.value ? "编辑服务器" : "添加服务器");
const modalSub = computed(() => clearTokenConfirm.value
  ? props.server?.name ?? ""
  : isEdit.value
    ? `当前群 ${props.groupLabel} / ${props.server?.name ?? ""}`
    : `当前群 ${props.groupLabel} / 保存后立即更新本群列表`);
const sourceError = ref("");
const syncing = ref(false);
const motdPreview = reactive({ loading: false, text: "", error: "", runs: [] as RemarkRun[] });
const editMotd = ref(props.server?.remark_motd ?? "");
const editMotdAt = ref(props.server?.remark_motd_at ?? 0);
let previewSeq = 0;
const form = reactive({
  name: props.server?.name ?? "",
  host: props.server
    ? `${props.server.host}${props.server.game_port === 25565 ? "" : `:${props.server.game_port}`}`
    : "",
  api_port: props.server?.api_port == null ? "" : String(props.server.api_port),
  remark: props.server?.remark ?? "",
  remark_src: (props.server?.remark_src ?? "manual") as "manual" | "motd",
  token: "",
});
const errors = reactive<Record<string, string>>({});
const copiedId = ref(false);
let copiedTimer: number | undefined;
async function copyServerId() {
  const id = props.server?.server_id;
  if (!id) return;
  if (!await copyText(id)) {
    toast("复制失败，请手动选择服务器 ID");
    return;
  }
  copiedId.value = true;
  window.clearTimeout(copiedTimer);
  copiedTimer = window.setTimeout(() => { copiedId.value = false; }, 2000);
}
onBeforeUnmount(() => window.clearTimeout(copiedTimer));

/* 父级保存备注来源后会把新的 server 对象传回来：只同步 MOTD 缓存，不覆盖正在输入的表单。 */
watch(() => props.server, (server) => {
  if (!server) return;
  editMotd.value = server.remark_motd ?? "";
  editMotdAt.value = server.remark_motd_at ?? 0;
  form.remark_src = server.remark_src ?? "manual";
});

/* 地址/端口一改，之前取到的 MOTD 就作废：别让提交时把旧地址的值写进新服务器。 */
watch(() => [form.host, form.api_port], () => {
  if (isEdit.value) return;
  previewSeq += 1;
  motdPreview.text = "";
  motdPreview.error = "";
  motdPreview.loading = false;
  motdPreview.runs = [];
});

function parseHost(): { host: string; gamePort: number } | null {
  const match = /^([A-Za-z0-9_][A-Za-z0-9_.-]*)(?::(\d{1,5}))?$/.exec(form.host.trim());
  if (!match || match[1].length > 253) return null;
  const gamePort = match[2] ? Number(match[2]) : 25565;
  if (!Number.isInteger(gamePort) || gamePort < 1 || gamePort > 65535) return null;
  return { host: match[1], gamePort };
}

function validate(): boolean {
  for (const key of Object.keys(errors)) delete errors[key];
  const name = form.name.trim();
  if (!name) errors.name = "请输入服务器名称。";
  else if (Array.from(name).length > 20 || /\s|[\[\]【】]/.test(name)) {
    errors.name = "名称最多 20 个字符，不能含空格或方括号。";
  }
  if (!form.host.trim()) errors.host = "请输入主机地址。";
  else if (!parseHost()) errors.host = "只填域名或标准 IPv4，可带 :游戏端口。";
  if (form.api_port.trim()) {
    const apiPort = Number(form.api_port);
    if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) {
      errors.api_port = "模组 API 端口必须是 1–65535，留空表示未安装模组。";
    }
  }
  if (Array.from(form.remark).length > 50) errors.remark = "备注最多 50 个字符。";
  if (form.token && (form.token.length < 16 || form.token.length > 128 || /[^\x21-\x7e]/.test(form.token))) {
    errors.token = "Token 长度应为 16–128 个字符。";
  }
  return !Object.keys(errors).length;
}

function submit() {
  if (!validate()) return;
  const target = parseHost()!;
  emit("save", {
    server_id: props.server?.server_id ?? "",
    name: form.name.trim(),
    host: target.host,
    game_port: target.gamePort,
    api_port: form.api_port.trim() ? Number(form.api_port) : null,
    hidden: props.server?.hidden ?? false,
    remark: form.remark.trim(),
  }, form.token, form.remark_src);
}

function clearToken() {
  if (!props.server) return;
  clearTokenConfirm.value = false;
  emit("clear-token", props.server);
}

/* 真正取到 MOTD 时才用主题色突出；提示语、获取中、失败原因仍走灰阶。 */
const hasMotdValue = computed(() => isEdit.value
  ? !!editMotd.value
  : !motdPreview.loading && !motdPreview.error && !!motdPreview.text);

function motdTimeText(): string {
  if (!isEdit.value) {
    if (motdPreview.loading) return "正在获取…";
    if (motdPreview.error) return "获取失败";
    if (motdPreview.text) return "已获取，添加时写入缓存";
    return "填好地址后可先获取";
  }
  if (syncing.value) return "正在同步…";
  const at = Number(editMotdAt.value || 0);
  if (!at) return "还没采到过值";
  const d = new Date(at * 1000);
  const two = (n: number) => String(n).padStart(2, "0");
  return `采集于 ${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

function motdValueText(): string {
  if (!isEdit.value) {
    if (motdPreview.loading) return "正在向服务器查询 MOTD…";
    if (motdPreview.error) return `获取失败：${motdPreview.error}`;
    return motdPreview.text || "填好主机地址后点「获取 MOTD」，或提交后自动采集";
  }
  if (motdPreview.error) return `同步失败：${motdPreview.error}`;
  return editMotd.value || "还没有值：点「立即同步」";
}

async function fetchMotdPreview() {
  const target = parseHost();
  if (!target) {
    motdPreview.text = "";
    motdPreview.error = "先填正确的主机地址";
    return;
  }
  if (form.api_port.trim()) {
    const apiPort = Number(form.api_port);
    if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) {
      motdPreview.text = "";
      motdPreview.error = "模组 API 端口必须是 1–65535";
      return;
    }
  }
  const seq = ++previewSeq;
  motdPreview.loading = true;
  motdPreview.error = "";
  try {
    const result = await api.officialMotdPreview(props.environment, target.host, target.gamePort, form.api_port.trim());
    if (seq !== previewSeq) return;
    motdPreview.text = result.motd;
    motdPreview.runs = result.runs ?? [];
  } catch (e) {
    if (seq !== previewSeq) return;
    motdPreview.text = "";
    motdPreview.runs = [];
    motdPreview.error = e instanceof Error ? e.message : String(e);
  } finally {
    if (seq === previewSeq) motdPreview.loading = false;
  }
}

/* 开关已经打开、地址也填完了：失焦就自动取一次，不用再特意点按钮。 */
function maybeFetchMotdOnBlur() {
  if (isEdit.value || form.remark_src !== "motd") return;
  if (!form.host.trim() || motdPreview.text || motdPreview.loading) return;
  void fetchMotdPreview();
}

async function syncRemarkMotd() {
  const server = props.server;
  if (!server || syncing.value || !props.configId) return;
  syncing.value = true;
  motdPreview.error = "";
  try {
    const state = await api.officialRemarkSync(props.environment, props.configId,
      props.revision, server.server_id);
    emit("state", state);
    const updated = state.groups.find(group => group.config_id === props.configId)
      ?.servers.find(item => item.server_id === server.server_id);
    if (updated) {
      editMotd.value = updated.remark_motd ?? "";
      editMotdAt.value = updated.remark_motd_at ?? 0;
    }
  } catch (e) {
    motdPreview.error = e instanceof Error ? e.message : String(e);
  } finally {
    syncing.value = false;
  }
}

async function setRemarkSrc(value: boolean) {
  const next = value ? "motd" : "manual";
  const previous = form.remark_src;
  form.remark_src = next;
  sourceError.value = "";
  motdPreview.error = "";
  if (isEdit.value) {
    const server = props.server;
    if (!server || !props.configId) return;
    try {
      const state = await api.officialRemarkSrc(props.environment, props.configId,
        props.revision, server.server_id, next);
      emit("state", state);
      if (next === "motd" && !editMotd.value) await syncRemarkMotd();
    } catch (e) {
      form.remark_src = previous;
      sourceError.value = e instanceof Error ? e.message : String(e);
    }
  } else if (next === "motd" && form.host.trim() && !motdPreview.text && !motdPreview.loading) {
    void fetchMotdPreview();
  }
}
</script>

<template>
  <UiModal :cls="modalCls" :title="modalTitle" :sub="modalSub" @close="emit('close')">
    <template v-if="clearTokenConfirm">
      <p class="dialog-copy">清除后，/执行 对这台服务器不可用。其他服务器不受影响。</p>
      <UiNotice tone="warn">此操作会立即保存，关闭编辑弹窗不会撤销。</UiNotice>
    </template>

    <div v-else class="server-form">
      <UiField for="official-server-name" label="服务器名称" required help="最多 20 个字符，不含方括号。" :err="errors.name">
        <UiInput id="official-server-name" v-model="form.name" placeholder="请输入服务器名称" :error="!!errors.name" />
      </UiField>

      <div class="connection-fields">
        <UiField for="official-server-host" label="主机地址" required help="可包含 Minecraft 游戏端口。" :err="errors.host">
          <UiInput id="official-server-host" v-model="form.host" placeholder="survival.example.com:25565"
            :error="!!errors.host" @blur="maybeFetchMotdOnBlur" />
        </UiField>
        <UiField for="official-server-api-port" label="模组 API 端口" help="与游戏端口不同。" :err="errors.api_port">
          <UiInput id="official-server-api-port" v-model="form.api_port" inputmode="numeric" placeholder="留空不启用"
            :error="!!errors.api_port" @blur="maybeFetchMotdOnBlur" />
        </UiField>
      </div>

      <section v-if="isEdit && server?.server_id" class="server-id-field" aria-label="服务器 ID">
        <div class="server-id-head">
          <span class="field-label">服务器 ID</span>
          <UiBadge tone="neutral">模组 qq_official.serverId</UiBadge>
        </div>
        <div class="server-id-row">
          <code>{{ server.server_id }}</code>
          <UiButton kind="line" size="sm" @click="copyServerId">
            <UiIcon :name="copiedId ? 'check' : 'copy'" :size="15" />{{ copiedId ? "已复制" : "复制" }}
          </UiButton>
        </div>
        <p class="field-help">填到模组 <code>qq_official.serverId</code>，必须与这里完全一致；不是密钥。</p>
      </section>

      <UiField for="official-server-remark" label="手填备注" help="最多 50 个字符，切换来源不会丢失。" :err="errors.remark">
        <UiInput id="official-server-remark" v-model="form.remark" :maxlength="50" placeholder="请输入服务器备注"
          :error="!!errors.remark" />
      </UiField>

      <section class="motd-field" aria-label="备注来源">
        <div class="switch-row">
          <div class="switch-copy">
            <label class="field-label">使用服务端 MOTD</label>
            <p class="field-help">开启后，在线卡片使用服务端介绍作为备注。</p>
          </div>
          <UiSwitch :on="form.remark_src === 'motd'" aria-label="使用服务端 MOTD"
            @toggle="setRemarkSrc(form.remark_src !== 'motd')" />
        </div>
        <div class="motd-result">
          <div class="motd-copy">
            <p class="motd-value"
              :class="{ muted: !hasMotdValue || form.remark_src !== 'motd', error: !!motdPreview.error }"><RemarkText :runs="!isEdit && !motdPreview.loading && !motdPreview.error && motdPreview.text ? motdPreview.runs : null" :fallback="motdValueText()" /></p>
            <p class="field-help">{{ motdTimeText() }}</p>
          </div>
          <UiButton v-if="isEdit" :disabled="syncing || saving" @click="syncRemarkMotd">
            <UiIcon name="refresh" :size="16" :class="{ spinning: syncing }" />{{ syncing ? "同步中…" : "立即同步" }}
          </UiButton>
          <UiButton v-else :disabled="motdPreview.loading || !form.host.trim() || saving" @click="fetchMotdPreview">
            <UiIcon name="refresh" :size="16" :class="{ spinning: motdPreview.loading }" />{{ motdPreview.loading ? "获取中…" : "获取 MOTD" }}
          </UiButton>
        </div>
        <p v-if="sourceError" class="field-error" role="alert">{{ sourceError }}</p>
      </section>

      <div class="token-field">
        <div class="token-label-row">
          <label class="field-label" for="official-server-token">命令 Token</label>
          <UiBadge v-if="isEdit && hasToken">已设置</UiBadge>
          <button v-if="isEdit && hasToken" class="clear-token" type="button" :disabled="!!form.token || saving"
            :title="form.token ? '清空本次输入后，可清除已保存的 Token' : '清除现有 Token'"
            @click="clearTokenConfirm = true">清除现有 Token</button>
        </div>
        <UiSecretInput id="official-server-token" v-model="form.token" :has-value="isEdit && !!hasToken"
          :placeholder="isEdit ? '留空保留原 Token' : '留空不启用命令执行'" :error="!!errors.token" />
        <p class="field-help">16–128 个字符。已保存的值不回显；{{ isEdit ? "清除操作需单独确认。" : "需与模组的 consoleToken 一致。" }}</p>
        <p v-if="errors.token" class="field-error" role="alert">{{ errors.token }}</p>
      </div>

      <UiNotice v-if="isEdit && server && server.api_port === null" tone="warn">
        该服未配模组 API 端口，Token 写入后 /执行 仍不可用。
      </UiNotice>
    </div>

    <template #foot>
      <template v-if="clearTokenConfirm">
        <UiButton :disabled="saving" @click="clearTokenConfirm = false">取消</UiButton>
        <UiButton kind="danger" :disabled="saving" @click="clearToken">
          <UiIcon name="trash" :size="16" />确认清除
        </UiButton>
      </template>
      <template v-else>
        <UiButton @click="emit('close')">取消</UiButton>
        <UiButton kind="pri" :disabled="saving" @click="submit">
          <UiIcon name="check" :size="16" />{{ saving ? "保存中…" : isEdit ? "保存服务器" : "添加服务器" }}
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
.server-id-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border-radius: 6px;
  background: var(--thead);
}
.server-id-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.server-id-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}
.server-id-row code {
  min-width: 0;
  color: var(--ink);
  font: 12px/1.5 var(--fm);
  overflow-wrap: anywhere;
}
.server-id-field .field-help code {
  font-family: var(--fm);
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
  .clear-token {
    min-height: 44px;
  }
}
</style>
