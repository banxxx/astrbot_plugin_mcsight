<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { api, configSaving, S, toast } from "../store";
import type { ActivationPlatform, ActivationSelection, ActivationStatus } from "../lib/types";
import UiBadge from "./UiBadge.vue";
import UiButton from "./UiButton.vue";
import UiInput from "./UiInput.vue";
import UiNotice from "./UiNotice.vue";
import UiSelect from "./UiSelect.vue";

const form = reactive({ platform: "onebot" as ActivationPlatform, instance: "", application: "", environment: "", fresh: false, confirmed: false });
const baseline = ref("");
const expectedRevision = ref(0);
const saving = ref(false);
const error = ref("");
const loaded = ref(false);
const fingerprint = () => JSON.stringify({ platform: form.platform, instance: form.instance,
  application: form.application, environment: form.environment, fresh: form.fresh });
const dirty = computed(() => loaded.value && fingerprint() !== baseline.value);
const busy = computed(() => saving.value || configSaving.value);
const platforms = [{ value: "onebot", label: "OneBot v11" }, { value: "qq_official", label: "QQ 官方机器人" }];
const labels: Record<string, string> = { active: "已启用", configuring: "按群配置启用", loading: "等待加载", selection_required: "待选择", pending_restart: "待完整重启", missing: "实例缺失", ambiguous: "实例不唯一", not_ready: "业务尚未开放", offline: "适配器已停止", storage_error: "选择记录异常", invalid: "选择无效", unavailable: "暂不可用" };
const choices = computed(() => {
  const options = (S.activation?.choices ?? []).filter(c => c.platform === form.platform)
    .map(c => ({ value: c.instance_id, label: c.instance_id, description: c.adapter, disabled: false }));
  if (form.instance && !options.some(o => o.value === form.instance)) {
    options.push({ value: form.instance, label: `${form.instance}（未注册）`, description: "请重新选择有效实例", disabled: true });
  }
  return options;
});
const environments = computed(() => [
  { value: "", label: "按应用选择已有环境；没有时新建" },
  ...(S.activation?.environments ?? []).filter(e => e.platform === form.platform && e.application_ref === form.application)
    .map(e => ({ value: e.environment_id, label: e.environment_id })),
]);
const canSave = computed(() => !!S.activation && dirty.value && !busy.value && !!form.instance
  && S.activation.state !== "storage_error"
  && (form.platform !== "qq_official" || (!!form.application.trim() && form.confirmed)));
function describe(selection: ActivationSelection | null | undefined) {
  return selection ? `${selection.platform === "onebot" ? "OneBot" : "QQ 官方"} · ${selection.instance_id}` : "尚未选择";
}
function reset(status: ActivationStatus) {
  const saved = status.saved_selection;
  form.platform = saved?.platform ?? "onebot";
  form.instance = saved?.instance_id ?? "";
  form.application = saved?.application_ref ?? "";
  form.environment = saved?.environment_id ?? "";
  form.fresh = false;
  form.confirmed = false;
  expectedRevision.value = status.config_revision;
  baseline.value = fingerprint();
  loaded.value = true;
}
watch(() => S.activation, value => { if (value && (!loaded.value || !dirty.value)) reset(value); }, { immediate: true });
function changePlatform(value: string) {
  form.platform = value as ActivationPlatform;
  form.instance = "";
  form.application = "";
  form.environment = "";
  form.fresh = false;
  form.confirmed = false;
}
function changeApplication(value: string) {
  form.application = value;
  form.environment = "";
  form.confirmed = false;
}
async function refresh() {
  if (busy.value) return;
  saving.value = true;
  error.value = "";
  try { const status = await api.activation(); S.activation = status; reset(status); }
  catch (e) { error.value = e instanceof Error ? e.message : "无法读取机器人选择"; }
  finally { saving.value = false; }
}
async function save() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = "";
  try {
    const status = await api.activationUpdate({
      platform: form.platform, instance_id: form.instance,
      application_ref: form.application, environment_id: form.fresh ? "" : form.environment,
      expected_revision: expectedRevision.value, new_environment: form.fresh, confirm_application: form.confirmed,
    });
    S.activation = status;
    reset(status);
    toast("机器人选择已保存；完整重启 AstrBot 后应用");
  } catch (e) { error.value = e instanceof Error ? e.message : "保存失败，请刷新状态确认"; }
  finally { saving.value = false; }
}
</script>

