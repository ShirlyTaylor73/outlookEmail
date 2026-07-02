# iCloud HME 邮箱集成实现计划

> **面向 AI 代理的工作者：** 必需子技能：平台支持子代理且计划较大/可安全分 wave 时使用 superpowers:parallel-executing-plans；计划较小、任务强耦合或平台不支持子代理时使用 superpowers:serial-executing-plans。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 iCloud Hide My Email 地址作为一等邮箱接入现有普通邮箱体系，支持共享 IMAP 接收源、独立邮箱视图、分组、分享、外部 API、领取和已有 HME 地址同步。

**架构：** 新增 `icloud_hme_sources` 保存共享接收源配置，并在 `accounts` 上增加 `icloud_hme_source_id`；每个 HME 地址仍是独立 `accounts` 记录，`account_type=icloud_hme`、`provider=icloud_hme`。邮件读取、详情、分享和外部 API 在遇到 HME 账号时通过 source 的接收邮箱 IMAP 拉信，并按 HME 地址做归属校验。

**技术栈：** Flask、SQLite、pytest、标准库 `imaplib` / `email`、现有静态 JavaScript / HTML / CSS、现有 `encrypt_data` / `decrypt_data`。

---

## 文件结构

- `outlook_web/segments/01_bootstrap.py`：数据库迁移、provider 元数据、文件夹映射。
- `outlook_web/segments/02_groups_accounts.py`：HME source 数据访问、HME 账号导入和账号序列化。
- `outlook_web/segments/03_mail_helpers.py`：可复用的 HME 邮件解析、归属匹配、iCloud HME list 客户端。
- `outlook_web/segments/04_routes_groups_accounts.py`：HME source API、HME 导入 API、外部账号列表敏感字段过滤。
- `outlook_web/segments/05_routes_refresh_mail.py`：HME 邮件列表、详情、本地保留接入。
- `outlook_web/segments/06_routes_temp_email.py`：账号分享的 HME 详情读取与公开响应过滤。
- `outlook_web/segments/08_forwarding_scheduler_errors.py`：外部邮件 API、验证码详情读取和关键字过滤接入 HME 分支。
- `templates/partials/index/dialogs-primary.html`、`templates/partials/index/dialogs-management.html`：导入和编辑弹窗增加 HME source 入口。
- `static/js/index/02-groups.js`、`static/js/index/07-settings.js`：前端 provider label、HME source 管理、HME 导入和编辑交互。
- `static/css/index/04-account-panel.css`、`static/css/index/06-modals-toast.css`：HME source 管理区域样式。
- `tests/test_icloud_hme_sources.py`、`tests/test_icloud_hme_import.py`、`tests/test_icloud_hme_mail_fetch.py`、`tests/test_icloud_hme_external_api.py`、`tests/test_icloud_hme_share.py`、`tests/test_icloud_hme_sync.py`：新增覆盖。
- `README.md`、`.env.example`：补充 iCloud HME 配置和使用说明。

## 实现任务

### 任务 1：增加数据库结构和 provider 元数据

**依赖：** 无
**文件集：** `outlook_web/segments/01_bootstrap.py`, `tests/test_project_runtime.py`, `tests/test_icloud_hme_sources.py`
**导出/变更接口：** `01_bootstrap.py::MAIL_PROVIDERS`, `01_bootstrap.py::PROVIDER_FOLDER_MAP`, `01_bootstrap.py::init_db`
**消费接口：** 无
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/01_bootstrap.py`
- 修改：`tests/test_project_runtime.py`
- 创建：`tests/test_icloud_hme_sources.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_project_runtime.py` 增加断言：

```python
def test_init_db_creates_icloud_hme_schema(self):
    db = web_outlook_app.get_db()
    source_columns = {
        row[1] for row in db.execute("PRAGMA table_info(icloud_hme_sources)").fetchall()
    }
    account_columns = {
        row[1] for row in db.execute("PRAGMA table_info(accounts)").fetchall()
    }
    indexes = {
        row[1] for row in db.execute("PRAGMA index_list(icloud_hme_sources)").fetchall()
    }

    self.assertIn("receiver_email", source_columns)
    self.assertIn("receiver_imap_password", source_columns)
    self.assertIn("cookie", source_columns)
    self.assertIn("icloud_hme_source_id", account_columns)
    self.assertIn("idx_icloud_hme_sources_receiver", indexes)
