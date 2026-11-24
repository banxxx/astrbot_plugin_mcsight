# QQ 官方接入：文档复核、OneBot 对照与手工测试清单

更新日期：2026-11-24（Asia/Shanghai）。对象为当前插件和QQBindMod源码；框架公开源码基线仍为AstrBot v4.29.1，没有读取用户实际安装、登录态或真实配置。2026-11-24 增加 `/mc guide` 命令指引及其自动化回归；真实 Markdown/按钮权限仍需用户客户端验收。

**当前结论：F01–F05已修源码，可实现的功能差异已补齐主要入口；本次复核已把mentions中的member_nickname接入短期缓存。修改尚未测试，旧四Jar未重新构建，不能直接当成已验收上线版。** 成员列表为QQ内邀能力；昵称检测只能核验近期事件作者和被@目标。

本轮依用户要求修改正式源码，不运行Python/Java/Vue测试、类型检查或模组构建。前端只生成必要发布资源。下列手工用例留给后续集中验证，不表示本轮已执行；“业务一致”仅为源码语义比较。

## 1. 本次核验的现行官方资料

| 编号 | 第一方来源 | 与本项目有关的事实 |
|---|---|---|
| D1 | QQ 机器人官方更新记录 | 2026-08-10统一api.bot.qq.com；2026-09-03新增成员列表/信息管理接口；2026-11-16群@消息事件补充author/mentions的member_nickname与头像。应用获权和成员列表字段须分别核验 |
| D2 | QQ 官方接收群@消息文档（2026-11-16更新） | author和mentions内的成员对象都可带member_nickname；mentions另有member_openid/scope/bot/is_you。只覆盖当前消息作者及被提及目标，不表示成员列表可批量取全群群名片 |
| D3 | QQ 官方发送群聊消息接口 | 文本/富媒体消息、被动回复关联 msg_id、msg_seq、防重复与回复窗口属于官方约束；不能沿用 OneBot 任意时机发消息的假设 |
| D4 | QQ 官方群富媒体上传接口 | 群files接口支持file_data；file_name在参数表为条件必填，描述明确分片或二进制直传时必填。当前插件正使用file_data直传，所以不能忽略文件名；返回文件引用后仍须发送媒体消息。业务错误按调用指南的err_code处理 |
| D5 | AstrBot 官方 QQ 接入教程 | WebSocket 和 Webhook 均有接入说明；应用信息与真实凭据由用户在框架配置。Webhook还涉及部署回调，不意味着本插件已经通过真实回调验证 |
| D6 | AstrBot v4.29.1 官方事件源码 | 原始事件/本地图片处理为固定版本适配证据；这份旧版源码不能替代更新后的 QQ 云端协议及应用权限核验 |
| D7 | QQ官方群成员列表 | GET /v2/groups/{group_openid}/members，cursor分页，members/next_cursor，最多30条/页、60QPM；内邀能力，未获权11253；成员username不能替代群昵称 |
| D8 | QQ官方群成员信息 | GET /v2/groups/{group_openid}/members/{member_openid}，30QPM，同样内邀；没有全量可靠群名片保证 |

公开定位（只访问公开页面，不使用已登录控制台）：

- D1：`https://bot.q.qq.com/wiki/develop/api-v2/changelog.html`
- D2：`https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html`
- D3：`https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html`
- D4：`https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_files.post.html`
- D5：`https://docs.astrbot.app/platform/qqofficial.html`
- D6：`https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.29.1/astrbot/core/platform/sources/qqofficial/qqofficial_message_event.py`
- D7：`https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_members.get.html`
- D8：`https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_members_member_openid.get.html`

本轮已取得D7/D8正文与公开发布导航证据，按已核实路径接入有界只读列表，不调用成员修改接口。应用是否获权由用户自行确认；无权不推测名单，分页结束只表示扫描结束，不构成实时群资格证明。

## 2. 已修改源码的5项（均未测试）

