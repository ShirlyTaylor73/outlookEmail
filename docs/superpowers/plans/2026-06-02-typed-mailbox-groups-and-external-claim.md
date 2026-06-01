# 类型化邮箱分组与外部领取 API 实现计划

> **面向 AI 代理的工作者：** 必需子技能：平台支持子代理且计划较大/可安全分 wave 时使用 superpowers:parallel-executing-plans；计划较小、任务强耦合或平台不支持子代理时使用 superpowers:serial-executing-plans。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通账号和临时邮箱都迁移到真实 typed group 模型，并新增 API Key 鉴权的单个邮箱 claim、complete、release 外部接口。

**架构：** `groups.mailbox_type` 区分普通账号组和临时邮箱组，`temp_emails.group_id` 接入真实分组；启动时在 `init_db()` 中幂等迁移旧库。后端新增 `mailbox_claims` 表和事务化 claim 状态机，外部 API 根据源分组类型领取 `accounts` 或 `temp_emails` 资源。前端改用 `mailbox_type` 判断分组类型，临时邮箱生成、导入、列表和移动都按真实 `group_id` 运行。

**技术栈：** Flask, SQLite, pytest, unittest, 原生 JavaScript, CSS

---

## 文件结构

- `outlook_web/segments/01_bootstrap.py`：新增 `groups.mailbox_type`、`temp_emails.group_id`、`mailbox_claims` 表、索引和幂等旧库迁移。
- `outlook_web/segments/02_groups_accounts.py`：新增分组类型 helper，调整分组排序、创建、更新、删除、统计和类型校验。
- `outlook_web/segments/04_routes_groups_accounts.py`：调整分组 API、普通账号移动校验、导出逻辑，并新增外部 mailbox claim API。
- `outlook_web/segments/06_routes_temp_email.py`：让临时邮箱支持 `group_id` 过滤、生成、导入、移动和默认落点。
- `templates/partials/index/dialogs-primary.html`：新建分组弹窗增加分组类型控件，更新排序提示。
- `static/js/index/02-groups.js`：前端分组类型判断、分组排序、下拉过滤和当前分组加载逻辑。
- `static/js/index/03-temp-emails.js`：按当前临时邮箱分组加载列表，生成临时邮箱时传 `group_id`。
- `static/js/index/04-accounts.js`：导入临时邮箱时传 `group_id`，普通账号编辑下拉只显示普通账号组。
- `static/js/index/07-settings.js`：同步设置页内重复的账号导入和编辑逻辑。
- `static/js/index/10-batch-actions.js`：批量移动时按资源类型选择目标组并调用对应 API。
- `tests/test_temp_email_groups.py`：typed group、临时邮箱迁移、分组计数、导入生成、移动校验测试。
- `tests/test_external_mailbox_claim.py`：外部 claim、complete、release、惰性回收、并发和安全字段测试。
- `docs/api.md`：新增外部 mailbox claim API 说明。
- `README.md`：新增类型化分组和外部领取 API 使用说明。

## 任务

### 任务 1：编写 typed group 和临时邮箱分组失败测试

**依赖：** 无
**文件集：** `tests/test_temp_email_groups.py`
**导出/变更接口：** 无
**消费接口：** `web_outlook_app.py::app`
**复杂度：** standard

**文件：**
- 创建：`tests/test_temp_email_groups.py`

- [ ] **步骤 1：建立隔离测试夹具**

  使用 `unittest.TestCase`，设置 `TESTING=True`、`WTF_CSRF_ENABLED=False`。`setUp()` 中调用 `init_db()`，登录 client session，并清理：

  ```sql
  DELETE FROM temp_email_tags;
  DELETE FROM temp_email_messages;
  DELETE FROM temp_email_shares;
  DELETE FROM temp_emails;
  DELETE FROM accounts;
  DELETE FROM groups WHERE name NOT IN ('默认分组', '临时邮箱');
  UPDATE groups SET sort_order = CASE WHEN name = '临时邮箱' THEN 0 ELSE 1 END;
  ```

  设置默认登录 session：

  ```python
  with self.client.session_transaction() as sess:
      sess['logged_in'] = True
  ```

- [ ] **步骤 2：覆盖 schema 迁移和默认类型**

  编写 `test_init_db_adds_group_type_and_temp_email_group_id`：

  ```python
  db = web_outlook_app.get_db()
  group_columns = {row['name'] for row in db.execute('PRAGMA table_info(groups)').fetchall()}
  temp_columns = {row['name'] for row in db.execute('PRAGMA table_info(temp_emails)').fetchall()}
  self.assertIn('mailbox_type', group_columns)
  self.assertIn('group_id', temp_columns)
  temp_group = db.execute("SELECT * FROM groups WHERE name = '临时邮箱'").fetchone()
  default_group = db.execute("SELECT * FROM groups WHERE name = '默认分组'").fetchone()
  self.assertEqual(temp_group['mailbox_type'], 'temp_email')
  self.assertEqual(default_group['mailbox_type'], 'account')
  ```