```

在 `tests/test_icloud_hme_sources.py` 增加 provider 元数据测试：

```python
def test_icloud_hme_provider_meta_exists(self):
    meta = web_outlook_app.get_provider_meta("icloud_hme", "alias@icloud.com")
    self.assertEqual(meta["key"], "icloud_hme")
    self.assertEqual(meta["account_type"], "icloud_hme")
    self.assertEqual(meta["label"], "iCloud Hide My Email")
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_project_runtime.py::ProjectRuntimeTestCase::test_init_db_creates_icloud_hme_schema tests/test_icloud_hme_sources.py::ICloudHmeSourceTestCase::test_icloud_hme_provider_meta_exists -q -p no:cacheprovider`
预期：FAIL，缺少表、列或 provider。

- [ ] **步骤 3：实现 schema 和 provider**

在 `MAIL_PROVIDERS` 增加：

```python
"icloud_hme": {
    "label": "iCloud Hide My Email",
    "imap_host": "",
    "imap_port": 993,
    "account_type": "icloud_hme",
},
```

在 `PROVIDER_FOLDER_MAP` 增加与 `_default` 相同的 IMAP 文件夹候选。创建 `icloud_hme_sources` 表，字段采用规格文档定义；给 `accounts` 增加 `icloud_hme_source_id` 迁移；增加索引：

```sql
CREATE INDEX IF NOT EXISTS idx_icloud_hme_sources_receiver
ON icloud_hme_sources(receiver_email COLLATE NOCASE)
```

新增 `idx_accounts_icloud_hme_source_id` 索引。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_project_runtime.py::ProjectRuntimeTestCase::test_init_db_creates_icloud_hme_schema tests/test_icloud_hme_sources.py::ICloudHmeSourceTestCase::test_icloud_hme_provider_meta_exists -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add outlook_web/segments/01_bootstrap.py tests/test_project_runtime.py tests/test_icloud_hme_sources.py
git commit -m "feat: add icloud hme schema"
```

### 任务 2：实现 HME source 后端模型和管理 API

**依赖：** 任务 1
**文件集：** `outlook_web/segments/02_groups_accounts.py`, `outlook_web/segments/04_routes_groups_accounts.py`, `tests/test_icloud_hme_sources.py`
**导出/变更接口：** `02_groups_accounts.py::create_icloud_hme_source`, `02_groups_accounts.py::update_icloud_hme_source`, `02_groups_accounts.py::delete_icloud_hme_source`, `02_groups_accounts.py::get_icloud_hme_source_by_id`, `02_groups_accounts.py::list_icloud_hme_sources`, `04_routes_groups_accounts.py::api_get_icloud_hme_sources`, `04_routes_groups_accounts.py::api_create_icloud_hme_source`, `04_routes_groups_accounts.py::api_update_icloud_hme_source`, `04_routes_groups_accounts.py::api_delete_icloud_hme_source`, `04_routes_groups_accounts.py::api_test_icloud_hme_source_imap`
**消费接口：** `01_bootstrap.py::init_db`
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/02_groups_accounts.py`
- 修改：`outlook_web/segments/04_routes_groups_accounts.py`
- 修改：`tests/test_icloud_hme_sources.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_icloud_hme_sources.py` 增加 Flask client 测试：

```python
def test_create_source_encrypts_secret_and_serializes_safely(self):
    payload = {
        "name": "Gmail receiver",
        "region": "global",
        "receiver_email": "receiver@gmail.com",
        "receiver_provider": "gmail",
        "receiver_imap_host": "imap.gmail.com",
        "receiver_imap_port": 993,
        "receiver_imap_password": "app-password",
        "receiver_folder": "INBOX",
        "use_ssl": True,
        "cookie": "X-APPLE-WEBAUTH-USER=secret",
        "maildomain_host": "p68-maildomainws.icloud.com",
    }
    response = self.client.post("/api/icloud-hme/sources", json=payload)
    data = response.get_json()
    self.assertTrue(data["success"])
    source = data["source"]
    self.assertNotIn("receiver_imap_password", source)
    self.assertNotIn("cookie", source)

    row = web_outlook_app.get_db().execute(
        "SELECT receiver_imap_password, cookie FROM icloud_hme_sources WHERE id = ?",
        (source["id"],),
    ).fetchone()
    self.assertNotEqual(row["receiver_imap_password"], "app-password")
    self.assertNotEqual(row["cookie"], "X-APPLE-WEBAUTH-USER=secret")
```

增加删除保护测试：source 下存在 HME 账号时删除返回失败。

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_icloud_hme_sources.py -q -p no:cacheprovider`
预期：FAIL，API 和 helpers 不存在。

- [ ] **步骤 3：实现模型 helpers**

在 `02_groups_accounts.py` 实现：

- `normalize_icloud_hme_region(value) -> str`：只允许 `global`、`china`。
- `serialize_icloud_hme_source(row, include_secret=False) -> Dict`：默认不输出密码和 Cookie。
- `create_icloud_hme_source(data) -> Dict`：校验必填字段，加密 `receiver_imap_password` 和 `cookie`。
- `update_icloud_hme_source(source_id, data) -> Optional[Dict]`：空密码表示保留原密文，非空则重加密。
- `delete_icloud_hme_source(source_id) -> bool`：若存在 `accounts.account_type='icloud_hme'` 且绑定该 source，拒绝删除。
- `get_icloud_hme_source_by_id(source_id, include_secret=False)`。
- `list_icloud_hme_sources()`。

