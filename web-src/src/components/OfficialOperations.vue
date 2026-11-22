<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../store";
import type { OfficialOperationDetail, OfficialOperationPage, OfficialOperationScope } from "../lib/types";
import UiButton from "./UiButton.vue";
import UiInput from "./UiInput.vue";
import UiNotice from "./UiNotice.vue";
import UiSelect from "./UiSelect.vue";

const props = defineProps<{ environment: string; configId: string; revision: number; enabled: boolean; blocked: boolean; servers?: {server_id: string; name: string}[] }>();
const emit = defineEmits<{ busy: [value: boolean] }>();
const busy = ref(false), error = ref(""), notice = ref("");
const filter = ref("pending"), lookup = ref(""), confirmed = ref(false), fresh = ref(false);
const serverId=ref("");
const serverOptions=computed(() => [{value:"",label:"中心权威（hybrid / remote）"},...(props.servers ?? []).map(s => ({value:s.server_id,label:s.name}))]);
const page = ref<OfficialOperationPage | null>(null), detail = ref<OfficialOperationDetail | null>(null);
const scope = computed<OfficialOperationScope>(() => ({ environment_id: props.environment, config_id: props.configId, expected_revision: props.revision,...(serverId.value ? {server_id:serverId.value} : {}) }));
const key = computed(() => `${props.environment}/${props.configId}/${props.revision}/${serverId.value}`);
const blocked = computed(() => props.blocked || !props.enabled || busy.value);
const terminal = computed(() => !!detail.value && ["COMMITTED", "REJECTED", "ABORTED"].includes(detail.value.state));
let generation = 0;
watch(key, () => { generation++; page.value = null; detail.value = null; lookup.value = ""; confirmed.value = false; fresh.value = false; error.value = ""; notice.value = ""; });
watch(busy, value => emit("busy", value), { flush: "sync" });
onBeforeUnmount(() => { generation++; emit("busy", false); });
function sameScope(current: number, selected: OfficialOperationScope) {
  return current === generation && selected.environment_id === props.environment
    && selected.config_id === props.configId && selected.expected_revision === props.revision && (selected.server_id ?? "")===serverId.value;
}
async function request(work: () => Promise<void>) {
  if (blocked.value) return;
  const current = generation, currentKey = key.value;
  busy.value = true; error.value = ""; notice.value = "";
  try { await work(); }
  catch (e) { if (current === generation && currentKey === key.value) error.value = e instanceof Error ? e.message : "查询或恢复未完成，请查询原操作。"; }
  finally { busy.value = false; }
}
async function list(next = false) {
  const current = generation, selected = { ...scope.value };
  const cursor = next ? page.value?.next_cursor ?? undefined : undefined;
  if (next && !cursor) return;
  await request(async () => {
    const result = await api.officialOperations(selected, filter.value === "all" ? "all" : "pending", cursor);
    if (sameScope(current, selected)) page.value = result;
  });
}
async function inspect(operationId = lookup.value.trim()) {
  const current = generation, selected = { ...scope.value };
  await request(async () => {
    confirmed.value = false; fresh.value = false;
    const result = await api.officialOperation(selected, operationId);
    if (sameScope(current, selected)) { detail.value = result; lookup.value = result.operation_id; fresh.value = true; }
  });
}
async function act(action: "retry" | "cancel") {
  const operation = detail.value, current = generation, selected = { ...scope.value };
  if (!operation || !fresh.value || !confirmed.value || (action === "retry") !== terminal.value) return;
  await request(async () => {
    confirmed.value = false; fresh.value = false;
    const result = await api.officialOperationAction(selected, operation, action);
    if (sameScope(current, selected)) {
      detail.value = result.operation; fresh.value = true;
      notice.value = result.code === "CONFLICT" ? "中心状态已变化，中止未生效，请核对当前结果。"
        : action === "cancel" ? "中心已确认中止；验证码作废及其它分项仍以当前状态为准。"
        : result.operation.pending ? "已重查原操作，仍有分项待确认；没有重新绑定或扩大目标。"
        : "原操作的待办已确认；这不代表 QQ/MC 实机验收已经完成。";
    }
  });
}
function changeFilter(value: string) { if (!blocked.value) { filter.value = value; page.value = null; } }
</script>

