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

**前置要求**：AstrBot **4.23 及以上**（WebUI 配置页需要 **4.24 及以上**——更早内核没有 `astrbot.api.web`，接口不注册，聊天侧命令不受影响）；平台支持 QQ 群侧三种适配器：`aiocqhttp`（OneBot v11 / NapCat 等）、`qq_official`、`qq_official_webhook`，其它平台未做适配。同样的口径写在 `metadata.yaml` 的 `astrbot_version` 与 `support_platforms` 两个字段里。

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
- QQ 官方模式下，旧接口仍只开放 `/api/status`、`/api/tps`、`/api/stats*` 等只读入口；新版模组额外提供 `POST /api/v2/binding/settings/whitelist`，配置页的「登录限制」可在线切换，立即生效、写回模组配置并重新校验在线玩家，无需重启服务器。旧版模组没有该接口时页面自动保持只读。官方绑定、控制台与广播继续走 `/api/v2/binding/*`，不受影响。
- 官方模式要求模组 `apiToken` 至少 16 位可见 ASCII 字符。旧版官方环境里的 `qq_official/<环境UUID>/mod_token.json` 不再读取；升级前请把该值填入「公共参数 → `mod_api_token`」，并让所有 OneBot 模组的 `apiToken` 与它一致。
- 下载地址见模组仓库 README。

### 5. 启用

在 AstrBot WebUI 中重载 / 启用插件，发送 `/帮助` 验证。

---

## ⚙️ 配置

配置在 AstrBot WebUI 的插件配置面板中修改（落盘为 `data/config/astrbot_plugin_mcwatcher_config.json`）。「全局参数」只保留两个平台共用的**公共参数**：模组 Token、权限名单与中心数据库连接；群主/管理员权限、卡片显示、OneBot 与 QQ 官方中心库开关都在「群与服务器」中按群设置。

### 生效机器人与完整重启

插件配置页 → **全局参数 → 生效机器人**，整组选择平台、具体实例和官方应用环境。

- 首次 OneBot 只有一个候选时，等待框架加载完成后自动保存明确的实例 ID；多个候选时需要手动选择。旧框架缺少加载完成钩子时不猜测唯一实例，可先明确填写首次初始化选项 `active_bot_instance`。
- 已保存的运行选择跨重启保留。实例缺失或适配器停止时暂停业务，不自动启用另一台机器人；“适配器运行”也不代表已验证 QQ 在线。
- 保存新选择只准备下次启动目标。**完整重启 AstrBot 后才应用，插件热重载仍使用原运行选择。** 页面同时显示当前运行选择与下次完整启动目标。
- 官方选择需要用户核对 AppID 与环境归属；AppID 不变、仅实例更名可以保留环境，更换应用须使用其它环境。不要在 AppID 字段填写 AppSecret。
- 官方群登记、查询、绑定、解绑、广播与管理已有独立入口；绑定按权威配置和每次模组能力握手开放，控制台另需本服独立令牌。源码存在不代表目标环境已经就绪。
- 选择记录独立存储在 `data/plugin_data/astrbot_plugin_mcsight/activation.json`；旧 `active_platform`、`active_bot_instance` 仅用于首次兼容初始化。已有记录后，用专门的选择区域保存，不能靠修改旧字段覆盖它。
- 记录损坏时暂停并保留原文件；并发编辑冲突要求刷新，不自动覆盖另一页的选择。正式切换前安排维护窗口并核对在途操作。

### QQ 官方群：登记、查询与管理

1. 在“全局参数 → 生效机器人”选定官方实例与应用环境，维护窗口完整重启 AstrBot。
2. 打开插件页面的 **QQ 官方群 → 新增群**，填写群别名（可选展示群号），点「生成验证码」。验证码只显示一次，5 分钟内有效。
3. 在目标官方群发送 `/mc setup 验证码`。验证码一次性消费，消费后立即创建本群配置（默认未启用），弹窗会自动检测并关闭。
4. 添加服务器主机、游戏端口和可选模组 API 端口。内网地址由后台管理员自行核对；独立配置不会自动导入旧 OneBot 群或绑定。
5. 在“全局参数 → 公共参数”配置 `mod_api_token`，与每台服务器模组里的 apiToken 完全一致；OneBot 与 QQ 官方共用。未装模组的服务器仍可用 mcstatus 查询在线状态。
6. 启用本群功能后可使用 `/mc status [-s 名称]`、`/mc tps [-s 名称]` 或 `/mc tps 名称`、`/mc stats 玩家名 [-s 名称]` 或 `/mc stats 玩家名 名称`、`/mc rank [类型] [总|周|月] -s 名称 [-n 1–50]`。选服语法与 OneBot 一致，返回的也是同一套图片。公开统计不要求调用者先绑定；官方统计使用本环境独立的 30 日历史并复用六类统计图，历史积累足够时显示 7 日对比。官方本人排行按当前权威的有效账号关联，不会从旧 OneBot 绑定表推断身份。