- [ ] **步骤 3：覆盖旧临时邮箱迁移到默认临时邮箱组**

  编写 `test_existing_temp_emails_are_backfilled_to_default_temp_group`。用 SQL 手工插入 `temp_emails(email, status)`，把 `group_id` 置空，然后调用 `init_db()`。断言所有 `group_id IS NULL` 的记录都被更新到 `name='临时邮箱'` 的真实 ID。

- [ ] **步骤 4：覆盖 `/api/groups` 类型和计数**

  编写 `test_groups_api_counts_by_mailbox_type`：

  - 插入一个普通账号到默认分组。
  - 插入两个临时邮箱到默认临时邮箱组。
  - 请求 `GET /api/groups`。
  - 断言默认分组 `mailbox_type='account'` 且 `account_count=1`。
  - 断言临时邮箱组 `mailbox_type='temp_email'` 且 `account_count=2`。

- [ ] **步骤 5：覆盖临时邮箱列表、生成和导入的 group_id**

  添加测试：

  - `test_get_temp_emails_filters_by_group_id`：创建两个 `temp_email` 分组，各插入一个临时邮箱，`GET /api/temp-emails?group_id=<id>` 只返回该组数据。
  - `test_generate_temp_email_defaults_to_default_temp_group`：mock provider 创建成功，未传 `group_id` 时落到默认临时邮箱组。
  - `test_import_temp_email_accepts_temp_group_and_rejects_account_group`：传 `mailbox_type='temp_email'` 分组成功，传普通账号组返回 `400`。

- [ ] **步骤 6：覆盖类型限制和删除规则**

  添加测试：

  - `test_account_cannot_move_to_temp_email_group`：`POST /api/accounts/batch-update-group` 目标为临时邮箱组返回 `400`。
  - `test_temp_email_can_move_only_to_temp_group`：新增临时邮箱移动接口后，移动到普通账号组返回 `400`，移动到临时邮箱组成功。
  - `test_system_temp_group_cannot_be_deleted_but_can_be_sorted`：删除默认临时邮箱系统组返回错误；排序接口允许包含临时邮箱组并保持请求顺序。

- [ ] **步骤 7：运行测试确认失败**

  运行：

  ```bash
  python -m pytest tests/test_temp_email_groups.py -q -p no:cacheprovider
  ```

  预期：FAIL，错误集中在缺少 `mailbox_type`、`temp_emails.group_id`、分组类型校验和临时邮箱移动接口。

- [ ] **步骤 8：提交失败测试**

  ```bash
  git add tests/test_temp_email_groups.py
  git commit -m "test: cover typed mailbox groups"
  ```

### 任务 2：编写外部 mailbox claim 失败测试

**依赖：** 无
**文件集：** `tests/test_external_mailbox_claim.py`
**导出/变更接口：** 无
**消费接口：** `web_outlook_app.py::app`
**复杂度：** standard

**文件：**
- 创建：`tests/test_external_mailbox_claim.py`

- [ ] **步骤 1：建立外部 API 测试夹具**

  使用 `unittest.TestCase`。`setUp()` 中：

  - 设置 `external_api_key='test-external-key'`。
  - 清理 `mailbox_claims`、`temp_emails`、`accounts`。
  - 确保存在一个普通账号组和一个临时邮箱组。
  - 准备 helper：

  ```python
  def _headers(self):
      return {'X-API-Key': 'test-external-key'}
  ```

  ```python
  def _insert_account(self, email_addr, group_id, status='active'):
      ...
  ```

  ```python
  def _insert_temp_email(self, email_addr, group_id, status='active'):
      ...
  ```

- [ ] **步骤 2：覆盖鉴权和参数错误**

  添加测试：

  - `test_claim_requires_api_key`：无 API Key 返回 `401`。
  - `test_claim_rejects_missing_source_group`：缺 `source_group_id` 返回 `400`。
  - `test_claim_rejects_missing_caller_or_task`：缺 `caller_id` 或 `task_id` 返回 `400`。
  - `test_claim_unknown_source_group_returns_404`：不存在分组返回 `404`。

- [ ] **步骤 3：覆盖普通账号和临时邮箱领取**

  添加测试：

  - `test_claim_account_group_returns_oldest_active_account`：同组两个 active 普通账号，按 `id` 升序领取第一个，响应包含 `resource_type='account'`、`resource_id`、`email`、`claim_token`、`lease_expires_at`，不包含 `password`、`refresh_token`、`imap_password`、`proxy_url`。
  - `test_claim_temp_email_group_returns_oldest_active_temp_email`：临时邮箱组返回 `resource_type='temp_email'`，不包含 `duckmail_token`、`duckmail_password`、`cloudflare_jwt`。
  - `test_claim_returns_null_when_no_active_mailbox`：无 active 资源时 `200` 且 `mailbox is None`。

- [ ] **步骤 4：覆盖并发不重复和 release**

  添加测试：

  - `test_claim_twice_does_not_duplicate_resource`：连续两次 claim 同一源组，返回两个不同资源；只有一个资源时第二次返回 `mailbox=None`。
  - `test_release_keeps_resource_in_source_group_and_allows_reclaim`：release 后资源仍在源组，再次 claim 可拿到同一资源但新 token 不同。
  - `test_release_with_wrong_token_returns_409`。

