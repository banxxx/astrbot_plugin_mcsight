<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import UiIcon from "./components/UiIcon.vue";
import UiButton from "./components/UiButton.vue";
import DialogHost from "./components/DialogHost.vue";
import GroupsPage from "./pages/GroupsPage.vue";
import CfgPage from "./pages/CfgPage.vue";
import OfficialPage from "./pages/OfficialPage.vue";
import ConstPage from "./pages/ConstPage.vue";
import MapPage from "./pages/MapPage.vue";
import HelpPage from "./pages/HelpPage.vue";
import { S, boot, dirtyCount, openDialog, requestPage, saveDrafts } from "./store";
import { useIsMobile } from "./lib/useMediaQuery";
import type { PageKey } from "./store";

const NAV: { group: string; items: { key: PageKey; label: string; icon: string }[] }[] = [
  { group: "配置", items: [
    { key: "srv", label: "群与服务器", icon: "server" },
    { key: "cfg", label: "全局参数", icon: "sliders" },
  ] },
  { group: "参考", items: [
    { key: "map", label: "命令对照", icon: "list-tree" },
    { key: "help", label: "命令帮助", icon: "book-open" },
    { key: "const", label: "代码常量", icon: "code" },
  ] },
];
const PAGES = { srv: GroupsPage, cfg: CfgPage, official: OfficialPage, const: ConstPage, map: MapPage, help: HelpPage };
const pageComp = computed(() => PAGES[S.page]);
const isMobile = useIsMobile();
const menuOpen = ref(false);
const pageLabel = computed(() => NAV.flatMap(g => g.items).find(i => i.key === S.page)?.label ?? "");
const saving = ref(false);
async function save() {
  if (saving.value) return;
  saving.value = true;
  try { await saveDrafts(); } finally { saving.value = false; }
}
function navigate(key: PageKey) { menuOpen.value = false; requestPage(key); }
watch(() => S.page, () => { menuOpen.value = false; });
onMounted(boot);
</script>

<template>
  <div class="shell" :class="{ mobile: isMobile, 'has-savebar': isMobile && S.page === 'cfg' && !!dirtyCount }">
    <header v-if="!isMobile" class="app-header">
      <div class="brand-area"><b class="brand">MCSight</b><span class="brand-divider">/</span><span class="brand-context">插件配置</span></div>
      <div class="header-actions"><span class="admin-label">超级管理员</span><UiButton kind="line" @click="openDialog('reload-plugin')"><UiIcon name="refresh" :size="16" />重载插件</UiButton></div>
    </header>
    <nav v-if="!isMobile" class="sidebar" aria-label="主导航">
      <template v-for="group in NAV" :key="group.group">
        <div class="nav-group">{{ group.group }}</div>
        <button v-for="item in group.items" :key="item.key" type="button" class="nav-item" :class="{ active: S.page === item.key }" :aria-current="S.page === item.key ? 'page' : undefined" @click="navigate(item.key)">
          <UiIcon :name="item.icon" :size="17" /><span>{{ item.label }}</span>
        </button>
      </template>
      <div class="sidebar-caption" :title="S.pluginVersion ? `MCSight v${S.pluginVersion}` : undefined">AstrBot 插件配置中心</div>
    </nav>
    <header v-else class="mobile-header">
      <div class="mobile-brand"><b class="brand">MCSight</b><span>{{ pageLabel }}</span></div>
      <UiButton kind="tool" icon-only aria-label="页面导航" aria-haspopup="menu" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><UiIcon name="menu" :size="20" /></UiButton>
      <button v-if="menuOpen" class="mobile-menu-backdrop" type="button" aria-label="关闭导航" @click="menuOpen = false" />
      <nav v-if="menuOpen" class="mobile-menu" aria-label="页面导航" @keydown.esc.stop="menuOpen = false">
        <template v-for="group in NAV" :key="group.group">
          <div class="mobile-menu-label">{{ group.group }}</div>
          <button v-for="item in group.items" :key="item.key" type="button" :class="{ active: S.page === item.key }" :aria-current="S.page === item.key ? 'page' : undefined" @click="navigate(item.key)"><UiIcon :name="item.icon" :size="17" />{{ item.label }}<UiIcon v-if="S.page === item.key" name="check" :size="16" class="menu-check" /></button>
        </template>
        <div class="menu-divider" />
        <button type="button" @click="menuOpen = false; openDialog('reload-plugin')"><UiIcon name="refresh" :size="17" />重载插件</button>
      </nav>
    </header>
    <main class="content">
      <section v-if="S.noBridge" class="bridge-empty" role="status">
        <UiIcon name="lock" :size="28" /><h1>请从 AstrBot 打开</h1><p>进入插件页面，再打开 MCSight 配置中心。</p>
      </section>
      <component v-else :is="pageComp" />
    </main>
    <div v-if="isMobile && S.page === 'cfg' && dirtyCount" class="mobile-savebar">
      <div><b>更改未保存</b><span>保存后按提示重载</span></div>
      <UiButton kind="pri" :loading="saving" :disabled="saving" @click="save"><UiIcon name="check" :size="16" />保存更改</UiButton>
    </div>
    <div v-if="S.toast" class="toast" role="status" aria-live="polite">{{ S.toast }}</div>
    <DialogHost />
  </div>
