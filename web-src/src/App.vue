<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import UiIcon from "./components/UiIcon.vue";
import UiButton from "./components/UiButton.vue";
import ThemeDrawer from "./components/ThemeDrawer.vue";
import DialogHost from "./components/DialogHost.vue";
import GroupsPage from "./pages/GroupsPage.vue";
import CfgPage from "./pages/CfgPage.vue";
import OfficialPage from "./pages/OfficialPage.vue";
import ConstPage from "./pages/ConstPage.vue";
import MapPage from "./pages/MapPage.vue";
import HelpPage from "./pages/HelpPage.vue";
import { S, applySkin, boot, dirtyCount, openDialog, requestPage, saveDrafts, saveSkin } from "./store";
import type { Skin } from "./store";
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
const NAV_ITEMS = NAV.flatMap(group => group.items);
const PAGES = { srv: GroupsPage, cfg: CfgPage, official: OfficialPage, const: ConstPage, map: MapPage, help: HelpPage };
const pageComp = computed(() => PAGES[S.page]);
const isMobile = useIsMobile();
const isCream = computed(() => S.skin === "cream");
const menuOpen = ref(false);
const pageLabel = computed(() => NAV_ITEMS.find(i => i.key === S.page)?.label ?? "");
const saving = ref(false);
async function save() {
  if (saving.value) return;
  saving.value = true;
  try { await saveDrafts(); } finally { saving.value = false; }
}
function navigate(key: PageKey) { menuOpen.value = false; requestPage(key); }
watch(() => S.page, () => { menuOpen.value = false; });
onMounted(boot);

/* 抽屉里选中只预览；取消回到打开前的主题，应用成功后才写入偏好文件。 */
const themeOpen = ref(false);
const themeBase = ref<Skin>("classic");
const themeSaving = ref(false);
function openThemeDrawer() {
  themeBase.value = S.skin;
  themeOpen.value = true;
}
function previewTheme(skin: Skin) {
  applySkin(skin);
}
function closeThemeDrawer() {
  applySkin(themeBase.value);
  themeOpen.value = false;
}
async function applyTheme(skin: Skin) {
  if (themeSaving.value) return;
  themeSaving.value = true;
  try {
    if (await saveSkin(skin)) {
      themeBase.value = skin;
      themeOpen.value = false;
    }
  } finally {
    themeSaving.value = false;
  }
}
function openThemeFromMenu() {
  menuOpen.value = false;
  openThemeDrawer();
}
</script>