- [ ] **步骤 5：覆盖 complete 移动和类型校验**

  添加测试：

  - `test_complete_account_moves_to_account_target_group`：普通账号 complete 后 `accounts.group_id` 更新到目标普通组，claim 状态为 `completed`。
  - `test_complete_temp_email_moves_to_temp_target_group`：临时邮箱 complete 后 `temp_emails.group_id` 更新到目标临时邮箱组。
  - `test_complete_rejects_target_type_mismatch`：普通账号目标为临时邮箱组返回 `400`。
  - `test_complete_with_wrong_token_returns_409`。

- [ ] **步骤 6：覆盖惰性过期状态机**

  添加测试：

  - `test_claim_lazily_expires_old_claims`：手动把 `lease_expires_at` 改到过去；下一次 claim 先把旧记录标记为 `expired`。
  - `test_late_complete_after_expired_allowed_if_not_reclaimed`：旧 claim 已 `expired`，资源当前没有新的 `claiming`，旧 token complete 成功并移动目标组。
  - `test_late_release_after_expired_returns_409`。
  - `test_old_token_complete_returns_409_after_resource_reclaimed`：旧 claim `expired` 后资源被新 claim 占用，旧 token complete 返回 `409`。

- [ ] **步骤 7：运行测试确认失败**

  运行：

  ```bash
  python -m pytest tests/test_external_mailbox_claim.py -q -p no:cacheprovider
  ```

  预期：FAIL，错误为缺少 `mailbox_claims` 表和 `/api/external/mailboxes/*` 路由。

- [ ] **步骤 8：提交失败测试**

  ```bash
  git add tests/test_external_mailbox_claim.py
  git commit -m "test: cover external mailbox claim api"
  ```

### 任务 3：实现 typed group 迁移和后端分组行为

**依赖：** 任务 1
**文件集：** `outlook_web/segments/01_bootstrap.py`, `outlook_web/segments/02_groups_accounts.py`, `outlook_web/segments/04_routes_groups_accounts.py`, `outlook_web/segments/06_routes_temp_email.py`
**导出/变更接口：** `outlook_web/segments/01_bootstrap.py::init_db`, `outlook_web/segments/02_groups_accounts.py::normalize_mailbox_type`, `outlook_web/segments/02_groups_accounts.py::get_default_temp_email_group_id`, `outlook_web/segments/02_groups_accounts.py::get_group_mailbox_type`, `outlook_web/segments/02_groups_accounts.py::add_group`, `outlook_web/segments/02_groups_accounts.py::update_group`, `outlook_web/segments/02_groups_accounts.py::delete_group`, `outlook_web/segments/06_routes_temp_email.py::load_temp_emails`, `outlook_web/segments/06_routes_temp_email.py::add_temp_email`
**消费接口：** `outlook_web/segments/02_groups_accounts.py::get_group_by_id`, `outlook_web/segments/04_routes_groups_accounts.py::api_get_groups`, `outlook_web/segments/06_routes_temp_email.py::get_temp_email_count`
**复杂度：** deep

**文件：**
- 修改：`outlook_web/segments/01_bootstrap.py`
- 修改：`outlook_web/segments/02_groups_accounts.py`
- 修改：`outlook_web/segments/04_routes_groups_accounts.py`
- 修改：`outlook_web/segments/06_routes_temp_email.py`

- [ ] **步骤 1：在 `init_db()` 实现幂等 schema 迁移**

  在创建 `groups` 后检测列：

  ```python
  cursor.execute("PRAGMA table_info(groups)")
  group_columns = {row[1] for row in cursor.fetchall()}
  if 'mailbox_type' not in group_columns:
      cursor.execute("ALTER TABLE groups ADD COLUMN mailbox_type TEXT DEFAULT 'account'")
  ```

  在创建 `temp_emails` 后检测列：

  ```python
  cursor.execute("PRAGMA table_info(temp_emails)")
  temp_columns = {row[1] for row in cursor.fetchall()}
  if 'group_id' not in temp_columns:
      cursor.execute('ALTER TABLE temp_emails ADD COLUMN group_id INTEGER')
  ```

  删除 `TEMP_EMAIL_GROUP_ID = -1` 常量定义，或改为不再使用的注释清理。

- [ ] **步骤 2：迁移默认分组类型和旧临时邮箱**

  确保默认临时邮箱组存在后执行：

  ```sql
  UPDATE groups
  SET mailbox_type = 'temp_email'
  WHERE name = '临时邮箱';
  ```

  ```sql
  UPDATE groups
  SET mailbox_type = 'account'
  WHERE mailbox_type IS NULL OR mailbox_type = '' OR mailbox_type NOT IN ('account', 'temp_email');
  ```

  查默认临时邮箱组真实 ID，并执行：

  ```sql
  UPDATE temp_emails
  SET group_id = ?
  WHERE group_id IS NULL;
  ```

  创建索引：

  ```sql
  CREATE INDEX IF NOT EXISTS idx_temp_emails_group_created
  ON temp_emails(group_id, created_at);
  ```

