<script setup lang="ts">
import { computed, onMounted } from "vue";
import UiBadge from "../components/UiBadge.vue";
import UiIcon from "../components/UiIcon.vue";
import UiNotice from "../components/UiNotice.vue";
import { S, fail, loadConstants, loading } from "../store";
import { useIsMobile } from "../lib/useMediaQuery";
import type { ConstRow } from "../lib/types";

const isMobile = useIsMobile();
onMounted(() => { void loadConstants().catch(fail); });

const GROUP_ORDER = ["模组连接", "服务器与命令", "绑定与权限", "采样与快照", "名单输出", "状态卡片与出图", "其他"];
function groupOf(row: ConstRow): string {
  const source = row.source.replaceAll("\\", "/");
  const base = (source.split("/").pop() || "").split(":")[0];
  if (["mod_http.py", "checker.py", "controller.py"].includes(base)) return "模组连接";
  if (["server_config.py", "console.py", "management.py", "debounce.py", "common.py"].includes(base)) return "服务器与命令";
  if (["whitelist_config.py", "bind_guard.py", "bind_route.py", "bind.py"].includes(base)) return "绑定与权限";
  if (["stats_sampler.py", "stats_snapshot.py"].includes(base)) return "采样与快照";
  if (source.includes("bindcheck_image/") || ["bind_check.py", "nickname_check.py"].includes(base)) return "名单输出";
  if (base === "image_generator.py" || source.includes("features/")) return "状态卡片与出图";
  return "其他";
}
const groups = computed(() => {
  const result = new Map<string, ConstRow[]>(GROUP_ORDER.map((title) => [title, []]));
  for (const row of S.constRows) result.get(groupOf(row))!.push(row);
  return Array.from(result, ([title, rows]) => ({ title, rows })).filter((group) => group.rows.length);
});
function displayValue(row: ConstRow): string {
  return row.unit ? row.value + " " + row.unit : row.value;
}
</script>

<template>
  <div class="page ref-page">
    <section class="page-heading">
      <div><h1>代码常量</h1><p>查看插件的固定限制、时间参数和来源位置。</p></div>
      <UiBadge tone="neutral">只读</UiBadge>
    </section>
    <UiNotice>这些参数写在源码中。需要调整时，应修改对应代码并更新插件。</UiNotice>

    <template v-if="isMobile">
      <section v-for="group in groups" :key="group.title" class="ref-mobile-card">
        <header class="ref-mobile-heading"><h2>{{ group.title }}</h2><span>{{ group.rows.length }} 项</span></header>
        <article v-for="(row, index) in group.rows" :key="row.source + row.name + index" class="ref-mobile-item">
          <div class="ref-mobile-title"><h3>{{ row.title }}</h3><span class="ref-value">{{ displayValue(row) }}</span></div>
          <div class="ref-mobile-meta"><span class="ref-meta-label">常量</span><code class="ref-constant">{{ row.name }}</code></div>
          <div class="ref-mobile-meta"><span class="ref-meta-label">源码</span><code class="ref-source">{{ row.source }}</code></div>
        </article>
      </section>
      <p v-if="!S.constRows.length" class="ref-empty" role="status">{{ loading ? '正在读取代码常量…' : '暂无代码常量' }}</p>
      <p v-else class="ref-mobile-foot">共 {{ S.constRows.length }} 项 / 只读查看</p>
    </template>

    <section v-else class="ref-table ref-constants" role="table" aria-label="代码常量">
      <div class="ref-grid ref-table-head" role="row"><span role="columnheader">含义</span><span role="columnheader">常量名称</span><span role="columnheader">数值 / 单位</span><span role="columnheader">源码位置</span></div>
      <div v-for="group in groups" :key="group.title" role="rowgroup">
        <div class="ref-group" role="row"><div class="ref-group-content" role="cell" aria-colspan="4"><h2>{{ group.title }}</h2></div></div>
        <div v-for="(row, index) in group.rows" :key="row.source + row.name + index" class="ref-grid ref-table-row" role="row">
          <span class="ref-label" role="cell">{{ row.title }}</span>
          <code class="ref-constant" role="cell">{{ row.name }}</code>
          <span class="ref-value" role="cell">{{ displayValue(row) }}</span>
          <code class="ref-source" role="cell">{{ row.source }}</code>
        </div>
      </div>
      <div v-if="!S.constRows.length" class="ref-empty" role="status">{{ loading ? '正在读取代码常量…' : '暂无代码常量' }}</div>
      <footer class="ref-table-foot"><span class="ref-footer-note"><UiIcon name="lock" :size="15" />数值由插件源码提供，此页面不支持直接编辑。</span><span>共 {{ S.constRows.length }} 项</span></footer>
    </section>
    <UiNotice v-if="S.error" tone="bad">{{ S.error }}</UiNotice>
  </div>
</template>
