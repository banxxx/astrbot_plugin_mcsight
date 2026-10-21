<script setup lang="ts">
import { computed, onMounted } from "vue";
import UiIcon from "../components/UiIcon.vue";
import UiNotice from "../components/UiNotice.vue";
import { S, fail, loadConstants } from "../store";
import type { ConstRow } from "../lib/types";

onMounted(() => {
  loadConstants().catch(fail);
});

/* 画布把常量按来源分带；后端只给扁平 rows，这里按源文件名归带，同带的连续行合并成一段。 */
const BANDS: Record<string, string> = {
  "mod_http.py": "模组 HTTP 与在线探测",
  "checker.py": "模组 HTTP 与在线探测",
  "debounce.py": "命令防抖与绑定链路",
  "bind_guard.py": "命令防抖与绑定链路",
  "bind_route.py": "命令防抖与绑定链路",
  "bind.py": "命令防抖与绑定链路",
  "console.py": "命令防抖与绑定链路",
  "management.py": "命令防抖与绑定链路",
  "server_config.py": "配置与文件落点",
  "whitelist_config.py": "配置与文件落点",
  "stats_sampler.py": "采样与出图",
  "stats_snapshot.py": "采样与出图",
  "image_generator.py": "采样与出图",
  "bind_check.py": "采样与出图",
  "nickname_check.py": "采样与出图",
  "controller.py": "采样与出图",
  "common.py": "采样与出图",
};

function bandOf(row: ConstRow): string {
  const base = row.source.split("/").pop() || "";
  return BANDS[base] || "其他";
}

const groups = computed(() => {
  const out: { title: string; rows: ConstRow[] }[] = [];
  for (const row of S.constRows) {
    const title = bandOf(row);
    const last = out[out.length - 1];
    if (last && last.title === title) last.rows.push(row);
    else out.push({ title, rows: [row] });
  }
  return out;
});
</script>

<template>
  <div class="page">
    <section class="ph">
      <div class="ph-t">
        <h1>代码常量</h1>
        <p>{{ S.constRows.length }} 个运行期参数 · 写死在源码里 · 本页不提供输入框</p>
      </div>
      <span class="tag"><UiIcon name="lock" :size="12" />只读 · 改源码 + 重载插件才生效</span>
    </section>

    <UiNotice tone="info">
      唯一配置读入口：whitelist_config 的 _get_config_value / _get_config_list。
    </UiNotice>

    <section class="card">
      <div class="th">
        <div class="cols">
          <span class="c-t1">参数含义</span><span class="c-t2">常量名</span><span class="c-t3">当前值</span><span
            class="c-t4">源码位置</span>
        </div>
      </div>

      <template v-for="grp in groups" :key="grp.title">
        <div class="band"><span class="bar" /><b>{{ grp.title }}</b><i>{{ grp.rows.length }} 项</i></div>
        <div v-for="r in grp.rows" :key="r.name" class="row dense">
          <div class="cols">
            <span class="c-t1"><b class="t-title ell">{{ r.title }}</b></span>
            <span class="c-t2"><span class="cname mono ell">{{ r.name }}</span></span>
            <span class="c-t3"><b class="cval mono">{{ r.value }}{{ r.unit }}</b></span>
            <span class="c-t4"><span class="csrc mono ell">{{ r.source }}</span></span>
          </div>
        </div>
      </template>

      <div class="tf">
        <span>共 {{ S.constRows.length }} 项</span>
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
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--thead);
  color: var(--ink2);
  font-size: 11px;
  line-height: 26px;
  white-space: nowrap;
}

.cname {
  font-size: 12px;
  color: var(--ink2);
}

.cval {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}

.csrc {
  font-size: 11px;
  color: var(--ink3);
}
</style>
