# QQ 官方兼容开发交付与用户验收

日期：2026-11-20。本文是用户维护说明，助手不执行涉及真实配置、凭据、数据库或玩家数据的步骤。F01–F05已修源码，并补齐可实现的管理/查询差异；**本轮依用户要求未测试、未重编四模块，2026-11-19-complete-development旧Jar不含本轮修改**。只同步了前端发布资源。后续集中必要验证、更新四Jar，再按本文及[OneBot对照与待验收清单](QQ_OFFICIAL_TEST_CHECKLIST.md)接入；当前不作为直接上线依据。

## 已实现的运行入口

| 入口 | 行为 |
|---|---|
| `/mc bind <六码> -s <服>` | 官方身份、当前昵称及有效账号依据校验，按该服配置的权威提交 |
| `/mc check -s <服>`、`/mc unbind <游戏名或all> -s <服>` | 当前范围一对多查询及固定版本解绑 |
| `/mc check -s all`、`/mc unbind all -s all` | 至多4服，中心分区只处理一次，local逐服独立报告；未知服不回滚已成功服 |
| `/mc operation <编号> retry [原六码] -s <原服>` | 新消息重新授权；只继续原操作。首次未预占时可提交原代次的码；已外发的预占不盲重发 |
| `/mc operation <编号> cancel -s <原服>` | 原请求人或当前管理员显式中止，权威CAS裁决，已提交不能撤销 |
| `/mc rank … -s <服>` | 公开榜单保留，按当前权威active账号标注本人及本人名次；权威不可用时单独提示，不查旧绑定 |
| `/mc broadcast -s <服或all> <文字>` | 当前管理员，单次最多4服；插件和模组都先记DISPATCHED再发送 |
| `/mc console -s <服> <命令>` | 当前管理员及本服独立控制台令牌，必须单服；禁用命令规则仍生效，不在插件/模组API日志保存正文 |
| `/mc add/edit/del/hide/show …` | 当前管理员修改官方服务器条目，原子配置修订，持久事件回执防重复 |
| `/mc command <编号>` | 本人或本群管理员查询写命令回执，不重复执行正文 |
| `/mc lastonline [开或关]` | 查看/修改最后活动显示，数据缺失明确提示；后台同一策略 |
| `/mc guide [1|2]`、`/mc 命令指引` | 官方 Markdown + 自定义按钮菜单；点击只填入命令，仍需用户发送；页1为2列5行常用与帮助，页2为3列4行管理命令、需管理员且用 `/mc guide 2` 进入；明确拒绝时回退纯文本，未知结果不重发 |
| `/mc batchadd 名称:主机[:API端口],…`、`batchremove 名1,名2` | 管理员整批校验、一次配置修订及持久防重，最多20项 |
| `/mc move 名称 位置`、`swap 名1 名2` | 管理员排序，位置从0开始；服务器稳定标识不变 |
| `/mc check @成员 -s 服`、`unbind @成员 -s 服` | 使用当前事件可靠mentions；他人完整关联/解绑限管理员，也可显式user官方标识 |
| `/mc bindcheck -s 服` | 需要QQ内邀成员权限，与所选当前权威active用户比较；不读旧表、不自动解绑 |
| `/mc checknick`、`nicknamecheck …` | 有权时扫描；仅核验近期可靠昵称，未知单列；管理开关及忽略名单 |
| `/qqbind unbind <游戏名>` | MC权限4管理员或服务端控制台，后台读取权威并提交带版本解绑；不阻塞游戏线程 |
| `/qqbind list`、`/qqbind operation <完整编号>` | 原生管理员按范围查询；列表最多显示50游戏名，不显示官方用户标识 |
| `/qqbind operation <完整编号> retry` | 仅原游戏管理员继续本人原解绑；不能代替QQ用户提交绑定 |
| `/qqbind operation <完整编号> cancel` | 原生管理员显式中止未提交操作；已提交不能回滚 |
| `/qqbind status`、`/qqbind reconcile` | 内存状态与排队刷新；reconcile不产生用户授权 |

