<script setup lang="ts">
import { computed, onMounted } from "vue";
import UiIcon from "./components/UiIcon.vue";
import UiButton from "./components/UiButton.vue";
import DialogHost from "./components/DialogHost.vue";
import SrvPage from "./pages/SrvPage.vue";
import CfgPage from "./pages/CfgPage.vue";
import ConstPage from "./pages/ConstPage.vue";
import MapPage from "./pages/MapPage.vue";
import HelpPage from "./pages/HelpPage.vue";
import { S, boot, dirtyCount, loadGroup, loadSummary, saveDrafts, toast } from "./store";
import type { PageKey } from "./store";

const NAV: { group: string; items: { key: PageKey; label: string; icon: string }[] }[] = [
  {
    group: "配置",
    items: [
      { key: "srv", label: "群与服务器", icon: "server" },
      { key: "cfg", label: "全局参数", icon: "sliders" },
    ],
  },
  {
    group: "参考",
    items: [
      { key: "map", label: "命令对照", icon: "list" },
      { key: "help", label: "绑定帮助", icon: "book-open" },
      { key: "const", label: "代码常量", icon: "code" },
    ],
  },
];

const PAGES: Record<PageKey, unknown> = {
  srv: SrvPage,
  cfg: CfgPage,
  const: ConstPage,
  map: MapPage,
  help: HelpPage,
};

const pageComp = computed(() => PAGES[S.page]);

async function reload() {
  try {
    await loadSummary();
    await loadGroup();
    toast("已重新读取");
  } catch (err) {
    toast(String(err));
  }
}

onMounted(boot);
</script>

<template>
  <div class="shell">
    <header class="tb">
      <div class="tb-l">
        <span class="logo"><UiIcon name="server" :size="15" /></span>
        <b>MCSight</b>
        <span class="sep" />
        <span class="crumb">配置中心</span>
        <span class="ver mono">astrbot_plugin_mcsight{{ S.pluginVersion ? ` · v${S.pluginVersion}` : "" }}</span>
      </div>
      <div class="tb-r">
        <span class="who">当前身份 超级管理员</span>
        <UiButton @click="reload"><UiIcon name="refresh" :size="13" />重新读取</UiButton>
        <UiButton v-if="S.page === 'cfg'" kind="pri" :disabled="!dirtyCount || S.busy" @click="saveDrafts">
          <UiIcon name="check" :size="14" />保存更改{{ dirtyCount ? `（${dirtyCount}）` : "" }}
        </UiButton>
      </div>
    </header>

    <nav class="side">
      <template v-for="grp in NAV" :key="grp.group">
        <div class="nav-label">{{ grp.group }}</div>
        <button
          v-for="it in grp.items"
          :key="it.key"
          class="nav"
          :class="{ on: S.page === it.key }"
          type="button"
          @click="S.page = it.key"
        >
          <span class="bar" />
          <UiIcon :name="it.icon" :size="15" />
          <span class="ntx ell">{{ it.label }}</span>
        </button>
      </template>
    </nav>

    <main class="content">
      <div v-if="S.noBridge" class="card">
        <div class="empty">
          <b>本页面必须在 AstrBot WebUI 的插件页面里打开</b>
          内核会把桥脚本注入到文档末尾，直接用浏览器打开这个文件是拿不到数据的。
        </div>
      </div>
      <component v-else :is="pageComp" />
    </main>

    <div v-if="S.toast" class="toast">{{ S.toast }}</div>
    <DialogHost />
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  grid-template-rows: 56px minmax(0, 1fr);
  grid-template-areas:
    "tb tb"
    "side main";
  min-height: 100vh;
}

.tb {
  grid-area: tb;
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 56px;
  padding: 0 20px 0 16px;
  border-bottom: 1px solid var(--bd);
  background: var(--surface);
}

.tb-l,
.tb-r {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: var(--ac);
  color: #fff;
}

.tb-l b {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.sep {
  width: 1px;
  height: 16px;
  background: var(--bd);
}

.crumb {
  font-size: 12px;
  color: var(--ink2);
}

.ver {
  font-size: 11px;
  color: var(--ink4);
}

.who {
  padding: 0 10px;
  border: 1px solid var(--bd);
  border-radius: var(--r-ctl);
  background: var(--thead);
  color: var(--ink2);
  font-size: 11px;
  line-height: 26px;
  white-space: nowrap;
}

.side {
  grid-area: side;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 12px;
  box-shadow: inset -1px 0 0 var(--bd);
  background: var(--surface);
}

.nav-label {
  color: var(--ink3);
  font-size: 11px;
  line-height: 13px;
  letter-spacing: 0.6px;
}

.nav {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: none;
  border-radius: var(--r-ctl);
  background: none;
  color: var(--ink2);
  font-family: var(--ff);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.nav .bar {
  width: 3px;
  height: 18px;
  border-radius: 2px;
  background: transparent;
  flex: none;
}

.nav .ntx {
  flex: 1 1 auto;
  min-width: 0;
}

.nav.on {
  background: var(--ac-soft);
  color: var(--ac);
  font-weight: 600;
}

.nav.on .bar {
  background: var(--ac);
}

.content {
  grid-area: main;
  min-width: 0;
  padding: 24px 28px 96px;
}

@media (max-width: 760px) {
  .shell {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: 56px auto minmax(0, 1fr);
    grid-template-areas:
      "tb"
      "side"
      "main";
  }

  .side {
    flex-direction: row;
    align-items: center;
    gap: 8px;
    overflow-x: auto;
    padding: 10px 12px;
    box-shadow: none;
    border-bottom: 1px solid var(--bd);
  }

  .nav-label {
    display: none;
  }

  .nav {
    width: auto;
    white-space: nowrap;
  }
}
</style>