- [ ] **步骤 3：新增分组类型 helper**

  在 `02_groups_accounts.py` 新增：

  ```python
  MAILBOX_TYPE_ACCOUNT = 'account'
  MAILBOX_TYPE_TEMP_EMAIL = 'temp_email'
  MAILBOX_TYPES = {MAILBOX_TYPE_ACCOUNT, MAILBOX_TYPE_TEMP_EMAIL}
  ```

  实现：

  ```python
  def normalize_mailbox_type(value: Any, default: str = MAILBOX_TYPE_ACCOUNT) -> str:
      normalized = str(value or '').strip().lower()
      return normalized if normalized in MAILBOX_TYPES else default
  ```

  ```python
  def get_default_temp_email_group_id(db=None) -> Optional[int]:
      database = db or get_db()
      row = database.execute(
          "SELECT id FROM groups WHERE mailbox_type = 'temp_email' AND name = '临时邮箱' LIMIT 1"
      ).fetchone()
      if row:
          return int(row['id'])
      row = database.execute("SELECT id FROM groups WHERE mailbox_type = 'temp_email' ORDER BY id LIMIT 1").fetchone()
      return int(row['id']) if row else None
  ```

  ```python
  def get_group_mailbox_type(group_id: int, db=None) -> Optional[str]:
      row = (db or get_db()).execute('SELECT mailbox_type FROM groups WHERE id = ?', (group_id,)).fetchone()
      return normalize_mailbox_type(row['mailbox_type']) if row else None
  ```

- [ ] **步骤 4：调整分组排序、创建、更新和删除**

  - `load_groups()`：不再用 `name = '临时邮箱'` 固定最前，只按 `sort_order, id`。
  - `get_movable_group_ids()`：返回所有分组 ID，不再排除临时邮箱。
  - `set_group_position()`：允许默认临时邮箱组排序。
  - `add_group(..., mailbox_type='account')`：插入 `mailbox_type`，默认 `account`。
  - `update_group(..., mailbox_type=None)`：如果分组已有资源，不允许改类型；无资源时允许更新类型。
  - `delete_group()`：`is_system=1` 分组不可删；删除普通账号组时账号回默认账号组，删除临时邮箱组时临时邮箱回默认临时邮箱组。

- [ ] **步骤 5：调整分组 API 和普通账号移动校验**

  在 `04_routes_groups_accounts.py`：

  - `api_get_groups()`：按 `mailbox_type` 分别调用普通账号计数或临时邮箱计数。
  - `api_add_group()` 和 `api_update_group()`：读取 `mailbox_type`，默认 `account`。
  - `api_batch_update_account_group()`：目标分组必须 `mailbox_type='account'`，否则返回 `400`。
  - `api_export_group()` 和 `build_group_export_content()`：按 `group['mailbox_type']` 决定普通账号或临时邮箱导出。

- [ ] **步骤 6：调整临时邮箱数据 helper 和 API**

  在 `06_routes_temp_email.py`：

  - `get_temp_email_group_id()` 改为调用 `get_default_temp_email_group_id()`。
  - `load_temp_emails(group_id=None)` 支持可选分组过滤。
  - `get_temp_email_count(group_id=None)` 支持按分组统计。
  - `add_temp_email(..., group_id=None)` 未传时使用默认临时邮箱组，写入 `temp_emails.group_id`。
  - `api_get_temp_emails()` 支持 `group_id` 查询参数。
  - `api_import_temp_emails()` 和 `api_generate_temp_email()` 读取可选 `group_id`，校验目标分组为 `temp_email`。
  - 新增 `POST /api/temp-emails/batch-update-group`，接收 `temp_email_ids` 和 `group_id`，目标组必须为 `temp_email`。

- [ ] **步骤 7：运行 typed group 测试**

  运行：

  ```bash
  python -m pytest tests/test_temp_email_groups.py -q -p no:cacheprovider
  ```

  预期：PASS。

- [ ] **步骤 8：提交 typed group 后端实现**

  ```bash
  git add outlook_web/segments/01_bootstrap.py outlook_web/segments/02_groups_accounts.py outlook_web/segments/04_routes_groups_accounts.py outlook_web/segments/06_routes_temp_email.py
  git commit -m "feat: add typed mailbox groups"
  ```

### 任务 4：实现外部 mailbox claim API

**依赖：** 任务 2, 任务 3
**文件集：** `outlook_web/segments/01_bootstrap.py`, `outlook_web/segments/04_routes_groups_accounts.py`
**导出/变更接口：** `outlook_web/segments/04_routes_groups_accounts.py::api_external_claim_mailbox`, `outlook_web/segments/04_routes_groups_accounts.py::api_external_complete_mailbox_claim`, `outlook_web/segments/04_routes_groups_accounts.py::api_external_release_mailbox_claim`, `outlook_web/segments/04_routes_groups_accounts.py::claim_external_mailbox`, `outlook_web/segments/04_routes_groups_accounts.py::complete_external_mailbox_claim`, `outlook_web/segments/04_routes_groups_accounts.py::release_external_mailbox_claim`
**消费接口：** `outlook_web/segments/02_groups_accounts.py::get_group_by_id`, `outlook_web/segments/02_groups_accounts.py::get_group_mailbox_type`, `outlook_web/segments/03_mail_helpers.py::api_key_required`
**复杂度：** deep

