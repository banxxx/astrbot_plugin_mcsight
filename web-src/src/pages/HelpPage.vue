<script setup lang="ts">
import { onMounted } from "vue";
import UiBadge from "../components/UiBadge.vue";
import UiNotice from "../components/UiNotice.vue";
import { S, fail, loadCommands, loading } from "../store";
import { useIsMobile } from "../lib/useMediaQuery";

const isMobile = useIsMobile();
onMounted(() => { void loadCommands().catch(fail); });
</script>

<template>
  <div class="page ref-page">
    <section class="page-heading">
      <div><h1>命令帮助</h1><p>查看命令用法、别名及使用权限。</p></div>
      <UiBadge tone="neutral">只读</UiBadge>
    </section>
    <UiNotice>命令说明与 /帮助 图片同源；管理权限以每条命令的标签为准。</UiNotice>

    <template v-if="isMobile">
      <section v-for="section in S.cmdSections" :key="section.title" class="ref-mobile-card">
        <header class="ref-mobile-heading"><h2>{{ section.title }}</h2><span>{{ section.rows.length }} 条</span></header>
        <article v-for="(row, index) in section.rows" :key="section.title + index" class="ref-mobile-item">
          <h3 class="ref-command">{{ row.cmd }}</h3>
          <p class="ref-note">{{ row.desc }}</p>
          <div class="ref-mobile-meta"><span class="ref-meta-label">别名</span><span class="ref-alias">{{ row.aliases || '—' }}</span></div>
          <div class="ref-mobile-meta"><span class="ref-meta-label">权限</span><UiBadge v-if="row.tags" :tone="row.tags.includes('超管') ? 'warn' : 'neutral'">{{ row.tags }}</UiBadge><span v-else class="ref-muted">—</span></div>
        </article>
      </section>
      <p v-if="!S.cmdSections.length" class="ref-empty" role="status">{{ loading ? '正在读取命令帮助…' : '暂无命令帮助内容' }}</p>
      <p v-else class="ref-mobile-foot">{{ S.cmdTotal }} 条命令 / {{ S.cmdSections.length }} 个分组</p>
    </template>

    <section v-else class="ref-table ref-help" role="table" aria-label="命令帮助">
      <div class="ref-grid ref-table-head" role="row"><span role="columnheader">命令</span><span role="columnheader">作用</span><span role="columnheader">别名</span><span role="columnheader">权限</span></div>
      <div v-for="section in S.cmdSections" :key="section.title" role="rowgroup">
        <div class="ref-group" role="row"><div class="ref-group-content" role="cell" aria-colspan="4"><h2>{{ section.title }}</h2><span>{{ section.rows.length }} 条</span></div></div>
        <div v-for="(row, index) in section.rows" :key="section.title + index" class="ref-grid ref-table-row" role="row">
          <span class="ref-command" role="cell">{{ row.cmd }}</span>
          <span class="ref-note" role="cell">{{ row.desc }}</span>
          <span class="ref-alias" role="cell">{{ row.aliases || '—' }}</span>
          <div role="cell"><UiBadge v-if="row.tags" :tone="row.tags.includes('超管') ? 'warn' : 'neutral'">{{ row.tags }}</UiBadge><span v-else class="ref-muted">—</span></div>
        </div>
      </div>
      <div v-if="!S.cmdSections.length" class="ref-empty" role="status">{{ loading ? '正在读取命令帮助…' : '暂无命令帮助内容' }}</div>
      <footer class="ref-table-foot"><span>{{ S.cmdTotal }} 条命令 / {{ S.cmdSections.length }} 个分组</span><span>与群内 /帮助 图片保持一致</span></footer>
    </section>
    <UiNotice v-if="S.error" tone="bad">{{ S.error }}</UiNotice>
  </div>
</template>
