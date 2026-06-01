# 外部验证码获取 API 实现计划

> **面向 AI 代理的工作者：** 必需子技能：平台支持子代理且计划较大/可安全分 wave 时使用 superpowers:parallel-executing-plans；计划较小、任务强耦合或平台不支持子代理时使用 superpowers:serial-executing-plans。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增 API Key 鉴权的 `GET /api/external/verification-code`，从候选邮件完整正文中提取 6 位数字验证码，只返回验证码和命中邮件元信息。

**架构：** 后端实现放在现有外部 API 覆盖层 `outlook_web/segments/08_forwarding_scheduler_errors.py`，复用 `resolve_account_for_email_api`、`fetch_account_emails`、详情读取 helper 和 `strip_html_content`。测试使用 Flask test client 和 mock 邮件列表/详情读取，避免真实上游请求。文档同步更新 `docs/api.md` 和 `README.md` 的对外 API 章节。

**技术栈：** Flask, SQLite, unittest, pytest, Python 标准库 `re`/`time`

---

## 文件结构

- `outlook_web/segments/08_forwarding_scheduler_errors.py`：新增验证码提取常量、刷新节流状态、参数解析、详情读取、响应构建和 `/api/external/verification-code` 路由。
- `tests/test_external_verification_code_api.py`：新增外部验证码 API 的鉴权、参数、提取、过滤、节流和安全回归测试。
- `docs/api.md`：新增 `GET /api/external/verification-code` 文档。
- `README.md`：在对外 API 使用说明中增加验证码接口入口和示例。

### 任务 1：编写失败测试

**依赖：** 无
**文件集：** `tests/test_external_verification_code_api.py`
**导出/变更接口：** 无
**消费接口：** `web_outlook_app.py::app`
**复杂度：** standard

**文件：**
- 创建：`tests/test_external_verification_code_api.py`

- [ ] **步骤 1：建立最小测试夹具**

  采用 `unittest.TestCase`，复用项目已有测试风格：

  ```python
  import importlib
  import os
  import tempfile
  import unittest
  from unittest.mock import patch

  os.environ.setdefault('SECRET_KEY', 'test-secret-key')
  _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-verification-tests-')
  os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

  web_outlook_app = importlib.import_module('web_outlook_app')
  ```

  `setUp()` 中清理 `accounts`、`account_aliases`、`account_tags`、`account_refresh_logs`，设置 `external_api_key=test-external-key`，新增默认 Outlook 账号 `user@outlook.com` 和别名 `alias@example.com`。

- [ ] **步骤 2：覆盖鉴权和基础参数**

  编写以下测试：

  - `test_requires_api_key`：无 API Key 请求 `/api/external/verification-code?email=user@outlook.com` 返回 `401`。
  - `test_rejects_invalid_api_key`：错误 API Key 返回 `401`。
  - `test_missing_email_returns_400`：缺少 `email` 返回 `400`，错误为 `缺少 email 参数`。
  - `test_unknown_email_returns_404`：不存在邮箱返回 `404`，错误为 `邮箱账号不存在`。
  - `test_invalid_folder_returns_400`：`folder=archive` 返回 `400`，响应包含支持的 folder 列表。

- [ ] **步骤 3：覆盖正文提取和 preview 不可靠场景**

  mock `fetch_account_emails` 返回两封候选邮件，第二封详情正文包含 ChatGPT 风格 HTML：

  ```python
  list_result = {
      'success': True,
      'emails': [
          {'id': '1', 'subject': 'Welcome', 'from': 'noreply@example.com', 'body_preview': 'no code', 'folder': 'inbox'},
          {
              'id': '4',
              'subject': 'ChatGPT の一時的な認証コード',
              'from': 'ChatGPT <noreply@tm.openai.com>',
              'body_preview': '<html><head><title>ChatGPT</title></head>',
              'folder': 'inbox',
              'method': 'imap',
              'id_mode': 'sequence',
              'date': '01-Jun-2026 02:46:44 +0800',
          },
      ],
  }
  detail_result = {
      'success': True,
      'email': {
          'id': '4',
          'subject': 'ChatGPT の一時的な認証コード',
          'from': 'ChatGPT <noreply@tm.openai.com>',
          'date': '01-Jun-2026 02:46:44 +0800',
          'body': '<html><body><p>認証コード</p><strong>051949</strong></body></html>',
          'body_type': 'html',
      },
  }
  ```

  断言响应 `success=true`、`found=true`、`code=051949`、`source=body`、`message_id=4`、`checked_count=2`，并且响应 JSON 序列化后不包含 `body`、`refresh_token`、`password`、`proxy_url`。

