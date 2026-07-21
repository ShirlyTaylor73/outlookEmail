# iCloud HME 全局管理、长时注册与批量停用设计

日期：2026-07-07

## 目标

在现有 iCloud Hide My Email（HME）集成基础上，将 HME Cookie、接收邮箱 IMAP 配置、地址查看、长时注册和批量停用删除统一放入右上角“系统设置”的 `iCloud HME` 分区中，作为全局管理能力。

本设计覆盖四个用户目标：

1. 将当前 HME Cookie 登录、IMAP 转发/接收配置等 HME 源配置迁移到右上角设置中。
2. 完善 HME 邮箱管理，支持批量查看 iCloud 使用中地址，并显示是否已导入本项目及对应 `group_id`。
3. 加入类似 `hidemyemail-generator/long_generate_hme.bat` 的长时定时注册任务，支持成功/失败延迟、注册数量、开始和中止，并用 SQLite 记录注册结果。
4. 通过检测发送给 HME 邮箱的 `OpenAI - Access Deactivated [C-...]` 标题邮件，生成候选列表，用户确认后批量停用并删除对应 HME 地址。

## 已确认决策

- UI 入口采用“右上角设置新增一级 `iCloud HME` 分区”，不是独立页面。
- 保留现有多 HME 源能力，不降级为单源。
- HME 地址列表以 iCloud 实时 `/v2/hme/list` 为准，并左连接本项目 `accounts` 和 `groups` 显示导入状态。
- 长时注册成功后同时写入专用 SQLite 记录表，并自动导入 `accounts` 到用户选择的 group。
- 全局同时只允许一个 HME 长时注册任务运行。
- OpenAI 停用删除采用“先扫描候选，用户确认后执行”的两阶段流程。
- UI 风格必须对齐现有设置页：灰白面板、黑色主按钮、灰色次按钮、红色危险按钮，复用 `settings-section`、`settings-panel`、`settings-subpanel`、`settings-sidebar-link` 这类视觉语言。
- “使用中地址与导入状态”是设置分区内的可展开抽屉/折叠面板，不做独立页面。

## 当前代码上下文

现有项目已经具备以下基础：

- `icloud_hme_sources` 表保存 HME 源，包括区域、接收邮箱、IMAP host/port/password/folder、Cookie、maildomain host 和同步状态。
- `accounts` 表通过 `account_type='icloud_hme'`、`provider='icloud_hme'` 和 `icloud_hme_source_id` 表示导入后的 HME 地址。
- `outlook_web/segments/02_groups_accounts.py` 已包含 HME 源 CRUD、HME 地址导入、同步 HME 地址到账户表等逻辑。
- `outlook_web/segments/03_mail_helpers.py` 已包含 `fetch_icloud_hme_list()`，可调用 iCloud HME list endpoint。
- `templates/partials/index/dialogs-management.html` 已有系统设置弹窗和独立 HME 源管理弹窗。
- `static/js/index/07-settings.js` 已有设置页保存逻辑和 HME 源管理前端函数。
- `hidemyemail-generator` 的长时脚本逻辑是循环生成 1 个地址，成功等待 `SUCCESS_DELAY`，失败等待 `FAILURE_DELAY`。
- `Go-iClient` 参考实现确认 HME 停用/删除 API 是 `POST /v1/hme/deactivate` 和 `POST /v1/hme/delete`，body 均为 `{"anonymousId":"..."}`，删除前必须先停用。

## 用户体验设计

### 设置导航

在系统设置左侧导航新增：

- 标题：`iCloud HME`
- 描述：`Cookie、接收源、地址导入与批量停用`
- 点击后滚动到 `settingsIcloudHmeSection`。

现有导入/编辑账号中的“管理 HME 源”按钮应弱化或移除，改为指向设置页 HME 分区；导入账号时只选择已有 HME 源。

### iCloud HME 分区结构

`iCloud HME` 分区包含四个子面板：

1. HME 源与接收邮箱
2. 使用中地址与导入状态
3. 长时定时注册任务
4. OpenAI Access Deactivated 扫描与批量删除

顶部摘要卡显示：

- HME 源数量
- iCloud 使用中地址数量
- 已导入账号数量
- 未导入地址数量
- OpenAI 停用候选数量

### HME 源与接收邮箱面板