一轮只展示第一页，其它页发送 `/mc page <页码>`，缓存限本人、群、应用环境及配置修订，10 分钟失效。统计图完整单页输出；图片能力不可用或超过安全大小时返回分页文字。固定 AstrBot 4.29.1 事件族的本地图片载荷已核对公开源码，未知事件族继续文字降级。不会为发送图片把玩家数据上传到私人图床。合成测试覆盖本插件的单次 HTTP 调用与超时不重发；QQ 图片权限、限额及 WebSocket/Webhook 实际收发仍需用户环境验收。

新增群验证码5分钟有效、用过即失效，同一验证码不能再次创建。创建配置不自动授予管理员权限；原生角色只取author.member_role，未知角色不提权，群昵称缺失不拿username代替。群主/管理员角色默认可作为管理命令使用者，黑名单优先；退群、被踢或聊天黑名单不自动游戏解绑。昵称检测忽略名单在群内使用 `/mc nicknamecheck 忽略 @成员` 管理。超管可在官方群内用 `/mc whitelist add|remove @成员`、`/mc whitelist super add|remove @成员`、`/mc blacklist add|remove @成员` 维护本环境的补充名单：只接受 @ 唯一一位成员，不要求也不回显 openid，写入 `qq_official/<环境>/config.sqlite3` 的 `environment_access` 表，不修改 OneBot 共用的公共名单；成员标识按群匹配，换群后需重新添加，`/mc whitelist list` 只显示脱敏标识。`/mc help`、`/mc bindhelp` 使用官方独立帮助图，说明官方登记、绑定与未决操作流程，不沿用OneBot QQ号或旧码；无图片能力时回退官方文字。

官方群还可发送 `/命令指引` 或 `/管理指引` 获得 Markdown + 自定义按钮菜单，不必把全部命令塞进 QQ 群命令面板（平台上限 10 项）；也可用等价写法 `/mc guide [1|2]`、`/mc 命令指引 [1|2]`。这两个中文命令可直接填进 QQ 开放平台的机器人指令配置。第 1 页为 2 列 5 行常用查询与帮助入口；第 2 页为 3 列 4 行管理命令，仅群管理员（级别 ≥3）可看，发送 `/管理指引` 或 `/mc guide 2` 进入，不再额外放“管理指引”按钮。服务器列表、操作查询、控制台、操作回执、令牌与权限名单等不放入按钮，仍可直接输入对应命令。按钮属于“填入命令”型（`action.type=2`）：在群聊点击只会把 `@机器人 /mc …` 填入输入框，仍要用户自己点发送，官方群不支持按钮点击后直接执行；`/mc add` 等带参数命令需继续补充参数。Markdown 自定义内容一般可直接使用；自定义按钮目前属于内邀开通 / 按钮模版能力，客户端版本和应用权限仍可能影响显示。平台明确拒绝时插件回退为纯文本命令清单，发送结果未知或进入审核时不重复补发。该功能只存在于官方运行时，OneBot 命令与交互不受影响。

本轮补齐以下源码入口（2026-11-20，尚未测试）：

| 功能 | 官方用法与边界 |
|---|---|
| 最后在线 | `/mc lastonline`查看、`开/关`修改；状态使用模组最后活动字段，无数据明确提示；后台也可设置 |
| 批量与排序 | `batchadd 名称:主机[:API端口],…`、`batchremove 名1,名2`，整批校验后原子修改；`move 名称 位置`从0开始，`swap 名1 名2` |
| 服务器维护 | `/mc list` 显示主机、模组端口与隐藏标记；`/mc hide 名称`、`/mc show 名称` 切换是否参与公开查询；`/mc del 名称` 等同 `/mc remove` |
| 广播与控制台 | `/mc say [名称] 消息`（也可 `-s`）、`/mc console -s 名称 命令`（也可 `[名称]`）；`/mc token 名称 <Token\|清除>` 管理单服控制台 Token |
| 白名单 | `/mc whitelist list` 查看脱敏配置；`/mc whitelist add\|remove @成员`、`/mc whitelist super add\|remove @成员`、`/mc blacklist add\|remove @成员` 由超管按 @成员 维护本环境补充名单；公共名单仍在 WebUI 配置面板操作 |
| 成员定位 | 管理员`check @成员`或`check user 官方标识`查看他人完整关联；`unbind @成员`撤销目标账号；不把数字QQ当OpenID |
| 群绑定检测 | `/mc bindcheck -s 名称`，需要QQ内邀成员接口权限；只查当前中心或所选local权威，不读取旧绑定 |
| 群昵称检测 | `/mc checknick`；`nicknamecheck 开/关/忽略 @成员/取消忽略 @成员/忽略列表`；核验近5分钟插件收到的群消息作者或@目标上的可靠群昵称，其余标无法核验 |
| 后台策略 | 最后在线、昵称检测、绑定昵称要求/规则/模板；缺可靠昵称不自动关闭要求 |
| 命令指引 | `/命令指引`（常用）/ `/管理指引`（管理）：发送官方 Markdown + 自定义按钮菜单，绕开群命令面板 10 项限制，可直接填进 QQ 开放平台的机器人指令配置；等价写法 `/mc guide 1\|2`、`/mc 命令指引 1\|2`。按钮点击只填入 `@机器人 /mc …`，仍需用户发送；第 1 页公开、2 列 5 行，第 2 页限管理员、3 列 4 行；敏感或低频命令不放入按钮 |

