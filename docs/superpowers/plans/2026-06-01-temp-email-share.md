# 临时邮箱分享功能实现计划

> **面向 AI 代理的工作者：** 必需子技能：平台支持子代理且计划较大/可安全分 wave 时使用 superpowers:parallel-executing-plans；计划较小、任务强耦合或平台不支持子代理时使用 superpowers:serial-executing-plans。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为全部临时邮箱新增 `/shared/<token>` 只读分享链接，未登录访问者可查看并按 30 秒节流刷新邮件。

**架构：** 在 SQLite 中新增 `temp_email_shares` 表，临时邮箱分段路由负责登录态分享管理和公开只读 API。登录后 UI 在临时邮箱三点菜单打开分享弹窗；公开页使用独立模板和静态资源调用 `/api/shared/*`。

**技术栈：** Flask, SQLite, pytest, 原生 JavaScript, CSS, DOMPurify

---

## 文件结构

- `outlook_web/segments/01_bootstrap.py`：初始化 `temp_email_shares` 表、索引和旧库迁移。
- `outlook_web/segments/06_routes_temp_email.py`：分享 helper、登录态分享 API、公开 API、公开页面路由、共享格式化逻辑和公开刷新节流。
- `static/js/index/03-temp-emails.js`：登录后分享弹窗、分享链接创建、复制和删除。
- `static/css/index/06-modals-toast.css`：分享弹窗样式。
- `templates/shared_temp_email.html`：未登录可访问的公开分享页面。
- `static/js/shared-temp-email.js`：公开页数据加载、刷新、列表和详情交互、DOMPurify 净化渲染。
- `static/css/shared-temp-email.css`：公开分享页布局和响应式样式。
- `tests/test_temp_email_share.py`：后端分享表、API、公开访问、刷新节流和敏感字段测试。
- `docs/api.md`：补充临时邮箱分享接口说明。
- `README.md`：补充用户侧临时邮箱分享说明。

## 任务

### 任务 1：编写后端失败测试

**依赖：** 无
**文件集：** `tests/test_temp_email_share.py`
**导出/变更接口：** 无
**消费接口：** `web_outlook_app.py::app`, `web_outlook_app.py::init_db`, `web_outlook_app.py::get_db`, `web_outlook_app.py::add_temp_email`, `web_outlook_app.py::save_temp_email_messages`
**复杂度：** standard

**文件：**
- 创建：`tests/test_temp_email_share.py`

- [ ] **步骤 1：创建测试类和隔离数据库清理**

  使用 `unittest.TestCase`，参照 `tests/test_project_runtime.py`。`setUp` 中设置 `TESTING=True`、`WTF_CSRF_ENABLED=False`，创建登录态 `self.client` 和未登录 `self.public_client`。在 app context 中调用 `init_db()`，并清理：

  ```sql
  DELETE FROM temp_email_shares;
  DELETE FROM temp_email_tags;
  DELETE FROM temp_email_messages;
  DELETE FROM temp_emails;
  ```

  提供 helper：

  ```python
  def _create_temp_email(self, email_addr='share@example.com', provider='gptmail') -> int:
      web_outlook_app.add_temp_email(email_addr, provider=provider)
      row = web_outlook_app.get_db().execute(
          'SELECT id FROM temp_emails WHERE email = ?',
          (email_addr,)
      ).fetchone()
      return int(row['id'])
  ```

- [ ] **步骤 2：覆盖登录态分享管理**

  添加测试：

  ```python
  def test_create_share_defaults_to_thirty_days_and_allows_multiple_links(self):
      temp_email_id = self._create_temp_email()
      first = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={})
      second = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={'expires_in': 0})
      self.assertEqual(first.status_code, 201)
      self.assertEqual(second.status_code, 201)
      first_payload = first.get_json()
      second_payload = second.get_json()
      self.assertTrue(first_payload['success'])
      self.assertTrue(second_payload['success'])
      self.assertNotEqual(first_payload['share']['token'], second_payload['share']['token'])
      self.assertIsNotNone(first_payload['share']['expires_at'])
      self.assertIsNone(second_payload['share']['expires_at'])

      listed = self.client.get(f'/api/temp-emails/{temp_email_id}/shares')
      payload = listed.get_json()
      self.assertEqual(listed.status_code, 200)
      self.assertEqual(payload['total'], 2)
  ```

  添加测试：

  ```python
  def test_create_share_rejects_non_preset_expiry(self):
      temp_email_id = self._create_temp_email()
      response = self.client.post(
          f'/api/temp-emails/{temp_email_id}/shares',
          json={'expires_in': 12345}
      )
      self.assertEqual(response.status_code, 400)
      self.assertFalse(response.get_json()['success'])
  ```

  添加测试：

  ```python
  def test_delete_share_revokes_public_access(self):
      temp_email_id = self._create_temp_email()
      created = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={}).get_json()['share']
      deleted = self.client.delete(f'/api/temp-emails/{temp_email_id}/shares/{created["id"]}')
      self.assertEqual(deleted.status_code, 200)
      public = self.public_client.get(f'/api/shared/{created["token"]}')
      self.assertEqual(public.status_code, 404)
  ```