<template>
  <section class="activation-panel" aria-labelledby="activation-heading">
    <div class="activation-heading">
      <div><h2 id="activation-heading">生效机器人</h2><p>本插件一次只处理一个具体机器人实例</p></div>
      <UiBadge :tone="S.activation?.state === 'active' ? 'ok' : 'neutral'">{{ labels[S.activation?.state ?? 'unavailable'] ?? '暂不可用' }}</UiBadge>
    </div>
    <div v-if="S.activation" class="activation-body">
      <div class="activation-summary">
        <div><span>当前运行选择</span><strong>{{ describe(S.activation.selection) }}</strong></div>
        <div><span>下次完整启动</span><strong>{{ describe(S.activation.saved_selection) }}</strong></div>
      </div>
      <UiNotice :tone="S.activation.state === 'storage_error' ? 'bad' : 'info'">{{ S.activation.message }}<br />注册状态：{{ S.activation.registered ? '已注册' : '未注册' }} · 适配器：{{ S.activation.adapter_status }} · 在线状态：未知</UiNotice>
      <UiNotice v-if="S.activation.pending_restart" tone="warn">新选择已保存，当前运行选择保持生效。请在维护窗口完整重启 AstrBot；仅重载插件不会应用。</UiNotice>
      <UiNotice v-if="dirty && expectedRevision !== S.activation.config_revision" tone="warn">选择已在其它页面更新。本页草稿尚未覆盖它，请放弃修改并刷新。</UiNotice>
      <div class="activation-fields">
        <label for="activation-platform">平台<UiSelect id="activation-platform" :model-value="form.platform" :options="platforms" :disabled="busy" aria-label="生效平台" @update:model-value="changePlatform" /></label>
        <label for="activation-instance">具体机器人实例<UiSelect id="activation-instance" :model-value="form.instance" :options="choices" :disabled="busy" placeholder="请选择已注册实例" aria-label="生效机器人实例" @update:model-value="value => { form.instance = value; form.confirmed = false; }" /></label>
        <template v-if="form.platform === 'qq_official'">
          <label for="activation-app">官方应用 AppID<UiInput id="activation-app" :model-value="form.application" :disabled="busy" placeholder="填写所选实例所属应用的 AppID" @update:model-value="changeApplication" /></label>
          <label for="activation-environment">绑定环境<UiSelect id="activation-environment" :model-value="form.fresh ? '' : form.environment" :options="environments" :disabled="busy || form.fresh" aria-label="官方绑定环境" @update:model-value="value => form.environment = value" /></label>
        </template>
      </div>
      <template v-if="form.platform === 'qq_official'">
        <UiNotice tone="warn">QQ 官方目前开放群登记和配置后的只读查询，绑定与远程写操作仍关闭。保存后完整重启，再到“QQ 官方群”完成配置。AppID 用于区分应用；请勿填写 AppSecret。</UiNotice>
        <label class="activation-check"><input v-model="form.fresh" type="checkbox" :disabled="busy" />为该应用新建绑定环境（重新开始绑定时使用）</label>
        <label class="activation-check"><input v-model="form.confirmed" type="checkbox" :disabled="busy" />我已核对：所选实例属于填写的官方应用，环境选择正确</label>
      </template>
      <UiNotice v-if="error" tone="bad">{{ error }}</UiNotice>
      <div class="activation-actions">
        <p>机器人选择单独整组保存，应用需完整重启。</p>
        <UiButton kind="line" :disabled="busy" @click="refresh">{{ dirty ? '放弃修改并刷新' : '刷新实例状态' }}</UiButton>
        <UiButton kind="pri" :loading="saving" :disabled="!canSave" @click="save">保存机器人选择</UiButton>
      </div>
    </div>
    <div v-else class="activation-empty"><span>正在读取机器人选择…</span><UiButton kind="line" :disabled="busy" @click="refresh">重新读取</UiButton></div>
  </section>
</template>

<style scoped>
.activation-panel { margin: 20px 0; border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); }
.activation-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 22px; border-bottom: 1px solid var(--row); }
.activation-heading h2 { margin: 0; font-size: 16px; }
.activation-heading p, .activation-actions p { margin: 5px 0 0; color: var(--ink3); font-size: 12px; }
.activation-body { display: grid; gap: 14px; padding: 20px 22px; }
.activation-summary, .activation-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.activation-summary div { display: grid; gap: 6px; min-width: 0; }
.activation-summary span { font-size: 12px; color: var(--ink3); }
.activation-summary strong { font-size: 14px; overflow-wrap: anywhere; }
.activation-fields label { display: grid; gap: 8px; min-width: 0; font-size: 13px; }
.activation-check { display: flex; align-items: flex-start; gap: 9px; font-size: 13px; line-height: 1.6; }
.activation-check input { margin-top: 4px; }
.activation-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding-top: 4px; }
.activation-actions p { flex: 1 1 220px; }
.activation-empty { display: flex; align-items: center; justify-content: space-between; padding: 20px; }
@media (max-width: 640px) {
  .activation-heading { padding: 16px; align-items: flex-start; }
  .activation-body { padding: 16px; }
  .activation-summary, .activation-fields { grid-template-columns: minmax(0, 1fr); }
  .activation-actions p { flex-basis: 100%; }
}
</style>