## 三种权威的配置

- 插件仍只选一个官方实例和一个应用环境；官方群和玩家重新登记/绑定，禁止导入旧OneBot身份或表。
- 插件数据目录中的 `qq_official/<environment_uuid>/center_settings.json`：中心模式使用 `examples/qq-official-center.json.example`；纯local使用 `examples/qq-official-local.json.example`。用户自行填写真实值。`enabled` 默认false，只在维护准备完成后改true。
- 超管在官方群内用 `/mc whitelist add|remove @成员`、`/mc whitelist super add|remove @成员`、`/mc blacklist add|remove @成员` 维护的补充名单保存在 `qq_official/<environment_uuid>/config.sqlite3` 的 `environment_access` 表，与 OneBot 共用的公共名单只读合并。该名单按群成员标识生效，换群需重新添加；备份/恢复官方配置时以 `config.sqlite3` 为准（旧版 `state.json` / `access.json` 已迁入 `legacy/`）。官方列表命令与回执脱敏，不要求用户提供或回显 openid。
- `storage_mode` 为默认模式，`server_modes` 是可选的稳定服务器UUID→hybrid/remote/local映射；空映射沿用默认值。它不是同群多身份域：中心分区仍必须共享同一账号体系，各local服是独立权威。官方环境不进行双平台双活。
- hybrid/remote使用同一官方中心两表、相同结构核验和断库策略。remote现也持久保留自己的可信镜像/副作用待办；断库保留既有登录，暂停新增/解绑，不自动切换local权威。
- local只使用本服单个官方原子状态文件；绑定、操作、预占墓碑和白名单/在线待办同次提交，不依赖中心。local不接受中心sync。配置中省略official.database，allowCenterSync=false。
- 模组中心模板为 `QQBindMod/examples/qq-official-config.template.json`，remote将storageMode设为remote；local用 `QQBindMod/examples/qq-official-local.json.example`。环境、群标识和serverId必须与插件一致，中心模式allowCenterSync需显式true。
- 新安装仅一次：用户先确认目标目录不存在，再设createFreshState=true启动初始化。此时业务不开放；完整停服、改false后再次启动。不删除已存在状态/选择标记来重建，更不能把local与center文件互换。local的权威目录在既定官方scope目录下额外使用 `local/` 隔离。
- 控制台需模组独立consoleToken，与apiToken不能相同。仅超级管理员可在群内用 `/mc token <服务器名> <Token>` 设置、用 `/mc token <服务器名> 清除` 关闭；Token 不会回显、不会写入日志，但消息本身会经过 QQ 平台，请自行评估。也可按 `examples/qq-official-console-token.json.example` 手工放到 `qq_official/<环境>/console_tokens/<server_uuid>.json`；真实值只由用户本地处理。空令牌表示关闭。
- 普通后台页面不读取中心秘密来猜测整体就绪；运行条件逐次握手确认。capabilities必须通过本服名单桥/初始镜像和适用中心结构/读写权限核验后才声明就绪。

## 数据库DDL和权限

`sql/qq_official_v1.sql` 是本次版本一结构定稿，仅包含新官方两表。用户选好空的官方schema后自行执行；不对旧bindings执行迁移、复制或删除，不添加第三张中心表。使用InnoDB、utf8mb4_bin、UTC、微秒时间和既定唯一约束。运行账户只需两表SELECT/INSERT/UPDATE及驱动元数据读取/无写EXPLAIN核验；建表由独立维护账户执行，运行代码不建表、不自动修复结构。实际TiDB/MySQL版本的结构/权限/行锁/隔离级别及TLS主机名验证必须自行确认。

## 用户维护窗口