- [ ] **步骤 3：覆盖公开访问和隔离**

  添加测试：

  ```python
  def test_public_shared_email_and_message_detail_do_not_require_login(self):
      temp_email_id = self._create_temp_email('public@example.com')
      web_outlook_app.save_temp_email_messages('public@example.com', [{
          'id': 'msg-1',
          'from_address': 'sender@example.com',
          'subject': 'Verify',
          'content': 'Code 123456',
          'html_content': '<p>Code <strong>123456</strong></p>',
          'has_html': True,
          'timestamp': 1717200000,
      }])
      token = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={}).get_json()['share']['token']

      info = self.public_client.get(f'/api/shared/{token}')
      messages = self.public_client.get(f'/api/shared/{token}/messages')
      detail = self.public_client.get(f'/api/shared/{token}/messages/msg-1')

      self.assertEqual(info.status_code, 200)
      self.assertEqual(messages.status_code, 200)
      self.assertEqual(detail.status_code, 200)
      self.assertEqual(info.get_json()['email']['email'], 'public@example.com')
      self.assertEqual(messages.get_json()['emails'][0]['id'], 'msg-1')
      self.assertEqual(detail.get_json()['email']['body_type'], 'html')
  ```

  添加测试：

  ```python
  def test_public_message_detail_rejects_message_from_another_mailbox(self):
      first_id = self._create_temp_email('first@example.com')
      self._create_temp_email('second@example.com')
      web_outlook_app.save_temp_email_messages('second@example.com', [{
          'id': 'other-msg',
          'from_address': 'sender@example.com',
          'subject': 'Other',
          'content': 'secret',
          'timestamp': 1717200000,
      }])
      token = self.client.post(f'/api/temp-emails/{first_id}/shares', json={}).get_json()['share']['token']
      response = self.public_client.get(f'/api/shared/{token}/messages/other-msg')
      self.assertEqual(response.status_code, 404)
  ```

- [ ] **步骤 4：覆盖过期、删除邮箱和敏感字段**

  添加测试：

  ```python
  def test_expired_share_returns_gone(self):
      temp_email_id = self._create_temp_email()
      token = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={}).get_json()['share']['token']
      with self.app.app_context():
          web_outlook_app.get_db().execute(
              "UPDATE temp_email_shares SET expires_at = datetime('now', '-1 minute') WHERE token = ?",
              (token,)
          )
          web_outlook_app.get_db().commit()
      response = self.public_client.get(f'/api/shared/{token}')
      self.assertEqual(response.status_code, 410)
  ```

  添加测试：

  ```python
  def test_deleted_temp_email_invalidates_share(self):
      temp_email_id = self._create_temp_email('deleted@example.com')
      token = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={}).get_json()['share']['token']
      self.client.delete('/api/temp-emails/deleted@example.com')
      response = self.public_client.get(f'/api/shared/{token}')
      self.assertEqual(response.status_code, 404)
  ```

  添加测试：

  ```python
  def test_public_payload_does_not_expose_provider_credentials(self):
      temp_email_id = self._create_temp_email('secret@example.com', provider='duckmail')
      with self.app.app_context():
          web_outlook_app.get_db().execute(
              'UPDATE temp_emails SET duckmail_password = ?, duckmail_token = ?, cloudflare_jwt = ? WHERE id = ?',
              ('password-secret', 'token-secret', 'jwt-secret', temp_email_id)
          )
          web_outlook_app.get_db().commit()
      token = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={}).get_json()['share']['token']
      body = self.public_client.get(f'/api/shared/{token}').get_data(as_text=True)
      self.assertNotIn('password-secret', body)
      self.assertNotIn('token-secret', body)
      self.assertNotIn('jwt-secret', body)
      self.assertNotIn('duckmail_password', body)
      self.assertNotIn('duckmail_token', body)
      self.assertNotIn('cloudflare_jwt', body)
  ```