</template>

<style scoped>
.shell { display:grid; grid-template-columns:200px minmax(0,1fr); grid-template-rows:64px minmax(0,1fr); grid-template-areas:"header header" "nav main"; min-height:100vh; }
.app-header { grid-area:header; position:sticky; top:0; z-index:30; display:flex; align-items:center; justify-content:space-between; height:64px; padding:0 24px; gap:16px; border-bottom:1px solid var(--bd); background:var(--surface); }
.brand-area,.header-actions { display:flex; align-items:center; gap:12px; }
.brand { color:var(--ink); font-family:var(--fb,Manrope,sans-serif); font-size:20px; font-weight:700; line-height:1.5; }
.brand-divider { color:var(--ink4); font-size:14px; }
.brand-context { color:var(--ink3); font-size:13px; }
.admin-label { color:var(--ink3); font-size:12px; }
.sidebar { grid-area:nav; position:sticky; top:64px; align-self:start; height:calc(100vh - 64px); display:flex; flex-direction:column; gap:6px; padding:24px 12px; background:var(--surface); border-right:1px solid var(--bd); }
.nav-group { padding:10px 12px 8px; color:var(--ink3); font-size:11px; }
.nav-item { display:flex; align-items:center; gap:10px; width:100%; height:42px; padding:0 12px; color:var(--ink3); background:transparent; border:0; border-radius:8px; text-align:left; font:400 13px var(--ff); cursor:pointer; transition:background 140ms,color 140ms; }
.nav-item:hover { background:var(--thead); }
.nav-item.active { color:var(--ac); background:var(--ac-soft); font-weight:600; }
.sidebar-caption { margin-top:auto; color:var(--ink3); font-size:11px; text-align:center; padding-top:24px; }
.content { grid-area:main; min-width:0; padding:32px; }
.bridge-empty { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:12px; padding:64px 24px; border:1px solid var(--bd); border-radius:12px; background:var(--surface); color:var(--ink3); }
.bridge-empty h1 { color:var(--ink); font-size:20px; font-weight:600; margin:0; }
.bridge-empty p { margin:0; font-size:13px; }
.mobile-header { grid-area:header; position:sticky; top:0; z-index:30; display:flex; justify-content:space-between; align-items:center; height:72px; padding:0 16px; border-bottom:1px solid var(--bd); background:var(--surface); }
.mobile-brand { display:flex; flex-direction:column; gap:2px; }
.mobile-brand .brand { font-size:18px; }
.mobile-brand>span { color:var(--ink3); font-size:12px; }
.mobile-menu-backdrop { position:fixed; inset:0; border:0; background:transparent; z-index:1; cursor:default; }
.mobile-menu { position:absolute; top:64px; right:16px; z-index:2; width:min(280px,calc(100vw - 32px)); padding:8px; border:1px solid var(--bd); border-radius:10px; background:var(--surface); box-shadow:0 6px 18px #24335312; }
.mobile-menu-label { padding:8px 12px 6px; font-size:11px; color:var(--ink3); }
.mobile-menu button { display:flex; align-items:center; gap:10px; width:100%; height:44px; padding:0 12px; background:transparent; border:0; border-radius:6px; color:var(--ink); font:400 13px var(--ff); text-align:left; cursor:pointer; }
.mobile-menu button:hover,.mobile-menu button.active { background:var(--ac-soft); color:var(--ac); }
.menu-check { margin-left:auto; }
.menu-divider { height:1px; margin:6px 0; background:var(--row); }
.mobile-savebar { position:fixed; bottom:0; left:0; right:0; z-index:35; min-height:76px; padding:16px 16px calc(16px + env(safe-area-inset-bottom)); display:flex; align-items:center; justify-content:space-between; gap:10px; background:var(--surface); border-top:1px solid var(--bd); }
.mobile-savebar>div { display:flex; flex-direction:column; gap:3px; }
.mobile-savebar b { font-size:13px; font-weight:500; }
.mobile-savebar span { color:var(--ink3); font-size:11px; }
.shell.mobile { grid-template-columns:minmax(0,1fr); grid-template-rows:72px minmax(0,1fr); grid-template-areas:"header" "main"; }
.shell.mobile .content { padding:16px; }
.shell.mobile.has-savebar .content { padding-bottom:calc(108px + env(safe-area-inset-bottom)); }
</style>