**文件：**
- 修改：`outlook_web/segments/01_bootstrap.py`
- 修改：`outlook_web/segments/04_routes_groups_accounts.py`

- [ ] **步骤 1：新增 `mailbox_claims` 表和索引**

  在 `init_db()` 中创建表：

  ```sql
  CREATE TABLE IF NOT EXISTS mailbox_claims (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      resource_type TEXT NOT NULL,
      resource_id INTEGER NOT NULL,
      source_group_id INTEGER NOT NULL,
      target_group_id INTEGER,
      claim_token TEXT NOT NULL UNIQUE,
      caller_id TEXT NOT NULL,
      task_id TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'claiming',
      lease_expires_at TIMESTAMP NOT NULL,
      result_detail TEXT DEFAULT '',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      completed_at TIMESTAMP
  )
  ```

  新增索引：

  ```sql
  CREATE INDEX IF NOT EXISTS idx_mailbox_claims_resource_status
  ON mailbox_claims(resource_type, resource_id, status);
  ```

  ```sql
  CREATE INDEX IF NOT EXISTS idx_mailbox_claims_status_lease
  ON mailbox_claims(status, lease_expires_at);
  ```

- [ ] **步骤 2：新增 claim 常量和序列化 helper**

  在 `04_routes_groups_accounts.py` 外部 API 区域新增：

  ```python
  MAILBOX_CLAIM_STATUS_CLAIMING = 'claiming'
  MAILBOX_CLAIM_STATUS_COMPLETED = 'completed'
  MAILBOX_CLAIM_STATUS_RELEASED = 'released'
  MAILBOX_CLAIM_STATUS_EXPIRED = 'expired'
  MAILBOX_CLAIM_TOKEN_PREFIX = 'mclm_'
  ```

  实现 `parse_mailbox_claim_lease_seconds(value)`，默认 600，限制 1 到 3600。

  实现 `serialize_claimed_mailbox(resource_type, row, claim_token, lease_expires_at)`，只返回 `resource_type`、`resource_id`、`email`、`group_id`、`claim_token`、`lease_expires_at`，不返回凭据。

- [ ] **步骤 3：实现惰性回收和领取事务**

  实现 `expire_stale_mailbox_claims(db, now_str)`：

  ```sql
  UPDATE mailbox_claims
  SET status = 'expired', updated_at = ?
  WHERE status = 'claiming' AND lease_expires_at <= ?;
  ```

  实现 `claim_external_mailbox(source_group_id, caller_id, task_id, lease_seconds)`：

  - `BEGIN IMMEDIATE`。
  - 回收过期 claim。
  - 查询源分组，校验存在。
  - `mailbox_type='account'` 时从 `accounts` 选择 `status='active'` 且无有效 `claiming` 的最小 `id`。
  - `mailbox_type='temp_email'` 时从 `temp_emails` 选择 `status='active'` 且无有效 `claiming` 的最小 `id`。
  - 插入 `mailbox_claims`，生成 `mclm_` 前缀高熵 token。
  - 没有可领取资源时 commit 并返回 `None`。

- [ ] **步骤 4：实现 complete 事务**

  实现 `complete_external_mailbox_claim(resource_type, resource_id, claim_token, target_group_id, caller_id, task_id, detail)`：

  - `BEGIN IMMEDIATE`。
  - 回收过期 claim。
  - 查匹配 `resource_type/resource_id/claim_token` 且状态在 `claiming, expired` 的 claim。
  - 如果资源当前存在其他 `claiming`，返回冲突。
  - 校验目标分组存在且 `mailbox_type` 与 `resource_type` 一致。
  - 更新 `accounts.group_id` 或 `temp_emails.group_id`。
  - 更新 claim 为 `completed`，写 `target_group_id`、`result_detail`、`completed_at`。

- [ ] **步骤 5：实现 release 事务**

  实现 `release_external_mailbox_claim(resource_type, resource_id, claim_token, caller_id, task_id, detail)`：

  - `BEGIN IMMEDIATE`。
  - 回收过期 claim。
  - 只接受匹配且 `status='claiming'` 的 claim。
  - 更新为 `released`，写 `result_detail`。
  - 如果已经 `expired`、`completed`、`released` 或被新 claim 占用，返回冲突。