- [ ] **步骤 5：覆盖公开刷新节流**

  添加测试：

  ```python
  def test_public_refresh_is_throttled_by_token(self):
      temp_email_id = self._create_temp_email('refresh@example.com')
      token = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={}).get_json()['share']['token']
      with patch.object(web_outlook_app, 'get_temp_emails_from_api', return_value=[{
          'id': 'msg-refresh',
          'from_address': 'sender@example.com',
          'subject': 'Refresh',
          'content': 'fresh',
          'timestamp': 1717200001,
      }]) as fetch_mock:
          first = self.public_client.post(f'/api/shared/{token}/refresh')
          second = self.public_client.post(f'/api/shared/{token}/refresh')
      self.assertEqual(first.status_code, 200)
      self.assertEqual(second.status_code, 200)
      self.assertEqual(fetch_mock.call_count, 1)
      self.assertFalse(first.get_json()['throttled'])
      self.assertTrue(second.get_json()['throttled'])
  ```

- [ ] **步骤 6：运行测试并确认失败**

  运行：`python -m pytest tests/test_temp_email_share.py -q`

  预期：FAIL，主要错误为 `no such table: temp_email_shares` 或路由 404。

- [ ] **步骤 7：Commit 测试**

  ```bash
  git add tests/test_temp_email_share.py
  git commit -m "test: cover temp email share behavior"
  ```

### 任务 2：实现分享表、helper 和登录态分享 API