该面板替代现有独立 `icloudHmeSourceModal` 的主要能力。

字段：

- 默认 HME 源
- 源名称
- 区域：`global` / `china`
- iCloud Cookie
- Maildomain Host
- 接收邮箱地址
- 接收邮箱 provider
- IMAP host
- IMAP port
- IMAP password
- IMAP folder
- Use SSL

操作：

- 新建源
- 保存源
- 删除未绑定账号的源
- 测试 IMAP
- 同步 HME

敏感字段策略：

- Cookie 和 IMAP 密码继续加密保存。
- 编辑源时，密码/Cookie 输入框为空不覆盖旧值。
- API、公开分享、外部 API 不返回 Cookie、IMAP 密码、maildomain 内部配置。

### 使用中地址与导入状态抽屉

该列表默认折叠，用户点击后展开。列表不跳转到独立页面。

数据来源：

1. 调用 iCloud `/v2/hme/list` 获取远端 HME 地址。
2. 按 HME 地址左连接本项目 `accounts`。
3. 对已导入账号左连接 `groups`，显示 `group_id` 和 group name。

表格列：

- 复选框
- HME 地址
- Label
- iCloud 状态：使用中 / 已停用
- 导入状态：未导入 / 已导入 / 冲突
- Account ID
- Group ID
- Group Name
- 创建时间
- anonymousId
- 操作

筛选：

- 关键词：邮箱、label、anonymousId
- iCloud 状态：使用中、已停用、全部
- 导入状态：全部、仅未导入、仅已导入、冲突
- Group：全部或指定 group

操作：

- 刷新列表
- 导入单个未导入地址
- 批量导入选中地址
- 查看已导入账号
- 查看冲突

导入行为：

- 批量导入必须选择目标 group。
- 已导入同源地址不重复创建，可更新状态/备注。
- 跨源或非 HME 账号冲突不自动覆盖，必须显示冲突信息。

### 长时定时注册任务面板

该面板必须是完整任务控制台，而不是只有状态展示。

参数：

- HME 源
- 目标 group
- 注册数量
- Label
- 成功延迟秒数，默认 `780`
- 失败延迟秒数，默认 `3900`
- 每次生成数量，固定为 `1`
- 失败重试上限，`0` 表示不限
- 备注

操作：

- 开始注册
- 中止任务
- 刷新状态
- 查看任务日志

运行规则：

- 全局同时只允许一个 HME 注册任务运行。
- 每轮调用 HME generate，然后 reserve。
- 成功产生并保存地址后：
  - 写入 HME 注册记录表。
  - 自动导入 `accounts` 到目标 group。
  - 等待成功延迟。
- 失败或未产生新地址后：
  - 写入失败记录和错误原因。
  - 等待失败延迟。
- 达到目标数量后任务完成。
- 用户点击中止后，当前轮请求结束后停止，不再进入下一轮等待/注册。

状态展示：

- 当前任务 ID
- Label
- 目标数量
- 成功数量
- 失败数量
- 当前状态：idle/running/stopping/completed/failed/stopped
- 下一次执行倒计时
- 最近生成邮箱
- 最近错误
- 进度条
- 最近日志

### OpenAI Access Deactivated 扫描与批量删除

匹配目标标题：

- `OpenAI - Access Deactivated [C-...]`
- 中括号内 ID 可变。

扫描策略：

1. 选择 HME 源、group 或选中地址作为扫描范围。
2. 从本地 HME source cache 或按需刷新接收源邮件读取候选邮件。
3. 通过标题匹配 `OpenAI - Access Deactivated`。
4. 使用现有 HME 邮件归属逻辑确认邮件发送给哪个 HME 地址。
5. 将 HME 地址映射到 iCloud list 中的 `anonymousId`。
6. 生成候选列表，不立即删除。

候选列表列：

- HME 地址
- Account ID
- Group ID
- Group Name
- 匹配邮件标题
- 邮件时间
- anonymousId
- 当前 iCloud 状态
- 处理状态

执行策略：

- 用户勾选候选并点击“确认停用并删除选中”后执行。
- 对每个候选先调用 deactivate，再调用 delete。
- 如果地址已停用，可以直接尝试 delete。
- 每个地址独立记录结果；单个失败不阻塞其他地址。
- 成功删除后：
  - 本地账号状态标记为 `inactive` 或 `deleted` 风格的不可用状态；若现有状态枚举不支持 `deleted`，使用 `inactive` 并在备注或 HME 删除记录中记录删除时间。
  - HME 删除记录表写入操作结果。