- [ ] **步骤 6：新增 API Key 路由**

  新增：

  - `POST /api/external/mailboxes/claim`
  - `POST /api/external/mailboxes/complete`
  - `POST /api/external/mailboxes/release`

  三个接口均使用 `@csrf_exempt` 和 `@api_key_required`。错误码：

  - 参数缺失或类型不匹配：`400`。
  - 资源或分组不存在：`404`。
  - token 冲突或状态冲突：`409`。
  - 无可领取资源：`200` 且 `{'success': True, 'mailbox': None}`。

  路由定义后用 `assert_endpoint_protection` 检查 `_requires_api_key`。

- [ ] **步骤 7：运行 claim 测试**

  运行：

  ```bash
  python -m pytest tests/test_external_mailbox_claim.py -q -p no:cacheprovider
  ```

  预期：PASS。

- [ ] **步骤 8：运行 typed group 和 claim 聚合测试**

  运行：

  ```bash
  python -m pytest tests/test_temp_email_groups.py tests/test_external_mailbox_claim.py -q -p no:cacheprovider
  ```

  预期：PASS。

- [ ] **步骤 9：提交 claim API 实现**

  ```bash
  git add outlook_web/segments/01_bootstrap.py outlook_web/segments/04_routes_groups_accounts.py
  git commit -m "feat: add external mailbox claim api"
  ```

### 任务 5：更新前端 typed group 和临时邮箱分组交互

**依赖：** 任务 3
**文件集：** `templates/partials/index/dialogs-primary.html`, `static/js/index/02-groups.js`, `static/js/index/03-temp-emails.js`, `static/js/index/04-accounts.js`, `static/js/index/07-settings.js`, `static/js/index/10-batch-actions.js`, `tests/test_temp_email_groups.py`
**导出/变更接口：** `static/js/index/02-groups.js::isTempMailboxGroup`, `static/js/index/02-groups.js::isAccountMailboxGroup`, `static/js/index/02-groups.js::updateGroupSelects`, `static/js/index/03-temp-emails.js::loadTempEmails`
**消费接口：** `outlook_web/segments/04_routes_groups_accounts.py::api_get_groups`, `outlook_web/segments/06_routes_temp_email.py::api_get_temp_emails`, `outlook_web/segments/06_routes_temp_email.py::api_import_temp_emails`, `outlook_web/segments/06_routes_temp_email.py::api_generate_temp_email`
**复杂度：** standard

**文件：**
- 修改：`templates/partials/index/dialogs-primary.html`
- 修改：`static/js/index/02-groups.js`
- 修改：`static/js/index/03-temp-emails.js`
- 修改：`static/js/index/04-accounts.js`
- 修改：`static/js/index/07-settings.js`
- 修改：`static/js/index/10-batch-actions.js`
- 修改：`tests/test_temp_email_groups.py`

- [ ] **步骤 1：新建分组弹窗增加类型选择**

  在 `groupDescription` 后加入：

  ```html
  <div class="form-group" id="groupMailboxTypeGroup">
      <label class="form-label">分组类型</label>
      <select class="form-select" id="groupMailboxType">
          <option value="account">普通邮箱</option>
          <option value="temp_email">临时邮箱</option>
      </select>
      <div class="form-hint">一个分组只能管理一种邮箱资源，保存后有资源时不能切换类型。</div>
  </div>
  ```

  将排序提示改为「所有分组按排序值显示，系统默认分组不可删除但可排序。」

- [ ] **步骤 2：前端改用 `mailbox_type` 判断分组类型**

  在 `02-groups.js` 新增：

  ```javascript
  function isTempMailboxGroup(group) {
      return String(group?.mailbox_type || '').toLowerCase() === 'temp_email';
  }
  function isAccountMailboxGroup(group) {
      return String(group?.mailbox_type || 'account').toLowerCase() === 'account';
  }
  ```

  替换 `group.name === '临时邮箱'` 判断。渲染分组时临时邮箱图标基于 `isTempMailboxGroup(group)`。

- [ ] **步骤 3：调整分组创建和编辑**

  - `showAddGroupModal()` 默认 `groupMailboxType.value = 'account'`，编辑模式显示当前类型。
  - `editGroup(groupId)` 填充 `groupMailboxType`。
  - `saveGroup()` 请求体加入 `mailbox_type`。
  - 编辑已有且有资源的分组时，保留控件但禁用，提示「分组内已有邮箱，不能切换类型」。

- [ ] **步骤 4：调整下拉过滤和当前分组导入**

  `updateGroupSelects()`：

  - `importGroupSelect` 在普通导入模式显示普通账号组；临时邮箱导入模式显示临时邮箱组。
  - `editGroupSelect` 和 `tokenSaveGroupSelect` 只显示普通账号组。
  - 新增 `getGroupsByMailboxType(type)`，减少重复过滤。

  `isTempImportGroup()` 改为根据当前 `importGroupSelect` 的分组 `mailbox_type` 判断。

- [ ] **步骤 5：临时邮箱列表、生成和导入传真实 group_id**

  在 `03-temp-emails.js`：

  - `loadTempEmails(forceRefresh=false)` 请求 `/api/temp-emails?group_id=${currentGroupId}`。
  - 缓存 key 使用 `temp:${currentGroupId}`，避免多个临时邮箱分组共用缓存。
  - `doGenerateTempEmail()` 请求体加入 `group_id: currentGroupId`。

  在 `04-accounts.js` 和 `07-settings.js` 的临时邮箱导入分支，请求 `/api/temp-emails/import` 时加入 `group_id: groupId`。