- [ ] **步骤 4：实现 API routes**

在 `04_routes_groups_accounts.py` 增加登录保护 API：

- `GET /api/icloud-hme/sources`
- `POST /api/icloud-hme/sources`
- `GET /api/icloud-hme/sources/<int:source_id>`
- `PUT /api/icloud-hme/sources/<int:source_id>`
- `DELETE /api/icloud-hme/sources/<int:source_id>`
- `POST /api/icloud-hme/sources/test-imap`

`test-imap` 使用传入配置尝试 `IMAP4_SSL` / `IMAP4` 登录并选择 folder，只返回 `success`、`error`、`available_folders`。

- [ ] **步骤 5：运行测试验证通过**

运行：`python -m pytest tests/test_icloud_hme_sources.py -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add outlook_web/segments/02_groups_accounts.py outlook_web/segments/04_routes_groups_accounts.py tests/test_icloud_hme_sources.py
git commit -m "feat: manage icloud hme sources"
```

### 任务 3：实现 HME 账号导入和账号序列化

**依赖：** 任务 1, 任务 2
**文件集：** `outlook_web/segments/02_groups_accounts.py`, `outlook_web/segments/04_routes_groups_accounts.py`, `tests/test_icloud_hme_import.py`, `tests/test_external_mailbox_claim.py`
**导出/变更接口：** `02_groups_accounts.py::parse_icloud_hme_import_line`, `02_groups_accounts.py::add_icloud_hme_account`, `02_groups_accounts.py::import_icloud_hme_accounts`, `02_groups_accounts.py::serialize_account_summary`, `04_routes_groups_accounts.py::api_import_icloud_hme_accounts`
**消费接口：** `02_groups_accounts.py::get_icloud_hme_source_by_id`, `01_bootstrap.py::MAIL_PROVIDERS`
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/02_groups_accounts.py`
- 修改：`outlook_web/segments/04_routes_groups_accounts.py`
- 创建：`tests/test_icloud_hme_import.py`
- 修改：`tests/test_external_mailbox_claim.py`

- [ ] **步骤 1：编写失败测试**

新增 `tests/test_icloud_hme_import.py`，覆盖：

```python
def test_import_hme_addresses_binds_selected_source(self):
    source_id = self._create_source()
    response = self.client.post("/api/icloud-hme/accounts/import", json={
        "source_id": source_id,
        "group_id": 1,
        "account_string": "abc@icloud.com\ndef@icloud.com----备注",
        "status": "active",
    })
    data = response.get_json()
    self.assertTrue(data["success"])
    self.assertEqual(data["imported_count"], 2)
    rows = web_outlook_app.get_db().execute(
        "SELECT email, account_type, provider, icloud_hme_source_id, remark FROM accounts ORDER BY email"
    ).fetchall()
    self.assertEqual(rows[0]["account_type"], "icloud_hme")
    self.assertEqual(rows[0]["provider"], "icloud_hme")
    self.assertEqual(rows[0]["icloud_hme_source_id"], source_id)
    self.assertEqual(rows[1]["remark"], "备注")
```

再覆盖：

- `abc@icloud.com----1` 被当作备注，不当作 source ID。
- 同一 HME 地址全局唯一。
- 跨 source 导入返回 `conflicts`。
- `/api/external/accounts` 和 claim 返回 `account_type=icloud_hme`、`provider=icloud_hme`，不返回 source 敏感字段。

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_icloud_hme_import.py tests/test_external_mailbox_claim.py -q -p no:cacheprovider`
预期：新增测试 FAIL。

- [ ] **步骤 3：实现导入 helpers**

在 `02_groups_accounts.py` 实现：

- `parse_icloud_hme_import_line(line) -> Optional[Dict]`：只解析 `email` 和可选 `remark`。
- `add_icloud_hme_account(email, source_id, group_id, remark='', status='active', tags=None) -> Dict`：创建 `accounts` 行，`account_type='icloud_hme'`、`provider='icloud_hme'`、`icloud_hme_source_id=source_id`。
- `import_icloud_hme_accounts(account_string, source_id, group_id, remark='', status='active', tags=None) -> Dict`：返回 `imported_count`、`updated_count`、`conflicts`、`errors`。

更新 `serialize_account_rows` / `serialize_account_summary`，输出 `icloud_hme_source_id` 和 source 安全摘要：`icloud_hme_source_name`、`receiver_email`，不输出密码或 Cookie。

- [ ] **步骤 4：实现导入 API**

在 `04_routes_groups_accounts.py` 增加：

- `POST /api/icloud-hme/accounts/import`

