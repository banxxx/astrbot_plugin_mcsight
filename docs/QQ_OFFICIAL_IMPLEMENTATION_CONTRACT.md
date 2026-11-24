# QQ 官方机器人兼容实现契约

- 版本：v1.22，2026-11-24；对应主方案v1.25，群昵称事件增量见附录AC；2026-11-24 增加官方群 Markdown + 自定义按钮命令指引，业务决定不变。
- F01–F05已修改源码，并补齐可实现管理/查询差异；nickname检测现消费author与mentions里的member_nickname。本轮不测试、不重编四模块，旧Jar不含本轮增量。既有附录通过数字不适用于未验证修改，真实QQ/MC/SQL/浏览器验收仍未完成。
- 业务已确认：同进程单一实例生效、官方全新绑定、一用户多账号、中心失联不新增/解绑、独立官方镜像。
- 既有批准：操作记录表，以及绑定行的撤销状态和版本。本次新增确认：账号体系隔离、昵称支持一对多、群资格与游戏解绑不自动联动。
- 用户已授权：开发前条件满足后，先告知再直接进入 S1。真实数据、凭据、建表、停启均由用户操作。
- 当前交付与用户验收步骤见[交付说明](QQ_OFFICIAL_DELIVERY.md)；执行清单和历次审阅记录仅在本机留存，不随 Git 提交。业务规则已确认不等于全部接口、DDL 或固定版本能力已经验证。

## 1. 不变量与命名空间

1. 活动连接最多一个；其它连接无业务副作用，也无setup例外。
2. 插件和属于同一绑定分区的模组使用一致的 environment_id、group_id、存储类型和协议版本。不同群或不同 local 服务器不因使用同一机器人应用而共享绑定。
3. environment_id为内部UUID，用于环境来源校验，不是OpenID、密钥或跨平台用户映射。换官方应用、重建空绑定环境或从旧备份重建权威数据时，明确建立新代次/环境，不能沿用旧镜像。
4. 官方group_id使用实际group_openid，qq使用member_openid；原样保存，不转换成数字、不更改大小写、不静默截断。
5. 输入ID必须为非空字符串、长度1–64、无控制字符及首尾空白；拒绝不合规值，不在存储前悄悄修剪出另一个ID。若未来平台长度变化，先改协议并说明兼容条件。
6. 游戏名沿用已支持的1–16位英文字母、数字、下划线。game_key为ASCII小写，所有查重、索引、JavaMap、Python和镜像统一按game_key识别；game_id保留展示大小写。
7. 中心模式的同一 environment/group/game_key 有一个稳定绑定行；local 的稳定记录另受 server_id 隔离。改绑定人、撤销或重新绑定都增加 revision，不删除行重置版本。
8. 一个用户有多个有效游戏绑定，不能建立用户唯一约束。
9. 不读取旧OneBot表/镜像兜底。不存在、无权限、损坏、网络失败分开报告。
10. 原版ban/其它服务器规则继续有效；绑定只处理原有准入与限制逻辑，不自动解除其它封禁。

游戏名大小写归一仅用于官方新存储及对应新模式，不对旧OneBot历史行批量变更。正版/离线身份、跨认证体系同名及游戏改名不在本次自动身份迁移范围。

### 1.1 已确认的账号体系与群资格规则

- 一个中心 environment/group 分区只容纳同一账号体系；同分区的模组共享该分区绑定。部署登记时由用户确认账号体系一致，普通握手不能证明真实玩家身份一致。
- 不同账号体系通过不同官方群绑定分区或各服 local 权威隔离；不能用相同 environment/group 配置后依靠不同 server_id 隔离中心行。当前中心唯一键不含 server_id。
- 本期不引入同一官方群下多个中心身份域，也不把正版、离线或不同登录系统的同名账号自动关联。若实际部署必须采用这种拓扑，应先扩展方案再接入。
- local 的唯一归属、查询、昵称依据、幂等操作和版本均限定 environment/group/server。不能拿 local A 服的身份关系授权 local B 服。
- 退群、被踢、聊天黑名单、成员列表缺席及群角色变化都不自动解绑。黑名单继续阻止聊天命令；需要撤销游戏关系时由管理员显式提交解绑操作。
- 缺失或不完整的成员列表不能作为自动撤销依据。重新入群不会新建、转移或恢复已撤销的关系；仍由当前绑定状态决定。

## 2. 插件配置归属

### 2.1 最小持久对象

| 对象 | 最小字段 | 规则 |
|---|---|---|
| activation | platform、instance_id、environment_id、config_revision | 整组原子保存，不允许一次仅改半组后重载 |
| 官方环境 | environment_id、公开应用引用、实例关联 | 不要求助手读取AppSecret；应用改变不得自动复用旧环境 |
| 官方群配置 | config_id、environment_id、group_openid、alias、display_group、servers、policy、revision | config_id为安全随机内部键 |
| 服务器条目 | server_id、name、host、game_port/api_port、绑定目标信息、已有凭据引用 | 同名/同地址不自动合并 |
| 官方名单 | 环境级超管、群级管理员/黑名单/昵称忽略项、环境库 `environment_access` 补充名单 | 范围显式；不沿用旧数字QQ身份；群内 @成员 只写补充名单，不改 OneBot 公共名单；openid 按群匹配，换群需重加 |

不将任意OpenID直接拼入文件路径。OneBot继续旧配置路径，官方使用独立目录/安全键；活动范围由统一解析器提供给聊天、WebUI与采样。

应用归属由用户在WebUI确认或由框架公开元信息核对，不能只因实例ID没变就认为AppID没变；不要求助手查看真实机器人配置。环境引用与bindingGroupId可由用户取得无密钥模板，自行应用到模组。

### 2.2 保存和恢复

- 每个配置写入按config_id加锁，并校验revision；冲突要求刷新，不能静默覆盖。
- 写临时文件后原子替换；失败保留上一份活动文件。
- registry与配置JSON不是一个事务：先写完整配置，再切换注册指针；准备记录/迁移日志支持中断恢复。
- 指针不存在或配置损坏不能创建空文件覆盖原归属。
- 旧数据只留作停用备份，不双写两个活动来源。
- 首期改变活动实例要求维护窗口完整停启；不实现群消息热切换。

### 2.3 首次登记

已选官方实例在CONFIGURING状态只开放setup/help。WebUI“新增群”填写别名后生成一次性验证码，5 分钟有效；在目标群发送 `/mc setup <验证码>` 后原子消费验证码并创建默认未启用的群配置。生成与消费均不访问MC、不读旧群、不写中心库；验证码只落哈希，不经 URL 或消息重复传输。

每环境最多保留 10 个未使用验证码，过期或已消费的条目按保留窗口清理。普通成员即使知道验证码也只能创建群配置，不能借此启用业务或提权。真实bindingGroupId由用户在界面自行复制到模组，不发送给助手。

非活动实例永远不响应setup。官方C2C和频道在配置管理器构造前拒绝进入该流程。

### 2.4 管理权限

- `/mc setup <验证码>` 不建立身份候选、不授予管理员权限；真实环境、群和发言者 ID 只从事件提取，不接收消息参数指定的 user_id 或角色。
- 管理命令按 QQ 官方事件中可识别的群主/管理员角色与群策略开关判定；未知角色不猜成管理员，群昵称缺失不影响该判定。
- 本版 WebUI 不再提供“待配置群”列表和身份核对授权界面；昵称检测忽略名单在群内使用 `/mc nicknamecheck 忽略 @成员` 管理。后端保留既有授权存储字段以供旧数据兼容，但新群不再依赖它才能使用管理命令。
- 黑名单优先级和“退群/被踢/聊天黑名单不自动解绑游戏账号”的运行时语义保持不变；WebUI 撤销授权不再作为常规操作入口。

## 3. 单活选择与WebUI

- 第一次旧OneBot唯一候选兼容初始化后，将具体ID写入公开选择字段；适配器还在加载时不得抢先持久选择。
- 官方显式选择，重载后复用明确ID；被删/缺失/不可用时不改选其它账号。
- 状态分selected、registered、online/unknown、configuring、ready；在线无法可靠得知则显示unknown，不伪造健康状态。
- setup不需要绑定表就绪；绑定写入需要全部中心能力就绪。当前S1的not_ready总开关在官方适配阶段拆成“是否选中”和“功能是否就绪”两级。
- 前端store、types和页面完整接收activation；显示待重载、缺失、歧义及未开放原因；构建后的views/config随发布提交。
- WebUI可以准备非活动平台的本地配置；远程读写必须指定活动环境。旧OneBot远程动作不能在官方活动时借当前全局Token继续执行。
- 插件不stop全局事件；不保证其它插件不会回复同一消息。

## 4. 数据库两表设计

本节是最终DDL的字段/约束规范，实际CREATE语句在实现验证后作为单独交付；本轮不在线执行SQL。

### 4.1 bindings_qqofficial

| 字段 | 类型方向 | 职责 |
|---|---|---|
| id | BIGINT UNSIGNED 自增主键 | 内部记录ID |
| environment_id | CHAR(36)，ASCII精确比较 | 当前官方环境 |
| group_id | VARCHAR(64)，utf8mb4_bin | group_openid |
| game_key | VARCHAR(16)，ASCII精确比较 | 小写规范游戏名 |
| game_id | VARCHAR(64)，utf8mb4_bin | 展示游戏名，API仍校验1–16位 |
| qq | VARCHAR(64)，utf8mb4_bin | member_openid；撤销行保留最近归属供受权审计 |
| state | active/revoked | 当前有效与否 |
| revision | BIGINT UNSIGNED | 每个稳定绑定行从1单调增加 |
| last_operation_id | CHAR(36) | 最近一次状态变更 |
| bind_time / updated_at | 带微秒UTC时间 | 当前绑定时间、最近更新 |

索引：

- 主键id。
- 唯一(environment_id, group_id, game_key)。
- 普通(environment_id, group_id, qq, state)，支持一用户多账号。
- 不设置qq或group+qq的唯一约束。
- 新增environment_id不意味着多应用并行生效；只是避免旧环境/镜像误用。

utf8mb4_bin提供大小写敏感比较；同时拒绝首尾空白，不能依赖某些排序规则对尾空格的特殊行为。长度超限由代码明确拒绝，数据库严格模式也要核验。不得依赖TiDB默认排序规则。

所有有效业务查询加state=active；同步快照则同时携带active和revoked。重新绑定复用同一行并增加revision，不覆盖成revision=1。

### 4.2 bindings_qqofficial_ops

| 字段组 | 内容 |
|---|---|
| 身份 | operation_id UUID主键、environment_id、group_id |
| 幂等 | request_key、payload_hash；同一个key只能一种载荷 |
| 意图 | bind/unbind_game/unbind_user、actor、受权范围、目标game_key/用户 |
| 验证信息 | 原服务器ID、token_generation/reservation_id；不存原始码 |
| 状态 | PREPARED、RESERVED、COMMITTED、REJECTED、ABORTED |
| 结果 | effects JSON、业务结果与错误分类、created_at/updated_at |
| 后续待办 | 码确认状态、每目标服务器同步/副作用状态、重试次数和下次时间 |

- 唯一(environment_id, request_key)，对操作状态/待办时间建必要索引。
- 绑定操作额外唯一(environment_id, 原server_id, token_generation)，阻止同一代验证码被两个操作消费；非绑定操作的验证码列为空。已经确定中止后必须发新generation，不复用旧generation创建新操作。
- request_key从已核验的平台事件标识、场景、请求人和动作派生；不使用“文本相同＋时间接近”去重。模组管理命令也产生稳定操作号。
- payload_hash覆盖环境/群/动作/目标/原用户与版本/验证代次，不保存可离线枚举六位码的普通散列。
- 同operation_id不同载荷拒绝；用户不能通过猜操作号读取其它人的操作结果。
- effects是本次实际变更账号、状态和版本的固定集合，批量解绑后不重新查询“现在有哪些账号”来决定同步。
- COMMITTED/REJECTED/ABORTED是业务终态，不因网络ACK失败改写成另一个结果；后续送达状态可变。
- 操作表兼作最小outbox，不再额外建设第三张同步表或跨平台身份表。

同操作的业务状态、码确认及各目标送达结果分别更新，锁住操作行合并，不能并发整块覆盖JSON丢失别的目标ACK。批量effects最多500项；超过时事务不执行并提示按游戏ID分批处理，不静默只解绑一部分。

未完成操作、当前被绑定行引用的操作、revoked行首先不自动清理；容量在管理页可观测。后续清理必须保证重放/墓碑窗口，不在首版默认执行DELETE。

## 5. 事务和并发约束

- 中心数据库是官方中心模式的权威；镜像不能反向当成中心成功。
- 一个绑定变更和对应COMMITTED结果、effects/待办在同一事务提交；沿用当前单句autocommit包装不足。
- 事务锁住操作行，并以行锁/条件更新保护目标绑定。无已有行的首次绑定由唯一约束处理并发，冲突后读取明确结果。
- 普通自助解绑把当前归属和预期revision放进修改条件，防止先查后删误伤另一人的新绑定。
- 批量解绑固定操作集合并按game_key排序加锁；只撤销当前身份在该群的有效行，不影响并发新增后不属于本次集合的记录。
- 一次批量解绑的数据库变更全成或全败；不同模组的送达结果可以分别待同步。新加入的账号不被旧请求再次枚举后顺带撤销。
- 短事务不含HTTP调用、等待验证码或发送聊天消息。事务冲突可在明确未提交时有限重试，提交结果不明时先按operation_id查询。
- 同用户再次绑定自己已有账号可返回“已绑定”幂等结果；新用户不能覆盖另一有效归属。已撤销账号重新验证后可形成新的有效归属和更高revision。
- 使用已有账号作为昵称校验依据时，提交前必须复查该账号仍为当前用户所有且 active；记录并校验依据账号的 revision 与群策略修订，相关行按 game_key 统一顺序锁定。依据已撤销或归属/策略改变则终止本次新增，不能继续使用旧权限依据。
- 公开玩家统计不因“只看本人绑定”被误改为仅本人可查询。

## 6. 验证码流程与恢复

### 6.1 接口概念

新增官方协议使用v2能力，旧OneBot接口保持原模式。官方路径固定如下，不用旧的无范围直绑路径兜底：

| 接口 | 必要内容 |
|---|---|
| GET /api/v2/binding/capabilities | 协议版本、环境/群/服务器、存储类型、schema版本、一对多、操作/版本/镜像能力 |
| POST /api/v2/binding/token/validate | JSON请求传码；返回定位、game_id、server_id、token_generation、期限；不消费，不据此放行 |
| POST /api/v2/binding/token/reserve | operation_id、环境/群/用户/game、token_generation、码；原子认领且持久确认 |
| POST /api/v2/binding/token/finalize | 同操作号的提交/中止确认，重复结果一致 |
| GET /api/v2/binding/operations/{operationId} | 经过服务鉴权的操作状态；插件向用户展示前再校验本人/管理员权限 |
| POST /api/v2/binding/sync | 已确认中心结果、各game_key的state/revision、目标来源，不接受无范围删除 |
| GET /api/v2/binding/check | 按 game_id 或 user_id 查询，支持 game_ids 列表和 cursor，鉴权及范围校验必需 |
| POST /api/v2/binding/bind | local的操作式绑定，或模组端发起的中心操作；必须走同一预占/持久结果契约，禁止普通gameId参数直绑 |
| POST /api/v2/binding/unbind | operation_id、目标模式、归属/版本条件；local与center按各自权威提交 |

新请求携带 protocol_version、environment_id、group_id 及目标 server_id；group_id 对应模组配置 bindingGroupId。原有 API 鉴权继续使用，不在 URL 传令牌或六位绑定码。能力摘要不回显数据库主机、账号、密码或完整 JDBC 连接串。

响应沿用success/code/message/data信封，data包含protocol_version、operation_id及必要state/revision；message只用于展示，程序按错误码判定。码预占回复包含reservation_id与token_generation；sync回复分别包含applied/ignored/conflict、持久化revision、白名单及在线状态的完成范围。错误响应不回显原始请求、码或完整用户身份。

群/环境是绑定隔离键，API服务鉴权不是用户权限：插件在转发前检查当前调用者，模组检查目标来源和协议；游戏内管理员命令由模组验证游戏权限后生成等价操作。客户端不能仅传任意actor就获得管理权。

官方功能握手未通过时，不回退到传统gameId直绑路径。更换表名、group键、协议或模式需完整停启。

V2 的验证码代次统一命名为 `token_generation`；`generation_id` 不作为另一种代次字段并存。完整字段映射、请求/响应类型、HTTP 状态与业务错误码按附录 B 冻结，不能由 Python 与 Java 各自解释。

#### 6.1.1 旧接口与存储模式门禁

| 入口 | OneBot 模式 | 官方模式 |
|---|---|---|
| 旧 `/api/bind`、`/api/unbind`、`/api/sync_binding` | 保留旧行为并回归 | 服务鉴权后返回 NOT_SUPPORTED，不进入存储或游戏副作用 |
| 旧 `/api/validate_token`、`/api/check` | 保留旧行为 | 返回 NOT_SUPPORTED；官方客户端使用带范围的 V2，避免旧字段误作完整结果 |
| 旧游戏内按 QQ/游戏名直接绑定命令 | 保留原模式与权限 | 明确拒绝旧直绑；新版入口必须生成等价操作并遵守验证/提交契约 |
| 游戏内管理员解绑 | 保留原模式 | 验证游戏权限后提交带范围、版本和操作号的新操作；断库不能转为本地成功 |
| 在线、统计、TPS、广播、控制台等非绑定接口 | 保留原能力 | 按各自能力、鉴权及权限开放，不因绑定表不可写一并关闭 |

官方 local 不能接受中心 `/binding/sync` 作为权威写入；即使 API 凭据有效也必须按模式/来源拒绝。不能仅在机器人侧限制而遗漏模组侧旧入口。

### 6.2 绑定状态机

1. 同一重复事件先查request_key；已有操作返回原状态，不重新消费码。
2. 通过所选群的服务器集合定位码；只有所有候选均可判定且恰好一台命中时才自动选服。多个命中，或任一候选超时、无权限、不支持新验证或结果未知时，要求指定服务器，不能把其它服务器没答复解释为不存在相同码。
3. 创建PREPARED操作，确定用户、服务器、群与游戏名；昵称/权限校验未通过不进入中心变更。
4. 模组原子预占token generation；一个generation只能属于一个operation_id。持久化预占日志成功后才回复已预占。
5. 中心操作更新为RESERVED，然后在短事务中复查状态/归属，提交绑定行和COMMITTED结果/待办。
6. 确认提交后请求消费该generation，并推送固定effects。已提交但模组不在线显示“中心已绑定，目标待同步”。
7. 聊天发送失败不回滚已提交绑定；重试查询同操作结果，不重新插入。

### 6.3 预占持久化