成员接口无权时明确提示，不冒充完整名单；扫描有时间/容量边界和5分钟缓存。群消息事件能给当前作者及mentions目标的member_nickname；未在近期插件处理事件中发言或被提及者仍无法核验。成员列表API的username不当作全量群名片。管理员分页按新事件当前权限复核，撤权后旧管理页失效。

官方环境的群配置、授权、模组令牌和事件防重记录保存在 `data/plugin_data/astrbot_plugin_mcsight/qq_official/<环境 UUID>/`，只由插件运行和用户后台使用。事件防重保留 7 天、每环境最多 4096 条；满时停止接收新请求，避免淘汰未过窗口的记录后重复回复。存储故障不自动覆盖原记录。真实消息收发、框架入口日志与应用权限仍需用户在自己的环境验收；助手的测试只使用合成数据。

既有七类业务已接通：原生管理员、本人新事件恢复、广播/控制台防重、本人排行、remote/local和维护交付。2026-11-19-complete-development为前包构建，**不含2026-11-20的协议、断库预占和成员检测增量**；本轮依用户要求不运行测试、类型检查或四模块构建，仅同步前端发布资源。后续需集中必要验证并更新四Jar，再按[交付说明](docs/QQ_OFFICIAL_DELIVERY.md)由用户配置、建表、备份、维护停启和实机验收；真实值不要发给助手。自动化保持暂停。

提交 Git 时保留正式源码、`views/config/` 发布资源、`sql/`、`examples/`、交付/协议说明和核心回归测试。`.scratch/`、构建缓存、一次性图片/夹具生成脚本、本机专用执行器和开发过程记录已忽略。`tests/` 与离线 Vue 回归保留在仓库，通过 `.gitattributes` 从发布源码归档排除；测试夹具为虚构数据，插件测试无需相邻的 QQBindMod 工作目录。
2026-11-20：F01–F05已修源码，包括统一API域名、member_role、上传file_name、err_code与异步审核分类、中心失败时本服预占查询分流；尚未测试。完整[OneBot功能对照及待验收清单](docs/QQ_OFFICIAL_TEST_CHECKLIST.md)记录已补能力和平台条件。
### 权限与显示（OneBot：群与服务器 → 本群规则）

OneBot 的群主/管理员权限和卡片显示**按群**保存，统一放在 `policy` 对象里，字段名、类型、默认值与 QQ 官方的群对象完全一致。全部群保存在同一个 SQLite 库里，按 `(scope_kind, scope_id)` 区分群聊与私聊作用域：

`data/plugin_data/astrbot_plugin_mcsight/onebot/config.sqlite3`

后台「群与服务器 → 更多群操作 → 导出群配置备份」会把整库导出成可编辑 JSON（不含命令 Token），其中每个群对应下面的群对象；修改后可整份导入。