请求字段：`source_id`、`group_id`、`account_string`、`remark`、`status`、`tags`。如果 `source_id` 缺失且系统只有一个 source，后端允许默认使用唯一 source；如果多个 source，返回错误要求前端选择。

- [ ] **步骤 5：运行测试验证通过**

运行：`python -m pytest tests/test_icloud_hme_import.py tests/test_external_mailbox_claim.py -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add outlook_web/segments/02_groups_accounts.py outlook_web/segments/04_routes_groups_accounts.py tests/test_icloud_hme_import.py tests/test_external_mailbox_claim.py
git commit -m "feat: import icloud hme accounts"
```

### 任务 4：实现 HME 邮件解析、列表和详情读取

**依赖：** 任务 1, 任务 2, 任务 3
**文件集：** `outlook_web/segments/03_mail_helpers.py`, `outlook_web/segments/05_routes_refresh_mail.py`, `tests/test_icloud_hme_mail_fetch.py`
**导出/变更接口：** `03_mail_helpers.py::ICLOUD_HME_RECIPIENT_HEADERS`, `03_mail_helpers.py::email_message_belongs_to_hme`, `03_mail_helpers.py::parse_hme_email_message`, `03_mail_helpers.py::get_icloud_hme_source_imap_config`, `05_routes_refresh_mail.py::fetch_icloud_hme_account_emails`, `05_routes_refresh_mail.py::fetch_icloud_hme_account_detail_response`, `05_routes_refresh_mail.py::fetch_account_emails`
**消费接口：** `02_groups_accounts.py::get_icloud_hme_source_by_id`, `03_mail_helpers.py::get_email_detail_imap_generic_result`, `05_routes_refresh_mail.py::upsert_retained_normal_mail_list_items`, `05_routes_refresh_mail.py::upsert_retained_normal_mail_detail`
**复杂度：** deep

**文件：**
- 修改：`outlook_web/segments/03_mail_helpers.py`
- 修改：`outlook_web/segments/05_routes_refresh_mail.py`
- 创建：`tests/test_icloud_hme_mail_fetch.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_icloud_hme_mail_fetch.py` 使用 fake IMAP 对象覆盖：

- `Delivered-To: abc@icloud.com` 命中 `abc@icloud.com`。
- `X-Original-To: abc@icloud.com` 命中。
- 正文包含 `abc@icloud.com` 作为 fallback 命中。
- 只有 `other@icloud.com` 的邮件不返回。
- 详情读取同一个 UID 时，属于当前 HME 返回成功，不属于当前 HME 返回失败。

核心断言：

```python
result = web_outlook_app.fetch_account_emails(account, "inbox", 0, 20)
self.assertTrue(result["success"])
self.assertEqual([item["id"] for item in result["emails"]], ["101"])
self.assertEqual(result["emails"][0]["id_mode"], "uid")
self.assertEqual(result["emails"][0]["method"], "imap")
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_icloud_hme_mail_fetch.py -q -p no:cacheprovider`
预期：FAIL，HME fetch 函数不存在或未分支。

- [ ] **步骤 3：实现解析 helpers**

在 `03_mail_helpers.py` 增加：

```python
ICLOUD_HME_RECIPIENT_HEADERS = (
    "To", "Delivered-To", "X-Original-To", "Envelope-To",
    "Apparently-To", "Original-Recipient", "Resent-To", "Cc",
)
```

实现：

- `normalize_hme_address(value) -> str`
- `extract_message_addresses(message, header_names) -> List[str]`
- `get_email_message_text_body(message) -> str`：复用或对齐现有正文解析逻辑。
- `email_message_belongs_to_hme(message, raw_body, hme_address) -> bool`
- `parse_hme_email_message(account, folder, uid, raw_message) -> Dict`
- `get_icloud_hme_source_imap_config(account) -> Dict`：读取 source、解密密码，返回 IMAP 登录配置。

- [ ] **步骤 4：实现列表和详情读取**

在 `05_routes_refresh_mail.py` 增加：

- `fetch_icloud_hme_account_emails(account, folder, skip, top)`：
  - 支持 `folder='all'` 时按现有聚合文件夹策略读取；
  - 每个候选邮件必须调用 `email_message_belongs_to_hme`；
  - 返回结构与 IMAP 邮箱一致；
  - 本地保留开启时继续由现有调用方按 HME `account_id` 写缓存。

- `fetch_icloud_hme_account_detail_response(account, folder, message_id, method, id_mode, proxy_url='')`：
  - UID 优先；
  - 详情必须重新归属校验；
  - 成功后调用 `upsert_retained_normal_mail_detail`。

更新 `fetch_account_emails`：`account_type == 'icloud_hme'` 先走 HME 分支。

- [ ] **步骤 5：运行测试验证通过**