| 编号 | 当前实际代码 | 与官方资料/既定契约的差异 | 实际影响与修正要求 |
|---|---|---|---|
| F01 | official_delivery统一使用api.bot.qq.com | 不猜测旧域名/沙箱别名仍可用 | 已改源码；保留框架认证、当前权限复查和单次外发 |
| F02 | event_scope读取author.member_role | 仅member/admin/owner，未知为普通成员 | 已改源码；后台独立授权与黑名单优先不变 |
| F03 | file_data上传增加mcsight.png | srv_send_msg=false；消息POST前可安全文字降级 | 已改源码；已尝试消息POST后不自动补发 |
| F04 | checked_response统一解析err_code与兼容code | 授权/限流/过期/拒绝分类；异步审核与201/202不当已送达 | 已改源码；不回显远端正文，未知结果不重发 |
| F05 | RuntimeSession对中心operations失败受控回查TokenApi | 只读取本服持久reservation；保留center_available=false/RESULT_UNKNOWN | 已改源码；不扩大到结构损坏/未授权回退，不依据本地回执提交变更 |

这些修改均未在本轮验证。2026-11-19-complete-development内四Jar不含本轮修改；本清单不是放行报告。

另外两项展示/使用缺口：

- `show_last_online`已消费模组现有最后活动字段，lastonline可查看/开关；数据缺失明确无可靠记录。
- 插件后台binding_ready改为null并标逐次握手；模组/api/status使用当前内存就绪状态，不能替代capabilities的中心结构/权限核验。

## 3. OneBot 与当前官方实现对照

状态：A=业务目标一致、有源码；B=有源码但交互/约束不同或有权限条件；C=当前未接；D=既定不支持/不做。所有新增A/B尚未测试，均需后续验证；不能把条件能力写成普遍可用。