**依赖：** 任务 1
**文件集：** `outlook_web/segments/01_bootstrap.py`, `outlook_web/segments/06_routes_temp_email.py`
**导出/变更接口：** `outlook_web/segments/01_bootstrap.py::init_db`, `outlook_web/segments/06_routes_temp_email.py::list_temp_email_shares`, `outlook_web/segments/06_routes_temp_email.py::create_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::delete_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::serialize_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::api_get_temp_email_shares`, `outlook_web/segments/06_routes_temp_email.py::api_create_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::api_delete_temp_email_share`
**消费接口：** `outlook_web/segments/06_routes_temp_email.py::get_temp_email_by_id`, `outlook_web/segments/06_routes_temp_email.py::delete_temp_email`
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/01_bootstrap.py`
- 修改：`outlook_web/segments/06_routes_temp_email.py`

- [ ] **步骤 1：新增数据库表和索引**

  在 `init_db()` 创建 `temp_email_messages` 后新增：

  ```sql
  CREATE TABLE IF NOT EXISTS temp_email_shares (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      temp_email_id INTEGER NOT NULL,
      token TEXT NOT NULL UNIQUE,
      expires_at TIMESTAMP,
      last_refreshed_at TIMESTAMP,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (temp_email_id) REFERENCES temp_emails (id)
  )
  ```

  同函数中新增索引：

  ```sql
  CREATE INDEX IF NOT EXISTS idx_temp_email_shares_temp_email_id
  ON temp_email_shares(temp_email_id)
  ```

  ```sql
  CREATE INDEX IF NOT EXISTS idx_temp_email_shares_token
  ON temp_email_shares(token)
  ```

- [ ] **步骤 2：删除临时邮箱时删除分享记录**

  修改 `delete_temp_email(email_addr)`，先查出邮箱 ID，再按 ID 删除分享：

  ```python
  row = db.execute('SELECT id FROM temp_emails WHERE email = ?', (email_addr,)).fetchone()
  if row:
      db.execute('DELETE FROM temp_email_shares WHERE temp_email_id = ?', (row['id'],))
  ```

  继续删除 `temp_email_messages` 和 `temp_emails`。

- [ ] **步骤 3：新增有效期常量和解析 helper**

  在临时邮箱数据库 helper 附近新增：

  ```python
  SHARE_EXPIRY_OPTIONS_MS = {
      60 * 60 * 1000,
      24 * 60 * 60 * 1000,
      3 * 24 * 60 * 60 * 1000,
      30 * 24 * 60 * 60 * 1000,
      0,
  }
  DEFAULT_SHARE_EXPIRY_MS = 30 * 24 * 60 * 60 * 1000
  SHARE_TOKEN_RETRY_LIMIT = 5
  ```

  新增 `normalize_share_expires_in(value)`：

  - `None` 使用默认 30 天。
  - 非整数返回 `(None, '有效期参数无效')`。
  - 不在预设集合返回 `(None, '不支持的有效期')`。
  - 合法返回 `(expires_in, None)`。

- [ ] **步骤 4：新增分享 CRUD helper**

  实现：

  ```python
  def serialize_temp_email_share(row: Dict[str, Any]) -> Dict[str, Any]:
      return {
          'id': row['id'],
          'temp_email_id': row['temp_email_id'],
          'token': row['token'],
          'created_at': row.get('created_at'),
          'expires_at': row.get('expires_at'),
      }
  ```

  ```python
  def list_temp_email_shares(temp_email_id: int) -> List[Dict[str, Any]]:
      rows = get_db().execute(
          '''
          SELECT * FROM temp_email_shares
          WHERE temp_email_id = ?
          ORDER BY created_at DESC, id DESC
          ''',
          (temp_email_id,)
      ).fetchall()
      return [serialize_temp_email_share(dict(row)) for row in rows]
  ```

  ```python
  def create_temp_email_share(temp_email_id: int, expires_in: int) -> Optional[Dict[str, Any]]:
      db = get_db()
      expires_at = None if expires_in == 0 else datetime.utcnow() + timedelta(milliseconds=expires_in)
      for _ in range(SHARE_TOKEN_RETRY_LIMIT):
          token = secrets.token_urlsafe(24)
          try:
              cursor = db.execute(
                  '''
                  INSERT INTO temp_email_shares (temp_email_id, token, expires_at)
                  VALUES (?, ?, ?)
                  ''',
                  (temp_email_id, token, expires_at.isoformat(sep=' ') if expires_at else None)
              )
              db.commit()
              return serialize_temp_email_share(dict(db.execute('SELECT * FROM temp_email_shares WHERE id = ?', (cursor.lastrowid,)).fetchone()))
          except sqlite3.IntegrityError:
              continue
      return None
  ```

  `delete_temp_email_share(temp_email_id, share_id)` 按两个 ID 删除，返回是否删除了行。

- [ ] **步骤 5：新增登录态分享 API**

  在临时邮箱路由区域新增：

  - `GET /api/temp-emails/<int:temp_email_id>/shares`
  - `POST /api/temp-emails/<int:temp_email_id>/shares`
  - `DELETE /api/temp-emails/<int:temp_email_id>/shares/<int:share_id>`

  约束：

  - 所有接口带 `@login_required`。
  - temp email 不存在返回 404。
  - 创建成功返回 HTTP 201 和 `{'success': True, 'share': share}`。
  - 非预设有效期返回 HTTP 400。

- [ ] **步骤 6：运行登录态相关测试**

  运行：`python -m pytest tests/test_temp_email_share.py -q`

  预期：登录态创建、列表、删除、有效期测试通过；公开 API 测试仍因 404 失败。

- [ ] **步骤 7：Commit 后端管理 API**

  ```bash
  git add outlook_web/segments/01_bootstrap.py outlook_web/segments/06_routes_temp_email.py
  git commit -m "feat: add temp email share management"
  ```

### 任务 3：实现公开分享 API 和刷新节流

**依赖：** 任务 2
**文件集：** `outlook_web/segments/06_routes_temp_email.py`
**导出/变更接口：** `outlook_web/segments/06_routes_temp_email.py::get_valid_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::format_temp_email_message_list`, `outlook_web/segments/06_routes_temp_email.py::refresh_shared_temp_email_messages`, `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email`, `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email_messages`, `outlook_web/segments/06_routes_temp_email.py::api_refresh_shared_temp_email_messages`, `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email_message_detail`
**消费接口：** `outlook_web/segments/06_routes_temp_email.py::serialize_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::get_temp_email_messages`, `outlook_web/segments/06_routes_temp_email.py::save_temp_email_messages`, `outlook_web/segments/06_routes_temp_email.py::get_temp_email_message_by_id`
**复杂度：** deep

**文件：**
- 修改：`outlook_web/segments/06_routes_temp_email.py`

- [ ] **步骤 1：新增分享校验 helper**

  实现 `get_valid_temp_email_share(token)`，返回 `(share, temp_email, response_tuple)`。

  行为：

  - 查不到 token：返回 404 JSON。
  - `expires_at` 不为空且早于当前 UTC：返回 410 JSON。
  - 关联 `temp_emails` 查不到：返回 404 JSON。
  - 成功：返回分享行和临时邮箱行。

  使用 SQLite 当前时间格式时，统一通过 Python 解析：

  ```python
  def parse_sqlite_timestamp(value: str) -> Optional[datetime]:
      if not value:
          return None
      return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
  ```

- [ ] **步骤 2：抽取统一列表格式化 helper**

  新增 `format_temp_email_message_list(messages)`：

  ```python
  return [{
      'id': msg.get('message_id') or msg.get('id'),
      'from': msg.get('from_address', '未知'),
      'subject': msg.get('subject', '无主题'),
      'body_preview': (msg.get('content', '') or '')[:200],
      'date': msg.get('created_at', ''),
      'timestamp': msg.get('timestamp', 0),
      'has_html': 1 if msg.get('has_html') else 0,
  } for msg in messages]
  ```

  保持现有登录态接口可继续使用原格式；首版只在公开 API 中使用该 helper。

- [ ] **步骤 3：新增公开邮箱信息和列表 API**

  新增无需 `@login_required` 的路由：

  - `GET /api/shared/<token>`
  - `GET /api/shared/<token>/messages`

  响应形状：

  ```json
  {
    "success": true,
    "email": {
      "email": "public@example.com",
      "provider": "gptmail",
      "provider_label": "GPTMail",
      "created_at": "2026-06-01 12:00:00",
      "expires_at": "2026-07-01 12:00:00"
    }
  }
  ```

  列表响应：

  ```json
  {
    "success": true,
    "emails": [],
    "count": 0
  }
  ```

- [ ] **步骤 4：新增公开详情 API 并限制邮箱归属**

  新增 `GET /api/shared/<token>/messages/<path:message_id>`。

  查询详情时不能只按 `message_id` 返回；必须同时校验：

  ```python
  msg = get_temp_email_message_by_id(message_id)
  if not msg or msg.get('email_address') != temp_email['email']:
      return jsonify({'success': False, 'error': '邮件不存在'}), 404
  ```

  返回 `body_type` 为 `html` 或 `text`，HTML 内容不在后端做截断。

- [ ] **步骤 5：新增 provider 刷新 helper**

  新增 `refresh_shared_temp_email_messages(share, temp_email)`，复用现有 provider 分支：

  - GPTMail：`get_temp_emails_from_api(email_addr)`，非 `None` 时 `save_temp_email_messages`。
  - DuckMail：`get_duckmail_token_for_email(email_addr)`，失败后 `duckmail_refresh_token(email_addr)`，成功后 `duckmail_get_messages(token)`，转换字段后保存。
  - Cloudflare：`get_cloudflare_jwt_for_email(email_addr)`，成功后 `cloudflare_get_messages(jwt)`，用 `parse_raw_email_to_temp_message` 转换后保存。

  返回：

  ```python
  {
      'success': True,
      'new_count': saved,
      'method': 'GPTMail',
      'emails': format_temp_email_message_list(get_temp_email_messages(email_addr)),
  }
  ```

  失败时返回固定统一错误：`{'success': False, 'error': '刷新邮件失败'}`，不包含上游原始响应。

- [ ] **步骤 6：实现 30 秒 token 级刷新节流**

  新增常量：

  ```python
  SHARED_TEMP_EMAIL_REFRESH_THROTTLE_SECONDS = 30
  ```

  新增 `POST /api/shared/<token>/refresh`：

  - `last_refreshed_at` 距当前小于 30 秒：不调用上游，返回缓存列表和 `throttled: true`。
  - 否则先更新 `last_refreshed_at`，再调用刷新 helper，返回 `throttled: false`。
  - 如果刷新失败，仍保持 `last_refreshed_at` 已更新，避免公开链接持续打上游。

- [ ] **步骤 7：运行后端分享测试**

  运行：`python -m pytest tests/test_temp_email_share.py -q`

  预期：全部通过。

- [ ] **步骤 8：Commit 公开 API**

  ```bash
  git add outlook_web/segments/06_routes_temp_email.py
  git commit -m "feat: expose shared temp email api"
  ```

### 任务 4：实现登录后分享弹窗 UI

**依赖：** 任务 2
**文件集：** `static/js/index/03-temp-emails.js`, `static/css/index/06-modals-toast.css`
**导出/变更接口：** `static/js/index/03-temp-emails.js::showTempEmailShareModal`, `static/js/index/03-temp-emails.js::loadTempEmailShares`, `static/js/index/03-temp-emails.js::createTempEmailShare`, `static/js/index/03-temp-emails.js::deleteTempEmailShare`, `static/js/index/03-temp-emails.js::copyTempEmailShareLink`
**消费接口：** `outlook_web/segments/06_routes_temp_email.py::api_get_temp_email_shares`, `outlook_web/segments/06_routes_temp_email.py::api_create_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::api_delete_temp_email_share`
**复杂度：** standard

**文件：**
- 修改：`static/js/index/03-temp-emails.js`
- 修改：`static/css/index/06-modals-toast.css`

- [ ] **步骤 1：在临时邮箱菜单新增分享入口**

  在 `renderTempEmailList` 的 `.account-menu-panel` 中，在「复制邮箱」后加入：

  ```html
  <button class="account-action-btn" type="button" onclick="event.stopPropagation(); closeAccountActionMenus(); showTempEmailShareModal(${Number(email.id)}, '${escapeJs(email.email)}')">分享</button>
  ```

  不改变删除按钮行为。

- [ ] **步骤 2：新增分享弹窗 DOM**

  新增 `showTempEmailShareModal(tempEmailId, emailAddress)`，动态创建 `tempEmailShareModal`：

  - 标题：`分享临时邮箱`
  - 副标题显示邮箱地址。
  - 下拉 `tempEmailShareExpiry`，选项值：`3600000`、`86400000`、`259200000`、`2592000000`、`0`。
  - 默认值：`2592000000`。
  - 创建按钮调用 `createTempEmailShare()`。
  - 链接列表容器 `tempEmailShareList`。

  使用现有 `showModal` / `setModalVisible` / `hideModal` 模式。

- [ ] **步骤 3：实现分享链接加载和渲染**

  新增状态：

  ```javascript
  let currentTempEmailShareTarget = null;
  ```

  `loadTempEmailShares()` 请求 `/api/temp-emails/${id}/shares`。

  渲染每条链接：

  - URL：`${window.location.origin}/shared/${share.token}`
  - 创建时间：使用已有全局 `formatAbsoluteDateTime(share.created_at)`。
  - 过期时间：`expires_at` 为空显示「永久」。
  - 已过期链接增加 `is-expired` 样式。
  - 按钮：复制、删除。

- [ ] **步骤 4：实现创建、复制和删除**

  `createTempEmailShare()`：

  ```javascript
  await fetch(`/api/temp-emails/${currentTempEmailShareTarget.id}/shares`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expires_in: Number(document.getElementById('tempEmailShareExpiry').value) })
  })
  ```

  成功后刷新列表并 toast。

  `copyTempEmailShareLink(token)` 调用现有全局 `copyTextToClipboard(url, '分享链接已复制')`。

  `deleteTempEmailShare(shareId)` 使用 `showConfirmModal` 确认后 DELETE。

- [ ] **步骤 5：新增弹窗样式**

  在 `static/css/index/06-modals-toast.css` 追加小范围样式：

  - `.temp-email-share-list`
  - `.temp-email-share-item`
  - `.temp-email-share-url`
  - `.temp-email-share-meta`
  - `.temp-email-share-actions`
  - `.temp-email-share-item.is-expired`

  样式保持现有 modal 和 account menu 风格，避免页面级大改。

- [ ] **步骤 6：补充轻量静态断言测试**

  在 `tests/test_temp_email_share.py` 增加读取 JS/CSS 的测试：

  ```python
  def test_temp_email_share_ui_hooks_exist(self):
      js = pathlib.Path(ROOT_DIR, 'static', 'js', 'index', '03-temp-emails.js').read_text(encoding='utf-8')
      css = pathlib.Path(ROOT_DIR, 'static', 'css', 'index', '06-modals-toast.css').read_text(encoding='utf-8')
      self.assertIn('showTempEmailShareModal', js)
      self.assertIn('/shares', js)
      self.assertIn('/shared/${share.token}', js)
      self.assertIn('temp-email-share-list', css)
  ```

- [ ] **步骤 7：运行后端和静态测试**

  运行：`python -m pytest tests/test_temp_email_share.py -q`

  预期：PASS。

- [ ] **步骤 8：Commit 登录态 UI**

  ```bash
  git add static/js/index/03-temp-emails.js static/css/index/06-modals-toast.css tests/test_temp_email_share.py
  git commit -m "feat: add temp email share dialog"
  ```

### 任务 5：实现公开分享页面

**依赖：** 任务 3
**文件集：** `outlook_web/segments/06_routes_temp_email.py`, `templates/shared_temp_email.html`, `static/js/shared-temp-email.js`, `static/css/shared-temp-email.css`, `tests/test_temp_email_share.py`
**导出/变更接口：** `outlook_web/segments/06_routes_temp_email.py::shared_temp_email_page`, `static/js/shared-temp-email.js::loadSharedTempEmail`, `static/js/shared-temp-email.js::loadSharedMessages`, `static/js/shared-temp-email.js::refreshSharedMessages`, `static/js/shared-temp-email.js::loadSharedMessageDetail`
**消费接口：** `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email`, `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email_messages`, `outlook_web/segments/06_routes_temp_email.py::api_refresh_shared_temp_email_messages`, `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email_message_detail`
**复杂度：** standard

**文件：**
- 修改：`outlook_web/segments/06_routes_temp_email.py`
- 创建：`templates/shared_temp_email.html`
- 创建：`static/js/shared-temp-email.js`
- 创建：`static/css/shared-temp-email.css`
- 修改：`tests/test_temp_email_share.py`

- [ ] **步骤 1：新增公开页面路由**

  在 `06_routes_temp_email.py` 新增：

  ```python
  @app.route('/shared/<token>', methods=['GET'])
  @csrf_exempt
  def shared_temp_email_page(token):
      share, temp_email, error_response = get_valid_temp_email_share(token)
      status_code = 200
      if error_response:
          status_code = error_response[1] if isinstance(error_response, tuple) else 404
      return render_template('shared_temp_email.html', token=token, initial_status=status_code)
  ```

  页面路由不使用 `@login_required`。对于失效 token，仍渲染页面，由前端调用公开 API 显示失效状态。

- [ ] **步骤 2：创建公开模板**

  `templates/shared_temp_email.html` 内容包含：

  - `<script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.8/dist/purify.min.js"></script>`
  - `<link rel="stylesheet" href="{{ url_for('static', filename='css/shared-temp-email.css') }}">`
  - `<body data-share-token="{{ token|e }}">`
  - 顶部标题、过期时间、刷新按钮 `id="sharedRefreshBtn"`。
  - 列表容器 `id="sharedEmailList"`。
  - 详情容器 `id="sharedEmailDetail"`。
  - `<script src="{{ url_for('static', filename='js/shared-temp-email.js') }}"></script>`

