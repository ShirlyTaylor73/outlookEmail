# HME 全局管理实现计划

> **面向 AI 代理的工作者：** 必需子技能：平台支持子代理且计划较大/可安全分 wave 时使用 superpowers-zh:parallel-executing-plans；计划较小、任务强耦合或平台不支持子代理时使用 superpowers-zh:serial-executing-plans。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在系统设置中实现 iCloud HME 全局配置、使用中地址导入状态列表、长时注册任务、OpenAI 停用候选扫描与确认删除。

**架构：** 继续使用现有 segmented Flask app。数据库 schema 放在 `01_bootstrap.py`；iCloud HME HTTP helper 扩展到 `03_mail_helpers.py`；新增 `10_routes_icloud_hme_management.py` 承载 HME 管理的服务函数和登录态 API，并在 `web_outlook_app.py` 加载。前端在现有设置弹窗内新增 `iCloud HME` 分区，复用现有灰白设置页风格。

**技术栈：** Python 3.9+, Flask, SQLite, threading, urllib, pytest, plain JavaScript, CSS

---

## 文件结构

- `web_outlook_app.py`：把新增 segment `10_routes_icloud_hme_management.py` 加入 `SEGMENT_FILES`。
- `outlook_web/segments/01_bootstrap.py`：创建 HME 地址缓存、长时注册任务、生成记录、任务日志、停用候选表；启动时清理中断的 running/stopping 任务。
- `outlook_web/segments/03_mail_helpers.py`：新增 generate/reserve/deactivate/delete HME API helper，并统一 HME API 响应结构。
- `outlook_web/segments/10_routes_icloud_hme_management.py`：新增 HME 地址列表、批量导入、长时注册、OpenAI 候选扫描和删除 API；包含该功能的服务层 helper。
- `templates/partials/index/dialogs-management.html`：在系统设置中新增 `iCloud HME` 导航项和分区，迁移 HME 源配置 UI，新增地址抽屉、长时注册控制台、OpenAI 扫描面板。
- `static/js/index/07-settings.js`：新增前端状态、API 调用、列表渲染、长时任务控制、候选扫描和删除交互。
- `static/css/index/06-modals-toast.css`：新增少量 HME 设置页样式，必须匹配现有灰白设置页风格。
- `tests/test_icloud_hme_management_schema.py`：验证新表和启动中断状态处理。
- `tests/test_icloud_hme_api_helpers.py`：验证 HME API helper 的请求结构、响应解析和错误处理。
- `tests/test_icloud_hme_address_management.py`：验证地址列表合并、筛选、导入和冲突。
- `tests/test_icloud_hme_long_runner.py`：验证长时注册参数校验、单任务锁、成功导入、失败记录、中止。
- `tests/test_icloud_hme_deactivation.py`：验证 OpenAI 候选扫描、anonymousId 映射、deactivate/delete 顺序和逐项结果。
- `README.md`, `CHANGELOG.md`：记录用户可见能力和使用说明。

## 任务

### 任务 1：数据库 schema 与中断任务恢复

**依赖：** 无
**文件集：** `outlook_web/segments/01_bootstrap.py`, `tests/test_icloud_hme_management_schema.py`
**导出/变更接口：** `outlook_web/segments/01_bootstrap.py::init_db`, `outlook_web/segments/01_bootstrap.py::reset_interrupted_icloud_hme_generation_tasks`
**消费接口：** 无
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/01_bootstrap.py`
- 创建：`tests/test_icloud_hme_management_schema.py`

- [ ] **步骤 1：编写 schema 测试**

  在 `tests/test_icloud_hme_management_schema.py` 中添加测试，按现有测试风格导入应用并检查表存在：

  ```python
  def test_icloud_hme_management_tables_exist(client):
      db = app_module.get_db()
      table_names = {
          row["name"]
          for row in db.execute(
              "SELECT name FROM sqlite_master WHERE type = 'table'"
          ).fetchall()
      }
      assert "icloud_hme_address_cache" in table_names
      assert "icloud_hme_generation_tasks" in table_names
      assert "icloud_hme_generated_addresses" in table_names
      assert "icloud_hme_generation_logs" in table_names
      assert "icloud_hme_deactivation_candidates" in table_names
  ```

  同文件增加中断任务测试：插入 `status='running'` 和 `status='stopping'` 任务，调用 `reset_interrupted_icloud_hme_generation_tasks(conn)`，断言状态变为 `stopped` 且 `last_error` 包含 `interrupted by process restart`。

- [ ] **步骤 2：运行 schema 测试确认失败**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_management_schema.py -q -p no:cacheprovider
  ```

  预期：因表和函数不存在失败。

