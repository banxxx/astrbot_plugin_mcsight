<script setup lang="ts">
import UiBadge from "../components/UiBadge.vue";
import UiNotice from "../components/UiNotice.vue";
import { useIsMobile } from "../lib/useMediaQuery";

const isMobile = useIsMobile();
interface CommandMapping { label: string; cmd: string | null; note: string }
const GROUPS: { title: string; rows: CommandMapping[] }[] = [
  { title: "服务器操作", rows: [
    { label: "添加服务器", cmd: "/mc add 名称 IP [端口]", note: "未填写模组端口时，该服按未安装模组处理。" },
    { label: "批量添加", cmd: "/mc batchadd 名:IP[:端口],…", note: "逐条校验；成功项保存，失败项显示原因。" },
    { label: "批量删除", cmd: "/mc batchremove 名,名", note: "删除所选条目，保留绑定记录。" },
    { label: "编辑名称", cmd: "/mc edit 名称 name 新名", note: "名称不能包含方括号。" },
    { label: "编辑主机地址", cmd: "/mc edit 名称 host 新地址", note: "填写服务器地址，可包含游戏端口。" },
    { label: "编辑模组端口", cmd: "/mc edit 名称 port 端口", note: "清空端口后禁用该服务器的模组功能。" },
    { label: "编辑手填备注", cmd: "/mc edit 名称 remark 备注", note: "显示在 /在线 卡片的服务器名称下方。" },
    { label: "备注使用 MOTD", cmd: null, note: "使用采集的 MOTD，保留原有手填备注。" },
    { label: "调整服务器顺序", cmd: "/mc move 名称 序号\n/mc swap 名1 名2", note: "序号从 0 开始，只改变当前群的显示顺序。" },
    { label: "设置 / 清除命令 Token", cmd: "/mc token 名称 Token（或 清除）", note: "Token 不回显；输入长度为 16–128 字符。" },
    { label: "登录限制开关", cmd: null, note: "控制该服务器模组的白名单校验。" },
    { label: "隐藏 / 显示服务器", cmd: null, note: "隐藏后停止展示与采集，保留绑定和 Token。" },
    { label: "删除服务器", cmd: "/mc remove 名称", note: "移除服务器条目与其 Token，保留绑定记录。" },
  ] },
  { title: "本群开关", rows: [
    { label: "显示上次在线", cmd: "/上次在线 开 | 关", note: "控制在线卡片是否显示上次在线时间。" },
    { label: "启用昵称检测", cmd: "/检测昵称 开 | 关", note: "英文别名：/mc nicknamecheck。" },
    { label: "管理昵称忽略名单", cmd: "/检测昵称 忽略 QQ\n/检测昵称 取消忽略 QQ", note: "查看名单：/检测昵称 忽略列表。" },
  ] },
  { title: "群配置", rows: [
    { label: "查看本群 JSON", cmd: "只读查看", note: "敏感字段显示占位符。" },
    { label: "新增群", cmd: null, note: "显式创建群配置。" },
    { label: "删除群配置", cmd: null, note: "移除本群服务器、开关与忽略名单。" },
  ] },
  { title: "全局参数", rows: [
    { label: "开关与普通输入项", cmd: null, note: "与 AstrBot 原生插件表单共用配置。" },
    { label: "管理员与黑名单", cmd: null, note: "编辑后加入草稿，返回全局页统一保存。" },
    { label: "模组 API 鉴权 Token", cmd: null, note: "与服务器模组的 apiToken 保持一致。" },
    { label: "数据库参数", cmd: null, note: "保存并重载后重建数据库连接。" },
    { label: "保存更改", cmd: null, note: "保存有改动的字段，按回执提示重载。" },
    { label: "查看全局 JSON", cmd: "只读查看", note: "密钥字段仅展示占位符。" },
  ] },
  { title: "插件操作", rows: [{ label: "重载插件", cmd: null, note: "与 AstrBot 插件列表中的重载操作一致。" }] },
];
const total = GROUPS.reduce((count, group) => count + group.rows.length, 0);
</script>

<template>
  <div class="page ref-page">
    <section class="page-heading">
      <div><h1>命令对照</h1><p>查找页面操作对应的 QQ 命令。</p></div>
      <UiBadge tone="neutral">只读参考</UiBadge>
    </section>
    <UiNotice>标记“仅页面支持”的操作没有对应 QQ 命令。下方按页面区域整理。</UiNotice>

    <template v-if="isMobile">
      <section v-for="group in GROUPS" :key="group.title" class="ref-mobile-card">
        <header class="ref-mobile-heading"><h2>{{ group.title }}</h2><span>{{ group.rows.length }} 项</span></header>
        <article v-for="row in group.rows" :key="row.label" class="ref-mobile-item">
          <h3>{{ row.label }}</h3>
          <UiBadge v-if="row.cmd === null" tone="neutral">仅页面支持</UiBadge>
          <p v-else class="ref-command" :class="{ 'ref-muted': row.cmd === '只读查看' }">{{ row.cmd }}</p>
          <p class="ref-note">{{ row.note }}</p>
        </article>
      </section>
      <p class="ref-mobile-foot">共 {{ total }} 项操作</p>
    </template>

    <section v-else class="ref-table ref-map" role="table" aria-label="页面操作与命令对照">
      <div class="ref-grid ref-table-head" role="row"><span role="columnheader">页面操作</span><span role="columnheader">对应 QQ 命令</span><span role="columnheader">使用说明</span></div>
      <div v-for="group in GROUPS" :key="group.title" role="rowgroup">
        <div class="ref-group" role="row"><div class="ref-group-content" role="cell" aria-colspan="3"><h2>{{ group.title }}</h2><span>{{ group.rows.length }} 项</span></div></div>
        <div v-for="row in group.rows" :key="row.label" class="ref-grid ref-table-row" role="row">
          <span class="ref-label" role="cell">{{ row.label }}</span>
          <div role="cell"><UiBadge v-if="row.cmd === null" tone="neutral">仅页面支持</UiBadge><span v-else class="ref-command" :class="{ 'ref-muted': row.cmd === '只读查看' }">{{ row.cmd }}</span></div>
          <span class="ref-note" role="cell">{{ row.note }}</span>
        </div>
      </div>
      <footer class="ref-table-foot"><span>共 {{ total }} 项操作</span><span>页面与命令共享同一功能逻辑</span></footer>
    </section>
  </div>
</template>
