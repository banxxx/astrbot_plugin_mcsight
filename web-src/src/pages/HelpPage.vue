<script setup lang="ts">
import { onMounted } from "vue";
import { S, fail, loadCommands } from "../store";

onMounted(() => {
  loadCommands().catch(fail);
});
</script>

<template>
  <div class="page">
    <section class="ph">
      <div class="ph-t">
        <h1>命令帮助</h1>
        <p>{{ S.cmdTotal }} 条命令 · 与 /帮助 图片同源 · 本页只读</p>
      </div>
      <span class="tag">只读 · 数据来自 /帮助 卡片结构</span>
    </section>

    <section class="card">
      <div class="th">
        <div class="cols">
          <span class="c-h1">命令</span><span class="c-h2">说明</span><span class="c-h3">别名 / 权限</span>
        </div>
      </div>

      <template v-for="sec in S.cmdSections" :key="sec.title">
        <div class="band"><span class="bar" /><b>{{ sec.title }}</b><i>{{ sec.rows.length }} 项{{ sec.hint ? ` · ${sec.hint}` : "" }}</i></div>
        <div v-for="(r, i) in sec.rows" :key="`${sec.title}-${i}`" class="row dense">
          <div class="cols">
            <span class="c-h1 cmd ell">{{ r.cmd }}</span>
            <span class="c-h2 t-sub ell">{{ r.desc }}</span>
            <span class="c-h3 alias-wrap">
              <span v-if="r.aliases" class="al ell">{{ r.aliases }}</span>
              <span v-else class="al dash">—</span>
              <span v-if="r.tags" class="tg">{{ r.tags }}</span>
            </span>
          </div>
        </div>
      </template>

      <div class="tf">
        <span>共 {{ S.cmdTotal }} 条 · {{ S.cmdSections.length }} 组</span>
        <span class="tip">群里发 /帮助 拿到的是同一批文案的图片版</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--gap-card);
}

.ph {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.ph-t h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
}

.ph-t p {
  margin: 5px 0 0;
  font-size: 12px;
  color: var(--ink3);
}

.tag {
  padding: 0 10px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--thead);
  color: var(--ink2);
  font-size: 11px;
  line-height: 26px;
  white-space: nowrap;
}

.cmd {
  font-size: 12px;
  font-family: var(--fm);
  color: var(--ac-deep);
}

.al {
  font-size: 11px;
  font-family: var(--fm);
  color: var(--ink3);
}

.al.dash {
  color: var(--ink5);
}

.alias-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.tg {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: var(--r-badge);
  background: var(--warn-badge-bg);
  color: var(--warn);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}
</style>