<template>
  <div v-if="!S.skinReady" class="boot-splash" role="status" aria-live="polite">
    <span class="boot-logo"><UiIcon name="compass" :size="26" /></span>
    <b>MCSight</b>
    <span>正在载入外观…</span>
  </div>

  <div v-else class="shell" :class="{ mobile: isMobile, cream: isCream, 'has-savebar': isMobile && S.page === 'cfg' && !!dirtyCount }">
    <header v-if="!isMobile && !isCream" class="app-header">
      <div class="brand-area"><b class="brand">MCSight</b><span class="brand-divider">/</span><span class="brand-context">插件配置</span></div>
      <div class="header-actions"><span class="admin-label">超级管理员</span><UiButton kind="line" @click="openDialog('reload-plugin')"><UiIcon name="refresh" :size="16" />重载插件</UiButton></div>
    </header>

    <nav v-if="!isMobile && !isCream" class="sidebar" aria-label="主导航">
      <template v-for="group in NAV" :key="group.group">
        <div class="nav-group">{{ group.group }}</div>
        <button v-for="item in group.items" :key="item.key" type="button" class="nav-item" :class="{ active: S.page === item.key }" :aria-current="S.page === item.key ? 'page' : undefined" @click="navigate(item.key)">
          <UiIcon :name="item.icon" :size="17" /><span>{{ item.label }}</span>
        </button>
      </template>
      <div class="nav-group">外观</div>
      <button type="button" class="nav-item theme-nav-item" @click="openThemeDrawer">
        <UiIcon name="palette" :size="17" /><span>主题样式</span>
        <span class="nav-item-hint">{{ isCream ? "奶油" : "经典" }}</span>
      </button>
      <div class="sidebar-caption" :title="S.pluginVersion ? `MCSight v${S.pluginVersion}` : undefined">AstrBot 插件配置中心</div>
    </nav>

    <header v-if="!isMobile && isCream" class="app-header cream-header">
      <div class="cream-brand">
        <span class="brand-tile"><UiIcon name="compass" :size="26" /></span>
        <div class="brand-copy"><b class="brand">MCSight</b><span>AstrBot 插件配置</span></div>
      </div>
      <nav class="top-nav" aria-label="主导航">
        <button v-for="item in NAV_ITEMS" :key="item.key" type="button" class="nav-pill"
          :class="{ active: S.page === item.key }" :aria-current="S.page === item.key ? 'page' : undefined"
          @click="navigate(item.key)">
          <UiIcon :name="item.icon" :size="17" /><span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="cream-actions">
        <span class="admin-pill">超级管理员</span>
        <button class="theme-trigger" type="button" @click="openThemeDrawer">
          <UiIcon name="palette" :size="17" /><span>主题样式</span>
        </button>
        <UiButton kind="line" @click="openDialog('reload-plugin')"><UiIcon name="refresh" :size="16" />重载插件</UiButton>
      </div>
    </header>

    <header v-if="isMobile && !isCream" class="mobile-header">
      <div class="mobile-brand"><b class="brand">MCSight</b><span>{{ pageLabel }}</span></div>
      <UiButton kind="tool" icon-only aria-label="页面导航" aria-haspopup="menu" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><UiIcon name="menu" :size="20" /></UiButton>
    </header>

    <header v-if="isMobile && isCream" class="mobile-header cream-mobile-header">
      <div class="mobile-brand cream-mobile-brand">
        <span class="brand-tile small"><UiIcon name="compass" :size="20" /></span>
        <div class="brand-copy"><b class="brand">MCSight</b><span>{{ pageLabel }}</span></div>
      </div>
      <div class="mobile-head-actions">
        <button class="mobile-icon-btn" type="button" aria-label="主题样式" @click="openThemeDrawer">
          <UiIcon name="palette" :size="19" />
        </button>
        <UiButton kind="tool" icon-only aria-label="页面导航" aria-haspopup="menu" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><UiIcon name="menu" :size="20" /></UiButton>
      </div>
    </header>

    <template v-if="isMobile">
      <button v-if="menuOpen" class="mobile-menu-backdrop" type="button" aria-label="关闭导航" @click="menuOpen = false" />
      <nav v-if="menuOpen" class="mobile-menu" aria-label="页面导航" @keydown.esc.stop="menuOpen = false">
        <template v-for="group in NAV" :key="group.group">
          <div class="mobile-menu-label">{{ group.group }}</div>
          <button v-for="item in group.items" :key="item.key" type="button" :class="{ active: S.page === item.key }" :aria-current="S.page === item.key ? 'page' : undefined" @click="navigate(item.key)"><UiIcon :name="item.icon" :size="17" />{{ item.label }}<UiIcon v-if="S.page === item.key" name="check" :size="16" class="menu-check" /></button>
        </template>
        <div class="mobile-menu-label">外观</div>
        <button type="button" @click="openThemeFromMenu"><UiIcon name="palette" :size="17" />主题样式<span class="menu-hint">{{ isCream ? "奶油" : "经典" }}</span></button>
        <div class="menu-divider" />
        <button type="button" @click="menuOpen = false; openDialog('reload-plugin')"><UiIcon name="refresh" :size="17" />重载插件</button>
      </nav>
    </template>

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

  <ThemeDrawer :open="themeOpen" :skin="themeBase" :saving="themeSaving"
    @preview="previewTheme" @cancel="closeThemeDrawer" @apply="applyTheme" />
</template>