- 六位码本身仍只存内存；每次发码产生随机 token_generation。
- 模组保存最小预占日志：环境/群/服务器、generation、operation_id、game_key、用户归属与状态；不保存/打印原始码。
- 无绑定关系的玩家退出/重启使未预占码失效；未确认预占操作不能因玩家退出或原码到期而自动换给另一身份。
- 重启从日志和中心ops对账；中心不可查时保留待确认，并暂停该账号新绑定，不生成可覆盖未决操作的新路径。
- PREPARED过期或想中止时，必须先在中心事务内将该操作CAS为ABORTED，保证后来的提交无法通过，再释放本地预占。
- 若中心已COMMITTED，只能确认消费，不能中止。
- 确定失败/中止后旧generation作废，用户取新码；不承诺失败后原码总能无缝复用。
- 本地预占日志、镜像均使用临时文件＋持久化＋原子替换，失败不能返回已保存。

没有跨数据库与HTTP的分布式事务。本协议通过持久结果、预占和重试恢复，不承诺任意故障下立即完成；未决状态须可查询和人工诊断。

### 6.4 定位缓存与用户恢复入口

- 显式选服的规范命令为 `/mc bind <六位码> -s <服务器名>`；中文别名需支持相同选择语义。选服后只验证该服，错误不回退别服。名称只用于选择，进入操作后固定 server_id。
- 路由缓存限定 environment/instance/group/配置修订，记录原 server_id、game_key、token_generation 与有效期；不能仅凭六码命中就信任旧结果。自动选服时，缓存只优化探测顺序，不能省略对其它候选的歧义检查。
- 新代次、配置或环境改变立即失效；再次验证返回的账号/代次不符时作废缓存，不沿用旧预占。
- 保留每用户、每群及服务端的有界失败预算；超时、无权限和暂时故障不计成“无效码”。全服定位的未命中不应耗尽全群共用出口的预算，具体额度在构造压力测试中确定。
- `/mc operation <操作短编号>` 与 WebUI 操作页提供状态查询，按本人或管理员权限限制。短编号仅供定位，解析歧义时要求更完整编号，不授予访问权限。
- 结果未知时查询同一操作；聊天发送失败不产生新绑定。用户用新事件重复提交同代次时，若属于同一身份与同一意图，应关联原操作；不同身份或载荷返回冲突。
- WebUI 中的恢复操作只能按状态机重查、重送已提交结果，或 CAS 中止尚未提交操作；不提供把未知结果强行改成成功的按钮。

## 7. 同步顺序和镜像

- scope、environment、group、server、协议不匹配立即拒绝，不能只因全局API鉴权通过就写入错误镜像。
- 增量按game_key比较revision：更大应用；更小忽略；相同且内容一致幂等ACK；同版本内容不同报告冲突并暂停相关自动覆盖。
- revoked状态也存入镜像，不能只删除Map项丢失版本。缓存数据必须绑定当前存储来源和版本。
- 正向索引game_key→用户，反向用户→Set<game_key>只包含active；删除A不清掉同用户B。
- 镜像先持久，再ACK镜像已应用。原版whitelist命令和在线解限是独立副作用，失败需重试/可诊断，不伪称已全部完成。
- 完整同步使用中心一致性读，携带active和revoked；加载中接收的增量也按同一锁/版本合并，不能直接replaceAll覆盖较新记录。
- 同一环境不物理删绑定行，因此快照里缺行不能随便解释为删除；本地较新或来源不一致需对账。手工清表/旧备份恢复必须更新环境/恢复代次再重建镜像。
- 断库前只有旧镜像时，已撤销但尚未送达的授权可能仍生效，无法承诺离线即时撤销；管理页显示待同步。
- 后台从COMMITTED操作恢复待办，按目标有限退避；首次2秒、随后10/30/60秒，再至5分钟上限，配置改变时先核对来源。
- 已提交任务可以在同环境重启后续发，不得随平台切换投递到新环境；真实切换前必须检查未决任务。

### 7.1 白名单与在线状态的过期任务

- 每项任务携带 environment/group/server/game_key、revision、目标 state 及原 operation_id；镜像送达与副作用完成分开 ACK。
- 按账号串行安排副作用；在游戏主线程实际执行前复查最新已持久镜像与来源，不能只在 HTTP 收包时查一次。较旧任务标记为 SUPERSEDED，不再执行白名单或玩家模式动作。
- 同版本同目标可以重复校正；同版本不同内容进入冲突处理。更新与副作用的排队需要协调，更新后的校正不能被旧任务最后覆盖；主线程不得等待数据库或文件 I/O。
- ACK 标明对应操作的 requested_revision、当前 effective_revision 及各副作用结果；旧任务的 ACK 不能覆盖新任务结果。被替代表示旧目标无需再应用，不表示旧动作曾成功执行。
- 验收必须包含“绑定 v1 加白名单失败 → 解绑 v2 完成 → 重试 v1”“解绑旧任务在重新绑定后到达”，以及动作已完成但 ACK 丢失、重启后重复校正。

## 8. 存储模式与断库矩阵

| 模式 | 新变更权威 | 失联/失败规则 | 验收 |
|---|---|---|---|
| OneBot旧模式 | 沿用原行为 | 本轮不扩大语义变更 | 必做回归 |
| 官方hybrid | 新中心两表 | 新变更提交不了即暂停；合法本环境镜像供已有绑定判断 | 首先验收 |
| 官方remote | 同一新中心两表 | 同样的失联与恢复对账，不保留当前“只连通不校验表”的漏洞 | 中心阶段单独验收 |
| 官方local | 官方本地存储 | 它本身是权威，无中心依赖；预占/状态写入必须在本地原子处理 | 后续单独验收，最终范围保留 |

- DB连接有效不等于目标表/schema/权限正确：缺表、无权限、格式错误与临时断网分开。
- local/remote/hybrid选择需显式配置并停启，不因数据库失败自动换成另一种绑定权威。
- 官方中心断库限制覆盖API、游戏内管理命令、插件和WebUI的绑定变更；不能只加机器人门禁。
- 插件可提交但某模组断库时，带鉴权且来源一致的已提交同步可落镜像；不得被“禁止新增”错误拦住。
- 无镜像、损坏、来源不明时不按旧OneBot镜像放行；没有绑定的非OP玩家仍按现有方式进入受限/取码流程，中心断网时明确绑定暂不可提交。
- 网络错误不能使失败记录写入本地后报告中心成功。
- 权威状态、镜像、原版白名单与游戏限制要分别诊断；不批量清空原版whitelist/OP/world。

### 8.1 local 的原子性与跨服结果

- 每服独立保存绑定记录、预占、操作结果与待执行副作用；采用附录 D 的单文件原子提交。分别原子替换两个文件不等于跨文件事务。
- local 的重启恢复只依据本服权威，不能要求中心 ops 表存在。原始验证码仍不落盘；已预占操作重启后可按记录恢复且不能重新分配给另一身份。
- 单服批量解绑全成或全败；跨服操作固定各目标，给每服独立操作号并逐服汇总成功、失败、待确认。不会为了回滚一台服而重新绑定另一台已经解绑的账号。
- 无法取得某服完整查询结果时，不能把该服表示成“无绑定”或“全部解绑成功”；重试沿用该服原操作号，不重新枚举并扩大旧请求范围。

## 9. 一对多API和命令

- V2 按用户查询返回 game_ids 列表（为空时 []），按 game_key 稳定排序。每页最多 100，cursor 明确后续结果；不能只返回第一行。
- 单账号查询返回 game_id、bound、状态/版本；普通用户不公开显示他人的完整 member_openid。
- 旧 gameId 单值字段只在旧 OneBot 接口兼容展示，不能被新官方插件当完整列表。官方写入前必须确认 peer 的 multi_account 能力。
- /查绑定不带参数查当前身份全部有效账号；游戏ID查询只在当前官方群范围。
- 官方增加“用户”语义，支持从事件中明确@成员或管理员后台定位；旧QQ参数可作为协议兼容别名，但不要求普通用户手输长OpenID。
- /解绑 A只撤销当前身份对A的归属；管理员按既有策略操作别人时必须审计本群和具体目标。
- 无参数/按用户解绑撤销当前范围全部目标，明确列出数量和结果，effects与实际事务一致。
- 对同一游戏的并发绑定由唯一键和版本条件决定，不靠防抖。
- 共享代码修正一对多不得误删OneBot原有多账号；旧客户端不支持列表时明确其限制，不伪称完整兼容。

### 9.1 已批准的昵称与多账号规则

- 本节只调整官方模式。关闭昵称要求时仍逐账号验码；开启时必须取得可靠的当前群昵称，不能用 username、旧发言缓存或游戏名推断。
- 当前权威范围内无 active 账号时，昵称必须按本群已配置的匹配规则匹配本次待绑定账号。
- 已有 active 账号时，可匹配本次待绑定账号，或匹配本人名下任一 active 账号。每个新增账号仍单独完成验证、预占和提交，旧账号不代替新账号验证码。
- 沿用现有昵称格式及其文本比较规则；game_key 只用于确定账号归属和版本。不得无说明改变旧 OneBot 昵称规则。
- 记录采用哪个账号及哪个 revision 作为依据；提交前复查见第 5 节。revoked、其它用户、其它群、其它 local 服和来源不明镜像均不能作为依据；中心无法读写时不能借镜像审批新绑定。
- 解绑 A 后 B 仍有效，但昵称若只匹配已撤销 A，再新增 C 时应失败并提示匹配 B 或 C；全部解绑后回到首次绑定规则。改昵称本身不自动解绑。
- 全群“昵称检测”的格式检查与绑定时的账号匹配是不同能力，保留原格式检查语义并在界面说明；不得因无法取全员昵称而声称全群账号归属已核验。

## 10. 能力、权限和命令失败

- 能力有AVAILABLE、UNSUPPORTED、UNAUTHORIZED、TEMPORARY/UNKNOWN，附来源/时间；权限失败与临时断网不同。
- 官方当前发言者角色/昵称优先使用实际原始事件；缺少字段时按普通成员/无法校验，不把username当群名片。
- 群管理员名单不能自动授予环境超管；既有OneBot名单范围保留。
- 成员列表须全部分页成功且未截断；失败只能给不完整结果或明确不可用。全群昵称还需要所有成员可靠群昵称。
- 本人绑定和公开统计不同：不泄露他人私有绑定，不改变公开统计可查他人的既有能力。
- 功能检查在进入相应外部请求前完成，缺新表不应让在线状态全部不可用。
- 错误分类至少包括INVALID_INPUT、UNAUTHORIZED、NOT_SUPPORTED、SCOPE_MISMATCH、SCHEMA_MISMATCH、DB_UNAVAILABLE、CONFLICT、RESULT_UNKNOWN、SYNC_PENDING。
- 对用户只显示必要说明和操作短编号，不打印DB异常原文、原始事件、完整平台ID或HTTP凭据。

### 10.1 官方权限矩阵

| 行为 | 最低授权 |
|---|---|
| setup/help、公开查询 | 非黑名单成员；setup 不授予管理权 |
| 绑定、查询本人全部账号、自助解绑 | 当前本人；绑定另检查验证码与昵称策略 |
| 查询他人绑定详情、解绑他人、全群检测 | 当前群管理员或显式授予的更高权限；仅限对应范围 |
| 广播、服务器条目管理、控制台 | 按既有开关允许的群主/群管、插件群管理员或更高权限；控制台还需该服独立令牌并显式选服 |
| 配置控制台令牌、管理插件授权名单 | 显式环境超管；不把群管自动升级为超管 |
| WebUI 配置与恢复 | 框架已认证后台管理员；不信任请求体提供的 actor/role |

聊天黑名单优先于其它聊天角色；原始角色缺失不提权，但已显式授予且范围匹配的插件权限仍可用。框架全局管理员的映射是否满足官方环境隔离须按固定版本验证，不能照搬旧数字 QQ 名单。普通成员查看他人游戏账号时最多给当前范围内的绑定状态，不公开对方完整官方用户标识或全部账号关联。

### 10.2 绑定以外的变更命令去重

- 官方广播、控制台和服务器/权限配置变更按 environment/instance/group/平台事件标识/请求人/规范动作建立持久接收记录；事件重投、重启、别名重入都查同一记录，短时防抖仍只用于限频。
- 不写第三张中心同步表；采用插件侧独立的最小持久记录，只有成功持久化才能执行副作用。记录不保存原始消息、控制台正文或凭据；载荷指纹不宣称匿名化，也不替代权限校验。
- 控制台在发出前持久标记 DISPATCHED；终态为 SUCCEEDED、REJECTED 或 UNKNOWN。DISPATCHED 后崩溃即使可能尚未真正发出也按 UNKNOWN 处理，不自动补发。同事件重复请求只返回原状态。
- 广播逐目标保存发送状态；不向已成功或结果未知的目标重复发送。配置变更用请求修订和操作标识确认已经应用，不能在重启后重复追加条目。
- 未决与变更去重记录首版不自动清理，并显示数量；后续清理须先验证平台重投窗口和事件有效期规则。无可靠事件标识时暂停这些官方变更命令，不用文本加时间窗口冒充幂等。

## 11. 分页、图片、日志和缓存

- 官方一轮计划最多4条实际发送，预留错误提示余量；不得只按event.send调用次数计算，框架拆包/回退须核验。
- 每页只发送一个图片组件，默认图片编码体积不超过4MiB；超限压缩/减小分页高度，仍不能满足则明确失败，不上传私人图床。
- 官方群 `/mc guide [1|2]`（别名 `/mc 命令指引`）使用已验证的 BotPy 群发送通道，单次 `msg_type=2`，仅携带 `markdown.content` 与 `keyboard.content.rows`，不携带文本 `content`。`rows` 每项为 `{"buttons": [...]}`；最多5行、每行最多5个按钮，按钮文字不超过10字符。自定义按钮属于QQ内邀/按钮模版能力，插件不假设所有应用已开通；未开通或客户端不支持时由平台拒绝并走文本降级。
- 按钮为 `action.type=2` 命令填充型：群聊点击只把 `@机器人` 与 `action.data` 填入输入框，不自动执行、不产生 `INTERACTION_CREATE`；插件不对命令填充本身计为业务执行。第1页公开，固定2列5行；第2页要求级别≥3，通过 `/mc guide 2` 进入，固定3列4行，不另设“管理指引”按钮，也不放令牌、权限名单、黑名单按钮。正文使用原生 `***` 分割线，不使用 Emoji。服务端仍对实际命令重新鉴权。
- Markdown 仅在其发送结果被平台明确拒绝（`OfficialApiError.rejected`）且尚未产生可见消息时回退一次纯文本命令清单；`pending`/`unknown` 或已有其它消息尝试时不得补发，避免重复可见回复。一次指引最多两个 HTTP 发送调用，仍计入一轮4次预算。
- 下一页使用新事件的回复凭据，不保存旧事件对象长期回复。
- 分页归属environment/instance/group/requester/query，TTL10分钟；每用户群只保留最新结果，全局最多32个快照且合计128MiB，超出最旧淘汰。
- 初始最多显示预算内页数，后续提示给完整下一页命令；配置/权限改变、停启、环境改变立即失效。
- 名单缓存5分钟；接口能力临时失败短缓存60秒；管理员许可不因名单缓存长期保留。
- 启动/退出只一套worker；取消协程不代表SQL或MC命令取消。存在未知写入时不能显示“切换已安全完成”。
- 日志不记录码、关联凭据、原始消息、Token、DSN或完整平台身份；操作号和内部对象ID供诊断。普通哈希不宣称匿名化。
- 日志验收覆盖框架入口、插件、模组及 HTTP 访问日志；插件自己的脱敏不代表框架或反向代理已脱敏。固定版本行为未核实前不承诺全链路满足；需要真实配置调整的步骤由用户按模板自行执行，验证只使用构造消息和虚构码，不要求助手读取真实日志。
- 测试不导入会读用户目录的真实初始化模块；先审查脚本再执行。构建需要读取受限制文件时明确说明，不靠隐藏输出绕过用户限制。

容量和时间是实现默认值；可在构造数据性能验证后调整，但不能放宽身份隔离、成功语义或无权限限制。

## 12. 版本、模式和验收门槛

### 12.1 固定基线与证据状态

- AstrBot 目标 4.29.1。本次已取得该 tag 的关键公开源码；已核验结论与仍需用户验收的边界见附录 A。此前失败的读取和 4.29.4 参考不作为本次证据。
- 必须确认实例枚举、get_platform_id/name、官方群/C2C/频道区分、原始author字段、媒体发送与WebUI认证能力。
- 优先在4.29.1做兼容封装；字段/应用无能力就降级。确需升级时先说明证据及影响，不自动要求用户换版本。
- 模组正式矩阵来自工程：Forge1.20.1、NeoForge1.21.1、Fabric1.20.1/1.21.1。其它目录不自动算发布模块。
- 每个模块构建和登录路径、hybrid/remote/local分别验收；最终范围不缩减，阶段性不支持明确标注。
- 前端源码与views/config发布产物一起更新；帮助、元数据与真实功能矩阵一致。

### 12.2 关键用例

- [ ] 同群双适配器只一个生效，非活动帮助/setup/@/别名/翻页均无业务副作用。
- [ ] 重载不改选实例，适配器未就绪不抢选；官方CONFIGURING可登记但不能绑定。
- [ ] 同用户A/B都保留；删除A不丢B，批量解绑结果与同步集合一致。
- [ ] 同游戏不同大小写不产生双归属；OpenID不同大小写不错误合并；越长值拒绝。
- [ ] 所有新表读取过滤active，revoked不被当成仍绑定。
- [ ] 码验证、预占、提交前后、消费ACK前后各断点重启，结果不重复提交也不重新分配未决码。
- [ ] DB提交超时查同操作，聊天失败不回滚；旧generation不能在解绑后复用。
- [ ] 解绑权限查验与更新原子，不能删除另一用户后来绑定的记录。
- [ ] bind旧同步在unbind后到达不复活；旧全量快照不覆盖新增量。
- [ ] 中心断库禁止API/游戏命令新增解绑；已提交同步在模组断库时可更新正确镜像。
- [ ] 缺表/无权限/损坏镜像/空新表/断网分别有结果，不回退旧表或旧镜像。
- [ ] 同步来源不匹配拒绝；中心成功但目标离线显示待同步；ACK范围准确。
- [ ] 非OP、开启检查的原玩家可以取新码；原版白名单和游戏模式不会被无说明批量改变。
- [ ] 4.29.1的群角色、昵称缺失、名单受限、图片/分页实际规则分别有合成和用户侧验收。
- [ ] 维护窗口停旧启新无双活，备份恢复不复活旧授权，旧OneBot回归通过。
- [ ] 构造测试及文档检查不访问真实数据、配置或凭据。
- [ ] 同一中心分区禁止混入不同账号体系；local 同名账号在不同服务器之间保持独立，跨服失败准确逐服展示。
- [ ] 首绑昵称匹配待绑账号；后续匹配 A 可绑定 B 且 B 必须验码；A 被撤销/换人后不能继续作为依据；全解绑后重新执行首绑规则。
- [ ] 退群、被踢、拉黑或成员列表不完整不写 revoked；显式解绑仍按操作契约处理。
- [ ] 一服命中且另一服超时要求选服；相同六码、多代次、配置变化不会因缓存误绑；超时不记作无效码。
- [ ] 官方模式调用旧绑定接口在存储前拒绝；local 不接受中心镜像作为权威。
- [ ] 白名单和在线状态的旧任务被新版本替代；副作用成功但 ACK 丢失及重启不会复活旧授权。
- [ ] setup 回执不能提权；管理员核对、授权与撤销流程无需手输或向助手提供真实 OpenID。
- [ ] 广播、控制台与配置事件重复投递及重启不重复执行；结果未知可查且不自动重发。
- [ ] 框架入口至模组全链路使用虚构码验证日志边界；图片预算统计实际发送及回退请求。