<template>
  <section class="operation-card">
    <h2>官方操作与恢复</h2>
    <p>按需读取当前官方群操作。local 模式先选择原服务器，查询使用完整操作编号；后台只能中止未提交操作或重查已确定终态，未决变更须由原请求人在官方群提供新消息。</p>
    <UiNotice v-if="!enabled || props.blocked" tone="warn">请先保存草稿、启用本群并选中当前官方环境，再查询操作。</UiNotice>
    <label>权威服务器<UiSelect v-model="serverId" :options="serverOptions" :disabled="blocked" aria-label="原操作权威服务器" /></label>
    <div class="operation-actions">
      <label>操作范围<UiSelect :model-value="filter" :options="[{ value: 'pending', label: '待确认与待同步' }, { value: 'all', label: '最近全部操作' }]" :disabled="blocked" aria-label="官方操作筛选" @update:model-value="changeFilter" /></label>
      <UiButton kind="line" :disabled="blocked" @click="list()">查询操作列表</UiButton>
      <UiButton kind="line" :disabled="blocked || !page?.next_cursor" @click="list(true)">操作下一页</UiButton>
    </div>
    <UiNotice v-if="error" tone="bad">{{ error }} 请重新查询原操作，确认结果后再处理。</UiNotice>
    <UiNotice v-if="notice">{{ notice }}</UiNotice>
    <template v-if="page">
      <p>本次快照 {{ page.total }} 条，每页 20 条，10 分钟有效。{{ page.truncated ? '仅展示最近 200 条，更早操作请按编号查询。' : '' }}</p>
      <div v-if="page.operations.length" class="operation-scroll"><table><thead><tr><th>操作编号</th><th>种类 / 中心状态</th><th>更新时间（UTC）</th><th>详情</th></tr></thead><tbody>
        <tr v-for="operation in page.operations" :key="operation.operation_id"><td><code>{{ operation.operation_id }}</code></td><td>{{ operation.kind }} / {{ operation.state }}</td><td>{{ operation.updated_at }}</td><td><UiButton kind="line" :disabled="blocked" @click="inspect(operation.operation_id)">查看操作</UiButton></td></tr>
      </tbody></table></div><p v-else>当前范围没有操作记录。</p>
    </template>
    <div class="operation-actions"><label class="operation-lookup">原操作编号<UiInput v-model="lookup" :disabled="blocked" :maxlength="36" placeholder="完整编号或至少8位前缀" aria-label="原操作编号" /></label><UiButton kind="line" :disabled="blocked || lookup.trim().length < 8" @click="inspect()">查询原操作</UiButton></div>
    <div v-if="detail" class="operation-detail">
      <p>操作：<code>{{ detail.operation_id }}</code></p><p>中心状态：<b>{{ detail.state }}</b> · 结果 {{ detail.result_code ?? '尚未确定' }} · 验证码确认 {{ detail.token_state ?? '无此待办' }}</p>
      <p v-if="!terminal">该操作尚未确定，后台不能代替原请求人提交。可由原请求人在官方群重查，或明确中止。</p>
      <p v-if="detail.effects.length">固定账号：<span v-for="effect in detail.effects" :key="effect.game_key">{{ effect.game_id }}（{{ effect.state }} / v{{ effect.revision }}） </span></p>
      <div v-if="detail.delivery.length" class="operation-scroll"><table><thead><tr><th>固定目标</th><th>镜像</th><th>白名单</th><th>在线限制</th></tr></thead><tbody><tr v-for="target in detail.delivery" :key="target.server_id"><td>{{ target.name }}</td><td>{{ target.mirror }}</td><td>{{ target.whitelist }}</td><td>{{ target.online }}</td></tr></tbody></table></div>
      <p>中心提交、镜像保存、白名单和在线限制分别确认。镜像 APPLIED 不等于全部完成。</p>
      <label><input v-model="confirmed" type="checkbox" :disabled="blocked || !fresh" />我已核对上述操作编号、固定目标和当前状态</label>
      <div class="operation-actions"><UiButton kind="line" :disabled="blocked || !fresh || !confirmed || !terminal" @click="act('retry')">重查终态并补偿</UiButton><UiButton kind="line" :disabled="blocked || !fresh || !confirmed || terminal" @click="act('cancel')">中止未提交操作</UiButton></div>
    </div>
  </section>
</template>

<style scoped>
.operation-card { padding: 22px; border: 1px solid var(--bd); border-radius: 12px; background: var(--surface); display: grid; gap: 14px; min-width: 0; }
h2 { font-size: 16px; margin: 0; }
p { margin: 0; font-size: 13px; line-height: 1.6; color: var(--ink3); overflow-wrap: anywhere; }
.operation-actions { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; font-size: 13px; }
.operation-actions label { display: grid; gap: 8px; min-width: 0; }
.operation-lookup { flex: 1; min-width: min(100%, 240px) !important; }
.operation-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12px; text-align: left; }
th, td { border-bottom: 1px solid var(--bd); padding: 10px 8px; vertical-align: top; }
th { color: var(--ink3); font-weight: 500; white-space: nowrap; }
code { overflow-wrap: anywhere; font-size: 12px; }
.operation-detail { display: grid; gap: 12px; padding-top: 14px; border-top: 1px solid var(--bd); font-size: 13px; }
@media (max-width: 640px) { .operation-card { padding: 16px; } }
</style>
