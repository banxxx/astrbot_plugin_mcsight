<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { S, activePlatform, api, fail, loadGroup, loadSummary, toast } from "../store";
import SrvPage from "./SrvPage.vue";
import OfficialPage from "./OfficialPage.vue";

const ready = ref(!!S.activation);
const activeLabel = computed(() => activePlatform.value === "qq_official" ? "QQ 官方" : "OneBot");

onMounted(async () => {
  if (!S.activation) {
    try {
      S.activation = await api.activation();
    } catch {
      toast("读取生效平台失败，页面暂时按 OneBot 显示。");
    }
  }
  try {
    // 页面始终跟随当前生效平台，进入时按它重新取一遍，避免沿用其它平台留下的空列表。
    await loadSummary();
    await loadGroup();
  } catch (err) {
    fail(err);
  }
  ready.value = true;
});
</script>

<template>
  <div class="groups-page">
    <header class="groups-heading">
      <div class="groups-title">
        <h1>群与服务器</h1>
        <p>按群管理服务器、显示方式与绑定规则。</p>
      </div>
      <span class="groups-active">当前生效：{{ activeLabel }}</span>
    </header>

    <template v-if="ready">
      <SrvPage v-if="activePlatform === 'onebot'" embedded />
      <OfficialPage v-else embedded />
    </template>
    <div v-else class="groups-loading" role="status">正在读取生效平台…</div>
  </div>
</template>

<style scoped>
.groups-page { display: grid; gap: 16px; }
.groups-heading { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 12px 20px; }
.groups-title { display: grid; gap: 5px; min-width: 0; }
.groups-title h1 { margin: 0; font-size: 24px; line-height: 1.35; font-weight: 600; color: var(--ink); }
.groups-title p { margin: 0; font-size: 13px; line-height: 1.5; color: var(--ink3); }
.groups-active { color: var(--ink3); font-size: 12px; white-space: nowrap; }
.groups-loading { padding: 40px 20px; border: 1px solid var(--bd); border-radius: 10px; background: var(--surface); color: var(--ink3); font-size: 13px; text-align: center; }
@media (max-width: 760px) {
  .groups-heading { align-items: flex-start; }
  .groups-title h1 { font-size: 22px; }
}
</style>