- [ ] **步骤 3：实现表结构**

  在 `init_db()` 中 `icloud_hme_source_message_recipients` 附近新增 `CREATE TABLE IF NOT EXISTS`：

  - `icloud_hme_address_cache`，unique `(source_id, hme)`
  - `icloud_hme_generation_tasks`
  - `icloud_hme_generated_addresses`
  - `icloud_hme_generation_logs`
  - `icloud_hme_deactivation_candidates`

  给常用查询创建索引：

  - `idx_icloud_hme_address_cache_source`
  - `idx_icloud_hme_generation_tasks_status`
  - `idx_icloud_hme_generated_addresses_task`
  - `idx_icloud_hme_generation_logs_task`
  - `idx_icloud_hme_deactivation_candidates_source_status`

- [ ] **步骤 4：实现中断任务恢复**

  在 `01_bootstrap.py` 增加：

  ```python
  def reset_interrupted_icloud_hme_generation_tasks(conn) -> None:
      conn.execute(
          """
          UPDATE icloud_hme_generation_tasks
          SET status = 'stopped',
              stop_requested = 1,
              last_error = 'interrupted by process restart',
              stopped_at = CURRENT_TIMESTAMP,
              updated_at = CURRENT_TIMESTAMP
          WHERE status IN ('running', 'stopping')
          """
      )
  ```

  在 `init_db()` 表创建后、`conn.commit()` 前调用该函数。

- [ ] **步骤 5：运行测试确认通过并提交**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_management_schema.py -q -p no:cacheprovider
  python -m compileall web_outlook_app.py outlook_web
  ```

  提交：

  ```bash
  git add outlook_web/segments/01_bootstrap.py tests/test_icloud_hme_management_schema.py
  git commit -m "feat: add hme management schema"
  ```

### 任务 2：iCloud HME API helper

**依赖：** 无
**文件集：** `outlook_web/segments/03_mail_helpers.py`, `tests/test_icloud_hme_api_helpers.py`
**导出/变更接口：** `outlook_web/segments/03_mail_helpers.py::request_icloud_hme_api`, `outlook_web/segments/03_mail_helpers.py::generate_icloud_hme`, `outlook_web/segments/03_mail_helpers.py::reserve_icloud_hme`, `outlook_web/segments/03_mail_helpers.py::deactivate_icloud_hme`, `outlook_web/segments/03_mail_helpers.py::delete_icloud_hme`
**消费接口：** `outlook_web/segments/03_mail_helpers.py::fetch_icloud_hme_list`
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/03_mail_helpers.py`
- 创建：`tests/test_icloud_hme_api_helpers.py`

- [ ] **步骤 1：编写 helper 测试**

  在 `tests/test_icloud_hme_api_helpers.py` 中 mock `urllib.request.urlopen`，断言：

  - `generate_icloud_hme(cookie, 'global', '')` 使用 `POST /v1/hme/generate`，body 包含 `{"langCode":"en-us"}`。
  - `reserve_icloud_hme(..., 'a@icloud.com', 'label', 'note')` 使用 `POST /v1/hme/reserve`，body 包含 `hme`、`label`、`note`。
  - `deactivate_icloud_hme(..., 'anon')` 使用 `POST /v1/hme/deactivate`，body 包含 `anonymousId`。
  - `delete_icloud_hme(..., 'anon')` 使用 `POST /v1/hme/delete`，body 包含 `anonymousId`。
  - HTTPError 返回 `success=False` 和脱敏错误。

