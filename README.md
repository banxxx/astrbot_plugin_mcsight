# MCSight

**Minecraft 多服务器 × QQ 群助手** for [AstrBot](https://github.com/AstrBotDevs/AstrBot)

> 一台机器人管多台 MC 服：在线状态出图、玩家统计出图、TPS、游戏内广播，以及 QQ ↔ 游戏ID 的绑定 / 解绑 / 核对。

配套模组 **QQBindMod**（Fabric / Forge / NeoForge 三端）提供 HTTP API，本插件优先走模组，模组缺席时自动回落 mcstatus。

---

## ✨ 功能特性

- 🖼️ **状态卡片**：`/在线` 一次并发查询全部服务器，Pillow 实时出图（头像、备注、延迟、版本、上次在线），超高度自动分页。
- 📊 **玩家统计**：`/查询 <玩家名>` 出图，展示游玩时长与探索 / 建筑 / 工业 / 战斗 / 社交 / 养老六个维度的强度对比（环形图 + 雷达图 + 明细），窗口为最近 7 天。
- 💓 **TPS 与广播**：`/tps` 逐服 TPS 图；`/广播` 一条消息同时下发到多台服务器。
- 🧭 **命令执行**：`/执行 [服务器名] <命令>` 通过模组 API 下发控制台命令并回显输出；Token 按服务器单独配置（`/mc token`），没配的服务器不启用。
- 🔗 **账号绑定**：游戏内 6 位绑定码 + 群昵称校验，把 QQ 与游戏ID 绑定；支持中心数据库（跨服共享）与单机模组两种模式。
- 🧩 **失败分类**：连不上 / 响应超时 / 模组过旧 / 模组拒绝 / 该服没有这条记录——分别给不同的说法，不把「服务器没回话」说成「没这个玩家」。
- 🛡️ **防刷与自我保护**：命令防抖、死服熔断、绑定失败预算、跨境库保活与 SQL 超时预算。

---

## 📸 效果预览

发送 `/在线`（或 `/mc status`），机器人回复一张状态卡片：

![预览效果](https://cos.poso.cc.cd/PixPin_2026-05-10_08-08-22.png)

> 图片会随版本迭代变化，实际样式以你部署后的输出为准；命令清单见 `/帮助`。

---

## 📦 安装

### 1. 放置插件

```
AstrBot/data/plugins/astrbot_plugin_mcsight/
```

### 2. 安装依赖

- 方式一：重启 AstrBot 后，在 WebUI「插件管理」中点击「安装依赖」。
- 方式二：在 AstrBot 的 Python 环境中执行：

  ```bash
  pip install -r requirements.txt
  ```

  实际用到的依赖：`aiohttp`（HTTP 与连接池）、`Pillow`（出图）、`mcstatus`（未装模组服务器的回落查询）、`pymysql` + `DBUtils`（中心数据库）。`requirements.txt` 里还列着 `asyncmy` / `aiomysql` / `pilmoji`，当前代码未引用它们。

### 3. 字体

| 用途 | 位置 | 说明 |
|------|------|------|
| 中文正文 | `resources/fonts/msyh.ttf` | 找不到则依次回落系统 `msyh.ttc` / `msyh.ttf` / `PingFang.ttc` / `wqy-microhei.ttc` |
| Emoji 图标 | `resources/emoji/<码位>.png` | 只带统计图用到的三个（🗡 💀 ）；其余 emoji 在图片里不显示，也不占位 |

### 4. 配套模组（推荐）

绑定、TPS、玩家统计、广播这几项能力依赖 MC 侧的 **QQBindMod**。在每台服务器装好模组并开放 API 端口后，把端口写进服务器条目（`api_port`）即视为「已装模组」。

- 未装模组的服务器仍然可以出现在列表里，`/在线` 会用 mcstatus 查询它，但 TPS / 统计 / 广播会跳过它。
- 下载地址见模组仓库 README。

### 5. 启用

在 AstrBot WebUI 中重载 / 启用插件，发送 `/帮助` 验证。

---

## ⚙️ 配置

配置在 AstrBot WebUI 的插件配置面板中修改（落盘为 `data/config/astrbot_plugin_mcsight_config.json`）。

### 权限与显示

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `enable_group_owner` | bool | `true` | 群主是否可用管理功能 |
| `enable_group_admin` | bool | `true` | 群管理员是否可用管理功能 |
| `show_server_version` | bool | `false` | 状态卡片是否显示服务器版本 |
| `show_server_latency` | bool | `false` | 状态卡片是否显示延迟 |
| `super_admins` | list | `[]` | 插件超级管理员（QQ 号字符串），可管理白名单、可添加内网地址 |
| `whitelist_admins` | list | `[]` | 白名单管理员，权限等同群管理员 |
| `blacklist` | list | `[]` | 黑名单：所有命令一律拒绝 |
| `default_group_id` | string | `""` | 仅当事件里取不到群号时兜底使用，通常留空 |

### 模组 API

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `default_api_port` | int | `25566` | 服务器条目未写 `api_port` 时使用的全局端口 |
| `mod_api_token` | string | `""` | 与模组配置里的 API Token **完全一致**，否则一律鉴权失败 |

### 中心数据库（可选）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `use_central_db` | bool | `false` | 开启后绑定数据写入中心库并跨服共享；关闭则各服独立走模组 API |
| `db_host` / `db_port` / `db_user` / `db_password` / `db_name` | - | - / `4000` / - / - / `qqbind_db` | MySQL / TiDB 连接参数（TiDB Serverless 需要 SSL，插件自带 `isrgrootx1.pem`） |

**两种模式的区别**

- 本地模式（默认）：绑定写入「验证到绑定码的那台服务器」的模组数据库，服务器之间互不相通。`/绑定检测` 不可用。
- 中心模式：绑定写入 `bindings (group_id, game_id, qq)` 表，写入成功后插件把结果**异步下行同步**到各服务器的模组；某台同步失败不影响绑定结果，会在回复里点名是哪台、为什么（模组过旧 / 暂不可达），玩家重新进服时自动生效。

---

## 🚀 命令一览

中文命令直接发送即可，括号内为英文写法或别名。`-s 服务器名` 是通用的「指定单台服务器」参数；`/执行` 例外，它必须写成 `[服务器名]` 紧跟命令前，省略即拒绝——因为一条打错服的命令比多打两个字符危险得多。

### 状态查询（所有人可用）

| 命令 | 英文 / 别名 | 说明 |
|------|-------------|------|
| `/在线` | `/online`、`/mc status` | 查询全部服务器在线情况并出图 |
| `/查询 <玩家名> [服务器名]` | `/mc stats` | 玩家数据统计图；缺省按列表顺序找第一台有记录的服务器 |
| `/tps [-s 服务器名]` | `/mc tps` | TPS 图，不带参数查全部 |
| `/广播 [服务器名] <消息>` | `/mc say [-s 服务器名] <消息>` | 游戏内广播；省略服务器名则发到全部启用模组 API 的服务器，最长 40 字符 |

### 账号绑定（详见 `/绑定帮助`）

| 命令 | 英文 / 别名 | 权限 |
|------|-------------|------|
| `/绑定 <6位绑定码>` | `/bind` | 所有人 |
| `/解绑 [游戏ID]` | `/unbind` | 所有人（仅限自己的账号） |
| `/解绑 ID <游戏ID>` / `/解绑 QQ <QQ号>` | - | 管理员 |
| `/查绑定 [游戏ID]` / `/查绑定 QQ <QQ号>` | `/绑定状态`、`/checkbind`、`/mc check` | 所有人 |
| `/绑定检测` | `/mc bindcheck` | 管理员，且需开启中心库 |

绑定流程三步：

1. 进入服务器，屏幕中央（Title / ActionBar）显示 **6 位绑定码**，5 分钟有效。
2. 把群昵称改成 `玩家（游戏ID）` 格式，例如 `张三（zhangsan）`——括号内必须与登录的游戏ID 一致。
3. 在本群发送 `/绑定 123456`。

> 60 秒内连续 5 次绑定码无效会进入冷却，提示里给出剩余秒数；被模组限流时不会算在你头上，提示会说明「码没失效，不用重新登录」。

### 服务器管理（群主 / 群管理员 / 白名单管理员 / 超管）

| 命令 | 说明 |
|------|------|
| `/mc add <名称> <IP> [端口]` | 添加服务器；端口即模组 API 端口，缺省用全局值 |
| `/mc remove <名称>` | 删除服务器 |
| `/mc edit <名称> name\|host\|port\|remark <值>` | 改名 / 改地址 / 改端口 / 改备注（备注显示在卡片名下方） |
| `/mc batchadd 名1:ip1[:端口],名2:ip2` | 批量添加 |
| `/mc batchremove 名1,名2` | 批量删除 |
| `/mc list` | 列出服务器及各自的 API 端口 |
| `/mc move <名称> <序号>` | 调整顺序（序号从 0 开始） |
| `/mc swap <名1> <名2>` | 交换两台位置 |
| `/上次在线 开\|关` | `/mc lastonline`，卡片是否显示最后活动时间 |
| `/检测昵称` | `/mc checknick`，列出群内昵称不符合「玩家（游戏ID）」的成员 |
| `/检测昵称 开\|关\|忽略 <QQ>\|取消忽略 <QQ>\|忽略列表` | `/mc nicknamecheck` |
| `/执行 [服务器名] <命令...>` | `/mc exec`，在指定服务器执行控制台命令并回显输出；需先用 `/mc token` 为该服配置命令 Token |
| `/mc token <名称> <Token\|清除>` | 超管：为该服务器单独设置命令 Token（与模组 `consoleToken` 一致）；清除即关闭该服的 `/执行` |

### 帮助与高级

| 命令 | 权限 | 说明 |
|------|------|------|
| `/帮助` | 所有人 | 通用命令帮助图（`/mc help`） |
| `/绑定帮助` | 所有人 | 绑定专用帮助图，含完整流程与常见问题（`/bindhelp`） |
| `/mc whitelist list` | 超管 | 查看白名单配置；增删请前往 WebUI 配置面板 |

---

## 🔐 权限模型

从低到高：`黑名单(0)` → `普通成员(1)` → `白名单管理员(2)` → `群主/群管理员(3)` → `超级管理员(4)`。

- 黑名单在最外层统一拦截，任何命令都不执行。
- AstrBot 的全局管理员自动等同插件超级管理员，且不受防抖限制，方便连测。
- 内网 / 回环地址只允许超级管理员添加，避免群管理员借机器人探测内网。
- 命令 Token 按服务器单独存在该服的条目里（`/mc token`，超管），没有全局默认值：某台没配就只有那台不启用 `/执行`，改错或泄露也只波及那一台。

### 频控

| 档位 | 窗口 | 覆盖命令 |
|------|------|----------|
| 重 | 5 秒 | `status` `stats` `tps` `checknick` `bindcheck` |
| 中 | 3 秒 | `bind` `unbind` `check` `say` `help` `bindhelp` `exec` `token` |
| 轻 | 1 秒 | 其余全部 |

按「会话 + 用户 + 子命令」计数，首次被拒会回一句「请 X 秒后再试」，连点不会重复回话。

---

## 🗂️ 数据与文件

运行期数据一律落在 AstrBot 的数据目录下，**不在插件目录里**：WebUI 点「更新」时 AstrBot 会删掉整个插件目录再按仓库内容重装，留在里面的东西会一起没了。下表路径以 `AstrBot/data/plugin_data/astrbot_plugin_mcsight/` 为前缀：

| 路径 | 内容 |
|------|------|
| `group_configs/servers_<群号>.json` | 每个群一份服务器列表（私聊用会话 ID 命名） |
| `stats_snapshots.sqlite3` | 玩家统计每日快照，保留 30 天 |
| `avatar_cache/` | 头像磁盘缓存，过期 7 天，另有一层内存缓存 |
| `help_cache/` | `/帮助`、`/绑定帮助` 渲染出的 PNG；删掉文件即重新渲染 |

| 路径 | 内容 |
|------|------|
| `AstrBot/data/config/astrbot_plugin_mcsight_config.json` | 插件配置（WebUI 面板写入） |
| `resources/`（插件目录内） | 字体与默认皮肤，随仓库一起分发 |

服务器条目格式：

```json
{
  "servers": [
    {"name": "生存服", "host": "play.example.com", "api_port": 25566, "remark": "主世界"},
    {"name": "空岛服", "host": "sky.example.com"}
  ],
  "last_online_enabled": false,
  "nickname_check_enabled": false,
  "nickname_check_ignore_ids": []
}
```

| 字段 | 说明 |
|------|------|
| `name` | 服务器名，命令里用它定位（`/执行` 写成 `[服务器名]`，`/mc say`、`/mc stats` 用 `-s 服务器名`），重名会添加失败；名称不能含 `[ ] 【 】` |
| `host` | 主机地址。**命令层只接受 IPv4 或域名**（不支持 IPv6，不接受 `IP:端口`）；直接编辑 JSON 时可写 `host:游戏端口`，只影响 mcstatus 回落与延迟测量 |
| `api_port` | 模组 HTTP API 端口。**有这一个字段即视为装了模组**；缺省继承全局 `default_api_port` |
| `remark` | 备注，显示在 `/在线` 卡片名字下方 |
| `console_token` | 该服的命令 Token，用 `/mc token` 写入；**没有这个字段就是 `/执行` 对该服未启用**，与模组配置里的 `consoleToken` 逐字一致 |

- 每个群最多 20 台服务器。
- 内网 / 回环地址与 `.localhost` / `.local` / `.internal` 一类内网主机名默认拒绝，仅超级管理员可添加（SSRF 防护）。

---

## 🧠 数据来源与稳定性

- **`/在线`**：配了 `api_port` 的服务器走模组 `/api/status`（含 UUID、是否正版、最后活动时间）；请求失败或未配端口的回落 mcstatus SLP。延迟用裸 TCP 建连计时，与状态采集并发，不排在关键路径上。
- **`/查询`**：走模组 `/api/stats/<玩家>`，在线玩家实时取，离线玩家由模组读磁盘记录；玩家名限 1–16 位字母、数字、下划线。7 天窗口读的是本地快照：每次查询落一份当日累计，另有每日 04:37 的定时采样补齐没人查询的日子，所以**要连续运行满 7 天才会出现完整的本期增量**；拿不到 7 天前的基线时自动改画生涯累计，并在图上标注原因。
- **模组请求层**：全局复用连接池；建连预算 0.5 秒、单次请求总预算 5 秒；同一 `ip:port` 连续 3 次「连不上」类失败后进入 60 秒熔断冷却（只对建连层失败计数，在线但报错的服务器不会被熔断）。原始异常与内网地址只写日志，不外泄到群消息。
- **中心库**：连接池 + 30 秒保活（失败按 10/30/120 秒退避），单条 SQL 5 秒超时预算。
- **图片发送**：绘制与 PNG 编码在线程池执行，多页逐页直发并留间隔，发完才删临时文件。

---

## ⚠️ 已知限制

- TPS 取自模组上报的服务器 tick 数据；模组在个别版本上读不到 tick 时会退回固定值 20，此时 `/tps` 图上的 20 不代表真实负载（可与 `/在线` 的延迟、`/查询` 的活跃度交叉判断）。
- `/绑定检测`、`/检测昵称` 需要读取群成员名单，依赖 OneBot 侧的 `get_group_member_list`；官方 QQ 机器人通道无成员列表能力时不可用。
- `/绑定` 要求插件能取到发送者的群昵称，因此必须在群聊中使用。
- 帮助图为缓存产物；改动配置后如未刷新，重载插件即可。

---

## 🏗️ 项目结构

```
astrbot_plugin_mcsight/
├── main.py                        # 命令注册：/mc 入口 + 中文别名 + 正则别名
├── _conf_schema.json              # WebUI 配置面板 schema
├── metadata.yaml / requirements.txt
├── commands/
│   ├── mc_handler.py              # 唯一分发入口：黑名单门 → 防抖 → 权限 → 路由
│   ├── console.py                 # /执行 [服务器名]：单台服务器的控制台命令与输出回显
│   ├── management.py              # add/remove/edit/batch/list/move/swap/lastonline/token
│   ├── services.py                # say(广播) / tps / status / stats
│   ├── bind.py                    # 绑定 / 解绑 / 查绑定，含下行同步
│   ├── bind_check.py              # /绑定检测 未绑定成员名单图
│   ├── nickname_check.py          # 昵称检测与忽略列表
│   ├── help_whitelist.py          # 帮助图 / 白名单查看
│   └── common.py                  # 数据库连接池、保活、SQL 执行
├── config/
│   ├── server_config.py           # 每群服务器列表读写
│   └── whitelist_config.py        # 白名单 / Token / 端口 / 中心库配置
├── features/
│   ├── player_status/
│   │   ├── controller.py          # /在线、/查询 流程与失败分类文案
│   │   ├── checker.py             # 模组 API 与 mcstatus 查询、ping、广播、TPS
│   │   ├── image_generator.py     # 状态卡片
│   │   ├── player_stats_generator.py  # 玩家统计图（环形图 / 雷达图 / 明细）
│   │   └── tps_image_generator.py # TPS 图
│   ├── help_image/                # 通用帮助图、绑定帮助图
│   └── bindcheck_image/           # 未绑定成员名单图
├── utils/
│   ├── plugin_paths.py            # 运行期数据落点（AstrBot 的 plugin_data/<插件名>）
│   ├── mod_http.py                # 模组 HTTP 统一层：超时、熔断、失败分类文案
│   ├── permission.py              # 权限等级与命令门槛
│   ├── debounce.py                # 命令防抖
│   ├── bind_guard.py              # 每用户绑定失败预算
│   ├── bind_route.py              # 绑定码 → 服务器 的短期路由缓存
│   ├── stats_sampler.py           # 每日统计采样
│   ├── stats_snapshot.py          # 本地 sqlite 快照
│   ├── avatar_cache.py            # 头像下载与两级缓存
│   ├── group_members.py           # 群成员名单（get_group_member_list）
│   ├── host_validator.py          # 地址合法性与内网限制
│   ├── text_renderer.py           # Emoji 混排渲染
│   └── temp_image.py              # 临时图片管理
└── resources/{fonts,images}/
```

---

## 🔮 后续计划

- [x] 服务器版本 / 延迟 / 备注显示
- [x] 玩家统计图与 7 天窗口
- [x] QQ ↔ 游戏ID 绑定（中心库跨服共享）
- [x] 游戏内广播、TPS 查询
- [x] 群内未绑定成员 / 昵称规范检测
- [ ] 玩家上下线主动通知
- [ ] 定时自动推送状态
- [ ] Bedrock 版服务器支持

欢迎提交 Issue 或 PR。

---

## 🔖 版本与发版

版本号有两个写法，数字必须一致：`metadata.yaml` 的 `version` **不带 `v`**（形如 `1.1.2`），GitHub 的 tag 与 Release **带 `v`**（`v1.1.2`）。WebUI 插件页显示的是前者。

发到插件市场（[AstrBot Cloud 发布页](https://cloud.astrbot.app/publish)）后，市场索引里的版本号会被规范化成不带 `v` 的形式；而「有新版本」标记是拿本地 `metadata.yaml` 的 `version` 和索引里的值**直接比字符串**、不做归一化。所以 `version` 一旦写成 `v1.1.2`，本地与市场就对不上，标记会一直挂着，点「更新」也灭不掉。

WebUI「插件管理」里的**更新**按钮按 `metadata.yaml` 的 `repo` 取包，取哪一份取决于写法：

| `repo` 写法 | 实际下载 |
|------|------|
| 不带 `/tree/<分支>`（本仓库当前用法） | 仓库**最新的 GitHub Release** 源码包；一个 Release 都没有才回落默认分支 |
| 带 `/tree/<分支>` | 该分支的最新提交，完全不看 Release |

发版四步，前三步都在仓库目录里执行：

1. 改 `metadata.yaml` 的 `version`（不带 `v`），在 `CHANGELOG.md` 顶部加一段，一起提交并推送；
2. 打 tag 并推上去：`git tag v1.1.2` → `git push origin v1.1.2`；
3. 建 Release：`gh release create v1.1.2 --generate-notes --title v1.1.2`；
4. 到 AstrBot Cloud 发布页发布同一个版本号，市场索引才会跟着更新（平台不会自己去扫你的 tag）。

**只做前两步是不够的**：少了 Release，点「更新」拿到的还是上一个 Release 的代码，不是刚推的提交。**少了第四步**：市场里挂的还是旧版本号，已安装用户看不到新版本。

发布压缩包（zip）大小不得超过 16MB，超了平台会直接拒收；仓库里的字体是主要体积来源，加图片前留意余量。

`CHANGELOG.md` 必须进仓库——WebUI 的「更新日志」读的是插件目录里的这个文件。更新会整包重装插件目录，运行期数据不受影响（见「数据与文件」）。

---

## 🤝 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件框架
- [mcstatus](https://github.com/py-mine/mcstatus) Minecraft 查询库
- [Pillow](https://python-pillow.org/) 图像绘制
- [Minotar](https://minotar.net/) / [Crafthead](https://crafthead.net/) 头像 API
- 配套模组 QQBindMod（Fabric / Forge / NeoForge）