```json
{
  "scope_kind": "group",
  "scope_id": "123456789",
  "enabled": true,
  "alias": "",
  "servers": [],
  "policy": {
    "enable_group_owner": true,
    "enable_group_admin": true,
    "bind_require_nickname": true,
    "bind_nickname_rule": "paren",
    "bind_nickname_template": "",
    "show_server_version": false,
    "show_server_latency": true,
    "show_last_online": false,
    "nickname_check_enabled": true,
    "use_central_db": false
  },
  "nickname_ignore": []
}
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `scope_kind` | string | `"group"` | `group`＝群聊，`private`＝私聊；导入备份时用于区分作用域 |
| `scope_id` | string | - | 群号或私聊会话 ID，字符串保存，不参与路径拼接 |
| `alias` | string | `""` | 群别名，仅用于后台显示 |
| `enabled` | bool | `true` | 本群功能总开关；关闭后本群所有 OneBot 命令不再响应，后台每日采样也跳过本群 |
| `policy.enable_group_owner` | bool | `true` | 本群群主是否可用管理功能 |
| `policy.enable_group_admin` | bool | `true` | 本群群管理员是否可用管理功能 |
| `policy.show_server_version` | bool | `false` | 本群状态卡片是否显示服务器版本 |
| `policy.show_server_latency` | bool | `true` | 本群状态卡片是否显示延迟 |
| `policy.show_last_online` | bool | `false` | 本群状态卡片是否显示上次在线时间 |
| `policy.nickname_check_enabled` | bool | `true` | 本群是否允许 `/检测昵称` |
| `policy.bind_require_nickname` | bool | `true` | 绑定时是否校验群名片 |
| `policy.bind_nickname_rule` | string | `"paren"` | 群名片匹配方式：`paren` / `equals` / `contains` / `template` |
| `policy.bind_nickname_template` | string | `""` | `template` 模式使用的模板，必须且只能包含一个 `{gameId}` |
| `policy.use_central_db` | bool | `false` | 本群绑定是否使用中心数据库；连接参数仍取「公共参数」 |
| `nickname_ignore` | list | `[]` | 昵称检测忽略的 QQ 号 |

旧全局群开关不再读取，也不会在 `_conf_schema.json` 中保留为隐藏兜底键。2.0.1 的 `onebot/<群号>/state.json` 与更早的 `group_configs/servers_<群号>.json` 会在升级后首次打开时一次性导入 `onebot/config.sqlite3`：`servers`、群级开关、绑定昵称规则和忽略名单都会映射到新结构；导入成功后才把原文件移到 `onebot/legacy/` 留档。同一群两种来源同时存在时，以 `onebot/<群号>/state.json` 为准。迁移完成后运行时只读 SQLite，`legacy/` 仅供核对；正式库缺失但留档仍在时插件会失败关闭，不会静默创建空库。`default_group_id` 已完全移除，群号只从当前事件获取。

切换中心库模式**不会自动迁移已有绑定**：本地切到中心后，玩家需要重新绑定或重新检测；中心切回本地后，读取的是各服模组自己的数据。切换前请先确认现有绑定数据。

### 公共参数

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `mod_api_token` | string | `""` | OneBot 与 QQ 官方共用；与模组配置里的 API Token **完全一致**，否则一律鉴权失败 |
| `super_admins` | list | `[]` | 两个平台共用。可填 QQ 号或 openid；拥有插件全部管理权限 |
| `whitelist_admins` | list | `[]` | 两个平台共用。可填 QQ 号或 openid；权限等同群主/群管理员 |
| `blacklist` | list | `[]` | 两个平台共用。可填 QQ 号或 openid；名单内成员所有命令一律拒绝 |
| `db_host` / `db_port` / `db_user` / `db_password` / `db_name` | - | - / `4000` / - / - / `qqbind_db` | 中心数据库公共连接参数（TiDB Serverless 需要 SSL，插件自带 `isrgrootx1.pem`）。OneBot 与 QQ 官方使用不同数据表，可共用同一个库 |

权限名单按平台各自的发送者 ID 精确匹配：OneBot 用 QQ 号，QQ 官方用 `member_openid`。同一份名单可以混填两种 ID，另一种平台的条目不会报错，也不会误匹配。格式校验：QQ 号必须是 5–12 位数字；openid 必须是 16–64 位英文字母或数字；中文、空格和其他符号会被拒绝。

QQ 官方群内用 `@成员` 添加的补充名单单独保存在 `qq_official/<环境UUID>/config.sqlite3` 的 `environment_access` 表，与上面的公共名单合并生效，但不会写入这里，也不影响 OneBot 的名单展示与匹配。官方 `member_openid` 按群区分，因此补充条目只在添加时的当前群匹配生效；换群后需重新 @成员 添加。群内指令只接受 @ 提及，避免用户手动粘贴或回显官方标识；`/mc whitelist list` 对两类名单统一脱敏显示。

注意：QQ 官方的 `member_openid` 通常按群区分，同一个用户在不同官方群可能是不同 openid。因此全局名单里的 openid 主要在该群生效；需要跨群管理时，请逐群添加，或使用官方环境管理员/本群管理员。OneBot 的 QQ 号则天然跨群稳定。

获取当前群的 `member_openid`：在目标官方群发送 `/mc sid`，插件只把发送者自己的标识回复给本人，并在 10 秒后自动撤回；该命令不要求本群已启用，也不要求已有超管，适合首次配置。也可以使用 AstrBot 内置指令 `/sid`，回复中的 `UID` 就是同一个 `member_openid`；内置 `/sid` 本身不会撤回。

**官方平台首次设置超级管理员**：在目标官方群发送 `/mc sid`（或 AstrBot 内置指令 `/sid`，需启用「内置指令」`builtin_commands`），回复里的成员标识（内置指令显示为 `UID`）就是该群的 `member_openid`；把这个 UID 填入插件配置的「超级管理员」（`super_admins`）并保存。刷新配置后，该账号即可使用 `/mc whitelist super add @成员` 等超管命令。AstrBot 全局管理员（`admins_id`）不会自动成为本插件的官方超管——官方权限只读取插件公共名单和环境管理员；同样，群主/群管理员只到等级 3，不能自行提权到 4。换群后要为新群重新执行 `/mc sid`（或 `/sid`）并添加对应 UID。

### QQ 官方群规则

官方群不再有全局专用开关；群主/管理员权限、卡片显示和中心数据库启用都放到「群与服务器 → QQ 官方 → 本群规则」逐群设置。

官方群「本群规则」中的字段与 OneBot 完全一致，使用同一个 `policy` 结构：启用中心数据库（本群）、允许群主使用管理功能、允许群管理员使用管理功能、显示服务器版本、显示服务器延迟、显示上次在线时间、昵称检测、绑定昵称规则。新建官方群的初始值为群主/管理员开启、版本关闭、延迟开启、昵称检测开启、中心数据库关闭，之后按群独立修改。旧版官方 `state.json` 会在环境首次打开时迁移到 `config.sqlite3`；其中 v1 文件只补齐缺失的 policy 字段，已有值保持不变，原 JSON 移到同目录 `legacy/` 留档。

升级说明：旧版全局 `official_center_enabled` / `official_center_mode` 已删除。已有官方群在首次读取时按**中心数据库关闭**处理；需要继续使用中心库的群，请在「本群规则」中打开「启用中心数据库（本群）」，并按 [`sql/qq_official_v1.sql`](sql/qq_official_v1.sql) 建表。环境级 `qq_official/<环境UUID>/center_settings.json` 仍可作为高级覆盖，设置 `storage_mode` 和按服 `server_modes`。

### 群配置存储布局与容量

运行数据根：`data/plugin_data/astrbot_plugin_mcsight/`

| 平台 | 路径 | 组织方式 |
|------|------|----------|
| OneBot | `onebot/config.sqlite3` | **整个平台一个 SQLite 库**（WAL），`groups` 按 `(scope_kind, scope_id)` 保存群与私聊作用域，`servers` 保存服务器条目，`server_tokens` 单独保存命令 Token；`meta.revision` 用于导出/导入的并发校验 |
| OneBot | `onebot/legacy/` | 旧 `onebot/<群号>/state.json` 与 `group_configs/servers_<群号>.json` 导入成功后的原文件留档，只读核对用 |
| QQ 官方 | `qq_official/<环境UUID>/config.sqlite3` | **一个环境一个 SQLite 库**（WAL），保存该环境所有群、服务器、群成员名单、候选群、环境管理员、授权审计、补充名单、验证码和备注；`meta.revision` 为环境级修订号 |
| QQ 官方 | `qq_official/<环境UUID>/console_tokens/<server_id>.json` | 每服独立的控制台命令 Token；值不进入 SQLite，也不回传 WebUI |
| QQ 官方 | `qq_official/<环境UUID>/center_settings.json` | 环境级中心库覆盖（仍为 JSON） |
| QQ 官方 | `qq_official/<环境UUID>/legacy/` | 升级时从旧 `state.json` / `access.json` / `setup_codes/` / `remarks/` 导入成功后的原文件留档，只读核对用 |

```text
data/plugin_data/astrbot_plugin_mcsight/
  onebot/
    config.sqlite3
    legacy/                 # 仅在从旧版升级时出现
      groups/
        1072429487/
          state.json
      group_configs/
        servers_1072429487.json
  qq_official/
    <环境UUID>/
      config.sqlite3
      console_tokens/
      center_settings.json
      legacy/               # 仅在从旧版升级时出现
        state.json
        access.json
        setup_codes/
        remarks/