- 不自动物理删除本地 `accounts` 记录，避免破坏历史邮件和审计。

## 后端设计

### iCloud HME API helper

扩展 `outlook_web/segments/03_mail_helpers.py` 或拆出 `outlook_web/icloud_hme.py`，提供：

- `fetch_icloud_hme_list(cookie, region, maildomain_host)`
- `generate_icloud_hme(cookie, region, maildomain_host)`
- `reserve_icloud_hme(cookie, region, maildomain_host, email, label, note)`
- `deactivate_icloud_hme(cookie, region, maildomain_host, anonymous_id)`
- `delete_icloud_hme(cookie, region, maildomain_host, anonymous_id)`

所有 helper 返回统一结构：

```python
{
    "success": bool,
    "data": dict | list,
    "error": str,
    "status_code": int | None,
}
```

HTTP 请求需要使用与现有 `fetch_icloud_hme_list()` 一致的 Cookie、Origin、Referer、User-Agent 和 region/maildomain host 解析。

### 地址列表 API

新增登录态 API：

- `GET /api/icloud-hme/addresses`

参数：

- `source_id`
- `active`: `true` / `false` / `all`
- `import_state`: `all` / `imported` / `not_imported` / `conflict`
- `group_id`
- `q`
- `limit`
- `offset`
- `refresh=1` 强制实时请求 iCloud；默认允许使用短期缓存。

响应：

```json
{
  "success": true,
  "source_id": 1,
  "summary": {
    "active": 684,
    "inactive": 12,
    "imported": 219,
    "not_imported": 465,
    "conflict": 1
  },
  "addresses": [
    {
      "hme": "river-lion-482@icloud.com",
      "label": "openai-batch-20260707",
      "is_active": true,
      "anonymous_id": "F5A7...",
      "created_at": "2026-07-07T01:12:00",
      "import_state": "imported",
      "account_id": 1201,
      "group_id": 49,
      "group_name": "OpenAI 注册池",
      "conflict": null
    }
  ],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "total": 684
  }
}
```

### 批量导入 API

新增：

- `POST /api/icloud-hme/addresses/import`

请求：

```json
{
  "source_id": 1,
  "group_id": 49,
  "addresses": ["a@icloud.com", "b@icloud.com"],
  "status": "active",
  "remark": "imported from HME address list"
}
```

行为复用现有 `import_icloud_hme_accounts()` / `add_icloud_hme_account()`，但返回更适合列表 UI 的逐项结果。

### 长时注册 API

新增：

- `GET /api/icloud-hme/long-runner/status`
- `POST /api/icloud-hme/long-runner/start`
- `POST /api/icloud-hme/long-runner/stop`
- `GET /api/icloud-hme/long-runner/logs`

`start` 请求：

```json
{
  "source_id": 1,
  "target_group_id": 49,
  "target_count": 100,
  "label": "openai-batch-20260707",
  "success_delay_seconds": 780,
  "failure_delay_seconds": 3900,
  "failure_retry_limit": 0,
  "note": "Generated by outlookEmail HME long runner"
}
```

约束：

- 如果已有任务处于 `running` 或 `stopping`，返回 409。
- `target_count` 必须为正整数。
- `success_delay_seconds` 和 `failure_delay_seconds` 必须为非负整数。
- `target_group_id` 必须是可用普通账号 group。
- `source_id` 必须存在且 Cookie 可用。

运行方式：

- 在进程内后台线程执行，符合当前项目已有 scheduler/SSE 均为单进程的部署约束。
- 任务状态写入 SQLite，应用重启后不会自动继续运行中的任务；重启时将遗留 `running/stopping` 标记为 `stopped`，并记录 `interrupted by process restart`。

### OpenAI 停用删除 API

新增：

- `POST /api/icloud-hme/deactivation-candidates/scan`
- `GET /api/icloud-hme/deactivation-candidates`
- `POST /api/icloud-hme/deactivation-candidates/delete`

扫描请求：