- [ ] **步骤 3：实现公开页 JS**

  `static/js/shared-temp-email.js` 实现：

  - `const token = document.body.dataset.shareToken;`
  - `escapeHtml(value)`
  - `formatSharedDate(value)`
  - `loadSharedTempEmail()`
  - `loadSharedMessages()`
  - `refreshSharedMessages()`
  - `renderSharedMessageList(emails)`
  - `loadSharedMessageDetail(messageId)`
  - `renderSharedMessageDetail(email)`
  - `renderSharedError(message)`

  渲染详情时：

  ```javascript
  if (email.body_type === 'html') {
      bodyEl.innerHTML = DOMPurify.sanitize(email.body || '');
  } else {
      bodyEl.textContent = email.body || '';
  }
  ```

  刷新按钮遇到 `throttled: true` 时显示「刷新过于频繁，已显示缓存邮件」。

- [ ] **步骤 4：实现公开页 CSS**

  `static/css/shared-temp-email.css` 采用工作台式布局：

  - 最大宽度 `1180px` 居中。
  - 桌面端双栏：左侧列表、右侧详情。
  - 移动端单列，详情在列表下方。
  - 按钮、列表项和状态提示使用克制的中性色，不使用营销式 hero。
  - 长邮箱和长主题使用换行或省略，避免溢出。