- [ ] **步骤 4：覆盖兜底来源、过滤和未找到**

  编写以下测试：

  - `test_falls_back_to_subject_and_preview_sources`：详情正文无数字时，从主题提取 `123456` 返回 `source=subject`；另一个子场景从 `body_preview` 提取 `654321` 返回 `source=body_preview`。
  - `test_filters_candidates_before_reading_details`：传 `subject_contains=verify&from_contains=openai&keyword=login` 时，只读取匹配候选详情。
  - `test_not_found_returns_200_found_false`：所有详情无验证码时返回 `200`、`success=true`、`found=false`、`checked_count` 等于已检查数量。

- [ ] **步骤 5：覆盖刷新节流和三种详情路径**

  编写以下测试：

  - `test_refresh_calls_fetch_once_then_throttles_for_30_seconds`：清空 `EXTERNAL_VERIFICATION_REFRESH_STATE`，连续两次 `refresh=1` 调用，mock `time.time` 为同一窗口；断言第一次 `throttled=false`，第二次 `throttled=true`，且第二次不额外触发刷新分支。
  - `test_uses_generic_imap_detail_for_imap_account`：新增 `account_type=imap` 账号，断言调用 `fetch_imap_account_detail_response(account, folder, message_id, method, id_mode, proxy_url)`。
  - `test_uses_graph_detail_for_graph_message`：Outlook 列表项 `method=graph` 或无 IMAP id mode 时，断言调用 `fetch_graph_detail_response`。
  - `test_uses_oauth_imap_detail_for_imap_id_mode`：Outlook 列表项 `method=imap&id_mode=uid` 时，断言调用 `fetch_oauth_imap_detail_response`。

- [ ] **步骤 6：运行测试确认失败**

  运行：

  ```bash
  python -m pytest tests/test_external_verification_code_api.py -q -p no:cacheprovider
  ```

  预期：至少因 `GET /api/external/verification-code` 未注册或 helper 未定义而失败。

### 任务 2：实现后端接口

**依赖：** 任务 1
**文件集：** `outlook_web/segments/08_forwarding_scheduler_errors.py`
**导出/变更接口：** `outlook_web/segments/08_forwarding_scheduler_errors.py::api_external_verification_code`, `outlook_web/segments/08_forwarding_scheduler_errors.py::extract_external_verification_code`, `outlook_web/segments/08_forwarding_scheduler_errors.py::fetch_external_verification_detail`
**消费接口：** `outlook_web/segments/02_groups_accounts.py::resolve_account_for_email_api`, `outlook_web/segments/03_mail_helpers.py::api_key_required`, `outlook_web/segments/03_mail_helpers.py::strip_html_content`, `outlook_web/segments/05_routes_refresh_mail.py::fetch_account_emails`, `outlook_web/segments/05_routes_refresh_mail.py::fetch_imap_account_detail_response`, `outlook_web/segments/05_routes_refresh_mail.py::fetch_graph_detail_response`, `outlook_web/segments/05_routes_refresh_mail.py::fetch_oauth_imap_detail_response`
**复杂度：** deep

**文件：**
- 修改：`outlook_web/segments/08_forwarding_scheduler_errors.py`

- [ ] **步骤 1：新增常量和参数解析 helper**

  在外部 API 覆盖层附近新增：

  ```python
  EXTERNAL_VERIFICATION_CODE_RE = re.compile(r'\b\d{4,8}\b')
  EXTERNAL_VERIFICATION_REFRESH_TTL_SECONDS = 30
  EXTERNAL_VERIFICATION_REFRESH_STATE: Dict[str, float] = {}
  ```

  新增 `parse_external_bool_arg(value)`，仅当小写值在 `{'1', 'true', 'yes', 'on'}` 时返回 `True`。`top` 使用 `parse_non_negative_int(request.args.get('top', 5), 5, 20)`，`skip` 使用 `parse_non_negative_int(..., 0)`。