- [ ] **步骤 6：批量移动适配临时邮箱**

  在 `10-batch-actions.js`：

  - 普通账号批量移动仍调用 `/api/accounts/batch-update-group`，目标分组过滤 `account`。
  - 临时邮箱批量移动调用 `/api/temp-emails/batch-update-group`，目标分组过滤 `temp_email`。
  - 批量菜单文案根据 `isTempEmailGroup` 显示「移动临时邮箱分组」或「移动账号分组」。

- [ ] **步骤 7：补充静态断言测试**

  在 `tests/test_temp_email_groups.py` 增加：

  ```python
  def test_frontend_uses_mailbox_type_for_temp_groups(self):
      groups_js = pathlib.Path(ROOT_DIR, 'static', 'js', 'index', '02-groups.js').read_text(encoding='utf-8')
      temp_js = pathlib.Path(ROOT_DIR, 'static', 'js', 'index', '03-temp-emails.js').read_text(encoding='utf-8')
      dialogs = pathlib.Path(ROOT_DIR, 'templates', 'partials', 'index', 'dialogs-primary.html').read_text(encoding='utf-8')
      self.assertIn('groupMailboxType', dialogs)
      self.assertIn('isTempMailboxGroup', groups_js)
      self.assertIn('mailbox_type', groups_js)
      self.assertIn('group_id', temp_js)
      self.assertIn('/api/temp-emails?', temp_js)
  ```

- [ ] **步骤 8：运行 typed group 测试**

  运行：

  ```bash
  python -m pytest tests/test_temp_email_groups.py -q -p no:cacheprovider
  ```

  预期：PASS。

- [ ] **步骤 9：提交前端适配**

  ```bash
  git add templates/partials/index/dialogs-primary.html static/js/index/02-groups.js static/js/index/03-temp-emails.js static/js/index/04-accounts.js static/js/index/07-settings.js static/js/index/10-batch-actions.js tests/test_temp_email_groups.py
  git commit -m "feat: adapt ui to typed mailbox groups"
  ```

### 任务 6：更新文档和外部 API 说明

**依赖：** 任务 4, 任务 5
**文件集：** `README.md`, `docs/api.md`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/04_routes_groups_accounts.py::api_external_claim_mailbox`, `outlook_web/segments/04_routes_groups_accounts.py::api_external_complete_mailbox_claim`, `outlook_web/segments/04_routes_groups_accounts.py::api_external_release_mailbox_claim`
**复杂度：** quick

**文件：**
- 修改：`README.md`
- 修改：`docs/api.md`

- [ ] **步骤 1：更新 README 分组说明**

  在分组或临时邮箱章节补充：

  ```markdown
  分组分为「普通邮箱」和「临时邮箱」两种类型。一个分组只能包含一种资源；普通账号不能移动到临时邮箱分组，临时邮箱也不能移动到普通邮箱分组。升级旧版本数据库时，已有临时邮箱会自动迁移到默认「临时邮箱」分组。
  ```

- [ ] **步骤 2：更新 README 外部领取示例**

  增加单个 claim 流程：

  ```bash
  curl -X POST "http://localhost:5000/api/external/mailboxes/claim" \
    -H "X-API-Key: your-api-key" \
    -H "Content-Type: application/json" \
    -d '{"source_group_id":1,"caller_id":"worker-01","task_id":"batch-001","lease_seconds":600}'
  ```

  complete 示例：

  ```bash
  curl -X POST "http://localhost:5000/api/external/mailboxes/complete" \
    -H "X-API-Key: your-api-key" \
    -H "Content-Type: application/json" \
    -d '{"resource_type":"account","resource_id":123,"claim_token":"mclm_xxx","target_group_id":5,"caller_id":"worker-01","task_id":"batch-001"}'
  ```

- [ ] **步骤 3：更新 `docs/api.md`**

  新增接口章节：

  - `POST /api/external/mailboxes/claim`
  - `POST /api/external/mailboxes/complete`
  - `POST /api/external/mailboxes/release`

  每个接口写明鉴权、请求字段、响应字段、错误码。状态机说明包含：

  - 租约超时采用惰性回收。
  - 迟到 complete 在资源未被重新领取时允许。
  - 迟到 release 在 claim 已 expired 时返回 `409`。

- [ ] **步骤 4：运行文档关键字检查**

  运行：

  ```bash
  rg -n "mailboxes/claim|mailboxes/complete|mailboxes/release|mailbox_type|惰性回收" README.md docs/api.md
  ```

  预期：README 和 docs/api.md 都能定位到新增说明。

- [ ] **步骤 5：提交文档**

  ```bash
  git add README.md docs/api.md
  git commit -m "docs: describe typed mailbox groups and claim api"
  ```

### 任务 7：总体验证和收口

**依赖：** 任务 4, 任务 5, 任务 6
**文件集：** `tests/test_temp_email_groups.py`, `tests/test_external_mailbox_claim.py`, `outlook_web/segments/01_bootstrap.py`, `outlook_web/segments/02_groups_accounts.py`, `outlook_web/segments/04_routes_groups_accounts.py`, `outlook_web/segments/06_routes_temp_email.py`, `templates/partials/index/dialogs-primary.html`, `static/js/index/02-groups.js`, `static/js/index/03-temp-emails.js`, `static/js/index/04-accounts.js`, `static/js/index/07-settings.js`, `static/js/index/10-batch-actions.js`, `README.md`, `docs/api.md`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/04_routes_groups_accounts.py::api_external_claim_mailbox`, `outlook_web/segments/06_routes_temp_email.py::api_get_temp_emails`, `outlook_web/segments/02_groups_accounts.py::get_default_temp_email_group_id`
**复杂度：** standard