| 功能 | OneBot既有行为 | 当前官方代码 | 状态/测试重点 |
|---|---|---|---|
| 同进程生效实例 | 选择OneBot实例 | 全局同样只允许一个具体实例，另一实例应静默 | A；测完整停启、重载和非活动实例 |
| 群初始化 | 数字群号/旧配置 | `/mc setup`登记原始官方群/本人，后台审核后启用；不导入旧群 | B；登记不授予权限 |
| 服务器列表 | 当前群条目 | `/mc list`列当前官方群非隐藏条目 | A；配置空间独立 |
| 在线人数与玩家 | 模组优先，mcstatus回落 | 同一数据来源和回落目标；mcstatus名单标明采样 | A；不是完整名单保证 |
| TPS | 模组读取 | `/mc tps -s <服>`读取同一TPS数据 | A；无模组时明确不可用 |
| 状态卡视觉 | 专用状态卡，可有头像/其它展示 | 官方使用独立通用卡片或文字；不是OneBot同版像素布局 | B；修F03后测图文 |
| 最后在线 | 有开关及卡片实现 | lastonline查询/开关、后台配置及状态展示已接 | A；模组无记录时明确提示 |
| 公开玩家统计 | 玩家名查询、六类统计图 | 复用六类统计绘制；查询仍公开，不要求先绑定 | A；对比同MC账号数值 |
| 7日统计历史 | 旧统计历史空间 | 官方独立30日保留、UTC日快照；不导入旧历史 | B；新装不能凭空有7日对比 |
| 公开排行榜 | 时长/死亡/击杀生物/钓鱼 | 同四类；总/周/月的周月仍是近期活跃者的累计排行 | A；不是窗口内新增量榜 |
| 本人排行 | 从旧绑定解析本人账号 | 从当前官方权威active账号标注本人，多账号分别展示 | B；中心/local范围独立；失败仍显示公开榜 |
| 帮助 | 图片帮助 | 独立官方 help / bindhelp 帮助图；无图片能力时回退官方文字 | B；不能照抄旧QQ号用法 |
| 多页消息 | 可多图/合并或普通发送 | 首次第一页，page新消息翻页；限本人/群/环境/修订/10分钟，管理结果保存最低权限 | B；管理员降权后不能继续翻看旧管理页 |
| 发送额度/超时 | OneBot发送能力 | 官方msg_id/msg_seq、回复窗口；插件额外限制最多4次HTTP调用和本地图片大小 | B；插件限额不是平台全部额度 |
| QQ消息发送时机 | OneBot可主动发送回复 | 本期请求式业务仍只用被动msg_id；定时主动公告不是既定移植业务 | B/D既定范围；不描述为平台绝对不支持主动消息 |
| MC取码与绑定 | 游戏取六码，昵称规则，提交绑定 | 同业务目标；官方OpenID、一用户多账号及独立权威事务 | B；每新增账号必须单独取码 |
| 群昵称校验 | 从OneBot成员信息取得名片 | 绑定读当前事件author.member_nickname；检测可读近期事件author/mentions内member_nickname | B；未发言或未被@成员仍无法核验，不使用username兜底 |
| 群主/群管权限 | OneBot成员角色查询 | member_role映射已修，策略开关及后台显式授权独立生效 | A目标；缺失或未知角色不提权 |
| 官方配置备份 | 无直接对应项 | 后台「更多群操作」导出 JSON；删除/改名一个群后再导入，群、服务器、policy、补充名单和备注恢复，环境修订号提升；错误环境或损坏文件被拒绝且原配置不变；正式库删除但 `legacy/` 存在时普通读取失败关闭 | A；验证码、控制台 Token 与审计明细不随备份导入，保留 `server_id` 时控制台 Token 继续有效 |
| 插件管理员/超管/黑名单 | 原名单与权限 | 官方群级管理员、环境超管、聊天黑名单独立；超管可用 `whitelist/blacklist add|remove @成员` 维护本环境补充名单，写入环境库 `environment_access` 表，不修改 OneBot 公共名单，列表脱敏 | B；旧数字QQ名单不继承，黑名单优先，目标须 @ 唯一成员 |
| 查询自己的绑定 | 旧QQ/群昵称等定位 | check默认本人全部active账号；按游戏名查当前绑定状态，普通用户不见他人归属 | B；完整他人关联限管理员 |
| 自助解绑 | 原有解绑参数和默认行为 | 必须明确游戏名或all；解绑A保留B | B；不带参数不会默认全部解绑 |
| 管理员解绑别人 | 可按QQ或ID操作 | 已有`unbind ID <游戏名>`、`unbind user <官方标识>`；需要本群权限和固定目标 | B；不接受把数字QQ直接视为OpenID |
| @成员定位/查询他人完整关联 | 有OneBotQQ参数/成员定位 | 已消费事件mentions；管理员check/unbind @成员，或显式user官方标识 | B；不按显示名/数字QQ猜身份，歧义时拒绝 |
| 中心跨服绑定 | 中心共享 | hybrid/remote共用官方两表、同账号体系；断库暂停新增/解绑，既有镜像保留 | B；F05修后重点测恢复 |
| local绑定 | 原单服存储 | 官方单服权威，一对多；操作/预占终态/镜像/任务同文件提交 | B；不接受中心sync，不能跨服当一个事务 |
| 跨local汇总 | 原路径逐服处理 | `check -s all`及`unbind all -s all`最多4服，逐服报告，成功服不回滚 | B；完整操作编号与原服必需 |
| 原操作恢复 | 旧流程 | operation查询/retry/cancel；原用户用新事件继续，后台不代提交 | B；终态、镜像、白名单、在线效果分别确认 |
| MC白名单/在线限制 | 原有绑定副作用 | 四正式加载器原版同实例桥、版本化任务和ACK | A业务目标；构建通过不证明真实Mixin/登录成功 |
| MC管理员命令 | qqbind解绑/list/reload | 已接unbind/list/status/reconcile/operation retry/cancel，操作走后台 | B；新命令不等于自动提交用户绑定 |
| MC广播 | 选服或默认多服 | 官方必须显式`-s <服或all>`，最多4服，持久防重 | B；`/广播`/say可入路由但不保留OneBot省略目标行为 |
| 控制台命令 | 单服独立令牌、输出 | 同业务目标；官方显式单服、独立令牌、持久防重、瞬时过滤回显 | B；UNKNOWN不自动重发 |
| 服务器管理 | add/remove/edit等 | 已接add/edit/remove(映射del)/del/hide/show | A基本管理；原子修订及回执方式不同 |
| 批量增删、move/swap | OneBot已有，可部分成功 | 已接同名命令；批量整批校验后原子配置修订，排序从0开始 | B；不采用部分条目已写的反馈语义 |
| 白名单/令牌聊天管理 | 旧QQ参数或旧命令 | 官方授权在WebUI；控制台令牌用户本地按服填写，不走聊天token命令 | B/D按已定安全流程 |
| 全群绑定检测 | OneBot可取成员列表 | bindcheck已接内邀成员列表、中心可信快照及local只读active_roster分页 | B条件；无权限11253明确提示，扫描截断不冒充全量 |
| 全群昵称检测/忽略 | OneBot名单扫描和开关 | checknick/nicknamecheck及后台开关已接；核验近5分钟插件收到的官方群事件author/mentions可靠member_nickname | B条件；成员名单username不是群名片，未发言或未被@者为无法核验；只提示不自动解绑 |
| 退群/踢出/聊天黑名单 | 本项目不可假设自动撤销 | 官方按已确认规则不自动游戏解绑；管理员显式操作 | D自动解绑是明确不做 |
| 官方C2C/频道 | OneBot原有私聊可在其活动时使用 | event_scope明确拒绝官方C2C/频道，仅群聊 | D本期范围；QQ本身另有接口，非技术绝对不能 |
| 旧QQ身份迁移/双平台同时工作 | 旧空间可保留 | 不映射QQ号与OpenID、不自动迁移、不双活 | D既定方案，不是待验收功能 |