- [ ] **步骤 2：运行 helper 测试确认失败**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_api_helpers.py -q -p no:cacheprovider
  ```

  预期：新 helper 不存在。

- [ ] **步骤 3：实现统一请求 helper**

  在 `03_mail_helpers.py` 的 `fetch_icloud_hme_list()` 附近新增：

  ```python
  def request_icloud_hme_api(cookie: str, region: str, maildomain_host: str,
                             api_version: str, action: str, method: str = 'POST',
                             payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
      ...
  ```

  要求：

  - 使用与 `fetch_icloud_hme_list()` 相同的 region、web_origin、默认 host、header。
  - endpoint 格式：`https://{host}/{api_version}/hme/{action}`。
  - JSON body 用 UTF-8 编码。
  - 返回 `{"success": True, "data": payload, "status_code": 200}` 或 `{"success": False, "error": "...", "status_code": code}`。

- [ ] **步骤 4：实现 action helper**

  新增：

  ```python
  def generate_icloud_hme(cookie, region='global', maildomain_host=''):
      return request_icloud_hme_api(cookie, region, maildomain_host, 'v1', 'generate', payload={'langCode': 'en-us'})

  def reserve_icloud_hme(cookie, region, maildomain_host, email_addr, label, note):
      return request_icloud_hme_api(cookie, region, maildomain_host, 'v1', 'reserve', payload={'hme': email_addr, 'label': label, 'note': note})

  def deactivate_icloud_hme(cookie, region, maildomain_host, anonymous_id):
      return request_icloud_hme_api(cookie, region, maildomain_host, 'v1', 'deactivate', payload={'anonymousId': anonymous_id})

  def delete_icloud_hme(cookie, region, maildomain_host, anonymous_id):
      return request_icloud_hme_api(cookie, region, maildomain_host, 'v1', 'delete', payload={'anonymousId': anonymous_id})
  ```

- [ ] **步骤 5：运行测试确认通过并提交**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_api_helpers.py -q -p no:cacheprovider
  python -m compileall web_outlook_app.py outlook_web
  ```

  提交：

  ```bash
  git add outlook_web/segments/03_mail_helpers.py tests/test_icloud_hme_api_helpers.py
  git commit -m "feat: add icloud hme api helpers"
  ```

### 任务 3：HME 管理后端 segment 与 API

**依赖：** 任务 1, 任务 2
**文件集：** `web_outlook_app.py`, `outlook_web/segments/10_routes_icloud_hme_management.py`
**导出/变更接口：** `web_outlook_app.py::SEGMENT_FILES`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_get_icloud_hme_addresses`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_import_icloud_hme_addresses`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_start_icloud_hme_long_runner`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_stop_icloud_hme_long_runner`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_scan_icloud_hme_deactivation_candidates`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_delete_icloud_hme_deactivation_candidates`
**消费接口：** `outlook_web/segments/01_bootstrap.py::init_db`, `outlook_web/segments/03_mail_helpers.py::fetch_icloud_hme_list`, `outlook_web/segments/03_mail_helpers.py::generate_icloud_hme`, `outlook_web/segments/03_mail_helpers.py::reserve_icloud_hme`, `outlook_web/segments/03_mail_helpers.py::deactivate_icloud_hme`, `outlook_web/segments/03_mail_helpers.py::delete_icloud_hme`
**复杂度：** deep

**文件：**
- 修改：`web_outlook_app.py`
- 创建：`outlook_web/segments/10_routes_icloud_hme_management.py`

- [ ] **步骤 1：创建新 segment 并接入加载顺序**

  在 `web_outlook_app.py` 的 `SEGMENT_FILES` 末尾追加：

  ```python
  "10_routes_icloud_hme_management.py",
  ```

  新建 `outlook_web/segments/10_routes_icloud_hme_management.py`。该文件依赖前面 segment 注入的 `app`、`login_required`、`jsonify`、`request`、`get_db`、`get_icloud_hme_source_by_id`、`add_icloud_hme_account`、`fetch_icloud_hme_list` 等全局符号。

- [ ] **步骤 2：实现地址缓存和列表服务**

  在新 segment 中实现：

  - `normalize_hme_address_list_filters(args) -> Dict[str, Any]`
  - `extract_hme_item_fields(item) -> Dict[str, Any]`
  - `upsert_icloud_hme_address_cache(source_id, hme_items) -> None`
  - `load_cached_icloud_hme_addresses(source_id) -> List[Dict[str, Any]]`
  - `build_icloud_hme_import_status_map(source_id, addresses) -> Dict[str, Dict[str, Any]]`
  - `list_icloud_hme_addresses(source_id, filters) -> Dict[str, Any]`

  合并规则：

  - 同源 `accounts.account_type='icloud_hme' AND icloud_hme_source_id=?` 为 `imported`。
  - 邮箱存在但非同源 HME 或非 HME 为 `conflict`。
  - 不存在为 `not_imported`。
  - 返回项必须包含 `hme`、`label`、`is_active`、`anonymous_id`、`account_id`、`group_id`、`group_name`、`import_state`、`conflict`。

- [ ] **步骤 3：实现批量导入服务**

  实现：

  - `import_icloud_hme_address_selection(source_id, group_id, addresses, remark='', status='active')`

  行为：

  - 校验 `source_id` 存在。
  - 使用现有 `validate_account_target_group_id(group_id)`。
  - 对每个地址调用 `add_icloud_hme_account()`。
  - 返回逐项结果：`imported`、`updated`、`conflict`、`error`。

- [ ] **步骤 4：实现长时注册服务和线程控制**

  在新 segment 中实现进程内任务锁：

  ```python
  ICLOUD_HME_LONG_RUNNER_LOCK = threading.Lock()
  ICLOUD_HME_LONG_RUNNER_THREAD = None
  ICLOUD_HME_LONG_RUNNER_STOP = threading.Event()
  ```

  实现函数：

  - `get_running_icloud_hme_generation_task(db=None)`
  - `create_icloud_hme_generation_task(payload) -> Dict[str, Any]`
  - `append_icloud_hme_generation_log(task_id, level, message)`
  - `serialize_icloud_hme_generation_task(row) -> Dict[str, Any]`
  - `run_icloud_hme_generation_task(task_id)`
  - `request_stop_icloud_hme_generation_task(task_id=None)`

  `run_icloud_hme_generation_task()` 每轮：

  1. 检查 stop flag 和任务状态。
  2. 调用 `generate_icloud_hme()`。
  3. 从响应中取 `result.hme` 或 `data.result.hme`。
  4. 调用 `reserve_icloud_hme()`。
  5. 调用 `add_icloud_hme_account(hme, source_id, target_group_id, remark=note, status='active')`。
  6. 写 `icloud_hme_generated_addresses` 和 logs。
  7. 成功等待 `success_delay_seconds`，失败等待 `failure_delay_seconds`；等待期间每秒检查 stop flag。

  应用上下文：线程体必须 `with app.app_context():` 后访问数据库。

- [ ] **步骤 5：实现 OpenAI 候选扫描与删除服务**

  实现：

  - `scan_icloud_hme_deactivation_candidates(source_id, group_id=None, folder='all', subject_contains='OpenAI - Access Deactivated', limit=200, refresh=False)`
  - `list_icloud_hme_deactivation_candidates(source_id, status=None, limit=200)`
  - `delete_icloud_hme_deactivation_candidates(source_id, candidate_ids)`

  扫描来源：

  - 优先读 `icloud_hme_source_messages` + `icloud_hme_source_message_recipients`。
  - 标题包含 `subject_contains`。
  - 如果传 `group_id`，只保留该 group 下账号对应 HME 地址。
  - anonymousId 通过地址缓存或刷新 iCloud list 映射。

  删除顺序：

  - 对每个 candidate 先 `deactivate_icloud_hme()`，成功或“已停用”类响应后再 `delete_icloud_hme()`。
  - 逐项更新 `status`、`error`、`deactivated_at`、`deleted_at`。
  - 成功删除后将本地 `accounts.status` 更新为 `inactive`，并追加备注片段 `HME deleted at ...`，不物理删除账号。

- [ ] **步骤 6：实现 Flask routes**

  新增登录态 routes：

  - `GET /api/icloud-hme/addresses` -> `api_get_icloud_hme_addresses`
  - `POST /api/icloud-hme/addresses/import` -> `api_import_icloud_hme_addresses`
  - `GET /api/icloud-hme/long-runner/status`
  - `POST /api/icloud-hme/long-runner/start` -> `api_start_icloud_hme_long_runner`
  - `POST /api/icloud-hme/long-runner/stop` -> `api_stop_icloud_hme_long_runner`
  - `GET /api/icloud-hme/long-runner/logs`
  - `POST /api/icloud-hme/deactivation-candidates/scan` -> `api_scan_icloud_hme_deactivation_candidates`
  - `GET /api/icloud-hme/deactivation-candidates`
  - `POST /api/icloud-hme/deactivation-candidates/delete` -> `api_delete_icloud_hme_deactivation_candidates`

  Error contract：业务校验失败 400，未找到 source/candidate 404，已有任务运行 409，未捕获异常 500 且不泄露 Cookie。

- [ ] **步骤 7：运行 compile 并提交**

  运行：

  ```bash
  python -m compileall web_outlook_app.py outlook_web
  ```

  提交：

  ```bash
  git add web_outlook_app.py outlook_web/segments/10_routes_icloud_hme_management.py
  git commit -m "feat: add hme management api"
  ```

### 任务 4：地址管理接口测试

**依赖：** 任务 3
**文件集：** `tests/test_icloud_hme_address_management.py`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/10_routes_icloud_hme_management.py::api_get_icloud_hme_addresses`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_import_icloud_hme_addresses`
**复杂度：** standard

**文件：**
- 创建：`tests/test_icloud_hme_address_management.py`

- [ ] **步骤 1：编写地址列表合并测试**

  在测试中创建 HME source、groups、accounts。mock `fetch_icloud_hme_list()` 返回：

  - active 已导入地址
  - active 未导入地址
  - active 冲突地址
  - inactive 地址

  调用：

  ```python
  response = client.get('/api/icloud-hme/addresses?source_id=1&active=true&refresh=1')
  ```

  断言：

  - `summary.imported == 1`
  - `summary.not_imported == 1`
  - `summary.conflict == 1`
  - 已导入项包含 `group_id` 和 `group_name`
  - 响应不包含 Cookie 或 IMAP password

- [ ] **步骤 2：编写筛选测试**

  覆盖：

  - `import_state=not_imported`
  - `group_id=<id>`
  - `q=<label or email>`
  - `active=all`

  断言返回列表只包含匹配项，`pagination.total` 正确。

- [ ] **步骤 3：编写批量导入测试**

  调用：

  ```python
  response = client.post('/api/icloud-hme/addresses/import', json={
      'source_id': source_id,
      'group_id': group_id,
      'addresses': ['new-hme@icloud.com'],
      'remark': 'from address list'
  })
  ```

  断言：

  - 返回逐项 `status='imported'`
  - `accounts` 中新增 `account_type='icloud_hme'`
  - `group_id` 是请求的 group

- [ ] **步骤 4：运行测试并提交**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_address_management.py -q -p no:cacheprovider
  ```

  提交：

  ```bash
  git add tests/test_icloud_hme_address_management.py
  git commit -m "test: cover hme address management api"
  ```

### 任务 5：长时注册接口测试

**依赖：** 任务 3
**文件集：** `tests/test_icloud_hme_long_runner.py`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/10_routes_icloud_hme_management.py::api_start_icloud_hme_long_runner`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_stop_icloud_hme_long_runner`
**复杂度：** standard

**文件：**
- 创建：`tests/test_icloud_hme_long_runner.py`

- [ ] **步骤 1：编写参数校验测试**

  覆盖：

  - `target_count <= 0` 返回 400
  - `success_delay_seconds < 0` 返回 400
  - 不存在的 `source_id` 返回 404 或 400
  - 不存在的 `target_group_id` 返回 400

- [ ] **步骤 2：编写单任务锁测试**

  插入或启动一个 `running` 任务，再调用 start，断言返回 409，错误信息包含“已有 HME 注册任务正在运行”。

- [ ] **步骤 3：编写成功路径测试**

  将延迟设为 0，mock `generate_icloud_hme()` 返回 HME 地址，mock `reserve_icloud_hme()` 返回 success。启动 target_count=1 后轮询 status，断言：

  - 任务最终 `completed`
  - `success_count == 1`
  - `icloud_hme_generated_addresses` 有一条记录
  - `accounts` 中有对应 HME 且 group 正确

- [ ] **步骤 4：编写失败与中止测试**

  mock generate 返回失败，断言：

  - `failure_count == 1`
  - logs 记录错误
  - `last_error` 有值

  对 stop API：启动一个延迟等待中的任务，调用 stop，断言任务进入 `stopping` 或最终 `stopped`，`stop_requested == 1`。

- [ ] **步骤 5：运行测试并提交**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_long_runner.py -q -p no:cacheprovider
  ```

  提交：

  ```bash
  git add tests/test_icloud_hme_long_runner.py
  git commit -m "test: cover hme long runner"
  ```

### 任务 6：OpenAI 停用候选与删除测试

**依赖：** 任务 3
**文件集：** `tests/test_icloud_hme_deactivation.py`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/10_routes_icloud_hme_management.py::api_scan_icloud_hme_deactivation_candidates`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_delete_icloud_hme_deactivation_candidates`
**复杂度：** standard

**文件：**
- 创建：`tests/test_icloud_hme_deactivation.py`

- [ ] **步骤 1：编写候选扫描测试**

  准备：

  - HME source
  - HME account + group
  - `icloud_hme_source_messages` 中一封 subject 为 `OpenAI - Access Deactivated [C-5GiU3pJbeSBF]`
  - `icloud_hme_source_message_recipients` 绑定该 HME 地址
  - `icloud_hme_address_cache` 中该地址的 `anonymous_id`

  调用 scan API，断言生成一条 candidate，包含 `account_id`、`group_id`、`anonymous_id`。

- [ ] **步骤 2：编写非目标标题和 group 筛选测试**

  插入非 OpenAI 标题邮件，断言不会生成 candidate。插入其他 group 的 HME 邮件，传指定 group 时断言被过滤。

- [ ] **步骤 3：编写删除顺序测试**

  mock `deactivate_icloud_hme()` 和 `delete_icloud_hme()`，对选中 candidate 调用 delete API，断言：

  - 先 deactivate 后 delete
  - candidate 状态变为 `deleted`
  - accounts 状态变为 `inactive`
  - 响应逐项返回成功

- [ ] **步骤 4：编写逐项失败测试**

  两个 candidate 中一个 delete 失败，断言：

  - 成功项为 `deleted`
  - 失败项为 `failed`
  - API 整体 `success=True`，results 中包含逐项错误

- [ ] **步骤 5：运行测试并提交**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_deactivation.py -q -p no:cacheprovider
  ```

  提交：

  ```bash
  git add tests/test_icloud_hme_deactivation.py
  git commit -m "test: cover hme deactivation flow"
  ```

### 任务 7：设置页 HME UI 结构与样式

**依赖：** 无
**文件集：** `templates/partials/index/dialogs-management.html`, `templates/partials/index/dialogs-primary.html`, `static/css/index/06-modals-toast.css`
**导出/变更接口：** 无
**消费接口：** 无
**复杂度：** standard

**文件：**
- 修改：`templates/partials/index/dialogs-management.html`
- 修改：`templates/partials/index/dialogs-primary.html`
- 修改：`static/css/index/06-modals-toast.css`

- [ ] **步骤 1：新增设置导航项**

  在设置侧边栏中新增：

  ```html
  <li>
    <button type="button" class="settings-sidebar-link" data-target="settingsIcloudHmeSection" onclick="scrollSettingsSection('settingsIcloudHmeSection', this)">
      <strong>iCloud HME</strong>
      <span>Cookie、接收源、地址导入与批量停用</span>
    </button>
  </li>
  ```

- [ ] **步骤 2：新增 HME 设置分区**

  在 Cloudflare 设置前或后新增 `section#settingsIcloudHmeSection`，结构必须使用：

  - `settings-section`
  - `settings-section-head`
  - `settings-panel settings-panel-stack`
  - `settings-subpanel`
  - `settings-panel-grid`
  - `settings-action-row`

  包含四个子面板：

  1. HME 源与接收邮箱
  2. 使用中地址与导入状态抽屉
  3. 长时定时注册任务
  4. OpenAI Access Deactivated 扫描与批量删除

- [ ] **步骤 3：迁移 HME 源表单控件**

  复用现有 `icloudHmeSourceId`、`icloudHmeSourceName`、`icloudHmeSourceRegion`、`icloudHmeReceiverEmail`、`icloudHmeReceiverProvider`、`icloudHmeReceiverImapHost`、`icloudHmeReceiverImapPort`、`icloudHmeReceiverImapPassword`、`icloudHmeReceiverFolder`、`icloudHmeUseSsl`、`icloudHmeMaildomainHost`、`icloudHmeCookie` 这些 ID，避免 JS 大量改名。

  原独立 `icloudHmeSourceModal` 可以保留为兼容入口，但导入/编辑账号里的“管理 HME 源”按钮改为“打开 HME 设置”，调用后显示系统设置并滚动到 `settingsIcloudHmeSection`。

- [ ] **步骤 4：添加地址列表、长时任务、候选列表容器**

  在 HME 分区中添加以下容器 ID：

  - `icloudHmeSummaryCards`
  - `icloudHmeAddressDrawer`
  - `icloudHmeAddressTableBody`
  - `icloudHmeAddressSearchInput`
  - `icloudHmeAddressActiveFilter`
  - `icloudHmeAddressImportStateFilter`
  - `icloudHmeAddressGroupFilter`
  - `icloudHmeLongRunnerForm`
  - `icloudHmeLongRunnerStatus`
  - `icloudHmeLongRunnerLogs`
  - `icloudHmeDeactivationCandidateTableBody`

- [ ] **步骤 5：添加项目风格样式**

  在 `static/css/index/06-modals-toast.css` 的 HME 源样式附近新增：

  - `.hme-settings-summary-grid`
  - `.hme-settings-summary-card`
  - `.hme-settings-drawer`
  - `.hme-settings-drawer__head`
  - `.hme-settings-filter-grid`
  - `.hme-settings-table-wrap`
  - `.hme-status-pill`
  - `.hme-group-badge`
  - `.hme-long-runner-status`
  - `.hme-long-runner-log`

  样式约束：灰白背景、浅边框、黑色主按钮，不使用大面积高饱和蓝色。

- [ ] **步骤 6：运行静态检查并提交**

  运行：

  ```bash
  python -m compileall web_outlook_app.py outlook_web
  ```

  提交：

  ```bash
  git add templates/partials/index/dialogs-management.html templates/partials/index/dialogs-primary.html static/css/index/06-modals-toast.css
  git commit -m "feat: add hme settings ui shell"
  ```

### 任务 8：设置页 HME 前端交互

**依赖：** 任务 3, 任务 7
**文件集：** `static/js/index/07-settings.js`
**导出/变更接口：** `static/js/index/07-settings.js::loadIcloudHmeAddresses`, `static/js/index/07-settings.js::renderIcloudHmeAddressList`, `static/js/index/07-settings.js::importSelectedIcloudHmeAddresses`, `static/js/index/07-settings.js::startIcloudHmeLongRunner`, `static/js/index/07-settings.js::stopIcloudHmeLongRunner`, `static/js/index/07-settings.js::scanIcloudHmeDeactivationCandidates`, `static/js/index/07-settings.js::deleteSelectedIcloudHmeCandidates`
**消费接口：** `outlook_web/segments/10_routes_icloud_hme_management.py::api_get_icloud_hme_addresses`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_import_icloud_hme_addresses`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_start_icloud_hme_long_runner`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_stop_icloud_hme_long_runner`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_scan_icloud_hme_deactivation_candidates`, `outlook_web/segments/10_routes_icloud_hme_management.py::api_delete_icloud_hme_deactivation_candidates`
**复杂度：** standard

**文件：**
- 修改：`static/js/index/07-settings.js`

- [ ] **步骤 1：新增前端状态**

  在现有 `icloudHmeSourcesCache` 附近新增：

  ```javascript
  let icloudHmeAddressCache = [];
  let icloudHmeAddressPagination = { limit: 50, offset: 0, total: 0 };
  let icloudHmeSelectedAddresses = new Set();
  let icloudHmeLongRunnerStatus = null;
  let icloudHmeDeactivationCandidates = [];
  ```

- [ ] **步骤 2：实现打开 HME 设置入口**

  新增：

  ```javascript
  function openIcloudHmeSettings() {
      showSettingsModal();
      setTimeout(() => {
          const link = document.querySelector('[data-target="settingsIcloudHmeSection"]');
          scrollSettingsSection('settingsIcloudHmeSection', link);
      }, 0);
  }
  ```

  保持 `openIcloudHmeSourceModal()` 兼容，但新 UI 按钮优先调用 `openIcloudHmeSettings()`。

- [ ] **步骤 3：实现地址列表加载和渲染**

  新增：

  - `getIcloudHmeAddressFilters()`
  - `loadIcloudHmeAddresses({ refresh = false, offset = 0 } = {})`
  - `renderIcloudHmeAddressSummary(summary)`
  - `renderIcloudHmeAddressList(addresses)`
  - `toggleIcloudHmeAddressSelection(email, checked)`
  - `toggleAllIcloudHmeAddressSelection(checked)`

  渲染要求：

  - 已导入显示 `Group ID ${group_id}` 和 group name。
  - 未导入显示“未导入”并提供“导入”按钮。
  - 冲突显示 existing account/source 信息。
  - anonymousId 只在登录态设置页显示。

- [ ] **步骤 4：实现批量导入**

  新增 `importSelectedIcloudHmeAddresses()`：

  - 要求至少选择一个地址。
  - 要求选择目标 group。
  - POST `/api/icloud-hme/addresses/import`。
  - 成功后刷新地址列表和当前账号列表缓存。

- [ ] **步骤 5：实现长时注册交互**

  新增：

  - `getIcloudHmeLongRunnerPayload()`
  - `loadIcloudHmeLongRunnerStatus()`
  - `renderIcloudHmeLongRunnerStatus(status)`
  - `loadIcloudHmeLongRunnerLogs()`
  - `startIcloudHmeLongRunner()`
  - `stopIcloudHmeLongRunner()`

  开始按钮：

  - running/stopping 时 disabled。
  - idle/completed/failed/stopped 时 enabled。

  中止按钮：

  - running/stopping 时 enabled。
  - 其他状态 disabled。

- [ ] **步骤 6：实现候选扫描和删除交互**

  新增：

  - `getIcloudHmeDeactivationScanPayload()`
  - `scanIcloudHmeDeactivationCandidates()`
  - `loadIcloudHmeDeactivationCandidates()`
  - `renderIcloudHmeDeactivationCandidates(candidates)`
  - `deleteSelectedIcloudHmeCandidates()`

  删除前必须使用现有 `showConfirmModal()` 二次确认，确认文案包含“停用并删除 HME 地址，此操作不可逆”。

- [ ] **步骤 7：接入设置页生命周期**

  在 `showSettingsModal()` 或其已有加载流程中：

  - 调用 `loadIcloudHmeSources()`
  - 调用 `loadIcloudHmeLongRunnerStatus()`
  - 不自动刷新 iCloud 地址列表；只有用户展开抽屉或点击刷新时调用，避免打开设置页就请求 iCloud。

- [ ] **步骤 8：运行语法检查并提交**

  运行：

  ```bash
  node --check static/js/index/07-settings.js
  ```

  提交：

  ```bash
  git add static/js/index/07-settings.js
  git commit -m "feat: wire hme settings frontend"
  ```

### 任务 9：文档、回归验证和本地冒烟

**依赖：** 任务 4, 任务 5, 任务 6, 任务 8
**文件集：** `README.md`, `CHANGELOG.md`
**导出/变更接口：** 无
**消费接口：** 无
**复杂度：** standard

**文件：**
- 修改：`README.md`
- 修改：`CHANGELOG.md`

- [ ] **步骤 1：更新 README**

  在 iCloud Hide My Email 章节补充：

  - HME 设置已迁移到右上角系统设置。
  - 使用中地址列表可显示导入状态和 group id。
  - 长时注册任务参数：目标 group、数量、label、成功延迟、失败延迟、开始/中止。
  - OpenAI Access Deactivated 扫描采用候选确认后删除。

- [ ] **步骤 2：更新 CHANGELOG**

  在顶部 Unreleased 或当前版本区域增加条目：

  - 新增 HME 全局设置分区。
  - 新增 HME 使用中地址导入状态列表。
  - 新增长时 HME 注册任务。
  - 新增 OpenAI Access Deactivated 候选扫描和确认删除。

- [ ] **步骤 3：运行目标 pytest**

  运行：

  ```bash
  python -m pytest tests/test_icloud_hme_management_schema.py tests/test_icloud_hme_api_helpers.py tests/test_icloud_hme_address_management.py tests/test_icloud_hme_long_runner.py tests/test_icloud_hme_deactivation.py -q -p no:cacheprovider
  ```

  预期：全部通过。

- [ ] **步骤 4：运行项目指定回归**

  运行：

  ```bash
  python -m pytest tests/test_temp_email_share.py tests/test_account_share.py tests/test_external_verification_code_api.py tests/test_env_loading.py -q -p no:cacheprovider
  python -m compileall web_outlook_app.py outlook_web
  node --check static/js/index/07-settings.js
  ```

  预期：全部通过。

- [ ] **步骤 5：本地 HTTP 冒烟**

  启动或复用：

  ```bash
  python web_outlook_app.py
  ```

  在浏览器访问 `http://127.0.0.1:5000`，登录后验证：

  - 设置页有 `iCloud HME` 导航。
  - HME 源表单能显示已有源。
  - 地址抽屉展开后可显示 mock/真实接口返回，已导入行有 group id。
  - 长时注册表单有开始、中止、刷新状态。
  - OpenAI 扫描面板先生成候选，不自动删除。

  若本地 `.env` 有 `EXTERNAL_API_KEY`，继续运行：

  ```bash
  python scripts/e2e_external_api_smoke.py --group-ids 1,2,49,50 --claim-group-id 49
  ```

- [ ] **步骤 6：最终提交**

  提交：

  ```bash
  git add README.md CHANGELOG.md
  git commit -m "docs: document hme global management"
  ```

## 并行执行图

> 仅 `parallel-executing-plans` 使用；`serial-executing-plans` 忽略本节。

**Critical Path:** 任务 1 → 任务 3 → 任务 8 → 任务 9

- Wave 1（无依赖）：任务 1, 任务 2, 任务 7
- Wave 2（依赖 Wave 1）：任务 3（依赖 1, 2）
- Wave 3（依赖 Wave 2）：任务 4（依赖 3）, 任务 5（依赖 3）, 任务 6（依赖 3）, 任务 8（依赖 3, 7）
- Wave 4（依赖 Wave 3）：任务 9（依赖 4, 5, 6, 8）
- Wave FINAL（所有任务完成后）：F1 规格合规、F2 代码质量、F3 真实手测、F4 范围保真