- [ ] **步骤 2：实现刷新节流 helper**

  新增 `check_external_verification_refresh_throttle(resolved_email, folder)`：

  - key 为 `f'{resolved_email.lower()}:{folder}'`。
  - 使用 `time.time()`。
  - 距离上次小于 30 秒返回 `True`。
  - 未命中时写入当前时间并返回 `False`。
  - 刷新失败也保留写入时间。

- [ ] **步骤 3：实现详情读取 helper**

  新增 `fetch_external_verification_detail(account, item, fallback_folder)`：

  ```python
  message_id = str(item.get('id', '') or '')
  folder = normalize_folder_name(item.get('folder') or fallback_folder or 'inbox')
  method = str(item.get('method') or 'graph').strip().lower()
  id_mode = str(item.get('id_mode') or item.get('idMode') or '').strip().lower()
  proxy_url = get_account_proxy_url(account)
  fallback_proxy_urls = get_account_proxy_failover_urls(account)
  ```

  分支规则：

  - `account.get('account_type') == 'imap'`：调用 `fetch_imap_account_detail_response(account, folder, message_id, method, id_mode, proxy_url)`。
  - Outlook 且 `method == 'graph'` 且 `id_mode` 不在 `{'uid', 'sequence'}`：调用 `fetch_graph_detail_response(account, folder, message_id, method, id_mode, proxy_url, fallback_proxy_urls)`。
  - 其他 Outlook 场景：调用 `fetch_oauth_imap_detail_response(account, folder, message_id, method, id_mode, proxy_url, fallback_proxy_urls)`。

  返回值保持 `{success, email}` 形状；异常时只返回 `{'success': False, 'error': '获取验证码失败'}`。

- [ ] **步骤 4：实现候选过滤与验证码提取**

  新增 `external_verification_item_matches(item, subject_contains, from_contains)`，仅基于 `subject` 和 `from` 预过滤。

  新增 `extract_external_verification_code(detail_email, list_item, keyword)`：

  - 正文：`strip_html_content(str(detail_email.get('body') or ''))`。
  - 若 `keyword` 非空，先要求 `keyword.lower()` 出现在正文、主题或 preview 的任一位置。
  - 按 `body`、`subject`、`body_preview` 顺序应用 `EXTERNAL_VERIFICATION_CODE_RE.search`。
  - 找到时返回 `{'code': code, 'source': source}`，否则返回 `None`。

- [ ] **步骤 5：实现路由和注册保护**

  新增：

  ```python
  @app.route('/api/external/verification-code', methods=['GET'])
  @api_key_required
  def api_external_verification_code():
      ...
  ```

  行为：

  - 缺少 `email` 返回 `400` 和 `{'success': False, 'error': '缺少 email 参数'}`。
  - `folder` 使用 `normalize_folder_name`，不在 `VALID_MAIL_FOLDERS` 返回 `400` 和支持列表。
  - `resolve_account_for_email_api` 失败返回 `404` 和 `邮箱账号不存在`。
  - `refresh=1` 且未节流时调用 `fetch_account_emails(account, folder, skip, top)`；未刷新或节流时也调用同一列表读取逻辑获取候选。
  - 列表读取失败返回 `502`，响应只含 `{'success': False, 'error': '获取验证码失败'}`。
  - 命中时返回设计文档字段：`success`、`found`、`code`、`email`、`requested_email`、`resolved_email`、`message_id`、`subject`、`from`、`folder`、`method`、`id_mode`、`source`、`date`、`checked_count`、`throttled`。
  - 未命中返回 `200`：`success=true`、`found=false`、`email`、`requested_email`、`resolved_email`、`checked_count`、`throttled`。

  路由定义后补充：

  ```python
  assert_endpoint_protection('api_external_verification_code', '_requires_api_key', 'api_key_required')
  ```

- [ ] **步骤 6：运行后端测试**

  运行：

  ```bash
  python -m pytest tests/test_external_verification_code_api.py -q -p no:cacheprovider
  ```

  预期：全部通过。

### 任务 3：更新外部 API 文档