## 4. 可以照着执行的测试顺序

### 第一轮：更新源码/构建包后做测试群连通与配置（本轮不执行）

- [ ] T01 用户按AstrBot官方教程配置QQ官方连接；凭据只在自己的框架/开放平台填写，记录所用WebSocket或Webhook。两种连接一次只选一条生效。
- [ ] T02 插件WebUI选具体官方实例/应用环境并完整重启；另一OneBot/官方实例向本插件发命令应静默。不要同时对正式服启用两套入口。
- [ ] T03 在专用测试群`@机器人 /mc setup 测试登记`；应有文字登记回执，后台有候选，普通登记者不能因此自动成为管理员。若完全无回复，先区分框架接入/唤醒、F01域名和应用权限，不把问题直接归给MC。
- [ ] T04 `@机器人 /mc bindhelp`收到官方绑定帮助图（无图片能力时回退官方文字）；群启用前普通MC业务被拒绝。
- [ ] T05 用户后台核对候选后授权一个测试管理员、添加测试MC服务器并启用；`/mc list`只显示当前官方群非隐藏条目。
- [ ] T06 显式插件管理员与原生member_role分别按策略授权；普通成员和黑名单被正确限制，未知角色不提权。

**第一轮不要求连接真实中心或绑定正式玩家。所有后续涉及真实凭据、数据库、名单、玩家和部署的操作由用户自行在测试环境完成。**

### 第二轮：验F01-F04修正、消息、展示和权限

- [ ] T07 `/mc help`、`/mc status -s 测试服`、`/mc tps -s 测试服`正常；图片实际上传带安全file_name，不能只检查本地PNG生成。
- [ ] T08 正常图、无媒体权限、上传明确失败、消息发送结果未知分别验证；允许安全降级文字，未知消息禁止自动重发。
- [ ] T09 多页查询仅首屏，`/mc page 2`翻页；另一用户/群不能拿旧页，权限撤销或配置变化后旧页失效。
- [ ] T10 `/mc stats TestPlayer -s 测试服`与OneBot对同一合成测试玩家的统计数值一致；官方图样可以不同。新环境历史不足时明确只显示已有数据。
- [ ] T11 `/mc rank 时长 总 -s 测试服`及死亡/击杀生物/钓鱼，周/月为近期活跃累计排行；绑定前后本人标识符合当前权威账号。
- [ ] T12 当前官方member_role分别为member/admin/owner和缺失时，管理权限与开关正确；环境超管不因群管身份自动产生。
- [ ] T13 官方member_nickname符合/不符合/缺失时，昵称规则正确；缺失时不拿username代替，普通查询仍可用。
- [ ] T14 使用真实接口明确的未授权、额度和参数失败，插件能够给出安全分类，不回显QQ/SDK原始错误正文或令牌。此项用测试配置/虚构命令，由用户核对，不发真实日志给助手。

### 第三轮：在独立MC测试服验F05、绑定与恢复