- [ ] **步骤 5：补充页面路由和静态资源测试**

  在 `tests/test_temp_email_share.py` 增加：

  ```python
  def test_shared_page_renders_without_login(self):
      temp_email_id = self._create_temp_email()
      token = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={}).get_json()['share']['token']
      response = self.public_client.get(f'/shared/{token}')
      self.assertEqual(response.status_code, 200)
      html = response.get_data(as_text=True)
      self.assertIn('data-share-token', html)
      self.assertIn('shared-temp-email.js', html)
  ```

  增加：

  ```python
  def test_shared_page_uses_dompurify_for_html_detail(self):
      js = pathlib.Path(ROOT_DIR, 'static', 'js', 'shared-temp-email.js').read_text(encoding='utf-8')
      self.assertIn('DOMPurify.sanitize', js)
      self.assertIn('body_type ===', js)
  ```

- [ ] **步骤 6：运行分享页测试**

  运行：`python -m pytest tests/test_temp_email_share.py -q`

  预期：PASS。

- [ ] **步骤 7：Commit 公开页面**

  ```bash
  git add outlook_web/segments/06_routes_temp_email.py templates/shared_temp_email.html static/js/shared-temp-email.js static/css/shared-temp-email.css tests/test_temp_email_share.py
  git commit -m "feat: add public temp email share page"
  ```

