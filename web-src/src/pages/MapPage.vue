<script setup lang="ts">
import UiIcon from "../components/UiIcon.vue";
import UiNotice from "../components/UiNotice.vue";

interface Row {
  label: string;
  cmd: string;
  note: string;
  isNew: boolean;
}

const GROUPS: { title: string; rows: Row[] }[] = [
  {
    title: "服务器条目卡",
    rows: [
      { label: "添加服务器", cmd: "/mc add 名称 IP [端口]", note: "追加进 servers 数组；未填端口则不写 api_port，视为未装模组", isNew: false },
      { label: "批量添加", cmd: "/mc batchadd 名:IP[:端口],…", note: "逐条校验：失败项各自计入失败列表，其余照常写入", isNew: false },
      { label: "批量删除", cmd: "/mc batchremove 名,名", note: "一次删多台，不逐台弹确认", isNew: false },
      { label: "编辑 · 名称", cmd: "/mc edit 名称 name 新名", note: "名称不能含方括号：/执行 用 [服务器名] 定界", isNew: false },
      { label: "编辑 · 主机地址", cmd: "/mc edit 名称 host 新地址", note: "一条地址字段；含 : 会被现行校验拒绝（utils/host_validator.py:52）", isNew: false },
      { label: "编辑 · 模组端口", cmd: "/mc edit 名称 port 端口", note: "清空并保存＝删掉 api_port，该服退回「未装模组」", isNew: false },
      { label: "编辑 · 备注", cmd: "/mc edit 名称 remark 备注", note: "显示在 /在线 卡片服务器名下方", isNew: false },
      { label: "排序 ↑ ↓", cmd: "/mc move 名称 序号 · /mc swap 名1 名2", note: "序号从 0 开始；只改群内列表顺序", isNew: false },
      { label: "写 / 清除 Token", cmd: "/mc token 名称 Token（或 清除）", note: "密钥：页面不回显内值，长度门 16-128 字符", isNew: false },
      { label: "删除服务器", cmd: "/mc remove 名称", note: "只删条目；绑定记录不受影响", isNew: false },
    ],
  },
  {
    title: "本群开关卡",
    rows: [
      { label: "上次在线 显示开关", cmd: "/上次在线 开 | 关", note: "写同一份群文件，群内下一条消息即生效", isNew: false },
      { label: "昵称检测 开关", cmd: "/检测昵称 开 | 关", note: "英文别名 /mc nicknamecheck", isNew: false },
      { label: "忽略名单 添加 / 移除", cmd: "/检测昵称 忽略 QQ · 取消忽略 QQ", note: "查看名单：/检测昵称 忽略列表", isNew: false },
    ],
  },
  {
    title: "文件操作",
    rows: [
      { label: "查看 JSON（本群文件）", cmd: "只读，不发命令", note: "展示文件原文；console_token 以占位符显示，不回显明文", isNew: false },
      { label: "新增群", cmd: "— 无对应命令", note: "现在群文件由群内首次 /mc add 自动创建；页面把它显式化", isNew: true },
      { label: "删除群", cmd: "— 无对应命令", note: "删掉 servers_<群号>.json：本群 /在线 /绑定 /查询 全部失效，不会自动恢复", isNew: true },
    ],
  },
];

const TOTAL = GROUPS.reduce((n, g) => n + g.rows.length, 0);
const NEW = GROUPS.reduce((n, g) => n + g.rows.filter((r) => r.isNew).length, 0);
</script>

<template>
  <div class="page">
    <section class="ph">
      <div class="ph-t">
        <h1>命令对照</h1>
        <p>页面上每颗控件对应哪条现成命令 · {{ NEW }} 项标为页面新能力</p>
      </div>
      <span class="badge"><UiIcon name="lock" :size="12" />只映射，不新增语义</span>
    </section>

    <UiNotice tone="info">
      除最后两项外，每一项都能回到现有 QQ 命令：结果写进同一份 servers_&lt;群号&gt;.json。
    </UiNotice>

    <section class="card">
      <div class="th">
        <div class="cols">
          <span class="c-m1">页面控件</span><span class="c-m2 mono-th">等价命令</span><span class="c-m3">落点与约束</span>
        </div>
      </div>

      <template v-for="grp in GROUPS" :key="grp.title">
        <div class="band"><span class="bar" /><b>{{ grp.title }}</b><i>{{ grp.rows.length }} 项</i></div>
        <div v-for="r in grp.rows" :key="r.label" class="row dense" :class="{ isNew: r.isNew }">
          <div class="cols">
            <span class="c-m1">
              <span class="t-title">{{ r.label }}</span>
              <span v-if="r.isNew" class="tag-new">页面新能力</span>
            </span>
            <span class="c-m2 cmd ell">{{ r.cmd }}</span>
            <span class="c-m3 t-sub ell">{{ r.note }}</span>
          </div>
        </div>
      </template>

      <div class="tf">
        <span>共 {{ TOTAL }} 行 · 其中 {{ NEW }} 行标为页面新能力</span>
        <span class="tip">开关类控件在页内点按与群内发命令写的是同一个字段</span>
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

.badge {
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

.mono-th {
  font-family: var(--fm);
}

.c-m1 {
  display: flex;
  align-items: center;
  gap: 6px;
}

.c-m1 .t-title {
  font-weight: 400;
}

.tag-new {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border: 1px solid var(--warn-badge-bd);
  border-radius: var(--r-badge);
  background: var(--warn-badge-bg);
  color: var(--warn);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}

.cmd {
  font-size: 12px;
  font-family: var(--fm);
  color: var(--ac);
}

.row.isNew {
  background: var(--warn-bg);
}
</style>