**依赖：** 任务 2
**文件集：** `docs/api.md`, `README.md`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/08_forwarding_scheduler_errors.py::api_external_verification_code`
**复杂度：** standard

**文件：**
- 修改：`docs/api.md`
- 修改：`README.md`

- [ ] **步骤 1：更新 `docs/api.md` 目录和摘要**

  在外部 API 汇总表中新增：

  ```markdown
  | GET | `/api/external/verification-code` | API Key | JSON | 获取指定邮箱最新验证码 |
  ```

- [ ] **步骤 2：新增验证码接口章节**

  放在 `GET /api/external/emails` 章节后，包含：

  - 鉴权方式：`X-API-Key`、`api_key`、`apikey`。
  - 参数表：`email`、`folder`、`top`、`skip`、`refresh`、`subject_contains`、`from_contains`、`keyword`。
  - 成功命中、未找到、错误响应示例。
  - 说明 `body_preview` 不保证包含完整验证码，此接口会读取候选邮件详情但不返回正文。
  - 说明默认规则为 6 位数字，`top` 最大 20，`refresh=1` 按邮箱和 folder 30 秒节流。

- [ ] **步骤 3：更新 `README.md` 对外 API 示例**

  在对外 API 章节增加 curl 示例：

  ```bash
  curl -H "X-API-Key: your-api-key" \
    "http://localhost:5000/api/external/verification-code?email=user@outlook.com&folder=all&refresh=1&subject_contains=verify&top=5"
  ```

  说明响应只返回验证码与邮件元信息，不返回完整正文、附件或账号凭据。

- [ ] **步骤 4：检查文档关键字**

  运行：

  ```bash
  rg -n "verification-code|验证码|body_preview|refresh=1" README.md docs/api.md
  ```

  预期：能定位到新接口摘要、详细章节和 README 示例。

### 任务 4：整体验证与范围检查

**依赖：** 任务 2, 任务 3
**文件集：** `outlook_web/segments/08_forwarding_scheduler_errors.py`, `tests/test_external_verification_code_api.py`, `docs/api.md`, `README.md`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/08_forwarding_scheduler_errors.py::api_external_verification_code`, `outlook_web/segments/08_forwarding_scheduler_errors.py::extract_external_verification_code`, `outlook_web/segments/08_forwarding_scheduler_errors.py::fetch_external_verification_detail`
**复杂度：** standard

**文件：**
- 检查：`outlook_web/segments/08_forwarding_scheduler_errors.py`
- 检查：`tests/test_external_verification_code_api.py`
- 检查：`docs/api.md`
- 检查：`README.md`

- [ ] **步骤 1：运行聚焦测试**

  运行：

  ```bash
  python -m pytest tests/test_external_verification_code_api.py tests/test_imap_folder_resolution.py -q -p no:cacheprovider
  ```

  预期：全部通过，证明新接口和现有外部邮件列表兼容。

- [ ] **步骤 2：运行编译检查**

  运行：

  ```bash
  python -m compileall web_outlook_app.py outlook_web
  ```

  预期：无语法错误。

- [ ] **步骤 3：安全字段扫描**

  运行聚焦测试中的安全断言后，再人工检查实现响应构建，确认新接口没有把以下字段从账号或详情对象透传给外部响应：`password`、`refresh_token`、`client_id`、`imap_password`、`proxy_url`、`fallback_proxy_url_1`、`fallback_proxy_url_2`、`body`。

- [ ] **步骤 4：查看变更范围**

  运行：

  ```bash
  git diff -- outlook_web/segments/08_forwarding_scheduler_errors.py tests/test_external_verification_code_api.py docs/api.md README.md
  git diff --name-only
  ```

  预期：本功能只修改本计划文件集；若工作区已有其他未提交文件，保持不回退、不清理。

## 并行执行图

> 仅 `parallel-executing-plans` 使用；`serial-executing-plans` 忽略本节。

**Critical Path:** 任务 1 → 任务 2 → 任务 3 → 任务 4

- Wave 1（无依赖）：任务 1
- Wave 2（依赖 Wave 1）：任务 2（依赖 1）
- Wave 3（依赖 Wave 2）：任务 3（依赖 2）
- Wave 4（依赖 Wave 3）：任务 4（依赖 2, 3）
- Wave FINAL（所有任务完成后）：F1 规格合规、F2 代码质量、F3 真实手测、F4 范围保真