运行：`python -m pytest tests/test_icloud_hme_mail_fetch.py tests/test_normal_mail_retention.py -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add outlook_web/segments/03_mail_helpers.py outlook_web/segments/05_routes_refresh_mail.py tests/test_icloud_hme_mail_fetch.py
git commit -m "feat: fetch icloud hme mail"
```

### 任务 5：接入外部 API、验证码和领取响应

**依赖：** 任务 3, 任务 4
**文件集：** `outlook_web/segments/04_routes_groups_accounts.py`, `outlook_web/segments/08_forwarding_scheduler_errors.py`, `tests/test_icloud_hme_external_api.py`, `tests/test_external_verification_code_api.py`, `tests/test_external_mailbox_claim.py`
**导出/变更接口：** `08_forwarding_scheduler_errors.py::fetch_external_verification_detail`, `08_forwarding_scheduler_errors.py::email_matches_filters`, `08_forwarding_scheduler_errors.py::api_external_get_emails_v2`, `04_routes_groups_accounts.py::api_external_get_accounts`
**消费接口：** `05_routes_refresh_mail.py::fetch_icloud_hme_account_detail_response`, `05_routes_refresh_mail.py::fetch_account_emails`, `02_groups_accounts.py::serialize_account_summary`
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/04_routes_groups_accounts.py`
- 修改：`outlook_web/segments/08_forwarding_scheduler_errors.py`
- 创建：`tests/test_icloud_hme_external_api.py`
- 修改：`tests/test_external_verification_code_api.py`
- 修改：`tests/test_external_mailbox_claim.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_icloud_hme_external_api.py` 覆盖：

```python
def test_external_accounts_includes_hme_without_source_secrets(self):
    source_id = self._create_source(password="secret", cookie="cookie-secret")
    account_id = self._create_hme_account("abc@icloud.com", source_id)
    response = self.client.get("/api/external/accounts", headers=self.api_headers)
    payload = response.get_json()
    mailbox = next(item for item in payload["accounts"] if item["id"] == account_id)
    self.assertEqual(mailbox["resource_type"], "account")
    self.assertEqual(mailbox["account_type"], "icloud_hme")
    self.assertEqual(mailbox["provider"], "icloud_hme")
    self.assertNotIn("receiver_imap_password", mailbox)
    self.assertNotIn("cookie", mailbox)
```

另加测试：

- `/api/external/emails?email=abc@icloud.com` 调用 `fetch_account_emails`，返回 HME 邮件。
- `/api/external/verification-code` 对 HME 调用 `fetch_icloud_hme_account_detail_response`。
- `email_matches_filters` 对 HME 关键字过滤读取 HME 详情。

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_icloud_hme_external_api.py tests/test_external_verification_code_api.py tests/test_external_mailbox_claim.py -q -p no:cacheprovider`
预期：新增测试 FAIL。

- [ ] **步骤 3：更新外部账号响应**

确保 `api_external_get_accounts` / claim 序列化包含 HME 基本字段，并过滤所有 source 敏感字段。HME 不新增 `resource_type`，仍为 `account`。

- [ ] **步骤 4：更新邮件和验证码分支**

在 `08_forwarding_scheduler_errors.py` 中将 `account_type == 'imap'` 的详情分支扩展为：

```python
if account.get("account_type") == "icloud_hme":
    return fetch_icloud_hme_account_detail_response(...)
if account.get("account_type") == "imap":
    return fetch_imap_account_detail_response(...)
```

更新 `email_matches_filters` 的 keyword detail 读取，同样先处理 HME。

- [ ] **步骤 5：运行测试验证通过**

运行：`python -m pytest tests/test_icloud_hme_external_api.py tests/test_external_verification_code_api.py tests/test_external_mailbox_claim.py -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add outlook_web/segments/04_routes_groups_accounts.py outlook_web/segments/08_forwarding_scheduler_errors.py tests/test_icloud_hme_external_api.py tests/test_external_verification_code_api.py tests/test_external_mailbox_claim.py
git commit -m "feat: expose icloud hme external api"
```

### 任务 6：接入账号分享公开页

**依赖：** 任务 3, 任务 4
**文件集：** `outlook_web/segments/06_routes_temp_email.py`, `static/js/shared-temp-email.js`, `tests/test_icloud_hme_share.py`, `tests/test_account_share.py`
**导出/变更接口：** `06_routes_temp_email.py::refresh_shared_account_messages`, `06_routes_temp_email.py::fetch_shared_account_message_detail`, `06_routes_temp_email.py::format_shared_account_detail`
**消费接口：** `05_routes_refresh_mail.py::fetch_icloud_hme_account_detail_response`, `05_routes_refresh_mail.py::fetch_account_emails`
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/06_routes_temp_email.py`
- 修改：`static/js/shared-temp-email.js`
- 创建：`tests/test_icloud_hme_share.py`
- 修改：`tests/test_account_share.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_icloud_hme_share.py` 覆盖：

- HME 账号可创建 `/api/accounts/<id>/shares`。
- `/api/shared/<token>` 返回 `share_type='account'` 和 provider label。
- `/api/shared/<token>/messages` 只使用 HME `fetch_account_emails`。
- `/api/shared/<token>/messages/<id>` 调用 HME 详情分支。
- 公开响应不包含 `receiver_imap_password`、`cookie`、`maildomain_host`。

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_icloud_hme_share.py tests/test_account_share.py -q -p no:cacheprovider`
预期：新增测试 FAIL。