```

两个平台的**容器不同，但都使用 SQLite**：

- 群对象的字段已经统一：`enabled`、`servers`、`policy`、`nickname_ignore`，`policy` 的字段名和默认值完全一致。
- OneBot 仍以 QQ 群号为作用域 ID，私聊用会话 ID；所有群共享一个库。服务器有稳定的内部 `server_id`，改群号、改名或导入备份时命令 Token 按 `server_id` 继续匹配。
- 所有写入都在 `BEGIN IMMEDIATE` 事务中完成：改一个服务器不再重写整群文件，导出时也不会读到写了一半的配置。官方环境的群配置、候选、管理员和授权审计同样同库提交。
- 旧版 OneBot 与官方 JSON 都在首次打开时一次性导入 SQLite；导入成功后才把原文件移到 `legacy/`。数据库损坏、版本不符或缺少正式库但留档仍在时失败关闭，不自动覆盖，也不回退读取旧 JSON。
- 命令 Token 不随普通读取或导出返回：WebUI 只显示“是否已设置”，导出的 JSON 不含 Token。OneBot 的 Token 保存在 `server_tokens` 表，官方仍按服保存在 `console_tokens/`。
- OpenID 只作为字段值保存，不参与路径拼接；群使用平台提供的作用域标识，服务器使用稳定内部 `server_id`。

容量参考（Windows、热缓存、20 台服务器/群；QQ 官方环境，`config.sqlite3` 常驻一条 WAL 连接）：

| 群数 / 服务器数 | 点读一个群 | 环境管理员 | 读取全部群+服务器（管理页） | 修改一个群 |
|---:|---:|---:|---:|---:|
| 20 / 400 | 约 0.5 ms | 约 0.2 ms | 约 1.8 ms | 约 1 ms |
| 200 / 4,000 | 约 0.5 ms | 约 0.2 ms | 约 18 ms | 约 1 ms |
| 2,000 / 40,000 | 约 0.5 ms | 约 0.2 ms | 约 210 ms | 约 1 ms |

- 群内消息只走点读（群 + 环境管理员），不加载全部群；2,000 群时单次权限检查仍约 0.5 ms。
- 管理页需要展示全部群时会线性增长；2,000 群、40,000 服约 210 ms，数据库约 12 MB。若未来接近 10,000 群上限，应优先给管理页加分页，而不是继续扩大全量响应。
- 当前上限：每环境 10,000 个群、每群 20 台服务器、候选 1,000、环境管理员 1,000、授权审计 10,000 条；OneBot 仍为每群 20 台服务器。旧版 JSON 的 8 MB 文件上限只用于兼容迁移和命令回执等独立 JSON。
- OneBot 的实际规模通常远小于官方环境（每群 20 台服务器、群数由机器人所在 QQ 群决定）；一个库中按 `(scope_kind, scope_id)` 点读本群，`/在线`、权限检查和每日采样都只取需要的行，不随群数增长。
- 后台「群与服务器 → 更多群操作」提供「导出群配置备份 / 导入群配置备份」：OneBot 备份包含全部群、服务器、policy、隐藏标记、备注与 MOTD 缓存，不含命令 Token；导入会整份替换并在单事务中提升修订号，`server_id` 保留时 Token 继续生效。官方环境对应「导出环境配置备份 / 导入环境配置备份」，备份包含成员名单、环境管理员、补充名单和备注，不含验证码、控制台 Token 与审计明细。
- `config.sqlite3` 是内部存储，不建议用 SQLite 工具直接修改；需要批量编辑或备份时用后台导出的 JSON，改完再导入。`legacy/` 只是迁移留档，改了不会生效。正式库缺失但 `legacy/` 仍有历史配置时，插件会失败关闭并提示，避免静默建空库；请先导入备份，或确认留档不再需要后移走 `legacy/` 再重建。

### 中心数据库（公共连接 + 按群开关）

连接参数放在「公共参数」，两个平台共用；启用开关都在「群与服务器 → 本群规则」：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `use_central_db`（OneBot） | bool | `false` | 开启后本群绑定写入中心库并跨服共享；关闭则本群各服独立走模组 API |
| `use_central_db`（QQ 官方） | bool | `false` | 开启后本群官方绑定写入官方中心表；关闭则使用模组本地存储 |

`use_central_db` 只决定本群的读写路径，数据库连接池仍使用全局公共连接。OneBot 的 `bindings` 表本身带 `group_id`，不同群可以分别选择中心或本地。QQ 官方群开启后使用公共 `db_*` 连接，默认 `hybrid` 模式；环境级 `center_settings.json` 存在时完整覆盖全局默认值。该文件仍由用户自行维护，WebUI 不读取、不回显其中的连接信息。

**两种模式的区别**

- 本地模式（默认）：绑定写入「验证到绑定码的那台服务器」的模组数据库，服务器之间互不相通。`/绑定检测` 不可用。
- 中心模式：绑定写入 `bindings (group_id, game_id, qq)` 表，写入成功后插件把结果**异步下行同步**到各服务器的模组；某台同步失败不影响绑定结果，会在回复里点名是哪台、为什么（模组过旧 / 暂不可达），玩家重新进服时自动生效。

---

## 🚀 命令一览

中文命令直接发送即可，括号内为英文写法或别名。`-s 服务器名` 是通用的「指定单台服务器」参数，`/tps`、`/查询`、`/广播`、`/执行` 也可把服务器名直接写在原位。`/执行` 必须点名一台服务器（`[服务器名]` 或 `-s 服务器名`），省略即拒绝——因为一条打错服的命令比多打两个字符危险得多。

### 状态查询（所有人可用）

| 命令 | 英文 / 别名 | 说明 |
|------|-------------|------|
| `/在线 [-s 服务器名]` | `/online`、`/mc status` | 查询在线情况并出图；不带参数查全部，隐藏的服务器不出卡 |
| `/查询 <玩家名> [服务器名]` | `/mc stats`，也可 `-s 服务器名` | 玩家数据统计图；缺省按列表顺序找第一台有记录的服务器 |
| `/排行 [类型] [窗口] -s <服务器名> [-n N]` | `/mc rank` | 排行榜图：类型 时长/死亡/击杀生物/钓鱼，窗口 总/周/月；周/月只统计近 7/30 天活跃玩家；多台可查服时必须用 `-s` 指定，单台可省略 |
| `/tps [服务器名]` | `/mc tps`，也可 `-s 服务器名` | TPS 图，不带参数查全部 |
| `/广播 [服务器名\|all] <消息>` | `/mc say [服务器名\|all] <消息>`，也可 `-s` | 游戏内广播；省略服务器名或写 `all` 则发到全部启用模组 API 的服务器，最长 40 字符 |

### 账号绑定（详见 `/绑定帮助`）

| 命令 | 英文 / 别名 | 权限 |
|------|-------------|------|
| `/绑定 <6位绑定码> [-s 服务器名]` | `/bind` | 所有人；多服时可用 `-s` 指定验证哪一台 |
| `/解绑 [游戏ID\|all]` | `/unbind` | 所有人（仅限自己的账号；省略或写 `all` 解绑自己名下全部游戏ID） |
| `/解绑 ID <游戏ID>` / `/解绑 QQ <QQ号>` | - | 管理员 |
| `/查绑定 [游戏ID] [-s 服务器名]` / `/查绑定 QQ <QQ号>` | `/绑定状态`、`/checkbind`、`/mc check` | 所有人；`-s` 可指定查询哪台服务器 |
| `/绑定检测` | `/mc bindcheck` | 管理员，且需开启中心库 |

绑定流程三步：

1. 进入服务器，屏幕中央（Title / ActionBar）显示 **6 位绑定码**，5 分钟有效。
2. 把群昵称改成 `玩家（游戏ID）` 格式，例如 `张三（zhangsan）`——括号内必须与登录的游戏ID 一致。
3. 在本群发送 `/绑定 123456`。

> 60 秒内连续 5 次绑定码无效会进入冷却，提示里给出剩余秒数；被模组限流时不会算在你头上，提示会说明「码没失效，不用重新登录」。

### 服务器管理（群主 / 群管理员 / 白名单管理员 / 超管）

| 命令 | 说明 |
|------|------|
| `/mc add <名称> <主机[:游戏端口]> [模组API端口]` | 添加服务器；游戏端口写在主机里，第三段是模组 API 端口，不写则视为未装模组（`/执行`、`/广播` 不可用）。冒号前后的空格会被忽略 |
| `/mc remove <名称>` | 删除服务器（`/mc del` 为同义写法） |
| `/mc hide <名称>` / `/mc show <名称>` | 隐藏 / 取消隐藏：隐藏的服务器不再参与在线、TPS、统计和排行查询，但条目、绑定、Token 与备注都保留 |
| `/mc edit <名称> name\|host\|port\|remark <值>` | 改名 / 改地址 / 改模组 API 端口 / 改备注（备注显示在卡片名下方） |
| `/mc batchadd 名1:主机1[:模组API端口1],名2:主机2` | 批量添加；第三段固定是模组 API 端口，主机要带游戏端口时用 `/mc add` 逐个添加 |
| `/mc batchremove 名1,名2` | 批量删除 |
| `/mc list` | 列出服务器的主机、模组 API 端口，并标注隐藏状态 |
| `/mc move <名称> <序号>` | 调整顺序（序号从 0 开始） |
| `/mc swap <名1> <名2>` | 交换两台位置 |
| `/上次在线 开\|关` | `/mc lastonline`，卡片是否显示最后活动时间 |
| `/检测昵称` | `/mc checknick`，列出群内昵称不符合「玩家（游戏ID）」的成员 |
| `/检测昵称 开\|关\|忽略 <QQ>\|取消忽略 <QQ>\|忽略列表` | `/mc nicknamecheck` |
| `/执行 [服务器名] <命令...>` | `/mc exec`，在指定服务器执行控制台命令并回显输出；需先用 `/mc token` 为该服配置命令 Token |
| `/mc token <名称> <Token\|清除>` | 超管：为该服务器单独设置命令 Token（与模组 `consoleToken` 一致）；清除即关闭该服的 `/执行` |

> 地址写法：`主机` 可以是域名、IPv4 或 `域名:游戏端口`；只写域名时会先查 SRV。`/mc add 一服 vefc.ovo.ci : 25565`、`/mc edit 一服 host vefc.ovo.ci : 25565` 这种冒号前后带空格（或全角 `：`）的写法会被自动归一化。服务器名不能包含空格；`/排行 -s` 支持 `[服务器 名]` 写法以指定带空格的名称。

### 帮助与高级

| 命令 | 权限 | 说明 |
|------|------|------|
| `/帮助` | 所有人 | 通用命令帮助图（`/mc help`） |
| `/绑定帮助` | 所有人 | 绑定专用帮助图，含完整流程与常见问题（`/bindhelp`） |
| `/命令指引` | 所有人 | 官方群专用 Markdown 按钮菜单，列出常用查询与帮助入口（`/mc guide 1`） |
| `/管理指引` | 群管理员（≥3） | 官方群专用 Markdown 按钮菜单，列出服务器维护与排序命令（`/mc guide 2`） |
| `/mc sid` | 所有人（官方群） | 只向发送者本人回复其 `member_openid`，10 秒后尽力自动撤回；不要求本群已启用，供名单与首次超管配置使用 |
| `/mc whitelist list` | 超管 | 查看白名单配置（官方群脱敏）；官方群还可用 `@成员` 增删本环境补充名单（按当前群生效），公共名单与 OneBot 名单仍在 WebUI 配置面板操作 |

---

## 🔐 权限模型

从低到高：`黑名单(0)` → `普通成员(1)` → `白名单管理员(3)` / `群主、群管理员(3)` → `超级管理员(4)`。

「白名单管理员」与群主/群管理员同级：配置在名单里的人可以执行服务器管理命令（`/mc add`、`/执行` 等）。

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
| `onebot/config.sqlite3` | OneBot 全部群、服务器、群规则与命令 Token（SQLite/WAL）；旧 `onebot/<群号>/state.json` 与 `group_configs/servers_<群号>.json` 首次打开时导入并移到 `onebot/legacy/` |
| `qq_official/<环境UUID>/config.sqlite3` | 官方环境的群、服务器、名单、候选、管理员、审计、验证码与备注（SQLite/WAL）；旧 JSON 首次打开时迁移到 `legacy/` |
| `qq_official/<环境UUID>/console_tokens/<server_id>.json` | 官方每服独立的控制台命令 Token，不在 WebUI 回显 |
| `stats_snapshots.sqlite3` | 玩家统计每日快照，保留 30 天 |
| `avatar_cache/` | 头像磁盘缓存，过期 7 天，另有一层内存缓存 |
| `help_cache/` | `/帮助`、`/绑定帮助` 渲染出的 PNG；删掉文件即重新渲染 |

| 路径 | 内容 |
|------|------|
| `AstrBot/data/config/astrbot_plugin_mcwatcher_config.json` | 插件配置（WebUI 面板写入） |
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
| `name` | 服务器名，命令里用它定位（`/执行` 写成 `[服务器名]`，`/mc say`、`/mc stats` 用 `-s 服务器名`），重名会添加失败；名称不能含空格或 `[ ] 【 】` |
| `host` | 主机地址：IPv4 或域名（不支持 IPv6），**可带游戏端口**写成 `域名:游戏端口`；这段端口只被 mcstatus 回落与延迟测量使用，模组 API 端口另有 `api_port`。只写域名时会先查 SRV 记录；显式写端口（包括默认的 `:25565`）会跳过 SRV，端口不确定时优先只写域名 |
| `api_port` | 模组 HTTP API 端口。**有这一个字段即视为装了模组**；没有这个字段就是没装，`/执行`、`/广播` 对该服不可用 |
| `remark` | 手填的备注，显示在 `/在线` 卡片名字下方；最多 50 个字符（汉字按 2 计） |
| `remark_src` | 备注取值来源：`"motd"`＝卡片显示服务端 motd；**没有这个字段就是用手填的值**（默认）。只能在配置页的「编辑服务器」里切换，无对应命令 |
| `remark_motd` | 采集到的服务端 motd（已剥色码、折成一行、按 50 字符截断）。由 `/在线` 顺手写入，或在配置页点「立即同步」写入；**切回手填也不会删掉它** |
| `remark_motd_at` | 上面那份 motd 的采集时间（Unix 秒）。motd 没变时它停在那一刻，不代表最近查过 |
| `console_token` | 该服的命令 Token，用 `/mc token` 写入；**没有这个字段就是 `/执行` 对该服未启用**，与模组配置里的 `consoleToken` 逐字一致 |
| `hidden` | 只在值为 `true` 时写出，取消隐藏会把这整个键删掉。`true`＝这条服务器暂时从 `/在线`、`/tps`、`/查询`、`/排行` 撤下，**连查询都不发**；条目本身、绑定、Token、备注都不动，广播与管理命令照常能定位到它。可用 `/mc hide 名称`、`/mc show 名称` 或配置页的「隐藏 / 显示」按钮切换 |

- 每个群最多 20 台服务器。
- 内网 / 回环地址与 `.localhost` / `.local` / `.internal` 一类内网主机名默认拒绝，仅超级管理员可添加（SSRF 防护）。

---

## 🧠 数据来源与稳定性

- **`/在线`**：配了 `api_port` 的服务器走模组 `/api/status`（含 UUID、是否正版、最后活动时间）；请求失败或未配端口的回落 mcstatus SLP。延迟用裸 TCP 建连计时，与状态采集并发，不排在关键路径上。`hidden` 为 `true` 的条目在发第一个包之前就被摘掉，一台关着的服不会占掉整轮超时；全部隐藏时只回一句提示、不出图。
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