## 13. 开发前后责任

已由用户确认的业务不再反复询问。尚待完成的是本契约的实现、构造数据演练及真实环境能力验证，不能因写好文档就打勾。

开发前交付见以下附录。固定版本关键接口已有证据，协议与存储设计已固定；数据库执行、真实官方消息发送与部署权限不冒充已验证。通知用户后可先完成 S1；官方业务保持关闭，S2/S3/S4 的实现及各自验收仍须分别完成。最终可执行 DDL 与用户配置/停启手册随实现交付，助手不在线建表或重置用户数据。

若需要缩减最终模式、取消既定功能、改变失联准入策略、改动旧OneBot数据或提高实际最低版本，必须提前指出并取得用户决定。普通实现细节不再增加审批轮次。

## 附录 A：AstrBot 4.29.1 的公开源码证据

核验日期 2026-11-17，只访问公开源码与文档，没有启动真实框架。以下源码路径均位于 `AstrBotDevs/AstrBot` 的 `v4.29.1` tag；行号采用 GitHub 一基行号。

| 证据 | 已核验事实 | 本插件采用的规则 |
|---|---|---|
| `core/core_lifecycle.py:242,269,339`、`core/star/star_manager.py:1315` | 插件 initialize 在平台 initialize 前；on_astrbot_loaded 在框架启动完成阶段执行 | 首次 OneBot 自动选实例等待启动完成钩子；逐个平台加载钩子不能代表全部已加载 |
| `core/platform/manager.py:228,241`、`api/event/filter/__init__.py:19,28` | 可枚举已注册适配器；提供启动完成与单个平台加载钩子，平台钩子不传事件参数 | 使用 get_insts/meta 与公开钩子；重载用同一 Context 上本插件保存的公开生命周期标记，未证实就等待或要求显式选择 |
| `core/platform/platform.py:49`、`core/platform/manager.py:251` | status 表示适配器任务状态，RUNNING 不是实际 QQ 在线证明 | 显示 registered/adapter_status，online 始终保守为 unknown；错误或停止不自动接管 |
| `sources/qqofficial/qqofficial_platform_adapter.py:39,128,735,790` | patched message 保留 raw_data；消息对象保留 raw_message、message.id；群发送者取 member_openid、显示名取 username | 稳定事件键来自 message.id；群角色/群昵称读实际 raw_data.author，缺字段按既定降级，username 不作群名片 |
| `sources/qqofficial_webhook/qo_webhook_adapter.py:31,85,110` | Webhook 共用官方解析器与 parser；平台名为 qq_official_webhook；另有 qq_official | 两个传输归属同一平台类别但仍选具体实例；群/频道不得只按 GROUP_MESSAGE 枚举区分 |
| `sources/qqofficial/qqofficial_message_event.py:364,442,628,689` | 媒体会拆包，出站序号随机生成，失败路径可移除 msg_id 后回退；媒体上传另有重试 | S2 使用独立发送封装统计实际请求并限制回退；不能拿 event.send 次数证明预算。首版按请求分页，超时由用户新事件查询 |
| `core/star/context.py:687`、`dashboard/api/plugins.py:13,259` | register_web_api 支持同路径替换；页面桥使用的 v1 插件扩展路由依赖 plugin_scope_dependency，接受登录身份或具备插件范围的 API key | 沿用框架授予的插件范围权限，不信任请求体 actor；不承诺群级 WebUI 权限，部署须验证未认证/过期拒绝 |
| `core/event_bus.py:53,93` | 事件进入插件调度前记录 user_chat 类别的发送者和消息概要 | 插件过滤不足以覆盖框架日志；上线前由用户配置框架入口与访问日志，使用虚构码验证；不得读取真实日志来验收 |
| `pyproject.toml:7,46` | 项目要求 Python >=3.12，qq-botpy 固定 1.2.1 | 本机标准库隔离测试与真实框架运行分别记账；不把 Python 3.10 的隔离测试称为框架集成验证 |

源码入口：

- `https://github.com/AstrBotDevs/AstrBot/tree/v4.29.1/astrbot`
- `https://github.com/AstrBotDevs/AstrBot/blob/v4.29.1/pyproject.toml`
- `https://docs.pingcap.com/tidb/stable/character-set-and-collation/`
- `https://docs.pingcap.com/tidb/stable/transaction-overview/`

QQ 应用的成员接口授权、角色/群昵称实际下发、媒体与回复限额仍需用户按应用权限验收。2026-11-18 补查了 Webhook 事件的继承与 AstrBot 事件自带图片上传协议，见附录 I 和 QQ_OFFICIAL_SOURCE_EVIDENCE.md；此源码证据不扩大为两个传输全链路均已实机兼容。

## 附录 B：V2 线上字段与样例（全部为合成数据）

### B.1 公共类型和信封

- 新 V2 请求统一采用 snake_case：`protocol_version=2`、`environment_id`、`group_id`、`server_id`。配置项 bindingGroupId 映射到 group_id，SQL qq 映射到线上 user_id；V2 使用 game_id/game_ids，旧 gameId/gameIds 只属于旧接口。
- environment_id、server_id、operation_id、token_generation、reservation_id 为标准小写 UUID；OpenID 按第 1 节原样保存。`revision`、`expected_revision`、`store_revision`、`policy_revision` 在线上为十进制字符串，范围 1..9223372036854775807；避免 JavaScript 数值精度损失，也不超出 Java long。
- UTC 时间使用 RFC3339 `Z` 字符串，微秒可选。`code` 永远是稳定业务错误码；验证码字段另名 `token_code`，只在 validate/reserve/bind 请求体中短暂存在，不参与持久日志或普通散列。
- HTTP 200 为确定结果，202 为操作处理中/结果未知，400 为输入错误，401/403 为鉴权/授权，404 为不存在或不可向当前用户披露，409 为范围/版本/载荷冲突，503 为依赖暂不可用。程序仍按信封 code 判定。
- code 包含 OK、PREPARED、RESERVED、ALREADY_BOUND、INVALID_INPUT、UNAUTHORIZED、NOT_SUPPORTED、SCOPE_MISMATCH、SCHEMA_MISMATCH、DB_UNAVAILABLE、CONFLICT、RESULT_UNKNOWN、SYNC_PENDING、TOKEN_INVALID、SERVER_REQUIRED、STORAGE_ERROR、LIMIT_EXCEEDED。
- 所有端点先校验服务鉴权和目标范围；GET 的范围放查询参数，POST 放 JSON。服务凭据仅在现有认证头，不出现在样例、URL、日志或结果中。
- bind/unbind 另带 `actor={"kind":"qq_member","id":"SYNTHETIC_USER_A"}` 和 `authorization={"scope":"self","policy_revision":"1"}`；管理员 scope=group 或 environment，游戏端 kind=game_admin。该声明只接受已鉴权插件在验证事件和权限后转发，或模组本地验证游戏权限后生成；普通用户请求体不能自行声明高权限。操作表保存已校验的授权范围到 intent。

公共请求片段 S（下面每个请求均合入 S）：

```json
{"protocol_version":2,"environment_id":"11111111-1111-4111-8111-111111111111","group_id":"SYNTHETIC_GROUP_A","server_id":"22222222-2222-4222-8222-222222222222"}
```

成功与失败信封示例：

```json
{"success":true,"code":"OK","message":"已完成","data":{"protocol_version":2,"operation_id":"33333333-3333-4333-8333-333333333333","state":"COMMITTED"}}
```

```json
{"success":false,"code":"RESULT_UNKNOWN","message":"结果待确认，请查询操作状态","data":{"protocol_version":2,"operation_id":"33333333-3333-4333-8333-333333333333","state":"RESERVED"}}
```

state 是最后已知持久业务状态，不新增 RESULT_UNKNOWN 业务终态。已提交但未全部送达返回 success=true/code=SYNC_PENDING/state=COMMITTED。

### B.2 各端点请求与 data 样例

表中每个请求均带 S，响应 data 均带 protocol_version；operation_id 只在已有操作时出现。带尖括号的 UUID/散列是说明占位符，实际线上必须满足 B.1 类型。

| 方法/路径（前缀 /api/v2/binding） | S 以外的请求字段 | 响应 data 的必要内容/合成示例 |
|---|---|---|
| GET /capabilities | 无 | `{"storage_type":"qq_official","storage_mode":"hybrid","schema_version":1,"game_key_rule":"ascii_lower","multi_account":true,"operation_results":true,"revision_sync":true,"authority":"center","scope":S}` |
| POST /token/validate | `{"token_code":"123456"}`（虚构码） | `{"game_id":"Player_A","game_key":"player_a","token_generation":"<UUID>","expires_at":"2026-11-17T15:00:00Z","scope":S}`；失效为 TOKEN_INVALID |
| POST /token/reserve | `{"operation_id":"<UUID>","user_id":"SYNTHETIC_USER_A","game_key":"player_a","token_generation":"<UUID>","token_code":"123456"}` | `{"operation_id":"<UUID>","reservation_id":"<UUID>","token_generation":"<UUID>","state":"RESERVED","game_key":"player_a"}`；同代次不同身份或操作拒绝，不改归属 |
| POST /token/finalize | `{"operation_id":"<UUID>","reservation_id":"<UUID>","decision":"COMMITTED"}`，或 decision=ABORTED | `{"operation_id":"<UUID>","token_generation":"<UUID>","token_state":"CONSUMED"}`，中止为 INVALIDATED；中心模式应核对中心结果，暂时不可查保持待确认，不信任客户端自称成功 |
| GET /operations/{operation_id} | 无；界面层另检查本人/管理员 | `{"operation_id":"<UUID>","state":"COMMITTED","kind":"bind","effects":[{"game_key":"player_a","game_id":"Player_A","user_id":"SYNTHETIC_USER_A","state":"active","revision":"1"}],"delivery":{"<server UUID>":{"mirror":"APPLIED","whitelist":"PENDING","online":"PENDING"}}}` |
| POST /sync | `{"operation_id":"<UUID>","effects":[{"game_key":"player_a","game_id":"Player_A","user_id":"SYNTHETIC_USER_A","state":"active","revision":"1"}]}` | `{"operation_id":"<UUID>","results":[{"game_key":"player_a","result":"applied","requested_revision":"1","effective_revision":"1","persisted":true,"whitelist":"PENDING","online":"PENDING"}]}`；result=applied/ignored/conflict，副作用可为 SUPERSEDED |
| GET /check | game_id 或 user_id 二选一；limit=1..100、cursor 可省略 | 用户查询：`{"game_ids":["Player_A","Player_B"],"bindings":[{"game_id":"Player_A","game_key":"player_a","state":"active","revision":"1"},{"game_id":"Player_B","game_key":"player_b","state":"active","revision":"1"}],"next_cursor":null}`；单游戏未绑定：`{"game_id":"Player_C","bound":false,"revision":null}`，撤销行则带其当前 revision |
| POST /bind | `{"operation_id":"<UUID>","request_key":"<64位十六进制>","user_id":"SYNTHETIC_USER_A","game_id":"Player_A","token_generation":"<UUID>","token_code":"123456","policy_revision":"1","nickname_basis":null}` | 与 operations 的结果结构一致；local 在本服原子提交，center 走同一中心状态机；既有账号作为昵称依据时传 game_key/expected_revision，提交复查归属 |
| POST /unbind | `{"operation_id":"<UUID>","request_key":"<64位十六进制>","kind":"unbind_game","target_user_id":"SYNTHETIC_USER_A","targets":[{"game_key":"player_a","expected_revision":"1"}]}` | `{"operation_id":"<UUID>","state":"COMMITTED","effects":[{"game_key":"player_a","game_id":"Player_A","user_id":"SYNTHETIC_USER_A","state":"revoked","revision":"2"}],"delivery":{}}`；unbind_user 同样固定 targets，上限 500，不在重试时扩大目标 |

- GET /check 分页游标绑定范围与查询、最后 game_key 和查询快照，10 分钟失效；修改后重查返回新快照，不承诺跨不同请求的动态集合绝对一致。游标不是授权；服务鉴权和查询权限每次重查。
- effects、delivery、nickname_basis 单次总编码大小最多 1MiB；服务器目标最多 100 个，超过在提交前拒绝。两表操作记录与副作用大小有界，避免一个批量操作无限扩大 JSON。
- request_key=SHA256(规范事件来源元组)，包含环境、实例、场景、群、事件 ID、请求人、规范动作；源码依据未提供的业务字段不能凭空加入事件。payload_hash 仅覆盖排序后的规范意图 JSON：UTF-8、排序 ASCII 字段名、无空白分隔符、整数使用十进制字符串、列表按 game_key/server_id 排序；不含原始验证码、凭据和昵称全文。
- 中心插件直写两表时与模组 POST /bind 必须采用同一规范服务层；它们不是两个不同的成功定义。finalize 的中心确认不可达不阻止已经确认的同步落正确镜像，码确认仍单独待办。

### B.3 本服预占查询补充（2026-11-18，技术恢复协议定稿）

保留既有 GET /api/v2/binding/operations/{operation_id}，不增加新路径。中心业务状态与本服预占是独立事实，不能把中心 PREPARED 解释为已经预占，也不能把中心失联解释为本服无预占。

- capabilities 可追加布尔 token_reservation_query；缺失或 false 视为不具备此补充能力。true 仅由已经接入本服持久预占仓储、查询及空预占安全作废的模组声明，不能因客户端支持而自动填 true。当前 Java V2 HTTP 已注册，但实际绑定策略/官方取码/MC 动作/恢复调度尚未齐备，因此 capabilities 入口保持 NOT_SUPPORTED，不假报整体就绪。
- operations.data 追加 center_available 布尔和 reservation 对象。reservation 的完整字段固定为 protocol_version=2、scope=S、operation_id、state、token_generation、reservation_id、game_key、user_id；state 仅 ABSENT/RESERVED/CONSUMED/INVALIDATED。身份和版本均按现有规范比较。
- ABSENT 只表示本服已成功校验当前权威文件、未找到此 operation_id；代次、预占号、游戏键与用户字段全为 null。损坏、丢失主文件、来源不符、读取失败和未知提交绝不能用 ABSENT 代替。该 GET 不创建操作/墓碑、不消费码，也不撤销中心绑定。
- center_available=true 时保留原中心 state、kind、effects、delivery。中心可查且 PREPARED/RESERVED：HTTP 202、success=false、code 为对应状态；终态沿用现有确定结果规则。
- 中心暂不可查但本服文件有效：HTTP 202、success=false、code=RESULT_UNKNOWN，仍返回 reservation。data.state 如有值，只能是本服上次已核验的中心状态；没有依据时省略。center_available=false 不影响插件使用 reservation 的持久事实做对账，但不授权新绑定/解绑、不构造 COMMITTED。
- 只有 scope、operation、generation、reservation、game_key、user 全匹配的本服记录才能恢复成中心 RESERVED；随后插件必须重新检查当前授权/昵称策略再提交。CONSUMED/INVALIDATED 是经中心终态核验并落盘的本服确认，可用于丢 ACK 后的恢复确认。

未预占代次作废使用既有 POST /token/finalize 的可选补充字段：decision 必须 ABORTED，reservation_id 可为 null，此时 token_generation 必填。插件必须先在中心把该操作原子中止；中心 COMMITTED 赢得裁决则只能确认消费。模组不信任请求中的 decision，重新核验中心为 ABORTED/REJECTED、scope/operation/generation/目标一致后，在同一主文件写入 INVALIDATED 墓碑并生成稳定 reservation_id，再回成功。响应额外带 reservation_id 供中心记录该确认。重复结果一致；同代次/操作迟到 reserve 读到墓碑必须拒绝。中心失联时不写墓碑、不释放未决占用。

中心已知 reservation_id、但本服主文件有效且该行缺失时，中止确认可采用原 reservation_id 写墓碑，前提仍是当前中心确定中止；COMMITTED 缺失本服预占时返回 STORAGE_ERROR、停止自动确认消费，保持中心成功和待办，不能当作安全未绑定。手工恢复旧快照/重建空文件仍必须更换环境或恢复代次，禁止沿用原环境伪造 ABSENT。

## 附录 C：中心两表 DDL 草案（仅供审阅，不在线执行）

数据库名由用户选择已有 qqbind_db；草案不包含 USE、凭据、旧表变更或自动迁移。运行账号只需新两表 SELECT/INSERT/UPDATE；建表由用户的 DDL 账号执行。先核验目标数据库版本、严格模式、JSON/微秒时间、索引与排序规则；此草案尚未在 TiDB 实例执行。