```json
{
  "source_id": 1,
  "group_id": 49,
  "folder": "all",
  "subject_contains": "OpenAI - Access Deactivated",
  "limit": 200,
  "refresh": true
}
```

删除请求：

```json
{
  "source_id": 1,
  "candidate_ids": [1, 2, 3]
}
```

执行结果必须逐项返回。

删除执行前必须强制读取一次 Apple HME 完整列表，并以实时列表中的
`anonymousId` 为准，不得直接使用缓存中的旧值。批次内只请求一次完整列表：

- 完整列表请求失败或响应结构无效时，中止整批操作，不执行停用或删除。
- 地址仍在实时列表中时，使用实时 `anonymousId` 依次调用停用、删除接口。
- 地址已不在实时列表中时，不再调用 Apple action；候选记为
  `already_absent`，缓存记为 `deleted`，项目账号停用并追加自动处理备注。
- 完整列表刷新成功后，本地仍为 `active`、但本次未返回的缓存地址先记为
  `missing`；只有删除流程完成本地收尾后才记为 `deleted`。
- 实时列表仍包含地址、但 Apple action 返回 `-41003` 时继续记为 `failed`，
  不将该错误无条件视为“已删除”。

批量结果返回 `deleted_count`、`already_absent_count`、`error_count`，逐项状态为
`deleted`、`already_absent` 或 `failed`。

候选列表提供“全选/取消全选”，范围仅限当前加载的 `pending` 和 `failed`
候选；已完成状态不可选择。批量删除按钮显示当前已选数量且无选择时禁用。

## 数据库设计

新增表由 `init_db()` 创建，并通过 `ALTER TABLE` 兼容已有数据库。

### `icloud_hme_address_cache`

保存 iCloud list 的最近结果，减少设置页反复打开时频繁请求 iCloud。

字段：

- `id`
- `source_id`
- `hme`
- `label`
- `note`
- `anonymous_id`
- `is_active`
- `forward_to_email`
- `created_at_remote`
- `raw_json`
- `synced_at`
- unique `(source_id, hme)`

### `icloud_hme_generation_tasks`

记录长时注册任务。

字段：

- `id`
- `source_id`
- `target_group_id`
- `label`
- `note`
- `target_count`
- `success_delay_seconds`
- `failure_delay_seconds`
- `failure_retry_limit`
- `status`
- `success_count`
- `failure_count`
- `stop_requested`
- `last_email`
- `last_error`
- `next_run_at`
- `started_at`
- `stopped_at`
- `completed_at`
- `created_at`
- `updated_at`

### `icloud_hme_generated_addresses`

记录每个生成结果。

字段：

- `id`
- `task_id`
- `source_id`
- `account_id`
- `target_group_id`
- `hme`
- `label`
- `anonymous_id`
- `status`: `generated/imported/failed`
- `error`
- `raw_json`
- `created_at`

### `icloud_hme_generation_logs`

记录任务日志。

字段：

- `id`
- `task_id`
- `level`
- `message`
- `created_at`

保留最近日志条数可在实现中限制，例如每个任务最多保留 1000 条。

### `icloud_hme_deactivation_candidates`

记录扫描候选和删除结果。

字段：

- `id`
- `source_id`
- `account_id`
- `group_id`
- `hme`
- `anonymous_id`
- `message_subject`
- `message_sender`
- `message_received_at`
- `source_message_id`
- `status`: `candidate/deactivating/deactivated/deleting/deleted/failed/skipped`
- `error`
- `deactivated_at`
- `deleted_at`
- `created_at`
- `updated_at`

## 前端设计

主要改动在：

- `templates/partials/index/dialogs-management.html`
- `static/js/index/07-settings.js`
- `static/css/index/06-modals-toast.css`

### HTML

新增设置导航项：

- `settingsIcloudHmeSection`

新增 HME 分区，使用现有设置页结构：

- `settings-section`
- `settings-panel`
- `settings-panel-stack`
- `settings-subpanel`
- `settings-panel-grid`
- `settings-action-row`

### JavaScript 状态

新增前端状态：

- `icloudHmeAddressCache`
- `icloudHmeAddressPagination`
- `icloudHmeSelectedAddresses`
- `icloudHmeLongRunnerStatus`
- `icloudHmeDeactivationCandidates`

新增函数组：