**文件：**
- 检查：`tests/test_temp_email_groups.py`
- 检查：`tests/test_external_mailbox_claim.py`
- 检查：`outlook_web/segments/01_bootstrap.py`
- 检查：`outlook_web/segments/02_groups_accounts.py`
- 检查：`outlook_web/segments/04_routes_groups_accounts.py`
- 检查：`outlook_web/segments/06_routes_temp_email.py`
- 检查：`templates/partials/index/dialogs-primary.html`
- 检查：`static/js/index/02-groups.js`
- 检查：`static/js/index/03-temp-emails.js`
- 检查：`static/js/index/04-accounts.js`
- 检查：`static/js/index/07-settings.js`
- 检查：`static/js/index/10-batch-actions.js`
- 检查：`README.md`
- 检查：`docs/api.md`

- [ ] **步骤 1：运行聚焦测试**

  运行：

  ```bash
  python -m pytest tests/test_temp_email_groups.py tests/test_external_mailbox_claim.py -q -p no:cacheprovider
  ```

  预期：PASS。

- [ ] **步骤 2：运行相关回归测试**

  运行：

  ```bash
  python -m pytest tests/test_temp_email_share.py tests/test_account_share.py tests/test_external_verification_code_api.py -q -p no:cacheprovider
  ```

  预期：PASS，证明分享和现有外部验证码 API 未被破坏。

- [ ] **步骤 3：运行编译检查**

  运行：

  ```bash
  python -m compileall web_outlook_app.py outlook_web
  ```

  预期：无 SyntaxError。

- [ ] **步骤 4：运行安全字段扫描**

  运行：

  ```bash
  rg -n "password|refresh_token|imap_password|duckmail_token|duckmail_password|cloudflare_jwt|proxy_url" outlook_web/segments/04_routes_groups_accounts.py tests/test_external_mailbox_claim.py
  ```

  预期：敏感字段只出现在测试断言或内部查询，不出现在 claim API 响应构造。

- [ ] **步骤 5：运行旧特殊分组逻辑扫描**

  运行：

  ```bash
  rg -n "TEMP_EMAIL_GROUP_ID|name == '临时邮箱'|name = '临时邮箱'|group.name === '临时邮箱'|g.name === '临时邮箱'" outlook_web static templates tests
  ```

  预期：没有后端业务逻辑继续依赖 `TEMP_EMAIL_GROUP_ID = -1`；保留的 `临时邮箱` 字符串只用于默认系统组创建、显示文案或测试夹具。

- [ ] **步骤 6：检查 Git 状态和提交遗漏**

  运行：

  ```bash
  git status --short
  git diff --name-only
  ```

  预期：没有未提交实现文件。若有本计划文件集内遗漏，提交：

  ```bash
  git add tests/test_temp_email_groups.py tests/test_external_mailbox_claim.py outlook_web/segments/01_bootstrap.py outlook_web/segments/02_groups_accounts.py outlook_web/segments/04_routes_groups_accounts.py outlook_web/segments/06_routes_temp_email.py templates/partials/index/dialogs-primary.html static/js/index/02-groups.js static/js/index/03-temp-emails.js static/js/index/04-accounts.js static/js/index/07-settings.js static/js/index/10-batch-actions.js README.md docs/api.md
  git commit -m "feat: support typed mailbox groups and claim api"
  ```

## 并行执行图

> 仅 `parallel-executing-plans` 使用；`serial-executing-plans` 忽略本节。

**Critical Path:** 任务 1 → 任务 3 → 任务 4 → 任务 6 → 任务 7

- Wave 1（无依赖）：任务 1, 任务 2
- Wave 2（依赖 Wave 1）：任务 3（依赖 任务 1）
- Wave 3（依赖 Wave 2）：任务 4（依赖 任务 2, 任务 3）, 任务 5（依赖 任务 3）
- Wave 4（依赖 Wave 3）：任务 6（依赖 任务 4, 任务 5）
- Wave 5（依赖 Wave 4）：任务 7（依赖 任务 4, 任务 5, 任务 6）
- Wave FINAL（所有任务完成后）：F1 规格合规、F2 代码质量、F3 真实手测、F4 范围保真