- [ ] **步骤 3：更新后端分享逻辑**

在 `fetch_shared_account_message_detail` 中加入 HME 分支：

```python
if account.get("account_type") == "icloud_hme":
    return fetch_icloud_hme_account_detail_response(account, folder, message_id, method, id_mode, proxy_url)
```

确保 `format_shared_account_detail` 不输出 source 字段。`refresh_shared_account_messages` 可继续调用 `fetch_account_emails`，因为任务 4 已接入 HME 分支。

- [ ] **步骤 4：更新公开页 JS 文案**

在 `static/js/shared-temp-email.js` 确保 `provider_label` / `provider` 为 `icloud_hme` 时显示 `iCloud HME` 或 `iCloud Hide My Email`，不展示技术字段。

- [ ] **步骤 5：运行测试验证通过**

运行：`python -m pytest tests/test_icloud_hme_share.py tests/test_account_share.py -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add outlook_web/segments/06_routes_temp_email.py static/js/shared-temp-email.js tests/test_icloud_hme_share.py tests/test_account_share.py
git commit -m "feat: share icloud hme mailboxes"
```

### 任务 7：实现阶段 2 HME 地址同步

**依赖：** 任务 2, 任务 3
**文件集：** `outlook_web/segments/02_groups_accounts.py`, `outlook_web/segments/03_mail_helpers.py`, `outlook_web/segments/04_routes_groups_accounts.py`, `tests/test_icloud_hme_sync.py`
**导出/变更接口：** `03_mail_helpers.py::fetch_icloud_hme_list`, `02_groups_accounts.py::sync_icloud_hme_source_accounts`, `04_routes_groups_accounts.py::api_sync_icloud_hme_source`
**消费接口：** `02_groups_accounts.py::get_icloud_hme_source_by_id`, `02_groups_accounts.py::add_icloud_hme_account`, `03_mail_helpers.py::fetch_icloud_hme_list`
**复杂度：** deep

**文件：**
- 修改：`outlook_web/segments/02_groups_accounts.py`
- 修改：`outlook_web/segments/03_mail_helpers.py`
- 修改：`outlook_web/segments/04_routes_groups_accounts.py`
- 创建：`tests/test_icloud_hme_sync.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_icloud_hme_sync.py` mock `fetch_icloud_hme_list`：

```python
def test_sync_creates_and_updates_hme_accounts(self):
    source_id = self._create_source(cookie="encrypted-cookie")
    with patch.object(web_outlook_app, "fetch_icloud_hme_list", return_value={
        "success": True,
        "hmeEmails": [
            {"hme": "new@icloud.com", "label": "new label", "isActive": True},
            {"hme": "old@icloud.com", "label": "old label", "isActive": False},
        ],
    }):
        response = self.client.post(f"/api/icloud-hme/sources/{source_id}/sync")
    data = response.get_json()
    self.assertTrue(data["success"])
    self.assertEqual(data["created"], 2)
    self.assertEqual(data["inactive"], 1)
```

覆盖 Cookie 失效、跨 source 冲突、`last_sync_error` 写入。

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_icloud_hme_sync.py -q -p no:cacheprovider`
预期：FAIL，同步接口不存在。

- [ ] **步骤 3：实现 HME list 客户端**

在 `03_mail_helpers.py` 实现 `fetch_icloud_hme_list(cookie, region='global', maildomain_host='') -> Dict`。使用 `urllib.request` 或现有可用 HTTP 客户端，避免新增依赖。请求约定：

- `global` 默认 `web_origin=https://www.icloud.com`、`maildomain_host=p68-maildomainws.icloud.com`。
- `china` 默认 `web_origin=https://www.icloud.com.cn`、`maildomain_host=p217-maildomainws.icloud.com.cn`。
- endpoint：`https://{maildomain_host}/v2/hme/list`。
- headers 包含 `Origin`、`Referer`、`Cookie`、`User-Agent`。
- 返回统一结构：`{"success": True, "hmeEmails": [...]}` 或 `{"success": False, "error": "..."}`。

- [ ] **步骤 4：实现同步服务和 API**

在 `02_groups_accounts.py` 实现 `sync_icloud_hme_source_accounts(source_id) -> Dict`：