```sql
CREATE TABLE bindings_qqofficial (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    environment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    group_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    game_key VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    game_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    qq VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision BIGINT UNSIGNED NOT NULL,
    last_operation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bind_time DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_official_game (environment_id, group_id, game_key),
    KEY ix_official_user (environment_id, group_id, qq, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE bindings_qqofficial_ops (
    operation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    group_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    request_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    target_user_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    original_server_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    token_generation CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    reservation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    policy_revision BIGINT UNSIGNED NOT NULL,
    intent JSON NOT NULL,
    nickname_basis JSON NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result_code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    effects JSON NOT NULL,
    followup JSON NOT NULL,
    next_attempt_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (operation_id),
    UNIQUE KEY uq_official_request (environment_id, request_key),
    UNIQUE KEY uq_official_token (environment_id, original_server_id, token_generation),
    KEY ix_official_pending (environment_id, state, next_attempt_at),
    KEY ix_official_actor (environment_id, group_id, actor_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

- 时间由服务端以 UTC 写入，连接设 UTC；不信任客户端时钟。应用校验 state/action 枚举、字段界限、game_key 与 game_id 的关系、revision 正数及上界；不依赖旧 TiDB 可能不强制执行的 CHECK。
- 事务显式 BEGIN/COMMIT；锁操作行，再按 game_key 顺序锁绑定及昵称依据，条件更新同时检查原用户/状态/revision。无行首次插入以唯一键裁决，冲突后重读；COMMIT 超时先查同一 operation_id，不能凭回滚调用成功断言先前提交未发生。
- 同一事务内写 COMMITTED、固定 effects、token-finalize 待办与逐目标 delivery；其它线程合并 ACK 时锁操作行，不覆盖其它目标。DDL 不借 CREATE IF NOT EXISTS 隐藏结构不匹配。

## 附录 D：local、镜像与命令记录持久格式

- local 使用一个版本化 JSON 状态文件，每服独立目录、schema_version=1，包含 scope、store_revision、bindings（含撤销）、reservations、operations、pending_effects；一次绑定变更连同操作终态及待办全部落在同一文件版本。派生双向索引只在内存中构建，不作为另一份权威。
- 写入流程：账号锁/文件提交锁 → 构造完整下一版本 → 同目录独占临时文件 → flush/fsync → atomic replace → 支持时同步目录 → 发布内存版本 → ACK。文件系统不支持原子替换则停止写入，不退化成先删后写；替换后响应丢失通过文件中的操作号确认。
- 临时文件不作为成功记录；主文件损坏或来源不匹配停止变更，不能自动用可能更旧的副本放行。对具体 OS/文件系统的掉电持久性必须单独演练，不能凭 API 名称承诺所有硬件故障下不丢数据。
- 官方中心模式的镜像与预占同样采用版本化单文件，但 authority=center、本地不是中心 ops 权威；不可查中心时保留未决。同步只应用已确认的中心结果，不允许离线创建关系。
- 单个状态文件默认上限 64MiB，写入前检查；达到上限暂停新变更并提示维护，既有数据不截断。首版保留墓碑和未决/引用操作，后续有安全清理策略再调整。
- 非绑定命令使用插件独立 `command_receipts.json`，同样原子替换；记录 event_key、action、target_server_ids、payload_hash、config_revision、state 与每目标结果，不保存命令正文。PREPARED 尚未外发可中止；先持久 DISPATCHED 再外发，重启后的 DISPATCHED 为 UNKNOWN。容量同样有界，满时拒绝新动作而不丢旧去重记录。

## 附录 E：故障推演与用户反馈

| 断点/场景 | 固定结果与恢复 |
|---|---|
| 预占落盘前失败 | 无成功预占；若响应未知先查模组同 operation_id，不能假定未预占 |
| 模组已 RESERVED、中心仍 PREPARED | 重启按相同操作对账，确认预占后继续或中心 CAS 中止；不重新分配代次 |
| 中心 COMMIT 超时 | 返回 RESULT_UNKNOWN；查同 operation_id，COMMITTED 则成功，仍未决不盲插入 |
| 中心成功、finalize/镜像/副作用失败 | COMMITTED 保持终态；对应目标 SYNC_PENDING，后台重送，聊天失败不回滚 |
| abort 与 commit 并发 | 中心操作行的条件更新裁决，胜者唯一；只有确认 ABORTED 才能释放预占 |
| v1 旧同步或旧白名单任务晚于 v2 | 旧镜像忽略，副作用 SUPERSEDED，不复活已撤销关系 |
| 昵称依据 A 在新增 B 前被解绑 | 事务复查失败，返回 CONFLICT，B 不新增；用户匹配当前有效账号或 B 后重新取码 |
| 跨 local A 成功、B 失联 | 分别报告，B 结果未知继续查同子操作；不撤销 A 来伪装跨服原子性 |
| 六位码一服命中、另一服无响应 | SERVER_REQUIRED，要求 -s 明确服务器，尚不预占或提交 |
| 控制台 DISPATCHED 后崩溃 | UNKNOWN，同事件不重发；用户核对目标服后使用新事件明确再次执行 |
| 官方实例缺失或配置损坏 | 停止对应业务，不选另一实例、不加载旧绑定或覆盖损坏配置 |

这些是设计推演，后续测试必须实际覆盖；没有运行 TiDB、MC 或真实消息环境，不标记这些场景已通过。

## 附录 F：S1 实施边界

- `activation.json` 是只含公开选择字段的独立权威记录，含 schema_version、selection、官方环境引用登记。旧 active_platform/active_bot_instance 只用于首次初始化，已有记录后不再用旧字段覆盖它。
- selection 同次原子保存 platform/instance_id/environment_id/config_revision；官方应用引用由用户主动填写与确认，选择器只读 meta，不探查适配器 config、密钥或登录态。
- 保存是待完整重启的目标。同一 Context 保存本插件当前运行选择和加载完成标记，插件热重载沿用它；新的 Context/完整进程启动才应用磁盘选择。状态查询无选择、写盘或副作用。
- 已有明确选择可以按其 ID 等待注册；只有首次旧 OneBot 无 ID 的兼容初始化需要可靠的启动完成钩子。安装/重载时错过钩子且无选择时要求显式保存后完整重启，不根据第一条消息选实例。
- WebUI 使用专门的选择保存端点与 expected_revision，不能通过旧单字段接口保存半组。显示运行中与已保存的差异、具体候选、注册/适配器状态及 unknown 在线状态。
- S1 测试仅使用临时目录与构造配置/框架替身，不导入真实插件初始化；官方 READY_PLATFORMS 门禁维持关闭。源码层的 D0 已达到该阶段开工条件；实际官方发送、成员权限、DDL 和模组故障演练属于后续各阶段放行条件。

## 附录 G：S2 当前实现与验收边界

- 官方入口新增 accepts 实例门禁与独立 official_runtime 分发，allows 仍只授权旧 OneBot 业务；因此已选官方的合法群事件可登记/查询，C2C、频道、身份不一致或非活动实例提前退出。所有别名遵守相同门禁，广播别名也不能先构造旧群配置。
- 每环境使用一个 `config.sqlite3`（WAL）作为原子配置提交单元，集中保存 groups、servers、group_members、candidates、environment_admins、audit_log、environment_access、setup_codes 与 server_remarks；`meta.revision` 为环境级修订号，群级 `groups.revision` 独立。所有写入在 `BEGIN IMMEDIATE` 事务中完成；这是第 2 节 registry/群配置的具体实现，不声称跨数据库有事务。OpenID 只作为字段值，不参与路径拼接，群和服务器有独立内部 ID。环境超管在环境级存储；群黑名单、群管、昵称忽略分别保留，撤销一种权限不删除其它名单。群内 `@成员` 名单管理写 `environment_access`（三类名单各限 200 项），与 OneBot 公共名单只读合并；不修改公共名单。数据库损坏、schema 版本或环境 UUID 不匹配时失败关闭；正式库缺失但 `legacy/` 仍有迁移留档时同样失败关闭，不静默建空库。后台可导出可编辑 JSON 备份（群、服务器、policy、名单、备注；不含验证码、控制台 Token 与审计明细），导入时整份校验、单事务替换并提升修订号；明确导入时允许在留档存在的情况下重建并恢复。群内列表与回执不回显完整官方标识。
- 旧版 `state.json` / `access.json` / `setup_codes/` / `remarks/` 在环境首次打开时一次性导入 SQLite，成功后移到同目录 `legacy/` 留档；导入失败不创建正式库，也不覆盖旧文件。每服控制台 Token 仍保存在 `console_tokens/<server_id>.json`，不进入 SQLite、不回传 WebUI。
- 模组 API Token 与 OneBot 共用插件全局配置 mod_api_token，官方目录不另存凭据；运行时按需从注入的配置读取器取值，普通状态页面只检查是否已配置，不返回凭据内容。旧 OneBot 名单不自动复用；测试用临时目录与虚构值。
- setup 不访问 MC 或绑定表，普通成员只能建立限量且过期的候选。后台授权检查候选所属群，记录目标身份、来源群、候选回执、授权/撤销种类及时间。后台主体依赖框架认证，不接受请求体 actor 提权。
- 只读请求仅允许 /api/status、/api/tps、/api/stats/<合法玩家名>、/api/stats/all；关闭重定向、代理环境与自动重试，响应限 4MiB，不打印请求/响应或异常原文。无模组在线查询使用 mcstatus；隐藏条目不查询。官方统计按环境、群和稳定 server_id 写入独立 SQLite，仅存合成/公开数值字段，保留 30 日 UTC 日快照并复用共享六类统计图；同日覆盖，不导入 OneBot 历史，历史不可用时仍可读生涯统计。完整统计图保持单页，超编码限制回退分页文字。本人排行关联仍依赖 S3 当前官方绑定，不能读取旧 bindings 兜底。
- 图片发送只使用匹配已核验 AstrBot 官方事件族/上传方法签名的 BotPy 直发封装，或经签名核对的显式群文件接口。内部动作名不是宣称 BotPy 提供同名 helper；file_data 群载荷依照 AstrBot 4.29.1 公开源码构造。缺少能力、渲染失败或体积超限时提供本人文字分页；不传 URL 参数冒充 base64，也不上传私人图床。合成测试证明本插件的单次 request 与载荷，不证明应用权限、实际 QQ 媒体预算或真实 WebSocket/Webhook 收发均已兼容。

- 官方 `/mc help` 使用仅含官方能力和已开放命令的说明；`/mc bindhelp` 明确绑定尚未开放，不复用 OneBot QQ 号/旧取码流程。当前消息原始角色仅在字段可识别时用于权限；username 不作为群昵称。群昵称字段缺失时昵称校验明确不可用，全群成员名单/昵称检测保持关闭，不自动按退群、踢出或名单缺失解绑。
- event_receipts.json 在任何回复前落盘事件指纹，发送计数/序号先持久再发送。每环境最多 4096 条，保留 7 天；满时拒绝新请求。它只用于只读回复防重，不代替 S3 持久业务操作协议，也不承诺窗口之外或跨应用的严格一次。
- 查询结果至多 32 个、总量 128MiB、10 分钟有效，仅存编码图片/分页文字，不保存事件对象；权限/配置及环境变化使缓存失效，上传等待后发布前再检查。最多 16 个在途查询，同用户串行，45 秒查询预算。卸载取消请求且关闭分页，不把已外发未知请求记成可安全重发。
- 本机的 Python 标准库隔离测试、离线 Vue 自定义渲染器组件测试和 PNG 编码检查，不等于真实框架/QQ 网络或浏览器 CSS 验收。浏览器预览被 URL 策略拒绝后停止尝试，不绕过策略访问真实或其它浏览器登录态。

## 附录 H：S3 开发核心与续接约束（2026-11-18）

- 两表仓储只接受显式 DB-API 连接工厂，不加载配置/凭据或旧 OneBot 数据库池。当前验证是临时 SQLite 和合成连接器，未证明 MySQL/TiDB 的结构、权限、行锁、连接池无重试及隔离级别。结构握手和真实 SQL 方言核验完成前不能开放官方写入。
- 预占意图必须在外发前原子写入 followup.token_reservation；并发只有首次认领可发送 reserve。PREPARED 已发起预占、RESERVED 未提交，以及终态的 token/delivery 待办均可被恢复查询发现。next_attempt_at 不代表业务成功；恢复仍检查业务状态并采用相同 operation_id。
- 终态移除预占标记，保留单独 token_finalize 待办。COMMITTED 已确定时，finalize 超时或 ACK 丢失保持 success=true/code=SYNC_PENDING；可用内部 followup_code 标明未确认原因，不伪造绑定失败或回滚。
- 编排服务 policy_guard 必须由宿主实现：预定位阶段检查当前活动环境与授权；已解析 game_id 后核验可靠昵称及当前官方 active 账号依据；提交阶段再复核授权/策略并持有配置锁到事务完成。未接入 guard 即拒绝新变更。恢复不读取旧昵称缓存、不把请求体 actor 当作实际授权。17 项服务用例验证依赖注入边界，尚不证明具体插件权限/昵称集成。
- peer 回执是内部标准化对象，须绑定环境/群/原服务器、operation_id、generation、reservation、game_key 和用户。线上适配器须按附录 B 解析真实响应，再补入已鉴权上下文；不可把此内部对象宣布为新增公开 API。原服预占查询、尚未预占或代次丢失时的中止/确认仍需完成；未知时保留记录。
- /sync 的公开语义保持 effects 下行、results 回执；中心 acknowledge 只由已验证的调用结果触发，不注册成 /sync 的上行写入口。协议边界核对 COMMITTED 的固定 effects 和目标服务器后才可调用镜像服务；local 拒绝中心镜像。当前镜像 handler 是显式依赖，未提供实际 Java 镜像/白名单实现。
- /capabilities 必须来自显式能力探测，不能因 Python 模块存在就填 true。固定 expected_scope、服务鉴权、查询授权和操作可见性均需注入；错误信封只返回固定说明，不回显底层异常。PREPARED/RESERVED 响应为 202 且非成功，业务终态与 result_code 分开。
- /check 单游戏保留 revoked 的 revision，未绑定不存在时 revision=null；按用户只查 active。分页缓存最多 32 个快照、每次最多 4096 行及 1 MiB，十分钟过期；游标绑定 scope/可信请求人/查询用户/page size，逐页复核权限。重启或淘汰后要求重查，不能把游标视为授权。
- 当前 172 项完整 Python 回归包含 28 项仓储、17 项编排和 14 项协议用例。源码无生产 HTTP/聊天入口、后台恢复页、真实 token peer、官方连接配置或自动恢复 worker 接入，QQBindMod 未改。具体功能缺口与下一包见执行清单第 6 节，不能声明 S3/S4 完成。

## 附录 I：V2 出站模组传输与图片源码补查（2026-11-18）

- utils/official_transport.py 是出站模组客户端，不是生产中心 HTTP 服务。目标包含固定 environment/group/server、主机与端口，只有显式传入的令牌或令牌回调；没有初始化时网络/配置 I/O。每次新建标准库连接，连接成功后关闭隐式重连，仅一次 request，不跟随重定向、无代理或 Cookie。宿主应在线程中执行，尚未接插件路由/worker。
- 请求只允许附录 B 的端点与字段；GET 范围进入查询参数，码只进入 POST JSON 正文，认证只进头；输入、输出均受 1 MiB 上限。连接失败与外发后超时分开，变更发出后的无效/截断回执保持 RESULT_UNKNOWN/may_have_executed；不盲重发，不把网络失败计成无效码。远端 message 和异常原文不成为错误文本。
- 握手核对协议、目标 scope、storage_type、storage_mode、schema_version、game_key_rule、一对多、操作结果、revision_sync 和 authority。这里只验证对端声明，不证明实际表、索引、事务权限或四加载器已正确实现；中心 schema/权限探测仍待完成。握手失败清除 ready，不允许自动降到旧接口或其它存储权威。
- V2TokenPeer 对 validate/reserve/finalize 使用附录 B 的既定请求/响应字段。capabilities 和 validate 的 scope 必须由对端回显；reserve/finalize 兼容样例中的无 scope 回显，有回显则逐字核对。无回显时，范围与用户上下文仅来自固定握手目标和已持久原操作，并明确是内部补齐，不声称对端实际返回了缺失字段。
- 非活动 guard 在调用 token_source 前以及连接等待后执行；模块不保存真实令牌或验证码。只有已持久的预占认领才能调用 reserve；只有中心 COMMITTED/REJECTED/ABORTED 才可 finalize。操作号、代次、预占号和回显账号任一不符均不能消费确认。
- status 尚不能从附录 B 的中心 operations 推导本服预占，当前明确 NOT_SUPPORTED、无额外请求。下一包须设计/实现同一操作查询中的本服预占响应和安全中止/确认，不能悄悄增加协议外路径或把中心 PREPARED 当作已预占。本轮成功链路的假 HTTP 测试不代表这一恢复缺口消失。
- 默认连接超时 2 秒、请求期限 8 秒；可检查 I/O 阶段刷新 socket 剩余超时。阻塞 DNS 和单次慢速头解析仍不能宣称硬取消；生产线程/停启与真实 TLS/网络需要后续验证。HTTPS 保留系统证书验证，不关闭 TLS 校验。
- 本轮新增 33 项传输/字段适配用例和 2 项官方图片事件门禁用例，完整 Python 隔离回归 207 项通过。测试注入假连接、合成令牌与临时 SQLite，未真实访问模组、数据库或 QQ。
- 上游公开源码定位记录见 QQ_OFFICIAL_SOURCE_EVIDENCE.md。完整源码副本下载因网络执行自动审批接口故障未执行，没有取得本地散列，不冒充已归档源文件。此前附录 H 的 172 项结果属于前一包；本轮实际状态及后续任务以执行清单最新第 6 节为准。

## 附录 J：模组独立预占核心实施边界（2026-11-18）

- QQBindMod 新增 common-src 的 official 包：OfficialScope、OfficialException、OfficialModState、OfficialTokenService、OfficialTokenApi。它们没有 Minecraft、QQBindConfig、数据库连接池或旧 TokenManager 依赖；不静态启动线程/网络。代码未注册到 WebServer、发码 Title、登录事件、命令或真实配置，不能声称模组 V2 已部署可用。
- OfficialModState 每服一个 schema_version=1、authority=center 的 official-state.json，包含 scope、store_revision、bindings、reservations、operations、pending_effects。当前 token 阶段只允许 reservations 非空，其余保留字段如已有数据会拒绝，不覆盖未知镜像。预占与消费/作废确认都在同一版本文件原子提交；码本身只存内存，退出/重启/过期丢失未预占码，预占不会因上述事件失效。
- 新状态目录必须由用户/宿主明确指示 createFresh；默认恢复模式缺主文件拒绝，不自动按空文件放行。持有独占文件锁防止两个写入实例；临时文件 fsync 后同目录 ATOMIC_MOVE 替换，失败不退化成先删后写；替换结果未知时停止当前实例，重开并查询 operation。Windows 上没有宣称目录 fsync 或所有硬件掉电持久性；其它平台支持时同步目录。
- CenterInspector 是显式注入的可信中心操作读取接口，当前尚无生产 JDBC 实现。新预占与 finalize 都依赖中心确认，失联拒绝；operation 查询仍可返回本服持久记录。中心网络读取不在文件提交锁内等待；迟到预占与中止通过本服墓碑再检查裁决，不复活已作废代次。原版白名单、在线状态和其它封禁均未由 token 核心修改。
- 附录 B.3 的 ABSENT 示例及空预占 finalize 请求存于 QQBindMod/tests/fixtures/official_reservation_v2.json，Java 和 Python 均直接使用。该文件显式标注 synthetic，不含实际身份或码。生产未知/缺损数据不参与测试。
- 223 项 Python 隔离回归通过（含 41 项传输、25 项编排）；26 项独立 Java 测试在 JDK 17、JDK 21 均通过。Java 脚本只显式编译新 official 源文件和合成测试，-proc:none、临时 user.home、清空环境，仅复制指定公共 Gson jar；没有执行 Gradle、读取 gradle.properties、初始化真实模组或访问网络。这不是四加载器正式模块构建或 MC 实机验收。

## 附录 K：一对多镜像与版本化副作用核心（2026-11-18）

- 附录 J 中“仅允许 reservations 非空”是前一阶段边界，本包在同一 schema_version=1 文件实现 bindings、operations、pending_effects 校验与持久化；未将状态拆成多份分别提交的文件。预占操作和镜像写入都基于 store_revision 比较，冲突不会覆盖另一提交。
- OfficialMirrorModel 固定记录结构及 limits，OfficialMirrorService 对可信已提交 effects 持久落镜像与任务，再返回 results；原版白名单/在线动作尚未发生时分别 PENDING。固定操作载荷不能被重试缩小/扩大；全量一致性快照按行版本合并，缺行不删除；部分操作快照后可由完整 effects 补齐。旧快照/增量不复活撤销墓碑。
- 同版本同内容幂等，异内容使账号 conflicted 并持久 CONFLICT，暂停该账号的自动覆盖和放行；较高来包不会自行解除冲突。内存反向索引 user->Set<game_key>只收 active 且未冲突账号，解绑一个不会移除其余账号。状态/索引带环境、群、服务器来源。
- 持久任务主键为 operation_id:game_key，保存用户/游戏名/key、目标 state/revision 及 whitelist/online 两个独立结果。限 10000 绑定、10000 操作、50000 待办、64 MiB 状态文件；满时暂停，首版不清理墓碑/未决任务。
- I/O worker 只在持久镜像发布后捕获 Ticket；游戏线程 execute 检查 scope、版本、目标内容、conflicted、停用与写盘中标记后才执行 GameEffects。此函数不调用存储、中心或持锁等待。宿主必须提供准确 onGameThread 判断、同步幂等 GameEffects，并确保同一游戏线程串行；本包未提供 Minecraft 类的具体动作。
- 写盘中游戏线程直接返回 PENDING，不等待锁。旧动作进行中收到新版本时，结束后的检查标为 SUPERSEDED；新版本待办仍排在同一游戏线程后校正，避免旧任务在新任务之后落回旧状态。ACK 回到 I/O worker 再查最新版本，不能把旧 requested_revision 的结果写到新操作。
- execute 成功后 ACK 丢失，持久任务仍未完成；重启可同版本重复校正而不生成新绑定。APPLIED 是该版本和该分项已确认执行；SUPERSEDED 只表示旧目标被新版本替代，不冒充旧动作曾成功。旧 ACK 不回退已观察的新 effective_revision。
- Confirmation 是显式可信来源检查，接入时须核对已鉴权插件的 COMMITTED 操作及目标或可信一致性快照；不强制依赖模组自己的断库连接才能接受已经确认的插件下行。服务鉴权不足、来源不符、载荷冲突及 local 模式仍应拒绝；本包仍只有 center 权威。
- V2MirrorDelivery 按明确 scope 发一次固定 effects，下行 success 不等于游戏动作成功；完整 results 集合/请求版本经仓储复核后只合并该目标/账号分项。超时和本地 ACK 提交未知保持中心 COMMITTED，等待同操作查询/重送；没有内置定时器或假冒已接恢复 worker。
- 共享构造 fixture 为 QQBindMod/tests/fixtures/official_mirror_v2.json，Java API 与 Python 中心投递合并均使用。最新 233 项 Python、58 项独立 Java（JDK 17/21 各通过）是核心离线证据；真实中心 schema/JDBC、主线程动作、四加载器构建与真实 QQ/MC/TiDB 仍未验收。生产能力声明和官方绑定门禁保持关闭。

## 附录 L：中心接入基础与规范散列（2026-11-18）

附录 J/K 的“尚无 JDBC”属于当时阶段记录；本包新增只读 JDBC 适配，不等于完成 Java 写事务、生产接线或真实数据库验收。业务不变量和既定接口路径保持不变。

1. 两端共享 schema_version=1 字段/索引规范。核验两张固定表的 base-table/InnoDB、utf8mb4_bin、字段类型/长度/unsigned/nullable/微秒时间、ASCII/UTF-8 二进制比较及主键/索引顺序。额外唯一键拒绝，不能在官方用户列增加唯一约束；额外普通完整列索引可保留。不会读取旧 bindings、自动修表/迁移/创建空库，结构不符返回 SCHEMA_MISMATCH。
2. 连接必须是明确单主机、新连接、禁用透明重连，UTC、utf8mb4、严格模式与 REPEATABLE READ。Python 只接受显式设置回调和活动 guard；Java 只接受显式 Driver/设置回调，不发现旧池或多主机 URL。设置回调仅由未来正常运行时调用，开发测试一律注入虚构值。连接/读写/Socket 超时并非阻塞 DNS 的硬墙钟保证；未做真实 TLS/数据库实连。
3. Python CheckedOfficialBindings 每事务在业务查询前读取 information_schema 与会话设置，再对两表执行零行 SELECT，以及不含函数/子查询的 EXPLAIN INSERT/UPDATE。不得使用 EXPLAIN ANALYZE、探测性 DML、SHOW GRANTS、授权账号/角色记录或旧表来兜底。1064/1235 等不支持预检时返回 NOT_SUPPORTED，不静默关闭预检。目标 SQL 方言、最小权限和元数据语义须在用户实例验收；目前只有假 DB-API 证据，不能宣称线上权限已确认。
4. Java OfficialCenterSchema/OfficialJdbcCenter 当前只读取中心，不申请写权限。参数化按 operation/environment/group 定位，复核范围、规范意图散列、操作状态、固定 effects 和 followup 后才能充当 CenterInspector。缺记录返回无记录；断库、无权、外域或损坏分别报错，不能转成无记录/安全中止。不自动提交绑定、重连重放或读取镜像伪造中心终态。读取事务结束 rollback 并关闭连接，不影响中心业务状态。
5. payload_hash 使用既有规范意图：UTF-8，无空白，ASCII 字段名按字典序，版本为十进制字符串，目标列表已按游戏 key/服务器 ID 排序。两端拒绝数字/布尔替代字符串和孤立代理项；引号、反斜杠和控制字符转义一致，非 ASCII/补充平面字符及 U+2028/U+2029 保留为 UTF-8。仅规范意图入散列，不含原始验证码、凭据或昵称全文。新增共享 fixture 不是生产记录。
6. sql/qq_official_v1.sql 来自附录 C 并有一致性测试。该文本没有 USE、IF NOT EXISTS、旧表修改或自动执行入口；仍是待用户实例核验的手工建空表模板，不是已完成部署。测试生成器只读安全源码/契约，使用新临时 SQLite 和虚构身份，写共享夹具和 SQL 文本；合成时间固定为 2026-11-18T00:00:00Z。
7. 当前共 253 项 Python 回归和 77 项独立 Java 用例（26 预占、32 镜像/副作用、19 中心），JDK 17/21 各通过。未启动实际插件、游戏、网络或真实数据库。前端未改，沿用已有离线组件/类型/构建记录。
8. 放行前仍须补 Java 中心变更/恢复、生产配置与旧写入口门禁、已鉴权插件下行/一致性快照的 Confirmation、HTTP/登录发码/生命周期、实际游戏动作、插件 policy_guard/命令/worker/恢复页，以及 S4 和用户环境验收。capabilities 和官方绑定门禁继续保持未开放；单靠这些新类不能声明完整 ready。

## 附录 M：Java 中心变更与原操作恢复核心（2026-11-18）

附录 L 的 Java 只读边界属于前一包。本包新增独立变更核心与 JDBC 适配，不改协议路径/表结构，不表示 HTTP、游戏或后台恢复服务已经接通。

1. OfficialCenterMutations 只处理中心权威的规范意图；不接收原始验证码、口令、HTTP 请求或真实配置。prepare 复用相同 operation/request/generation；claimReservation 在任何预占外发前持久认领一次；recordReservation 只记录匹配的代次和预占回执；commit/abort 对同一操作行加锁裁决，不回退业务终态。没有本服持久预占不能提交新绑定。
2. Transaction 接口的单次工作必须原子覆盖两表：绑定行、COMMITTED 固定 effects、码确认和分目标同步待办一起提交。昵称依据/解绑目标按 game_key 排序锁行；批量全部校验后才写第一行，重试不扩大目标。原版白名单、在线游戏状态和 HTTP/发消息都不在该数据库事务中执行。
3. 已有自己的 active 账号沿用 Python 的 REJECTED/ALREADY_BOUND：原绑定和 revision 保持，新操作的码确认按 ABORTED 待作废。其它身份不能覆盖 active；撤销后重绑递增 revision。策略修订/昵称依据归属或版本失效时拒绝本次新增，版本达到 9223372036854775807 时明确 LIMIT_EXCEEDED，不溢出或重置。
4. 所有变更必须由宿主注入 PolicyGuard。acquire 按 PREPARE/CLAIM_RESERVATION/RECORD_RESERVATION/COMMIT/ABORT 区分动作，返回提供当前策略修订的 PolicyLease，持有真实权限/配置锁直到提交或回滚结束。真实群权限/可靠昵称和恢复 worker 的授权仍须宿主实现；缺回调明确 NOT_SUPPORTED，回调拒绝明确 UNAUTHORIZED，不凭输入中的 authorization 字段自行授予权限。纯接口与测试替身不是已完成生产权限校验的证据。
5. OfficialJdbcConnections 继续默认 READ_ONLY；中心变更需显式 Access.READ_WRITE。OfficialJdbcTransactions 拒绝只读、autocommit 或非 REPEATABLE READ 连接，不在仓储里偷偷改成可写。每事务先检查既定 schema、零行 SELECT 和 EXPLAIN 权限，再以参数化语句锁操作/绑定；条件更新核对环境/群/原归属/状态/revision，返回行数不符为 CONFLICT。运行时无 CREATE/ALTER、旧表兜底或透明重试。
6. COMMIT 任意异常均按 RESULT_UNKNOWN，不能因为随后 rollback/close 成功就声称先前未提交。用新的只读连接和原 operation_id 查询；确认为终态后重复进入只返回原结果，不重做绑定。明确语句冲突返回 CONFLICT，不在内部重放。独立核心没有自动轮询、后台 ACK 合并器或恢复 worker；它们仍属于后续生产接线。
7. 新增 23 项事务模型与 13 项假 JDBC 用例；总计 113 项独立 Java 用例在 JDK 17/21 各通过，Python 完整隔离回归 253 项通过。两个 Java 路径都与共享 fixture 中七个 Python 持久快照一致。模型用全局串行事务、JDBC 用明确替身，只证明业务/参数/提交边界与构造故障行为，不证明目标 MySQL/TiDB 的真实 SQL、行锁、并发、驱动重连/网络或应用权限。
8. 后续仍须接可信镜像来源/一致性快照、真实 PolicyGuard/配置锁、官方配置/旧入口门禁/生命周期、HTTP/登录发码/恢复调度与游戏动作；插件命令/恢复页/worker、多服定位、本人排行以及 S4 仍未完成。capabilities、binding_ready 和官方远程写继续保持未开放。真实建表、凭据、维护停启、部署及实机验收继续由用户操作。

## 附录 N：受控镜像来源与一致性快照（2026-11-18）

附录 M 的来源/快照缺口在本包补入具体门禁和读取器；实际 LeaseProvider、配置锁及 WebServer/游戏宿主接线仍未完成。以下为内部安全类型与实施约束，不新增线上路径、不改变既定业务。

1. OfficialMirrorAuthority 固定一个环境/群/服务器，且只接受 center 权威。宿主 LeaseProvider 必须从当前实际生效配置取得 scope、配置修订、active、显式中心发布权限和配置 Bearer，并持真实配置锁到整个镜像写入结束。缺配置提供方/停用来源拒绝；禁止把请求中的身份/布尔值或通用只读权限直接当作中心发布许可。不自动读取旧 OneBot 凭据或选择其它来源。
2. 获准的中心发布者是增量信任边界，必须只发布 COMMITTED 操作的完整固定 effects；该发送方约束由既有 V2MirrorDelivery/中心状态机保证并须在实际接入时保留。模组本身断库时不强制再查中心才能落这种合法下行；“客户端 JSON 自称成功”或未授权来源无效。此机制不承诺抵御已泄露的发布凭据，真实凭据/部署由用户操作，开发验证只使用合成值。
3. 正式同步适配使用 OfficialTokenApi.withAuthority/dispatchSync，仍为 POST /api/v2/binding/sync，scope/operation_id/effects 不变。核对显式发布权限与 Authorization Bearer；通道使用 16–1024 位可见 ASCII 随机令牌，拒绝缺失、默认占位或弱短值，比较不回显令牌。安全构造下 authenticated=true 的旧同步分支拒绝。WebServer 接线见附录 P，capabilities 仍未开放。
4. 私有 Permit 由当前来源验证后创建，绑定来源对象和配置修订，30 秒过期、单次使用；不包含令牌字符串。凭据轮换、源/模式/环境等实质配置变更须递增配置修订并关闭旧来源。镜像 Confirmation 仅认可当前调用线程内匹配操作及完整规范 effects 的短期上下文；直调原镜像 sync/mergeSnapshot 或重放旧 Permit 不构成证明。未知持久结果后的同操作重送需要重新鉴权，不自动转换为成功。
5. OfficialJdbcSnapshot 只接受显式只读、非自动提交、REPEATABLE READ 连接；schema/会话核验后用同一连接/同一事务读取整个当前分区绑定和它们的 last_operation_id。包含 active 与 revoked；每行须匹配 COMMITTED 操作的规范意图散列及固定 effects。不得调用会新建连接的 readOperation 来拼快照，不使用 FOR UPDATE 或旧绑定/镜像兜底。读事务结束 rollback/close，不修改中心。
6. 只有整份校验通过才创建私有 Snapshot，绑定来源/配置修订，60 秒过期、单次使用。Snapshot.rows 返回防御副本；只有本来源可以消费，应用时重新取得并持有当前租约。数据缺失/损坏/不匹配、来源变更、DB 失败或超限时没有可应用的半份快照。按已有 revision 合并，不以空/缺行删除镜像，旧快照不能覆盖加载期间更新的更高版本。
7. 内部全量上限为 10000 行/8 MiB；读取阶段预算 30 秒、单 SQL 查询超时 5 秒，超限或超过可检查阶段预算时拒绝整份。它不是 DNS/驱动的硬墙钟取消保证；大型或慢速实例须真实验收，首版不降级发布半份。快照内部容量不放宽既有线上单请求 1 MiB 和 /check 分页契约。
8. Python mirror_snapshot 提供对应的内部中心读取：一个事务取得墓碑和有效行并逐操作核对固定结果，拒绝非二进制比较引入的外域行；生产需使用既有显式 RR/结构门禁连接工厂。共享 official_center_v1.json 新增合成的已撤销 A/有效 B 及引用操作，保留此前七个事务快照。没有对外快照路由、数据迁移或自动删行。
9. 最新完整 Python 隔离回归 263 项，独立 Java 用例 136 项在 JDK 17/21 各通过；其中新增 Python 10 项、Java 来源/快照 23 项。证据来自临时 SQLite/文件、合成凭据与假 JDBC，不代表实际 SQL/TLS/鉴权配置或 QQ/MC/TiDB 已验收。前端与 SQL 模板内容未变，既有发布产物保留。
10. 下一阶段接实际来源/权限/配置租约、旧入口官方门禁与生命周期，再接 HTTP/登录发码/游戏动作/恢复 worker。插件命令/恢复页/多服定位/本人排行、官方广播/控制台等剩余能力、S4 remote/local 和正式四加载器仍待实施/验收；binding_ready/官方写入口继续关闭。

## 附录 O：实际配置、来源租约及原生启动隔离（2026-11-18）

附录 N 的配置/来源租约在本包接到真实注册表和四加载器启动源码；这是代码与隔离桥接证据，正式模块类型分析/构建及实机尚未完成。没有新增线上路径或更改身份/绑定业务规则。

| 配置项 | 约束与映射 |
|---|---|
| bindingPlatform | onebot / qq_official；合法旧配置没有官方字段且缺该项时保持 OneBot。未知/歧义选择拒绝，不回旧模式 |
| environmentId / bindingGroupId / serverId | 映射 environment_id / group_id / server_id；官方要求规范 UUID、原样群 OpenID、稳定 UUID。旧 OneBot 短 serverId 保留，不迁移身份 |
| storageMode | 沿用 hybrid / remote / local 名称，本包仅装配官方 hybrid；其余官方组合明确 NOT_SUPPORTED，不创建旧存储 |
| apiToken | 显式配置的官方 API 凭据，遵守附录 N 强度/范围；不从旧身份、旧池或环境变量猜测 |
| official.configurationRevision | 正十进制字符串；来源/凭据/实质配置变更须递增，并在维护窗口完整停启 |
| official.createFreshState | 默认 false；true 只初始化明确的新来源目录，不开放 V2/镜像写授权、发码或游戏绑定放行，随后需完整停启并改回 false |
| official.allowCenterSync | 默认 false；只有用户明确授权该中心发布通道时 true，不从请求体/布尔鉴权推导 |
| official.database | 显式 host/整数 port/database/user/password；不解析旧 jdbcUrl 或沿用旧数据库池；真实值由用户自行填入 |

1. OfficialRuntimeSettings 是不可变、显示脱敏的配置快照；JSON 上限 1 MiB，拒绝重复键。QQBindConfig 先完成字段转换/来源校验再发布，失败不部分替换静态凭据，不附解析片段/异常原文。实际文件读取仅发生于用户运行模组；助手验证只读测试在新 scratch cwd 自建的合成文件。
2. OfficialRuntimeRegistry 实际使用读写锁和唯一 owner。官方来源租约持有配置读锁到整个操作结束；重载尝试不能等待 I/O 锁，只能立即拒绝或应用。运行平台固定后，不热切换平台或官方身份/模式/凭据等；合法 OneBot 同平台重载保留。实际群角色/昵称及绑定授权 PolicyGuard 尚未接入，来源租约不等于所有业务权限已完成。
3. OfficialRuntimeSession 装配预占、镜像来源、中心读取与安全 API；不自动连接数据库或启动定时 worker。Driver/SQL 仅在显式 I/O 操作且 owner 有效时使用。主线程调用这些 I/O 入口拒绝；游戏绑定快速读取不取配置锁、不读文件/数据库，停用后立即拒绝放行。
4. 四正式加载器使用同一个 BindingBootstrap；旧存储工厂仅在 OneBot 分支延迟执行。官方配置错误/功能未就绪不能构造旧 JsonStorage/RemoteStorage。停止顺序为停 HTTP 后释放资源；官方先禁用 owner，等租约最多 5 秒。未排空时保留停用 owner，拒绝另一实例，不强行覆盖/关闭正在写入的状态，也不把未知远程结果当作安全取消。
5. 旧 HTTP 官方模式只保留 GET status/tps/stats，绑定读写/验证码/同步/缓存/名单配置/广播/控制台在基类入口拒绝；旧 BindingManager 和 TokenManager 本身还有模式防线。四登录入口先走统一内存门禁，OP 和显式关闭检查独立生效；普通有效新登录不强改模式，未绑定/未就绪显示维护提示，不生成旧码。已受限玩家解除限制必须等待版本化 GameEffects，不能用无 revision 的登录回调放行。
6. 目录固定为 qqbind/official/<environment UUID>/<server UUID>/<SHA-256(group_id UTF-8)>/，不把私有 OpenID 原文用作文件名，不读旧绑定。新目录初始化必须显式 createFreshState=true；该次仅初始化，旧只读查询仍可按 API 凭据认证，但所有 V2/镜像写授权与游戏绑定放行关闭。进入普通会话须用户完整停启并改 false。已有目录缺主文件/损坏/来源不符拒绝；数据丢失/恢复仍须按既定新环境/恢复代次流程，不把重新开 fresh 当作恢复方案。
7. config/qqbind/official-selection.marker 只含固定 qq_official 标记，不含身份或秘密，不作鉴权。明确选择官方（即使其它字段无效）即可留下拒绝降级痕迹；以后缺配置或存在官方状态目录时，冷启动不创建 OneBot 默认配置。不得删除标记来绕过恢复；全部配置/状态丢失不能据此证明安全，仍由用户做备份/对账/恢复。显式维护窗口切回 OneBot 的有效配置仍按规则处理，不自动迁移旧身份。
8. 模组 examples/qq-official-config.template.json 仅为后续手工交付预备，包含占位值，尚不可直接部署。配置、真实 Token、初始化、备份、停启和实机均由用户自行操作；开发者没有在真实目录执行这些步骤。官方 V2 WebServer 已按附录 P 接线；游戏发码、退出清码调度、白名单/模式 GameEffects、ACK/恢复 worker 仍待接通，capabilities/binding_ready 保持未开放。
9. 验证新增运行时 16 项、实际配置/启动桥接 8 项；连同原 136 项，共 160 项隔离执行用例在 JDK 17/21 各通过。另 5 组语法/结构检查只解析 19 个明确源码，不做 Minecraft 类型分析。桥接使用真实 QQBindConfig/BindingBootstrap，但日志/存储/驱动/游戏依赖为明确替身；没有启动真实模组/MC、读取用户配置或执行 Gradle。Python 263 项回归通过。不得把 165 项合计检查或两版 JDK 的重复运行称为四加载器构建、真实 JDBC/TLS 或实机验收。
10. 最新 HTTP 接线和后续任务见附录 P。官方取码 I/O、版本化游戏副作用/ACK/恢复调度、实际绑定授权策略及插件命令/恢复页/多服定位/本人排行仍待接入；其它官方写能力、S4 remote/local、四正式模块构建与 QQ/MC/TiDB/浏览器验收继续保留。

## 附录 P：模组 V2 HTTP 入口与离线验证（2026-11-18）

1. WebServer 注册既定前缀 /api/v2/binding，使用独立 OfficialHttpHandler，不继承 legacy BaseHandler，不绕过旧入口官方门禁。经 BindingBootstrap 委托唯一 OfficialRuntimeSession，不创建第二套状态、读取旧绑定或回退旧 Token/BindingManager；平台错误/初始化/已停用会话拒绝。
2. 读取正文前校验唯一 Authorization Bearer 和当前配置 owner；业务分发在来源租约内再次验证。预检后停止不能写镜像；sync 额外核对 allowCenterSync 发布权限。服务凭据只在头，不接受 URL/请求体凭据，也不把普通鉴权视作绑定用户授权。
3. GET 范围由查询解析，POST 仅 UTF-8 JSON；不将验证码放进 URL。拒绝重复 JSON/查询键、非法 UTF-8/控制字符、过深 JSON、POST 查询串/GET 正文、压缩输入、非规范路由及歧义长度/传输编码。沿用 1 MiB 请求/响应上限，另将查询编码串限制为 8192 个 ASCII 字符和最多 8 个字段；protocol_version 与 limit 转换为协议数字，其余身份/版本保持原字符串。
4. 每处理器最多 8 个在途解析/分发，超出返回 503/DB_UNAVAILABLE；每请求最多一次 dispatch，不透明重试。写分发后的非预期异常、响应缺失/超限等无法确认的回执按 RESULT_UNKNOWN 返回；响应写失败仅关闭 Exchange，不再次分发/回滚。错误只返回固定安全信封，不含请求/异常原文；SCHEMA_MISMATCH 为 503。响应使用 no-store、nosniff、Connection: close；不添加 API/访问日志或跨域放权。
5. 已接 validate/reserve/finalize/operations 和受控 sync 路由，保留 B.1–B.3 原字段及分项待办语义。缺实际 PolicyGuard/官方发码/MC GameEffects/恢复 worker 时，capabilities、bind/unbind/check 仍 NOT_SUPPORTED，插件 binding_ready=false。HTTP 注册不是绑定放行，更不表示白名单或在线动作已完成。
6. 新增 26 项内存 HttpExchange 用例、3 项真实 Bootstrap/配置桥接执行用例；Java 合计 189 项执行和 6 组语法/结构检查在 JDK 17/21 各通过，Python 263 项通过。内存预占到消费及 sync 经过真实协议/状态核心，中心/驱动/游戏是明确替身，没有运行真实 HttpServer/Socket/DB/MC/Gradle 或读取用户数据。19 文件解析不做原生类型分析，不能称为四加载器构建或真实网络/TLS/权限验收。
7. 字节/并发限制不构成慢速请求或阻塞 I/O 的硬墙钟取消保证；JDK HttpServer/代理/防火墙和线程池的真实压力、停启验收仍需后续安全环境验证。本包未改前端或 SQL 模板，保留既有产物及未提交工作。
8. 下一包接官方登录取码/退出清码 I/O 与连接代次复核，再接唯一游戏线程的版本化 GameEffects、逐项 ACK/恢复 worker，随后绑定授权/可靠昵称策略、插件命令/恢复页/多服定位/本人排行。S4 remote/local、正式四加载器及真实 QQ/MC/TiDB/浏览器仍未完成；敏感配置、建表、备份、部署和实机操作继续由用户自行执行。
9. 2026-11-23 增补：POST `/api/v2/binding/settings/whitelist` 只接受唯一 API Bearer、protocol_version=2、精确 scope 和恰好 `{enabled: bool}`。该路由在 registry.acquire 之前分流，直接更新官方运行期快照而不持配置读租约，避免与配置写锁自锁；随后写回服务器本地 `enableWhitelistCheck`，并到主线程重校验在线受限玩家。它不触碰旧 OneBot 写入口、中心绑定或配置修订；`/api/status` 的 `whitelist_check_writable=true` 仅声明该模组具备此在线写能力，旧模组缺字段时插件保持只读。

## 附录 Q：官方取码、在线副作用与异步 ACK 边界（2026-11-19 阶段记录，最新见 R）

1. OfficialLoginCodes 由会话按需创建一个单线程、有界 128 队列的 worker；初次登录、每 3 秒限流后的提醒只提交不可变登录/连接代次，不在主线程生成码或读状态文件/数据库。有码时重复提示读取本代次的 ConcurrentHashMap 内存快照；过期复查、停用/退出即时 invalidate 与文件扫尾不在玩家线程等待锁。原始六位码仍只在 worker 返回值、HTTP 请求体和目标玩家短暂消息中，不持久化或写日志。
2. 每个登录句柄绑定玩家 UUID、精确 scoreboard game_id、规范 game_key 及唯一 ServerGamePacketListenerImpl 引用。主线程任务按在线表 UUID 查找并比对连接对象，再检查连接仍接受消息、绑定检查仍启用、用户仍非 OP、有效镜像允许取码及该连接当前 generation 后才发送。退出事件立即使句柄不可用；旧回调和重复退出不接触同 UUID 的新连接。四加载器事件/API 源码已复核。
3. 未预占码退出后立即不能 validate/reserve；其六码在剩余有效期内仅于内存 retired 集合中保留，避免立即分配给重连玩家。持久 RESERVED/CONSUMED/INVALIDATED 不因玩家退出删除；中心失联且该账号有未决 RESERVED 时，新登录提示待确认，不生成新码或伪造安全撤销。
4. 官方在线限制信息按 UUID、连接引用和进入前 GameType 存内存；跳过旧 BindingManager/TokenManager。仅启用检查且非 OP 的玩家受限。配置关闭或玩家转为 OP 时，不再发送/重复应用限制；带版本的 active effect 恢复进入限制前的合法模式，revoked effect 使用既有旁观限制并异步申请新码。无效 game name 明确不给码。
5. 会话的有界队列还承载 online ticket 的 I/O 阶段。快速工作标记只根据持久 pending_effects 计算，不在游戏线程检查磁盘。I/O 批次最多 16 项；唯一游戏线程执行版本复核后将 receipt 交回 I/O 批量原子 ACK。ACK 失败保留完整 receipt；恢复退避为 2/10/30/60/300 秒。关闭后丢弃尚未执行的 callback，不承诺在途 MC 操作已取消。
6. vanilla UserWhiteList.add/remove 与 remove(K) 都会同步执行 StoredUserList.save() 覆盖 whitelist.json，因此白名单 ticket 不在本阶段游戏回调执行，继续为 PENDING/NOT_SUPPORTED。不能把在线限制 APPLIED 当成整个副作用完成。需先实现文件锁与版本化异步写回、内存玩家白名单一致性和 ACK/重启恢复，再单独开放白名单效果。
7. 中心键继续使用已确认的规范 game_id/game_key；在线 effect 按 game_id 查询在线玩家，登录异步回调另以 UUID 和连接对象复核同一连接，不新增跨账号体系或改名迁移。白名单需要的 Profile/UUID 适配仍未实现，不能据此宣称所有账号模式已验收。OP 变化按执行 tick 复核；正式事件/实机行为仍待验收。
8. 验证：JDK17/21 各 202 项隔离执行及 7 组源码解析/结构检查；另使用缓存且复制到新 scratch 的 Minecraft 1.20.1/JDK17、1.21.1/JDK21 merged server jars、Gson、Connector/J、SLF4J 及明确真实源码做 proc-none/implicit-none 原生 API 类型编译，两版通过。新增 7 项取码核心及 6 项内存玩家宿主用例。Python 263 项沿用此前通过；前端未改，无需重建。没有运行 Gradle/loader、真实 HTTP、SQL、MC 或 QQ。
9. 上述类型检查不等于正式 Forge/NeoForge/Fabric build；Forge/NeoForge/Fabric 事件依赖仍未进行正式模块集成编译。该阶段的实际 PolicyGuard/昵称、bind/unbind/check、恢复页、自动恢复、完整白名单动作、remote/local 和真实运维验收仍未完成；后续变化以附录 R 为准。
10. 下一步接实际授权与昵称 PolicyGuard、插件中心变更入口和操作恢复页；同时实现安全异步白名单 adapter，随后恢复 worker 故障点演练。已确认业务无需再问；真实秘密、DDL、备份、部署和实机继续由用户自行操作。

## 附录 R：事件授权工厂、初始快照与限制模式恢复（2026-11-19）

1. OfficialRuntime.binding_service_for 只为当前选中实例、当前环境的合法官方群事件创建编排服务；没有正式命令调用或生产中心依赖装配。具体 OfficialBindingPolicy 持实际官方群配置锁穿过 prepare/commit 事务，复查启用、黑名单优先、现有角色权限、修订及服务器范围；锁内不发 HTTP。事件授权最多 120 秒，失效须用新消息，不持久化事件、昵称或验证码。
2. 新绑定仅允许本人 self 授权。首绑匹配本次账号；后续可匹配本人 active 账号，固定其 game_key/expected_revision。提交前复核当前可靠昵称与原依据，再由中心事务锁行复核 owner/state/revision。依据被撤销时，先中心 CAS ABORTED/CONFLICT，再 finalize 作废，绝不先释放码再猜中心结果。缺可靠昵称返回 NOT_SUPPORTED 并保留未决，不能用 username 或管理员自己的昵称代办。
3. inspect/recover/cancel 在读取中心前检查当前官方群，在暴露操作前检查 actor/目标用户/现有管理员权限；普通成员不见他人操作。管理员可以查看及显式中止，但未决绑定只能由原请求人凭新事件继续提交。终态确认、中心提交成功与后续 ACK 仍分开处理，授权撤销不自动游戏解绑。
4. 初次可信快照尚未完成时，未知账号暂停新取码；已有同环境、同群、同服的持久 active 镜像仍可放行/恢复受限模式。中心读取失败不阻止本环境已提交镜像的本地 online 待办捕获、执行与 ACK；不把断库当成镜像缺失，不创建临时本服绑定。一个 worker 分别处理快照和本地待办，失败继续退避；这仍不是完整中心操作/预占恢复 worker。
5. 进入官方限制前，原模式以 HMAC-SHA256 标记交由原版实体 tags 持久化。签名绑定当前 API Token、固定域、环境/群/服务器、玩家 UUID 和 mode；只有有效标记及当前 active revision 可触发模式恢复。普通有效登录不强改模式；模式已被外部改为非旁观时不覆盖。伪造/外域标记不授权恢复。API Token 轮换会使旧标记失效，维护时用户自行处理已有受限玩家模式；开发不读取真实 tags/玩家文件。构造测试的 tags 拷贝不等于真实 NBT 保存验收。
6. 最新 Python 270 项隔离回归通过，其中 32 项编排/事件策略用例；新增 7 项事件策略覆盖当前昵称、已有账号依据、超时/黑名单、实际运行时工厂、预占期间依据撤销、操作可见性与管理员边界、缺昵称后新事件恢复。JDK17/21 各通过 207 项执行及 7 组源码检查（22 文件）；包含 7 项取码、7 项内存游戏宿主、35 项镜像和 17 项运行时用例。两版 MC 公共 API 类型检查可用 QQBindMod/tests/run-native-types-isolated.ps1 重现；不运行 Gradle/加载器/MC/HTTP/数据库。
7. 下一包仍为正式绑定命令/中心依赖装配、操作恢复页、异步白名单及完整恢复 worker；其后是其它官方写能力持久去重、本人排行、S4 remote/local 和四加载器安全构建。capabilities、bind/unbind/check 和插件 binding_ready 继续关闭。没有新增业务决定；真实 SQL/TLS/权限、QQ 媒体、玩家持久化和 QQ/MC/TiDB/浏览器验收尚未完成，不能宣称整体兼容已验收。

## 附录 S：受控聊天入口、独立中心装配与终态补偿（2026-11-19）

1. 正式聊天路由支持 bind/unbind/check/operation；只接当前已选实例的合法官方群事件，群未启用/黑名单/其它平台均不加载中心依赖。沿用真实 OfficialBindingPolicy 与同一两表状态机，不调用旧绑定、旧池或旧身份兜底。模组完整 capabilities 仍未开放；入口接线不等于允许生产绑定，binding_ready 继续 false。
2. 环境级配置文件为 `<插件数据目录>/qq_official/<environment_uuid>/center_settings.json`，只由用户依据 `examples/qq-official-center.json.example` 自行准备；默认 disabled。固定 schema_version=1、environment_id、enabled、storage_mode、connection 五个顶层字段，connection 仅 host/port/user/password/database/ssl_ca；可选 server_modes 按服务器覆盖。当前装配允许 hybrid/remote/local，真实 SQL/TLS/权限验收仍待完成，不能据此宣称生产就绪。文件存在时完整优先；不存在时才读取全局「公共参数」的 db_* 连接。是否使用中心库由官方群「本群规则」的 `use_central_db` 逐群决定，默认关闭；关闭时直接走模组本地权威，不读取环境级中心文件。全局值只在本进程内传给连接层；普通 WebUI 不读取、不回显任何环境级中心文件内容。真实值不粘贴给助手。
3. 先确认当前来源/群授权再读取显式 owner 文件；每请求创建 CheckedOfficialBindings/OfficialMySQLFactory，构造时不连接，真实数据库会话仍逐事务做 schema/权限预检。无配置/损坏/环境不符/失联均不自动建表、不用 OneBot 池、不扫描其它用户配置。文件元数据与活动来源变化阻止后续阶段，但不承诺原子取消已经在途的事务；配置/凭据变化仍须用户维护停启。
4. `/mc bind <六位码> -s <服务器名>` 只验证选中服；中文别名保留相同选服语义。未显式选服时最多探测 4 个当前可见模组服，开始后续探测前检查 40 秒预算；对所有候选均取得确定结果且只有一个命中才继续。任何未知/无权/超时或多重命中要求显式选服，不把错误当不存在；目标选定后再验证原代次，固定 server_id，不靠无码作用域缓存跳过歧义。
5. 入口有界预算为每分钟成员/群/全局 6/60/120 次绑定尝试、同时 4 个宿主请求、最多 4096 个短期预算项。它是请求预算，不声称超时/无权限属于无效码；无码正文/昵称/凭据入预算。稳定事件来源元组及规范动作产生 request_key，UUIDv5 从该键产生可复现 operation_id，均不混入六码或可离线枚举的六码散列。全局发送 ledger 继续先落盘去重，聊天发送失败不新建关系。
6. 自助解绑明确游戏名或 all；管理员可按 `user <官方用户标识>` 固定快照，不沿用数字 QQ 解析。相同事件重用原固定账号/revision；选择器变更为 CONFLICT，不在重试时扩大到后来新增账号。PREPARED 解绑在新事件策略复核后提交原目标，策略修订变化整笔拒绝；显式 cancel 可对未提交绑定/解绑做中心 CAS 中止，COMMITTED 不回滚。本人 check 只读取当前官方群 active 账号，用既有 10 分钟范围/修订缓存分页，不查旧库。
7. operation 支持完整 UUID 或至少 8 位十六进制/连字符前缀。先在当前环境/群且本人/目标用户或当前管理员可见集合内做最多两条查询；前缀歧义要求更长编号，不据他人不可见操作泄漏状态。之后仍走服务的真实 inspect/recover/cancel 授权；编号本身不授予访问权。管理员仍不能用自己的昵称代替原请求人继续未决绑定。
8. OfficialFollowupRecovery 接生命周期，每 5 秒尝试一轮；Context 上的同一 gate 跨插件重载保留，在途旧线程退出前新 worker 只返回 busy。关闭使后续来源检查失败，取消 asyncio 外壳不被解释为已撤销远程结果。每轮至多 8 个操作、预留最多 16 次 HTTP（握手/状态/finalize/sync），20 秒预算只限制后续请求启动，不宣称 DNS、驱动或 socket 的任意硬取消。
9. 后台 authority 只允许读取既有操作、确认 COMMITTED/REJECTED/ABORTED 的码终态、投递 COMMITTED 的固定 effects 与合并 ACK/退避；不是合成用户事件，也不能执行 prepare/commit/bind/unbind。PREPARED/RESERVED 保留等待本人新消息校验；完整未决预占自动对账尚未实现，不能把本 worker 称为所有恢复流程完成。中心原子待办是重启来源；逐服错误不回滚中心，未完成按既有 2/10/30/60/300 秒退避；同轮失败握手不再重试。
10. 回执分别报告中心状态、码确认、各固定目标镜像/白名单/在线状态。mirror APPLIED 不是 whitelist/online APPLIED，当前模组白名单仍 PENDING/NOT_SUPPORTED。操作只剩副作用时重送相同 revision，不重新绑定/递增 revision；ACK 未保存或结果未知仍保留原操作查询路径。
11. 新增 32 项入口/宿主与 17 项补偿/生命周期用例，Python 完整 319 项隔离回归通过。使用真实 Python 策略/事务/镜像回执适配、构造身份/配置、临时 SQLite 和内存对端，无真实 HTTP/数据库/插件/模组启动。修正失败夹具后重跑通过；本包 Java/前端/DDL 未改，既有结果沿用不当作新验收。
12. 剩余：异步原版白名单/Profile/UUID；WebUI 恢复页/正式管理 HTTP；模组原生管理员授权与完整未决预占对账；其它官方写能力持久去重、本人排行、remote/local、四加载器正式构建及用户环境验收。既有每小时 qq 自动化保持 ACTIVE，不重复创建；完成开发/必要离线验证或只剩用户实际操作时按总任务规则暂停，不将未验收说成完成。

## 附录 T：WebUI 原操作管理与恢复 HTTP（2026-11-19）

1. 新增接口只在既有框架插件扩展路由表注册，不是新增模组 V2 wire 路由：GET `official/operations`、GET `official/operation`、POST `official/operation/action`。认证沿用固定版本的 plugin_scope_dependency（登录或具备插件范围的 API key），不是请求体声称的 QQ 管理员；实际未认证、会话过期和部署权限仍须用户验收。不提高最低框架版本，旧宿主缺 Web API 时按现有机制不注册。
2. GET 公共参数为 environment_id/config_id/expected_revision；列表可加 filter=pending|all、cursor，详情加 operation_id。只接受活动官方环境、已启用群和当前配置修订；不允许从其它环境读取配置或回退旧表。未知字段、无效筛选/游标拒绝。管理 authority 不产生新 QQ/game_admin actor，不把管理员昵称用于提交别人的未决绑定。
3. OfficialBindingAdmin 的管理 guard 先于独立中心设置，组合进实际中心连接/网络来源 guard，并限制请求生命周期120秒；worker 返回后再复核环境/群修订，停用/关闭后旧结果不能继续返回。关闭只立即停用，不在事件循环等待持有缓存锁的数据库 I/O。框架认证是每个请求的前置条件，普通客户端不得直调内部宿主代替它。
4. operation_summaries 只查询固定元数据列与当前环境/群，pending 包含 PREPARED/RESERVED 或持久待办；ORDER BY created_at/operation_id，最多201行，缓存最近200条并明确 truncated。每页20、10分钟、最多16快照，游标固定环境/群/修订/筛选且每次授权，不作为权限令牌；旧记录可按唯一前缀/完整操作号查询，不承诺无限归档浏览或真实大库性能。
5. 摘要/详情只暴露操作种类、中心状态、结果码、时间/策略修订、固定账号/版本和各服分项状态，payload_hash 用于核对原载荷。去掉 actor/user ID、原始 intent、昵称依据、验证码、预占代次/ID 以及数据库/服务凭据。保留原操作的创建 actor，不伪造恢复操作者身份；框架权限/审计归属由后续实际部署验收。
6. POST 精确字段为 environment_id/config_id/expected_revision/operation_id/action/expected_state/payload_hash/confirmed；action仅retry或cancel，confirmed必须true。状态和规范载荷散列须匹配刚查的原操作，群修订变化要求重查。无actor/角色/user_id/原始码/新目标/新绑定意图入口。
7. cancel 只对 PREPARED/RESERVED，在真实群配置锁内复用中心 CAS abort；与 commit 竞争时以中心裁决为准，COMMITTED 胜出返回 CONFLICT 和真实终态，不报告已取消。中心已终态不能取消/回滚；码作废仍是后续待办，中心中止不等于本服已确认释放。
8. retry 只接受 COMMITTED/REJECTED/ABORTED，复用进程恢复 gate、单轮预算及固定 effects，不能调用 prepare/commit、生成新绑定、扩大账号集合或递增业务 revision。PREPARED/RESERVED 明确要求原请求人在官方群用新消息校验；后台不代办。失败只保留原操作及分项待办；未知结果不透明重发，中止/重查都不强行把未知改成功。
9. 页面按需查询，不在打开配置页时自动读取中心。未保存草稿时禁用操作，恢复忙碌时禁止父页切群/改配置和全局导航；旧范围响应丢弃。动作前明确确认操作/固定目标/状态；发生未知结果后清除确认并要求重新查询。中心、验证码、镜像、白名单、在线限制分开展示，已提交不等于副作用完成。
10. 新增27项管理/路由用例，Python完整346项隔离回归通过；离线Vue覆盖实际组件/模板的确认、未知结果锁定、忙碌传递、跨范围旧响应和既有草稿/授权保护。类型检查与Vite发布构建通过，views/config已同步。Ui子控件为替身，不是浏览器DOM/CSS/弹层或真实框架认证验收。没有启动真实框架/HTTP/模组/数据库或读取实际凭据/记录；Java/SQL未改，已有207项/类型结果不声称重跑。
11. 白名单仍待受控内存/保存桥：两版公共MC ABI复核确认StoredUserList.map和PlayerList.whitelist为private；原先已核对add/remove同步save，不可直接放入游戏线程。该证据不等于已经有异步Profile/UUID或四加载器适配。继续实现白名单、模组管理员/完整未决预占对账、其它写去重、本人排行、S4和正式构建/交付，官方整体门禁保持关闭。

## 附录 U：原版白名单文件协调器设计与实现（2026-11-19）

本包先实现可独立验证的文件协调器；本节不是原版白名单已接入或验收声明。保持中心两表、镜像/操作/待办单文件及现有V2字段不变，不添加身份迁移或第三张表。

1. 已核对两版公共UserWhiteListEntry字节码：原版条目使用uuid/name。协调器只接受标准原版数组和明确非空UUID/name，不静默丢弃坏行、额外字段或重复UUID；10000条/4MiB上限。只管理宿主显式指定的whitelist.json，不发现配置、玩家缓存或其它文件；测试只能使用新临时目录的构造内容。
2. 绑定游戏回调只创建不可变Profile快照和序号，无文件I/O、等待或文件锁。快照带协调器私有owner，不同对象/重开旧快照不能混用；初始IO加载必须和宿主当前内存快照完全一致。默认拒绝缺失原名单，只有显式allowAbsent且内存为空才可准备首个文件，正常重启不能默认传true；曾存在的文件丢失不静默创建。文件不能代替中心绑定权威，也不从条目导入旧绑定。
3. I/O阶段独占旁路锁、校验来源/路径/原内容散列，以临时文件fsync+原子替换保存；旧快照不能覆盖较新确认序号，所有原版无关条目一起保留。外部未协调修改时拒绝覆盖，不能猜删记录。Windows目录fsync边界单独返回，不承诺所有硬件掉电持久性。
4. 原子替换或原生保存发起后异常必须RESULT_UNKNOWN。恢复只核对原候选/原基线并重新确认持久性，不重放写入回调；候选吻合才确认、基线仍在则明确未应用、其它内容拒绝。失败不能伪造白名单APPLIED或重绑。
5. 未来原生宿主可通过独立的nativeSave/nativeLoad入口协调原版管理命令的既有同步I/O，并与异步绑定写入用同一锁/序号；这些入口不得从绑定GameEffects调用，不能把原版命令已存在的同步I/O说成已改为异步。显式重载只采纳原版名单，不改变官方绑定。
6. 后续还须实现Profile/UUID可信解析、原生内存更新/安装桥和两阶段ACK：内存动作→文件确认→游戏线程复核同账号/版本→持久ACK；旧任务由mirror revision裁决。不能仅因为文件写入成功就确认整个副作用。四加载器私有字段访问需固定版本证据和正式构建，不猜映射或反射字段。本包不接这些生产入口，WHITELIST、capabilities和binding_ready继续关闭。
7. File Snapshot.sequence仅为进程内文件排序，不写进原版数组，不是绑定revision或跨重启恢复权威。NEWER_SNAPSHOT只表示文件已有较新快照，绝不能直接写入业务SUPERSEDED；后续worker仍须检查当前绑定revision、内存目标与已确认文件目标，或重新校正。重开按当前宿主内存/磁盘重新核验，未完成业务依赖官方单文件待办恢复。
8. 初始化、重载和写入均先完整校验再确认持久性。读侧用4MiB限量UTF-8与流式JsonReader，在第10001条前停止，避免先构造无限JSON树；重复键/UUID、未知字段、坏类型/Unicode/编码均不修复覆盖。Windows目录fsync单独报告false，不冒充已完成；快速读取只代表最后已确认快照，不声称实时探测所有外部磁盘改动。
9. 实现为独立OfficialWhitelistFile，无Minecraft/配置/驱动导入或静态I/O；未在BindingBootstrap/GameEffects/正式路由实例化。初始化和显式原生重载均核对并确认已有文件，原生重载必须产生新的本对象快照；正常写入只在I/O线程。nativeSave/nativeLoad是待接宿主的协作入口模型，并非已装原版命令Hook。close会立即停用快速访问，但资源收尾可能等待在途I/O，只能由宿主生命周期收尾，不从绑定tick调用。
10. 新增31项文件协调器构造测试；最终JDK17/21各238项执行与7组原生源码检查、两版MC公共API类型检查通过。只使用新临时目录/合成UUID与名单、故障注入和假宿主，不是实际MC/跨进程/掉电验收；Python346项与前端结果沿用前包。本包没有Profile/UUID解析、原生内存/安装桥或业务ACK接线，生产WHITELIST/capabilities/binding_ready继续关闭，下一包仍须完成这些部分。

## 附录 V：白名单分阶段队列施工约束（2026-11-19）

本包把现有真实镜像/文件协调器连成可离线验证的共享队列；原生Profile解析/安装仍不在本包，不据此开放生产白名单。

1. 队列仅接受固定scope、已初始化的同scope文件协调器、独立I/O解析器及仅内存的游戏访问接口；一个镜像最多一个队列。每批4个固定ticket，按游标轮转，不生成用户事件、绑定关系或新业务revision。I/O与游戏阶段不能并行重入，关闭后不派发新动作，旧阶段未退出不释放队列owner。
2. 流程为I/O捕获/解析不可变Profile→游戏线程核对UUID/账号并执行内存动作→I/O确认完整文件快照→游戏线程复核当前业务revision、内存和最后确认文件→I/O原子ACK。解析器未知不当作无绑定，用户名/UUID冲突拒绝，不合并同名不同UUID或自动改名；不携带玩家对象或在游戏接口做I/O。
3. 游戏阶段的APPLIED只作暂存；文件尚未确认、来源失效或内存又变化时不能ACK成功。文件NEWER_SNAPSHOT只提示较新文件，必须逐目标检查；业务SUPERSEDED只由更高镜像revision决定。旧批次含已知过期/失败内存动作时不保存其整份快照。
4. 文件写入阶段持镜像I/O租约，先复核固定ticket并在写入期间串行后续镜像发布；游戏快速读取不取该锁。新native捕获使旧文件候选失效时先回游戏复核，不覆盖新意图。文件未知先查询原候选，不透明重写；ACK失败保留原回执，不重新执行内存/文件动作。
5. 原版管理命令仍是独立人工动作，不宣称永久强制覆盖其后续更改；完整队列还依赖真正无I/O的原生内存实现、可信Profile/UUID解析及生命周期安装。当前仅测试替身实现接口，不接RuntimeSession/BindingBootstrap，不改WHITELIST/capabilities/binding_ready。NativeSave/NativeLoad在I/O忙碌时非阻塞返回CONFLICT，不调用原版回调；实际命令保留/提示/重试尚未适配。

## 附录 W：白名单分阶段队列可执行模型（2026-11-19）

1. OfficialWhitelistQueue复用固定scope OfficialMirrorService及OfficialWhitelistFile；一个镜像只能被一个白名单队列认领，不起自有线程，不存Player实体。IO resolver只取ticket gameId回Profile UUID/name；GameAccess只能在宿主游戏线程读取完整名单快照或改一个Profile。resolver和游戏访问目前均为注入接口，不是已核验MC具体实现。
2. 单批最多4个当前未完成ticket，cursor轮转。不可解析/超时profile不作未绑定，保持PENDING或FAILED；返回值name必须遵守官方game-key范围。UUID重名/name换UUID/name改名等映射冲突不自动合并。ticket在游戏动作前由镜像当前revision裁决；账号A修改不能清除同用户账号B或无关vanilla名单项。
3. 一次pipeline严格按IO→game→IO→game→IO阶段单向推进。游戏线程只作内存动作/快照，不做文件操作；IO期间mirror更新等待文件lease且快速镜像读不取lease。部分失败不把旧批次整个文件快照写回。文件seq只在协调器寿命内排序，不能当binding revision；newer vanilla snapshot触发重新协调而非mirror SUPERSEDED。
4. 文件只有先确认候选原子持久化，且随后游戏线程重读target状态匹配时，才允许对应绑定效果ACK；后续由IO合并原mirror pending_effect。成功写盘未ACK/ACK未知时保持待办，重复运行必须先复查memory/file/current revision，不凭原有Receipt盲写回。不在同一业务版本以外扩展解绑目标。
5. 最新mirror发布/撤销会在file write前校验并串行等待；游戏回调只看无锁published view，不等待该文件锁。native管理save/load用tryLock；IO被占时立即CONFLICT，避免主线程阻塞；真实原生命令入口尚未接，本包不改变用户手动操作的行为。
6. 队列close立即阻止新phase；在途单线程callback退出后才释放mirror owner，防止队列重入。未来scheduler仍由现有runtime定时驱动及指数退避拥有，不在队列新增服务线程或在错误后重放未知操作。
7. 新增18项集成测试复用真实mirror revision、SQLite-无关本地OfficialModState和OfficialWhitelistFile，配合合成resolver与memory-map宿主。覆盖profiles冲突/丢失、不同账号、旧revision、文件写前/后未知、保存期间撤销、ACK故障重启及关停竞态。JDK17/21分别完整256项、8个结构检查组与两版MC公共API类型检查通过；不代表MC实机、真实profile、vanilla login whitelist、loader构建或四模块验收。生产queue尚未实例化，WHITELIST/capabilities/binding_ready保持关闭。

## 附录 X：固定版本Profile resolver与名单内存桥边界（2026-11-19）

1. 本轮只核验明确缓存MC合并Jar和Authlib API，不读运行时服务器、UserCache/whitelist记录或任何用户配置。两版MC均公开MinecraftServer.getProfileCache/getProfileRepository、GameProfileCache.get(String)/get(UUID)、PlayerList.getWhiteList、UserWhiteListEntry(GameProfile)；这说明存在候选解析入口，不证明缓存语义、档案来源可信度、网络超时或多加载器调用时序。
2. 下一步resolver应先查询绑定范围内的已确认UUID→GameProfile反向映射；缺失时可研究GameProfileCache并给ProfileRepository查询固定超时、隔离线程和有界并发。仅逐字匹配名称、验证UUID及账号变更流程后生成Profile；cache miss、网络失败、速率限制/查重冲突一律保持待办，不能回退离线UUID算法或视为未绑定。服务端的Authentication/online-mode状态亦须由显式宿主能力提供并参加配置来源修订。
3. Vanilla白名单登录检查按UserWhiteList中GameProfile UUID键查找，不能仅用大小写游戏名等值替换。Profile name/UUID从session、用户配置/缓存、认证服务哪个来源建立映射，是待实现/验收的身份链路；不得据纯文本名称自行认领一个用户UUID。已有同UUID改名/不同UUID同名须保持冲突，除非另行确定可审计的改名流程。
4. Public PlayerList.getWhiteList()返回内部UserWhiteList，但原生StoredUserList map和PlayerList.whitelist字段为private。add/remove会同步持锁写文件；直接从游戏线程调用有I/O，直接从worker调用又会对主线程读的HashMap并发修改。不能用vanilla getEntries()创建旁路shadow map并声称登录检查已更新，也不能把清单APPLIED回执视为PlayerList已经看到条目。
5. Native bridge须在三条路径共用同一个白名单实例：官方队列的无I/O游戏线程内存更新、原版添加/删除命令的协调保存、vanilla login membership lookup。应通过固定版本且经公开Mojang/Paper映射/安全项目配置编译的窄loader shim或维护的Accessor/API实现；若技术需要mixin/access widener/AT，要逐版本记录文件、映射及正式打包应用顺序，不能调用私有反射或依据字段名猜布局。原版管理命令在file worker忙时必须给出可重试的CONFLICT，游戏线程不等待worker。
6. 必须确保切换/下载数据不会覆盖操作过程中认证Profile发生变化：提交文件前的镜像revision重检、候选保存确认、回主线程复查同一Profile/UUID/账号状态以及最后版本化mirror ACK缺一不可。实现失败/未知或服务器读取Profile来源暂不可用时保持PENDING/FAILED，不错误ACK白名单，不回滚COMMITTED绑定。
7. “25源码纯语法解析”“共享源码Public API类型检查”均不编译对应Loader专属mixin/注入或资源metadata。下一阶段先产出隔离的Profile与内存bridge集成测试和单Loader compile/package；源码证据、构建属性/依赖归属和原生日志/启动时序安全审计通过后，再依同规则接其余Loader。直至四正式模块和状态范围验收完成，WHITELIST、bind/unbind/check能力和binding_ready不得开放。

8. 本轮对Authlib 4.0.43/6.0.54公开sources副本及两版MC merged public jar做scratch内核验。GameProfileCache.get(String)两版都会规范化名称、维护/删除过期条目，miss时调用ProfileRepository并写回/保存cache；不是只读内存lookup，不能从主线程调用以“补查绑定”。
9. GameProfileRepository.findProfilesByNames逐版本API不同：1.20.1要传Agent且失败回调携带GameProfile；1.21.1不需要Agent且失败回调携带String。两版实现都是同步分页HTTP并带失败重试/sleep后直接回调；接口本身不提供应用级可取消硬超时。公开源码签名不能替代真实Auth/profile服务验收。
10. 设计任何profile adapter必须优先用同scope/revision已核验中心/镜像的UUID映射；外部cache/repository miss只可在有界IO worker与来源租约内查询，成功结果逐字匹配请求game_id并保证唯一UUID。未知/超时/故障保留待确认，不能据错误推断未绑定。不同Authlib签名须逐loader shim；一个阻塞lookup的worker不能导致无限生成替代线程。
11. 原生PlayerList.getWhiteList给出现有UserWhiteList，但内部StoredUserList.map/PlayerList.whitelist字段private；同步add/remove不能在主线程白名单队列调用，worker改该HashMap也违反读线程安全，构造影子map不能影响vanilla login。需让认证/登录路径读写同一个名单对象，并保留versioned memory/file/ACK顺序。当前没有已验证的Mixin/Accessor/AT安装流水线，不新增猜测反射/字段映射。

## 附录 Y：Fabric原版名单生产接线（2026-11-19）

1. 本轮按用户最新要求优先完成正式调用链及模块成品，只保留相关编译与既有关键路径回归，不新增大量构造测试。保持两表/V2/单活/重新绑定/一对多等业务规则；没有增加身份域或提高框架最低版本。
2. 新增common-native-src原生层。StoredUserEntryMixin提供受限user accessor；StoredUserListMixin使用经固定jar/mappings确认的map/save/load声明，实际对象仍为PlayerList.getWhiteList返回的原实例，不替换PlayerList私有字段，不用反射或旁路名单。Fabric两个模块显式包含此源码/资源，manifest注册qqbind-official.mixins.json，Loom生成并打包独立refmap。Forge/NeoForge暂未安装这一层，不能据Fabric成功宣称四模块完成。
3. Fabric SERVER_STARTED在缓存真实服务器后安装宿主；BindingBootstrap/OfficialRuntimeSession使用同一个有界登录I/O worker调度初始化、队列IO和分项ACK。游戏tick只处理内存阶段，不读文件/查库/查Profile仓库，也不等待Future。名单初始文件必须已存在且与原生内存一致；缺失不静默重建。首次安装、失败和待确认分别报告状态。
4. Profile优先来自本宿主已接受的在线玩家和现有原版名单，以不可变UUID/name快照交给worker；这些只用于解析MC身份，不能建立/导入任何聊天绑定或替代中心权威。online-mode=true时缺失才在同一有界worker调用本服ProfileRepository，按Authlib4/6各自签名接回调。false时不请求Mojang、不猜离线UUID，缺少可信Profile继续PENDING。停止/对象替换及同名异UUID、同UUID改名均使后续动作拒绝；内存更新前和文件确认后的ACK前重新核对当前Profile。同步库调用无硬取消保证，不能因此无限增开线程。
5. 绑定白名单动作通过内存桥直改原map，不调用原版add/remove/save。原版人工add/remove/save/load由Mixin在修改前纳入文件协调：worker占用时立即拒绝且不先改内存；实际原生保存只执行一次，返回待确认，由worker核验结果。未核对的pending不允许新reload覆盖；已确认外部漂移可由管理员显式reload重新建立基线，仍须worker确认。错误反馈只含通用状态，不含Profile、路径或异常正文。
6. 宿主发现PlayerList/名单对象被替换时，关闭本宿主的快速来源许可；旧文件/队列也同时失去许可，不能继续写旧实例或ACK。关停保留既有来源租约与单写者约束；中心失联不阻塞同范围已提交镜像待办，游戏副作用失败不回滚COMMITTED。
7. 新增管理员 /qqbind status，只读取当前运行时内存，显示中心镜像和原版名单桥状态，不读配置、列玩家/绑定、查询数据库或暴露Token。当前这一命令接入Fabric两个目标；原生解绑管理员入口、完整未决预占恢复及其它S3/S4工作仍待续。
8. 隔离构建工具为QQBindMod/tools/build-fabric-isolated.ps1：只复制显式源码/资源，临时用户目录、清空继承环境、临时开发参数；不读取真实gradle.properties/用户init脚本/服务器配置和记录。公共artifact/POM缺失用原发布jar内组件及明确公共构建依赖补齐；不会用假Minecraft/假Loader类替代正式模块编译。构建结果、选定依赖版本和产物证据逐模块记录在最新执行清单。
9. 模块编译/remap/成品资源核对与真实Mixin注入、vanilla登录/名单命令、Auth/Profile服务和QQ/数据库验收分别记账。完整capabilities/binding_ready保持关闭，不把开发Jar作为已验收的正式发布；真实建表、凭据、部署和实机操作仍由用户自行进行，当前无新的业务决定需确认。

## 附录 Z：四加载器生产包与本服终态预占恢复（2026-11-19）

1. Forge1.20.1/NeoForge1.21.1完成附录Y同一名单宿主接线，ServerStartedEvent安装，Authlib4/6分别适配；同一主线程负责名单内存，既有唯一有界worker负责文件/Profile/ACK。四模块均有status/reconcile，仅限权限等级4；reconcile是内存排队提示，刷新可信镜像及终态预占，不因命令存在就授权任何新绑定/解绑。
2. Forge构建使用FG6公开GenerateSRG和extractSrg产生Mojmap→Searge文件，Mixin0.8.5 processor产生refmap及Shadow额外映射；jar、jarJar都包含配置/refmap，reobfJar/reobfJarJar均应用额外映射，Manifest的MixinConfigs注册qqbind-official.mixins.json。未采用下载失败的MixinGradle。NeoForgeModDev2.0.141沿用Mojmap运行时，neoforge.mods.toml声明独立无Fabric refmap配置，same Mixin源码中的map/save/load保留原运行名。
3. OfficialTokenService加载状态后维护只读快速未决标志；RuntimeSession在有本地RESERVED时会继续调度维护，即使没有其它待办。I/O每批最多4项、按operation_id轮转；取本地固定generation/reservation/范围和中心可信操作核对。中心COMMITTED才finalize为CONSUMED，REJECTED/ABORTED才INVALIDATED；PREPARED/RESERVED/记录缺失/断库/冲突保留原预占，按既有2/10/30/60/300秒退避。没有按重启/退出/过期做撤销，没有后台新建或提交绑定。仍缺完整未决授权恢复和原生管理员写入口，不称整体恢复完成。
4. finalize继续重新读取权威终态，不信任本地上次state或HTTP自称成功；持久文件原子提交和未知结果规则不变。收尾只改变本服预占，不伪造中心token_finalize ACK；后者由原插件补偿流程合并。分项镜像/白名单/在线ACK不混为整笔完成；已提交中心不回滚。
5. 四目标实际完整模块构建通过：Forge47.3.1/FG6.0.54/JDK17、Neo21.1.206/ModDev2.0.141/JDK21以及此前两版Fabric公开依赖。此版本组是隔离开发参数，未读取真实gradle.properties，不冒充用户部署版本。com.mysql:mysql-connector-j:8.0.33替代旧重定向坐标，未升级驱动版本。build均排除test，没有启动MC。
6. 新工具build-loader-isolated.ps1统一隔离并支持复用明确公共Maven目录，resume-loader-isolated.ps1只用于已审查快照。Forge使用固定真实patched/Mojmap jar及其POM/游戏公开清单作为离线编译输入，并实际执行FG映射、Mixin processor与重混淆；不复制用户输入哈希记录、配置/init或用假Loader类替代。NeoForge按实际NFRT工具和Mojang公开清单临时补齐依赖元数据后重编译5364个框架源。下载/缓存/中间编译失败不作成功证据；当前均产出最终开发Jar。
7. 成品路径QQBindMod/build/qqofficial-development/2026-11-19-four-loaders/，含四Jar、SHA256、SOURCE_HASHES、BUILD_INFO；源码逐文件和成功构建快照一致。Mixin注册/适用refmap、真工厂/运行时/恢复类、打包依赖已核对；javap确认Forge成员为f_11377_/m_11398_/m_11399_，NeoForge为map/save/load。不把这些结果等同真实Mixin应用/MC登录/QQ/SQL验收。
8. 只运行受影响token既有26项，在原重启场景增强自动终态收尾/失联保留检查，无新增用例数；Python/前端/统计未改不重跑。自动化qq按用户要求改为每30分钟、先正式功能后最小验证；最新待办是原生管理员解绑/查询、未决授权处理、其它官方写持久去重、本人排行、remote/local与交付。整体capabilities/binding_ready继续关闭，未扩大既定业务或最低框架版本。

## 附录 AA：剩余七类业务正式接线与交付（2026-11-19）

1. **原生管理员**：四模块 `/qqbind unbind <游戏名>`、list、operation <完整UUID> [retry|cancel] 调用共同 OfficialNativeAdmin，经本机权限4和当前玩家连接核验。只在游戏线程检查/刷新短期授权及回送结果，I/O使用现有有界worker。解绑固定owner/revision及原绑定的同步目标，中心按两表事务提交；retry只能由原game_admin继续原解绑，不能提交QQ用户绑定。事务工作结束前再次检查策略租约。原生list最多50游戏名，不显示官方身份。

2. **新事件恢复**：`/mc operation <编号> retry [原六码] -s <原服>`。原请求人在当前消息重新通过实例、群、权限、可靠昵称及原active账号依据；策略版本变化明确冲突。原操作PREPARED且尚未认领时，提供同代次/账号原码才可首次认领并发送；已经持久认领的reserve不重发，只查本服预占。模组/插件重启保留RESERVED，后台不提交，中心终态才收尾；未预占或无效预占在权威CAS中止后才作废。管理员可以查询/中止，不能代别人提交。

3. **官方写命令**：新增 POST `/api/v2/binding/command`，公共scope外固定 `operation_id/request_key/action/payload/config_revision`；action为broadcast或console。服务先核对选中scope和鉴权，广播使用绑定API令牌，控制台仅接受独立consoleToken且不得与apiToken相同。插件先校验当前角色/名单并持久DISPATCHED，模组在同一个官方状态文件的可选command_receipts中持久DISPATCHED后再排队执行。两端都不保存payload/控制台正文/回显；失败或重启DISPATCHED一律UNKNOWN，不重发。首次成功可返回经过既有输出过滤的瞬时output/result/truncated，重复请求只有原状态。控制台沿用硬禁及配置禁用规则，必须单服；广播最多4服且逐服报告。

   插件 `/mc add/edit/del/hide/show` 接官方配置原子修订，并以独立command_receipts.json记录事件散列、动作、固定目标、载荷散列、修订和分目标状态；每环境最多10000，不删除旧防重依据。同事件在重启后不重复追加条目。配置写后回执丢失明确UNKNOWN，由用户核对，不推测成功或重新apply。`add`/`edit host`/`batchadd` 复用 OneBot 的地址归一化、端口拆分和 SSRF 校验：内网、回环、链路本地及云元数据地址只有超级管理员可以写入。`/mc command <编号>`只允许本人/本群管理员查看状态。控制台Token仅超级管理员可用 `/mc token <服务器名> <Token|清除>` 设置，也可由用户放在环境console_tokens/<server_uuid>.json；命令路径不回显或记录Token，但消息会经过QQ平台。

4. **本人排行**：排行保留公开统计语义，使用原服务器配置的权威active_snapshot关联当前事件用户；一用户多账号分别标注本人和排名，排名保持并列规则。权威失败单独提示，本页仍显示公开榜单，不查旧bindings兜底。

5. **remote/hybrid**：两者共用独立两表、TLS/结构/权限核验、持久镜像、版本副作用和终态恢复。中心失联保留已持久关系，新增/解绑由中心失败而暂停，不改权威。除初次快照和主动reconcile，现有唯一worker每60秒预算刷新一致性快照，捕获跨服原生变更/晚加入目标；失败使用原退避、不清空镜像。capabilities要求初次可信镜像、真实名单桥就绪、显式allowCenterSync以及中心读写结构核验，不能以Java类存在声明支持。

6. **local**：单服OfficialModState authority=local，额外local_authority保存权威bindings和完整operations。local事务借OfficialMirrorService同一I/O协调提交：权威关系、操作终态、预占消费/作废墓碑、镜像和游戏待办同次原子替换，不是两文件事务。派生镜像/ACK不能改变未决绑定结果；local不构造数据库连接，不接收中心sync。local目录额外/local，禁止与center文件互换，仍schema_version=1；旧center格式继续接受，不自动迁移。

   插件center_settings.json允许storage_mode=hybrid|remote|local；可选server_modes按稳定UUID覆盖默认模式。connection在所有服纯local时可为null，否则使用既定中心字段。它不是中心同群多身份域，账号体系规则不变。local操作号按事件与服务器固定生成；check/unbind all -s all至多4服，中心分区处理一次，local逐服返回，未知服不回滚成功服。local operation须完整编号和原服；check游标仍绑定用户/本服/limit，10分钟且每页当前鉴权。

7. **完整操作回执与后台local**：模组operations/{id}额外返回已校验的operation对象（既有SQL字段规范，不含原码/昵称全文/凭据）供插件复核payload_hash、归属及固定effects，外层保留原状态/预占字段。bind可选resume/cancel/target_server_ids，token_code只在首次预占时需要；unbind可选cancel/target_server_ids。

   新增 GET `/api/v2/binding/operations`，仅local支持，公共scope外filter=pending|all、limit=1..201，返回最近摘要数组。后台原有三个插件范围路由可选server_id以选择local原服；HTTP框架认证不变，不接受客户端actor。受信服务 POST /unbind 可使用仅operation_id/management_action=cancel|retry管理已有操作；只允许CAS中止未提交或重查已终态固定待办，禁止prepare/commit。此形式与普通用户解绑互斥，中心仍使用原独立管理仓储。页面增加权威服务器选择，分页快照和旧响应保护同时绑定server_id。

8. **交付与验证**：两表SQL字段定稿未改变，仅更新交付标记；中心/local/控制台模板及维护切换/恢复/实际验收见QQ_OFFICIAL_DELIVERY.md。统一检查在上述开发之后进行；只保留相关已有编排/入口/管理回归、一个小型Java本地事务/重启/未决恢复和Python互通/写防重smoke，以及实际四模块编译打包与前端类型/构建。不存在QQ/MC/TiDB/浏览器实机通过的结论。

## 附录 AB：现行QQ协议修正与可实现差异（2026-11-20，未测试）

本附录是本轮正式源码增量，不改变前述单活、重新绑定、一对多、两表、断库和退群规则。依用户最新要求，本轮不运行测试、类型检查或四模块构建；只完成源码审阅与隔离Vite发布资源生成。前包通过数字不覆盖本轮，旧四Jar没有更新。

1. **QQ消息协议**：BotpyGroupTransport直接调用统一api.bot.qq.com，认证仍由已选框架实例维护，不调用SDK可能自动重发的消息request封装。上传只在路径携带group_openid，file_data带固定file_name=mcsight.png及srv_send_msg=false。统一检查HTTP与err_code/兼容code；201/202和异步审核不当确认送达。尚未尝试消息POST时，上传异常可在剩余发送预算/窗口及当前授权允许时降级文字；一旦尝试消息POST，未知结果不自动补发。上传与消息的发送预算仍最多4次，成员只读GET另用以下预算。

2. **事件角色与定位**：只读author.member_role的member/admin/owner；未知仍普通成员。仅接受事件mentions内最多20个、scope=single且非bot/is_you的member_openid。不根据昵称、username或数字QQ猜身份。check/unbind支持唯一可靠@目标和显式user标识；check按游戏名可查公开绑定状态，只有本人可见本人标注、当前管理员可见归属与他人的完整账号。所有关联只查当前权威active数据。

3. **管理员分页**：PageCache新增minimum_permission（1/3/4），管理查询/成员检测保存至少3；绑定及回执按取得结果时权限保存阈值。新消息page在读取和发送前复核当前事件角色、群配置修订、环境授权及阈值；管理员降为普通成员后旧管理页失效。缓存仍只属于原用户/群/实例/环境，10分钟，不保存原消息或回复凭据。

4. **管理与显示**：batchadd使用名称:主机[:模组API端口]逗号分隔，最多20项；全部校验后一次配置修订，不采用部分成功。batchremove同样整批；move位置从0开始，swap须两个不同现有服务器。lastonline查询/开关消费status现有last_activity_time毫秒和last_activity_player，无可靠数据时不推断。nicknamecheck提供查询、开关、忽略/取消忽略及分页忽略名单；只修改提示策略，不影响游戏绑定。所有写仍先持久防重；只读开关查询不创建写回执。官方群 policy 必须包含共享群策略的全部字段（与 OneBot 的 `utils/group_policy.py` 一致）。旧版 state.json 迁移 v1 时只补缺失 policy 字段；v2 缺字段直接判为文件损坏，不再静默补默认值。进入 SQLite 后，读取缺字段的 policy 一律按损坏处理。后台提供上述开关及原有绑定昵称规则/模板配置。

5. **内邀成员读取**：第一方端点GET /v2/groups/{group_openid}/members，cursor分页，members数组最多30条/页，next_cursor空字符串为末页，60QPM。详情GET /members/{member_openid}为30QPM，本轮扫描不额外调用详情。两者内邀，未获权11253。成员结构含member_openid/username/member_role/bot/joined_at/union_openid，未保证member_nickname；username仅显示。列表扫描单并发、间隔至少1.05秒、55次/分钟、最多34页/1000人/45秒；8秒单请求、响应512KiB、无重试/重定向。结果内存缓存5分钟最多8范围，失败60秒；配置/权限每页复核，截断明确报告，不当实时完整群资格证明，不写盘成员名单。

6. **绑定/昵称检测**：bindcheck限当前管理员，扫描非bot成员后，与当前中心可信mirror_snapshot的active用户或所选local权威比较，最多列30位未绑定；不读旧表。checknick仅对近5分钟插件处理的群事件author/mentions中可靠member_nickname检测OneBot同一括号非空格式，忽略bot和nickname_ignore；近期未发言/被提及者为无法核验，不标违规。成员列表API未保证全量member_nickname，不伪造全群可核验，也不因名单缺失/退群自动解绑。

7. **只读协议增量**：GET /api/v2/binding/check可选view=active_roster，且与game_id/user_id互斥；公共scope与认证不变，仅可信服务调用。返回与user_id查询相同的bindings/game_ids/next_cursor结构，只列active。limit=1..100；10分钟快照游标绑定本服、view、用户目标及limit，不能与其它查询混用。Python逐页重查授权，最多100页/10000账号，重复、非法游标或超限整次拒绝，不发布部分权威结论；不增加表、写接口或新权威。

8. **断库回执与就绪**：中心模式GET operations/{id}在BindingApi返回DB_UNAVAILABLE/RESULT_UNKNOWN/SCOPE_MISMATCH时才受控尝试原TokenApi；它仍校验范围/授权和持久预占，返回原reservation信封。结构损坏和UNAUTHORIZED不回退；不释放预占、不根据本地事实提交中心变更。插件后台binding_ready=null、binding_readiness=per_request_handshake；模组/api/status返回运行时内存就绪状态，仅作展示，实际变更仍须capabilities结构/读写核验。

待续仅涉及本轮必要验证与新四Jar、以及用户自己的真实权限/连接/MC/SQL/认证/视觉验收；本轮不自动提交、推送、发布或部署。命令对照见QQ_OFFICIAL_TEST_CHECKLIST.md，发布资源已同步views/config。

## 附录 AC：mentions 中的群昵称字段（2026-11-20，未测试）

官方官方群@事件的author及mentions对象可含member_nickname；mentions通过member_openid关联当前被提及成员，并含scope/bot/is_you。插件之前只缓存author昵称，本轮把有效单成员、非bot、非is_you目标的有效昵称加入OfficialScope，并由OfficialMembers.observe写入既有同环境/实例/群、最多3000人、5分钟的内存缓存。不写盘、不调用详情接口。checknick据此能检测近期发言或被@成员；未出现在近期插件事件的其他成员仍无法核验。成员列表API返回username不能推导全群群名片。本次仅公开文档复核与源码编辑，依用户要求没有测试或更新Jar。