<style scoped>
.shell { display:grid; grid-template-columns:200px minmax(0,1fr); grid-template-rows:64px minmax(0,1fr); grid-template-areas:"header header" "nav main"; min-height:100vh; }
.app-header { grid-area:header; position:sticky; top:0; z-index:30; display:flex; align-items:center; justify-content:space-between; height:64px; padding:0 24px; gap:16px; border-bottom:1px solid var(--bd); background:var(--surface); }
.brand-area,.header-actions { display:flex; align-items:center; gap:12px; }
.brand { color:var(--ink); font-family:var(--fb,Manrope,sans-serif); font-size:20px; font-weight:700; line-height:1.5; }
.brand-divider { color:var(--ink4); font-size:14px; }
.brand-context { color:var(--ink3); font-size:13px; }
.admin-label { color:var(--ink3); font-size:12px; }
.sidebar { grid-area:nav; position:sticky; top:64px; align-self:start; height:calc(100vh - 64px); display:flex; flex-direction:column; gap:6px; padding:24px 12px; background:var(--surface); border-right:1px solid var(--bd); overflow-y:auto; }
.nav-group { padding:10px 12px 8px; color:var(--ink3); font-size:11px; }
.nav-item { display:flex; align-items:center; gap:10px; width:100%; height:42px; padding:0 12px; color:var(--ink3); background:transparent; border:0; border-radius:8px; text-align:left; font:400 13px var(--ff); cursor:pointer; transition:background 140ms,color 140ms; }
.nav-item:hover { background:var(--thead); }
.nav-item.active { color:var(--ac); background:var(--ac-soft); font-weight:600; }
.nav-item-hint { margin-left:auto; color:var(--ink4); font-size:10px; font-weight:400; }
.sidebar-caption { margin-top:auto; color:var(--ink3); font-size:11px; text-align:center; padding-top:24px; }
.content { grid-area:main; min-width:0; padding:32px; }
.bridge-empty { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:12px; padding:64px 24px; border:1px solid var(--bd); border-radius:12px; background:var(--surface); color:var(--ink3); }
.bridge-empty h1 { color:var(--ink); font-size:20px; font-weight:600; margin:0; }
.bridge-empty p { margin:0; font-size:13px; }
.mobile-header { grid-area:header; position:sticky; top:0; z-index:30; display:flex; justify-content:space-between; align-items:center; height:72px; padding:0 16px; border-bottom:1px solid var(--bd); background:var(--surface); }
.mobile-brand { display:flex; flex-direction:column; gap:2px; }
.mobile-brand .brand { font-size:18px; }
.mobile-brand>span { color:var(--ink3); font-size:12px; }
.mobile-menu-backdrop { position:fixed; inset:0; border:0; background:transparent; z-index:38; cursor:default; }
.mobile-menu { position:fixed; top:80px; right:16px; z-index:39; width:min(280px,calc(100vw - 32px)); padding:8px; border:1px solid var(--bd); border-radius:10px; background:var(--surface); box-shadow:0 6px 18px #24335312; }
.mobile-menu-label { padding:8px 12px 6px; font-size:11px; color:var(--ink3); }
.mobile-menu button { display:flex; align-items:center; gap:10px; width:100%; height:44px; padding:0 12px; background:transparent; border:0; border-radius:6px; color:var(--ink); font:400 13px var(--ff); text-align:left; cursor:pointer; }
.mobile-menu button:hover,.mobile-menu button.active { background:var(--ac-soft); color:var(--ac); }
.menu-check { margin-left:auto; }
.menu-hint { margin-left:auto; color:var(--ink4); font-size:10px; }
.menu-divider { height:1px; margin:6px 0; background:var(--row); }
.mobile-savebar { position:fixed; bottom:0; left:0; right:0; z-index:35; min-height:76px; padding:16px 16px calc(16px + env(safe-area-inset-bottom)); display:flex; align-items:center; justify-content:space-between; gap:10px; background:var(--surface); border-top:1px solid var(--bd); }
.mobile-savebar>div { display:flex; flex-direction:column; gap:3px; }
.mobile-savebar b { font-size:13px; font-weight:500; }
.mobile-savebar span { color:var(--ink3); font-size:11px; }
.shell.mobile { grid-template-columns:minmax(0,1fr); grid-template-rows:72px minmax(0,1fr); grid-template-areas:"header" "main"; }
.shell.mobile .content { padding:16px; }
.shell.mobile.has-savebar .content { padding-bottom:calc(108px + env(safe-area-inset-bottom)); }

/* 奶油主题：顶栏导航、居中内容与更宽的首尾列。 */
.shell.cream { grid-template-columns:minmax(0,1fr); grid-template-rows:76px minmax(0,1fr); grid-template-areas:"header" "main"; }
.shell.cream .content { width:100%; max-width:1180px; justify-self:center; padding:30px 40px 52px; }
.cream-header { display:grid; grid-template-columns:minmax(170px,340px) minmax(0,1fr) minmax(250px,340px); height:76px; padding:0 40px; gap:16px; }
.cream-brand { display:flex; align-items:center; gap:12px; min-width:0; }
.brand-tile { display:inline-flex; flex:none; align-items:center; justify-content:center; width:40px; height:40px; border-radius:13px; background:#FBEDE7; color:#8B6FD8; border:1px solid #fff; box-shadow:0 2px 6px #5b3e7a1f; }
.brand-tile.small { width:36px; height:36px; border-radius:11px; }
.brand-copy { display:flex; min-width:0; flex-direction:column; gap:1px; }
.brand-copy .brand { font-family:"Noto Serif SC","Songti SC",serif; font-size:20px; font-weight:700; line-height:1.3; }
.brand-copy>span { color:var(--ink3); font-size:11px; line-height:1.4; white-space:nowrap; }
.top-nav { display:flex; min-width:0; justify-self:center; align-items:center; gap:2px; padding:5px; border-radius:999px; background:#EEE7F6; overflow-x:auto; scrollbar-width:none; }
.top-nav::-webkit-scrollbar { display:none; }
.nav-pill { display:inline-flex; flex:none; align-items:center; gap:7px; height:40px; padding:0 15px; border:0; border-radius:999px; background:transparent; color:var(--ink2); font:500 13px var(--ff); white-space:nowrap; cursor:pointer; transition:background var(--transition),color var(--transition),box-shadow var(--transition); }
.nav-pill:hover { background:#fff8; color:var(--ac); }
.nav-pill.active { background:#F1ECFB; color:#7458C4; font-weight:600; box-shadow:0 3px 8px #5b3e7a1f; outline:1px solid #fff; }
.cream-actions { display:flex; min-width:0; justify-self:end; align-items:center; gap:10px; }
.admin-pill { display:inline-flex; flex:none; align-items:center; height:32px; padding:0 12px; border:1px solid #E7DEF0; border-radius:999px; background:#F6F1FA; color:var(--ink2); font-size:12px; white-space:nowrap; }
.theme-trigger { display:inline-flex; flex:none; align-items:center; gap:7px; height:40px; padding:0 13px; border:1px solid #E7DEF0; border-radius:12px; background:#F6F1FA; color:#7458C4; font:600 12px var(--ff); white-space:nowrap; cursor:pointer; transition:background var(--transition),border-color var(--transition); }
.theme-trigger:hover { border-color:#7458C4; background:#F1ECFB; }
.cream-mobile-brand { flex-direction:row; align-items:center; gap:10px; }
.cream-mobile-brand .brand-copy>span { font-size:11px; }
.mobile-head-actions { display:flex; align-items:center; gap:8px; }
.mobile-icon-btn { display:inline-flex; align-items:center; justify-content:center; width:42px; height:42px; padding:0; border:1px solid var(--bd); border-radius:12px; background:var(--thead); color:var(--ac); cursor:pointer; }
.boot-splash { display:flex; min-height:100vh; flex-direction:column; align-items:center; justify-content:center; gap:10px; background:var(--canvas); color:var(--ink3); font-size:13px; }
.boot-splash b { color:var(--ink); font-family:var(--fb,Manrope,sans-serif); font-size:22px; }
.boot-logo { display:inline-flex; align-items:center; justify-content:center; width:52px; height:52px; margin-bottom:2px; border:1px solid var(--bd); border-radius:16px; background:var(--surface); color:var(--ac); }
@media (max-width:1180px) and (min-width:901px) {
  .cream-header { grid-template-columns:auto minmax(0,1fr) auto; padding:0 24px; }
  .brand-copy>span { display:none; }
  .admin-pill { display:none; }
}
@media (max-width:900px),(max-height:520px) {
  .cream-header { display:none; }
  .shell.cream .content { padding:16px; max-width:none; }
  .mobile-menu { top:80px; }
}
</style>