1. 自行备份插件选择、官方配置、官方两表、各服完整官方状态、原版名单及相关玩家限制标记。官方群配置优先在后台「更多群操作 → 导出配置备份」导出 JSON（不含验证码、控制台 Token 和审计明细）；也可停服后复制整个 `qq_official/<环境>/`。不要直接改 `config.sqlite3`，`legacy/` 只是迁移留档。备份由用户保管，不发给助手。记录未决编号和固定目标，在窗口内暂停新命令。
2. 全部停服和停止旧插件入口，确保单一实例。中心执行DDL、填写安全配置，待本轮源码必要验证并重新构建后安装对应加载器包；不要以2026-11-19旧Jar验本轮新功能。Forge使用含依赖且重混淆的all.jar，一台服务器只装一个对应加载器包。
3. 按上述两次启动完成首次官方状态初始化。官方选择须完整停启，不在线切换环境/应用/权威/令牌或serverId。
4. 启动模组并查看 `/qqbind status`；启用插件官方实例、后台确认群/管理员/服务器，填写独立模组令牌。用户自己检查QQ角色/昵称、媒体权限及框架入口日志对虚构码的处理。
5. 使用非OP测试账号依次验收下节；出现未知结果只查原编号，不盲目取新码或删除预占。本人新消息继续未决操作，管理员可中止；已提交仅补偿固定副作用。
6. 切换失败时暂停新命令并完整停启。未知中心提交先查原ops结果；恢复备份须成套处理权威操作/绑定/镜像和任务，禁止单独回退白名单或删墓碑。换回OneBot仍使用其旧独立空间，不把官方身份搬回旧表，不同时启动两套入口。

## 必须由用户完成的真实验收

- 四个目标分别安装启动，实际Mixin注入、登录取码、Profile/UUID、原生命令与名单保存/重载、绑定后重登及在线限制解除。
- 每种计划部署的hybrid/remote/local分别完成一用户两账号、解绑A保留B、撤销后旧同步不复活、进程重启与数据库失联恢复。中心验证事务/锁/TLS/权限；local验证本服隔离及跨服部分失败。
- 原生管理员解绑/查询/中止及原用户新消息恢复；不允许后台/其它管理员替QQ本人提交。
- 官方WebSocket或Webhook实际权限/昵称/角色、分页文本、媒体上传与会话限制；权限不满足的功能应明确降级。
- `/mc guide` 的 Markdown 自定义内容与自定义按钮需在 QQ 开放平台确认客户端版本和应用能力（自定义按钮当前为内邀 / 按钮模版能力）；验证群聊点击只填入命令而不自动发送、页2管理员限制、超管按钮显隐，以及平台明确拒绝时只回退一次纯文本。
- 成员列表/详情为QQ内邀能力（未获权11253）；bindcheck/checknick有权时才可扫描，达到边界明确部分结果。群消息事件提供当前作者和mentions目标的member_nickname，插件对近期处理的群命令事件缓存5分钟；未发言且未被提及成员仍无法核验。列表接口username不冒充全群群名片；管理员撤权后旧管理分页失效。
- 广播/控制台重复同事件或请求超时不重复执行，未知结果由管理员核对；控制台正文和输出不进入插件/模组API审计日志，MC本身及其它模组日志由用户另行核对。
- 框架WebUI未登录/会话过期拒绝，官方配置与local服务器操作页、浏览器布局和弹层。离线Vue挂载不能代替浏览器验收。

不要求用户向助手提供真实事件、凭据、玩家数据或日志，只需反馈成功/失败及不含私密值的错误类别。退群、被踢和聊天黑名单不自动游戏解绑，原版封禁独立生效。

## 回执容量与安全维护

绑定/操作/预占及非绑定命令回执均有容量上限，满时拒绝新增而不删除防重依据。原版状态文件禁止人工删局部字段；所有DISPATCHED重启后视为UNKNOWN，不自动续发。需要归档时由用户维护窗口停机、备份、核对终态和重复事件保留范围后处理；不提供自动清理真实记录的脚本。