- 解密 Cookie；
- 调用 `fetch_icloud_hme_list`；
- 创建 / 更新 `accounts` HME 记录；
- 跨 source 冲突写入 `conflicts`；
- 更新 `icloud_hme_sources.last_sync_*`。

在 `04_routes_groups_accounts.py` 增加：

- `POST /api/icloud-hme/sources/<int:source_id>/sync`

- [ ] **步骤 5：运行测试验证通过**

运行：`python -m pytest tests/test_icloud_hme_sync.py tests/test_icloud_hme_import.py -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add outlook_web/segments/02_groups_accounts.py outlook_web/segments/03_mail_helpers.py outlook_web/segments/04_routes_groups_accounts.py tests/test_icloud_hme_sync.py
git commit -m "feat: sync icloud hme addresses"
```

### 任务 8：实现前端 HME source 管理和导入交互

**依赖：** 任务 2, 任务 3, 任务 7
**文件集：** `templates/partials/index/dialogs-primary.html`, `templates/partials/index/dialogs-management.html`, `static/js/index/02-groups.js`, `static/js/index/07-settings.js`, `static/css/index/04-account-panel.css`, `static/css/index/06-modals-toast.css`, `tests/test_icloud_hme_import.py`
**导出/变更接口：** `02-groups.js::MAIL_PROVIDER_LABELS`, `02-groups.js::updateImportHint`, `07-settings.js::submitAccountImport`, `07-settings.js::loadIcloudHmeSources`
**消费接口：** `04_routes_groups_accounts.py::api_get_icloud_hme_sources`, `04_routes_groups_accounts.py::api_create_icloud_hme_source`, `04_routes_groups_accounts.py::api_import_icloud_hme_accounts`, `04_routes_groups_accounts.py::api_sync_icloud_hme_source`
**复杂度：** standard

**文件：**
- 修改：`templates/partials/index/dialogs-primary.html`
- 修改：`templates/partials/index/dialogs-management.html`
- 修改：`static/js/index/02-groups.js`
- 修改：`static/js/index/07-settings.js`
- 修改：`static/css/index/04-account-panel.css`
- 修改：`static/css/index/06-modals-toast.css`
- 修改：`tests/test_icloud_hme_import.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_icloud_hme_import.py` 增加静态 hook 测试：

```python
def test_icloud_hme_ui_hooks_exist(self):
    primary = Path("templates/partials/index/dialogs-primary.html").read_text(encoding="utf-8")
    js_groups = Path("static/js/index/02-groups.js").read_text(encoding="utf-8")
    js_settings = Path("static/js/index/07-settings.js").read_text(encoding="utf-8")

    self.assertIn('value="icloud_hme"', primary)
    self.assertIn("/api/icloud-hme/sources", js_settings)
    self.assertIn("/api/icloud-hme/accounts/import", js_settings)
    self.assertIn("iCloud HME", js_groups)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_icloud_hme_import.py::ICloudHmeImportTestCase::test_icloud_hme_ui_hooks_exist -q -p no:cacheprovider`
预期：FAIL，前端 hook 不存在。

- [ ] **步骤 3：更新导入 UI**

在导入邮箱弹窗 `importProviderSelect` 增加：

```html
<option value="icloud_hme">iCloud Hide My Email</option>
```

当 provider 为 `icloud_hme`：

- 显示 HME source 选择器；
- 显示「管理 HME 源」入口；
- 隐藏自定义 IMAP 行内配置；
- import hint 显示：

```text
格式：HME地址 或 HME地址----备注，每行一个。接收 IMAP 配置在 iCloud HME 源中管理。
```

- [ ] **步骤 4：实现 source 管理前端**

在 `static/js/index/07-settings.js` 增加：

- `loadIcloudHmeSources()`
- `openIcloudHmeSourceModal(sourceId)`
- `saveIcloudHmeSource()`
- `deleteIcloudHmeSource(sourceId)`
- `testIcloudHmeSourceImap()`
- `syncIcloudHmeSource(sourceId)`

在提交导入时，如果 provider 为 `icloud_hme`，请求 `/api/icloud-hme/accounts/import`，而不是普通 `/api/accounts/import`。

- [ ] **步骤 5：更新账号卡片展示**

在 `static/js/index/02-groups.js`：

- `MAIL_PROVIDER_LABELS.icloud_hme = 'iCloud HME'`；
- HME 账号不显示 Outlook Token 刷新；
- 编辑账号时显示 HME source 选择器，隐藏 Outlook 凭据和普通 IMAP 密码字段。

- [ ] **步骤 6：运行测试验证通过**