- HME 源设置：复用并迁移现有 `loadIcloudHmeSources()`、`saveIcloudHmeSource()`、`syncIcloudHmeSource()` 等。
- 地址列表：`loadIcloudHmeAddresses()`、`renderIcloudHmeAddressList()`、`importSelectedIcloudHmeAddresses()`。
- 长时注册：`loadIcloudHmeLongRunnerStatus()`、`startIcloudHmeLongRunner()`、`stopIcloudHmeLongRunner()`、`renderIcloudHmeLongRunnerStatus()`。
- 停用删除：`scanIcloudHmeDeactivationCandidates()`、`renderIcloudHmeDeactivationCandidates()`、`deleteSelectedIcloudHmeCandidates()`。

### 样式

必须复用现有风格：

- 主按钮：`btn btn-primary`
- 次按钮：`btn btn-secondary`
- 危险按钮：`btn btn-danger`
- 面板：`settings-panel` / `settings-subpanel`
- 标签：使用灰白底色为主，成功/警告/失败仅轻量使用绿/黄/红。

不采用高饱和蓝色大面积背景。

## 错误处理

- iCloud Cookie 缺失或过期：返回明确错误，UI 显示“Cookie 无效或已过期，请更新 HME 源 Cookie”。
- iCloud API 429/限流：长时注册记录失败，进入失败延迟；不立即高频重试。
- anonymousId 缺失：停用删除候选标记为 `failed`，提示需要刷新 iCloud 地址列表。
- IMAP 读取失败：扫描候选失败，但不影响现有账号数据。
- 任务启动冲突：已有运行任务时返回 409，UI 禁用开始按钮并显示当前任务。
- 应用重启：遗留 running 任务标记为 stopped，并记录中断原因。

## 安全与隐私

- 不在外部 API、公开分享或导出中暴露 Cookie、IMAP 密码、anonymousId 原始详情，除非是登录态设置页 API。
- 停用/删除 HME 是不可逆操作，必须二次确认。
- 批量删除只处理用户选中的候选，不自动删除全部扫描结果。
- 本地 `accounts` 不物理删除，保留审计和历史邮件关联。

## 测试计划

### 单元/接口测试

- HME address list 能正确合并 iCloud 返回和本地 accounts/groups。
- 已导入、未导入、冲突三种状态计算正确。
- 批量导入能创建账号并返回 group id。
- 长时注册 start 参数校验、单任务锁、stop 状态转换正确。
- 长时注册成功时写任务、地址记录并导入 accounts。
- 长时注册失败时写失败日志并设置下一次运行时间。
- OpenAI 扫描能匹配标题并映射 HME 地址。
- 停用删除按 `deactivate -> delete` 顺序调用，并逐项记录结果。
- Cookie/密码不出现在非登录态响应中。

### 前端测试/手工验证

- 设置页新增 `iCloud HME` 导航项并能滚动到分区。
- HME 源配置表单保持现有设置页视觉风格。
- 使用中地址抽屉可展开/折叠，筛选和分页可用。
- 已导入行显示 group id/group name。
- 长时注册参数表单包含开始、中止、刷新状态和日志。
- OpenAI 候选扫描先展示列表，只有确认后才执行删除。

### 运行验证

实现完成后需要运行：

```bash
python -m pytest tests/test_temp_email_share.py tests/test_account_share.py tests/test_external_verification_code_api.py tests/test_env_loading.py -q -p no:cacheprovider
python -m compileall web_outlook_app.py outlook_web
```

由于该功能涉及运行时行为、数据库和前后端流程，还需要按项目要求进行本地 HTTP 冒烟测试。测试应启动 `python web_outlook_app.py`，访问 `http://127.0.0.1:5000`，在登录态下验证 HME 设置页、地址列表和长时任务状态 API。若涉及外部 API 行为，继续运行项目已有 external API smoke。

## 交付边界

本规格聚焦一个实现计划可覆盖的范围：

- 迁移和扩展 HME 设置 UI。
- 增加 HME 地址列表与导入状态。
- 增加长时注册后台任务和记录表。
- 增加 OpenAI 停用候选扫描和确认删除。

不包含：

- 自动捕获 iCloud Cookie 的浏览器自动化。
- 多进程/分布式任务队列。
- HME 删除后物理删除本地账号。
- 对 Apple HME API 的登录密码认证流程。