- [ ] T15 按QQ_OFFICIAL_DELIVERY.md由用户做官方状态首次初始化；非OP且启用绑定检查的测试玩家登录能取得六码。OP绕过行为不要当作普通玩家验收。
- [ ] T16 本人用码绑定A、再用另一码绑定B；匹配当前A或B规则的可靠昵称可作为既定依据。查绑定见A/B，两账号均能在目标测试服放行。
- [ ] T17 他人拿同码/不同身份抢占应冲突；同事件重投/重启不重复增加revision。
- [ ] T18 `unbind A`只撤销A，B仍有效；再次绑定A版本递增；旧sync到达不复活旧归属。
- [ ] T19 消息/模组超时后查询原操作：未决由原请求人新事件retry，管理员只能查询/中止；不能因到期、退出、重启就释放预占。
- [ ] T20 模组自己的中心连接不可达但本地有预占时，仍能查询到持久reservation和center_available=false；不能回显中心已成功，也不能据本地回执新增/解绑。
- [ ] T21 中心失联：既有可信绑定按镜像保留，新绑定/解绑明确暂停；恢复后只收尾原操作，不重绑、不删除墓碑。
- [ ] T22 计划使用的hybrid/remote/local分别验一遍。local不需要中心，A服local账号不能授权B服；跨服一服成功一服失败应逐服保留结果。
- [ ] T23 四种目标按实际计划分别验Mixin注入、Profile/UUID、原版名单add/remove/reload、重启与在线限制解除；缺可靠Profile保持待确认，不猜UUID。
- [ ] T24 `/qqbind status`、`list`、`unbind TestPlayer`、`operation <编号>`与显式retry/cancel；管理员权限撤销后旧队列不能继续写，COMMITTED不能回滚。
- [ ] T25 后台操作页按中心/原local服务器查询；未登录/过期拒绝，普通用户不能读他人关联，后台不能新建或代办未决绑定。浏览器布局/弹层需实测。

### 第四轮：写命令及边界

- [ ] T26 `/mc broadcast -s 测试服 合成测试`、`-s all`：仅管理员、最多4服；每服恰当报告，重复同事件不重复广播。
- [ ] T27 `/mc console -s 测试服 list`：独立控制台令牌有效才执行；普通成员、错误令牌、未选服/all及被禁止命令均拒绝。瞬时输出返回，正文不进入插件/模组API持久记录。
- [ ] T28 在已DISPATCHED后中断/超时，`/mc command <编号>`显示UNKNOWN或确定结果，不自动再执行；用户自己核对目标实际结果。
- [ ] T29 add/edit/remove/hide/show、batchadd/batchremove/move/swap与WebUI按revision防覆盖；重复事件不重复追加，批量存在无效项时整批不改。
- [ ] T30 退群、被踢或聊天黑名单不自动游戏解绑；需要撤销时管理员显式提交。官方私聊/频道和旧QQ号迁移保持不支持，不视为本轮故障。
- [ ] T31 lastonline开关与后台保存一致；状态显示最后活动或无可靠记录，数据源时间按毫秒处理。
- [ ] T32 内邀成员有权/无权分别核对bindcheck/checknick；分页、游标重复、截断、author/mentions昵称缓存、未知昵称不伪装全量；检测不修改游戏绑定。
- [ ] T33 管理员check @成员/按游戏名/显式user定位；普通用户不能读他人完整关联，降权后旧管理员分页失效；绑定本人仍正常。
- [ ] T34 超管在官方群用 `/mc whitelist add @成员`、`/mc blacklist add @成员` 管理名单：回复不出现完整 openid；`/mc whitelist list` 只显示脱敏值；不 @ 或 @ 多人时拒绝；普通管理员被拒绝。确认 OneBot 公共名单文件/配置未被改写；官方 openid 按群匹配，换群后需重新 @成员 添加。
- [ ] T35 `/mc guide`、`/mc 命令指引`：Markdown 卡片和按钮正常显示；页1为2列5行，页2为3列4行；群聊点击只在输入框填入命令，不自动执行；普通成员无管理入口，级别3通过 `/mc guide 2` 进入；页2不显示“管理指引”、令牌、名单或返回按钮；不支持 Markdown 时只收到一次纯文本清单，未知/审核中不重复；确认 OneBot 无该入口且命令行为不变。

## 5. 建议的结果记录

只记录：用例编号、连接类型、AstrBot版本、模组加载器/MC版本、权威模式、通过/失败/待修以及不含私密值的错误类别。不要提供真实AppSecret/API Token、OpenID、群配置、绑定数据或真实日志；必要定位可在全新临时环境使用合成事件。

下一步：本轮只交付源码与前端发布资源，不跑本清单。后续集中做简洁必要验证、更新四Jar，再由用户逐组实机验收T01–T35；条件成员能力和群昵称未知边界保持明确。