运行：`python -m pytest tests/test_icloud_hme_import.py -q -p no:cacheprovider`
预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add templates/partials/index/dialogs-primary.html templates/partials/index/dialogs-management.html static/js/index/02-groups.js static/js/index/07-settings.js static/css/index/04-account-panel.css static/css/index/06-modals-toast.css tests/test_icloud_hme_import.py
git commit -m "feat: add icloud hme ui"
```

### 任务 9：更新文档并执行完整验证

**依赖：** 任务 5, 任务 6, 任务 8
**文件集：** `README.md`, `.env.example`, `tests/test_icloud_hme_sources.py`, `tests/test_icloud_hme_import.py`, `tests/test_icloud_hme_mail_fetch.py`, `tests/test_icloud_hme_external_api.py`, `tests/test_icloud_hme_share.py`, `tests/test_icloud_hme_sync.py`
**导出/变更接口：** 无
**消费接口：** `04_routes_groups_accounts.py::api_import_icloud_hme_accounts`, `05_routes_refresh_mail.py::fetch_account_emails`, `06_routes_temp_email.py::refresh_shared_account_messages`, `08_forwarding_scheduler_errors.py::api_external_get_emails_v2`
**复杂度：** standard

**文件：**
- 修改：`README.md`
- 修改：`.env.example`
- 测试：新增和相关回归测试文件

- [ ] **步骤 1：更新文档**

在 `README.md` 增加 iCloud HME 章节：

- 说明 HME 地址是独立邮箱；
- 说明必须先配置 HME 接收源；
- 说明接收邮箱可以是 iCloud Mail、Gmail 或自定义 IMAP；
- 给出导入格式：

```text
hme@icloud.com
hme@icloud.com----备注
```

- 说明阶段 2 同步需要 iCloud Cookie，且该能力依赖 Apple 网页端内部接口；
- 说明不会导出或公开 source 密码和 Cookie。

在 `.env.example` 增加可选说明注释，不写真实凭据。

- [ ] **步骤 2：运行新增功能测试**

运行：

```bash
python -m pytest tests/test_icloud_hme_sources.py tests/test_icloud_hme_import.py tests/test_icloud_hme_mail_fetch.py tests/test_icloud_hme_external_api.py tests/test_icloud_hme_share.py tests/test_icloud_hme_sync.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 3：运行相关回归测试**

运行：

```bash
python -m pytest tests/test_account_share.py tests/test_external_verification_code_api.py tests/test_external_mailbox_claim.py tests/test_normal_mail_retention.py tests/test_project_runtime.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 4：运行编译检查**

运行：

```bash
python -m compileall web_outlook_app.py outlook_web
```

预期：命令退出码为 0。

- [ ] **步骤 5：运行本地 HTTP smoke test**

如果本地 `.env` 有 `EXTERNAL_API_KEY`，运行：

```bash
python scripts/e2e_external_api_smoke.py --group-ids 1,2,49,50 --claim-group-id 49
```

预期：`/api/external/accounts`、claim、release 均成功。若本地没有可用 API key 或服务未启动，在最终报告中明确说明未运行原因，不声称通过。

- [ ] **步骤 6：Commit**

```bash
git add README.md .env.example tests/test_icloud_hme_sources.py tests/test_icloud_hme_import.py tests/test_icloud_hme_mail_fetch.py tests/test_icloud_hme_external_api.py tests/test_icloud_hme_share.py tests/test_icloud_hme_sync.py
git commit -m "docs: document icloud hme mailboxes"
```

## 并行执行图

> 仅 `parallel-executing-plans` 使用；`serial-executing-plans` 忽略本节。

**Critical Path:** 任务 1 → 任务 2 → 任务 3 → 任务 7 → 任务 8 → 任务 9

- Wave 1（无依赖）：任务 1
- Wave 2（依赖 Wave 1）：任务 2（依赖 1）
- Wave 3（依赖 Wave 2）：任务 3（依赖 1, 2）
- Wave 4（依赖 Wave 3）：任务 4（依赖 1, 2, 3）, 任务 7（依赖 2, 3）
- Wave 5（依赖 Wave 4）：任务 5（依赖 3, 4）, 任务 6（依赖 3, 4）, 任务 8（依赖 2, 3, 7）
- Wave 6（依赖 Wave 5）：任务 9（依赖 5, 6, 8）
- Wave FINAL（所有任务完成后）：F1 规格合规、F2 代码质量、F3 真实手测、F4 范围保真

## 执行交接

计划执行约束：

- 不修改 `docs/downstream-external-api-integration.md`，除非用户另行要求。
- 不提交真实邮箱密码、iCloud Cookie、API key 或本地数据库。
- 每个任务先写失败测试，再实现，再运行指定测试，再 commit。
- HME 用户导入格式不得支持 `----source_id`。
- HME 对外仍是 `resource_type='account'`，不新增第三种外部资源类型。

两种执行方式：

1. 子代理驱动：适合较大计划，平台支持子代理时按 wave 并行执行。
2. 串行执行：适合当前仓库这种多段 Flask 全局命名耦合代码，按任务编号推进，检查点更明确。