### 任务 6：更新文档

**依赖：** 任务 4, 任务 5
**文件集：** `README.md`, `docs/api.md`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/06_routes_temp_email.py::api_create_temp_email_share`, `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email`, `outlook_web/segments/06_routes_temp_email.py::api_refresh_shared_temp_email_messages`
**复杂度：** quick

**文件：**
- 修改：`README.md`
- 修改：`docs/api.md`

- [ ] **步骤 1：更新 README 功能说明**

  在临时邮箱功能特性附近补充：

  ```markdown
  - 🔗 **临时邮箱分享** - 支持为 GPTMail、DuckMail、Cloudflare 临时邮箱创建分享链接，其他人无需登录即可只读查看邮件，并支持受节流保护的公开刷新
  ```

  在使用说明中新增「临时邮箱分享」小节：

  - 打开「临时邮箱」分组。
  - 点击邮箱三点菜单 -> 「分享」。
  - 选择有效期：1 小时、24 小时、3 天、1 个月、永久。
  - 创建并复制 `/shared/<token>` 链接。
  - 说明公开访问者只能查看和刷新，不能删除或修改邮箱。

- [ ] **步骤 2：更新 API 文档**

  在 `docs/api.md` 中补充：

  - 登录态分享管理接口：
    - `GET /api/temp-emails/<temp_email_id>/shares`
    - `POST /api/temp-emails/<temp_email_id>/shares`
    - `DELETE /api/temp-emails/<temp_email_id>/shares/<share_id>`
  - 公开接口：
    - `GET /api/shared/<token>`
    - `GET /api/shared/<token>/messages`
    - `POST /api/shared/<token>/refresh`
    - `GET /api/shared/<token>/messages/<message_id>`
  - 说明 `expires_in` 单位为毫秒，默认 30 天，`0` 为永久。
  - 说明公开刷新按 token 30 秒节流。
  - 明确公开接口不返回 provider token、JWT、密码。

- [ ] **步骤 3：运行文档静态测试和分享测试**

  运行：`python -m pytest tests/test_temp_email_share.py -q`

  预期：PASS。

- [ ] **步骤 4：Commit 文档**

  ```bash
  git add README.md docs/api.md
  git commit -m "docs: describe temp email sharing"
  ```

### 任务 7：总体验证和收口

**依赖：** 任务 6
**文件集：** `tests/test_temp_email_share.py`, `outlook_web/segments/01_bootstrap.py`, `outlook_web/segments/06_routes_temp_email.py`, `static/js/index/03-temp-emails.js`, `static/css/index/06-modals-toast.css`, `templates/shared_temp_email.html`, `static/js/shared-temp-email.js`, `static/css/shared-temp-email.css`, `README.md`, `docs/api.md`
**导出/变更接口：** 无
**消费接口：** `outlook_web/segments/06_routes_temp_email.py::api_shared_temp_email`, `static/js/shared-temp-email.js::refreshSharedMessages`
**复杂度：** standard

**文件：**
- 检查：`tests/test_temp_email_share.py`
- 检查：`outlook_web/segments/01_bootstrap.py`
- 检查：`outlook_web/segments/06_routes_temp_email.py`
- 检查：`static/js/index/03-temp-emails.js`
- 检查：`static/css/index/06-modals-toast.css`
- 检查：`templates/shared_temp_email.html`
- 检查：`static/js/shared-temp-email.js`
- 检查：`static/css/shared-temp-email.css`
- 检查：`README.md`
- 检查：`docs/api.md`

- [ ] **步骤 1：运行目标测试**

  运行：`python -m pytest tests/test_temp_email_share.py -q`

  预期：PASS。

- [ ] **步骤 2：运行全量测试**

  运行：`python -m pytest`

  预期：PASS。

- [ ] **步骤 3：运行语法编译检查**

  运行：`python -m compileall web_outlook_app.py outlook_web`

  预期：无 SyntaxError。

- [ ] **步骤 4：检查公开接口无鉴权装饰器**

  运行：

  ```bash
  rg -n "@login_required|/api/shared|/shared/<token>" outlook_web/segments/06_routes_temp_email.py
  ```

  预期：`/api/shared/*` 和 `/shared/<token>` 附近没有 `@login_required`；登录态 `/api/temp-emails/<id>/shares` 接口有 `@login_required`。

- [ ] **步骤 5：检查敏感字段未出现在公开序列化**

  运行：

  ```bash
  rg -n "duckmail_password|duckmail_token|cloudflare_jwt|refresh_token" outlook_web/segments/06_routes_temp_email.py static/js/shared-temp-email.js templates/shared_temp_email.html
  ```

  预期：这些字段只出现在 provider 内部读取逻辑，不出现在公开响应构造、公开 JS 或公开模板中。

- [ ] **步骤 6：检查 Git 状态**

  运行：`git status --short`

  预期：只有用户已有的未跟踪 `AGENTS.md` 可保持未提交；本功能实现文件应已提交或明确待提交。

- [ ] **步骤 7：最终 Commit**

  如果前面任务还有未提交的实现文件：

  ```bash
  git add tests/test_temp_email_share.py outlook_web/segments/01_bootstrap.py outlook_web/segments/06_routes_temp_email.py static/js/index/03-temp-emails.js static/css/index/06-modals-toast.css templates/shared_temp_email.html static/js/shared-temp-email.js static/css/shared-temp-email.css README.md docs/api.md
  git commit -m "feat: support temp email sharing"
  ```

## 并行执行图

> 仅 `parallel-executing-plans` 使用；`serial-executing-plans` 忽略本节。

**Critical Path:** 任务 1 → 任务 2 → 任务 3 → 任务 5 → 任务 6 → 任务 7

- Wave 1（无依赖）：任务 1
- Wave 2（依赖 Wave 1）：任务 2（依赖 任务 1）
- Wave 3（依赖 Wave 2）：任务 3（依赖 任务 2）, 任务 4（依赖 任务 2）
- Wave 4（依赖 Wave 3）：任务 5（依赖 任务 3）
- Wave 5（依赖 Wave 4）：任务 6（依赖 任务 4, 任务 5）
- Wave 6（依赖 Wave 5）：任务 7（依赖 任务 6）
- Wave FINAL（所有任务完成后）：F1 规格合规、F2 代码质量、F3 真实手测、F4 范围保真
