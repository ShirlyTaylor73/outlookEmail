# 外部验证码获取 API 设计

## 背景

当前项目已经提供 API Key 鉴权的外部邮件列表接口：

- `GET /api/external/accounts`
- `GET /api/external/emails`

`/api/external/emails` 适合外部项目查询最新邮件，但只返回邮件列表与 `body_preview`。实际验证中，ChatGPT 验证码邮件的 `body_preview` 只包含 HTML 头部和标题附近内容，验证码 `051949` 只出现在完整正文 `body` 中。因此，外部项目仅依赖 `body_preview` 提取验证码不稳定。

本设计新增一个验证码专用外部接口，由服务端读取候选邮件完整正文并提取验证码，只向 API Key 调用方返回验证码和命中邮件元信息，不返回完整正文。

## 目标

- 为外部项目提供稳定的验证码获取 API。
- 沿用现有外部 API Key 鉴权机制。
- 支持主邮箱、别名邮箱、plus-address 和 Gmail/Googlemail 后缀回退能力。
- 支持从最新邮件的完整正文中提取验证码。
- 支持可选刷新，避免调用方为了等邮件反复操作多个接口。
- 控制安全暴露面：不返回完整邮件正文、账号凭据、代理配置或上游详细错误。

## 非目标

- 不实现通用 API Key 版完整邮件详情接口。
- 不返回附件、原始 `.eml`、完整 HTML 正文或完整纯文本正文。
- 不执行删除、标记已读、转发或账号管理操作。
- 不提供复杂验证码识别模型；第一版只做规则提取。
- 不替代现有 `/api/external/emails` 邮件列表接口。

## 新增接口

### `GET /api/external/verification-code`

鉴权方式沿用现有外部 API：

- Header：`X-API-Key: <external_api_key>`
- Query：`api_key=<external_api_key>` 或 `apikey=<external_api_key>`

#### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `email` | string | 必填 | 主邮箱、别名邮箱或 plus-address。解析逻辑复用现有外部邮件接口。 |
| `folder` | string | `all` | 支持 `inbox`、`junkemail`、`deleteditems`、`all`。 |
| `top` | int | `5` | 候选邮件数量，最大 `20`。 |
| `skip` | int | `0` | 分页偏移，主要用于调用方跳过已检查邮件。 |
| `refresh` | bool-like | `false` | 传 `1`、`true`、`yes`、`on` 时先刷新邮件。 |
| `subject_contains` | string | 空 | 主题过滤，大小写不敏感，保留 `+` 字符。 |
| `from_contains` | string | 空 | 发件人过滤，大小写不敏感，保留 `+` 字符。 |
| `keyword` | string | 空 | 在主题、预览和正文中做关键字过滤，大小写不敏感。 |

第一版不开放自定义正则参数。默认验证码规则固定为：

```regex
\b\d{4,8}\b
```

这样覆盖当前目标场景中的 6 位数字验证码，同时降低误匹配长订单号、URL 参数和时间戳的概率。

## 行为流程

1. 校验 API Key，复用 `api_key_required`。
2. 解析 `email`，复用 `resolve_account_for_email_api`，保持主邮箱、别名、plus-address 和 Gmail/Googlemail 回退行为一致。
3. 校验 `folder`，支持 `VALID_MAIL_FOLDERS`。
4. 解析分页参数：`skip >= 0`，`top` 默认 `5`，最大 `20`。
5. 如果 `refresh=1`：
   - 对 `email + folder` 做 30 秒节流。
   - 未节流时调用 `fetch_account_emails(account, folder, skip, top)` 获取最新列表。
   - 刷新失败时返回统一错误，不暴露上游细节。
6. 如果未刷新或刷新被节流：
   - 仍调用现有列表读取逻辑获取候选邮件。
7. 对候选邮件应用 `subject_contains`、`from_contains` 和 `keyword` 过滤。
8. 按列表顺序从新到旧读取候选邮件详情：
   - IMAP 账号：复用 `fetch_imap_account_detail_response`。
   - Outlook Graph：优先复用 `fetch_graph_detail_response`。
   - Outlook OAuth IMAP：复用 `fetch_oauth_imap_detail_response`。
   - 若列表项提供 `folder`、`method`、`id_mode`，详情读取必须沿用这些参数。
9. 对详情正文提取可读文本：
   - `body_type=html` 时使用现有 `strip_html_content` 去除 `script`、`style` 和 HTML 标签。
   - `body_type=text` 时也使用 `strip_html_content` 做安全归一化，兼容包含简单 HTML 片段的文本。
10. 提取顺序：
    - 完整正文文本。
    - 邮件主题。
    - `body_preview`。
11. 找到第一个匹配验证码即停止并返回。
12. 遍历完仍未找到时返回 `success=true, found=false`，方便外部项目轮询。

## 响应设计

### 找到验证码

```json
{
  "success": true,
  "found": true,
  "code": "051949",
  "email": "sandraescobar7397@hotmail.com",
  "requested_email": "SandraEscobar7397@hotmail.com",
  "resolved_email": "sandraescobar7397@hotmail.com",
  "message_id": "4",
  "subject": "ChatGPT の一時的な認証コード",
  "from": "ChatGPT <noreply@tm.openai.com>",
  "folder": "inbox",
  "method": "imap",
  "id_mode": "sequence",
  "source": "body",
  "date": "01-Jun-2026 02:46:44 +0800",
  "checked_count": 2,
  "throttled": false
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `found` | 是否提取到验证码。 |
| `code` | 提取到的验证码，仅在 `found=true` 时返回。 |
| `message_id` | 命中邮件 ID，可用于后续排查。 |
| `source` | 命中来源：`body`、`subject` 或 `body_preview`。 |
| `checked_count` | 本次读取详情并尝试提取的邮件数量。 |
| `throttled` | `refresh=1` 时是否命中刷新节流；未请求刷新时为 `false`。 |

### 未找到验证码

```json
{
  "success": true,
  "found": false,
  "email": "user@outlook.com",
  "requested_email": "user@outlook.com",
  "resolved_email": "user@outlook.com",
  "checked_count": 5,
  "throttled": false
}
```

未找到不是异常，保持 HTTP 200，便于外部项目按固定间隔轮询。

### 错误响应

| 场景 | HTTP | 响应 |
| --- | --- | --- |
| 缺少 `email` | 400 | `{"success": false, "error": "缺少 email 参数"}` |
| API Key 缺失 | 401 | 沿用现有 `api_key_required` 响应 |
| API Key 未配置 | 403 | 沿用现有 `api_key_required` 响应 |
| API Key 错误 | 401 | 沿用现有 `api_key_required` 响应 |
| 邮箱不存在 | 404 | `{"success": false, "error": "邮箱账号不存在"}` |
| `folder` 无效 | 400 | 返回支持的 folder 列表 |
| 上游列表或详情读取失败 | 200 或 502 | 第一版优先返回 `success=false, error="获取验证码失败"`，不暴露上游细节 |

外部响应不得包含以下字段或字段值：

- `password`
- `refresh_token`
- `client_id`
- `imap_password`
- `proxy_url`
- `fallback_proxy_url_1`
- `fallback_proxy_url_2`
- SMTP/Telegram/WebDAV 转发配置
- 完整上游错误对象
- 完整正文或原始 MIME 内容

## 刷新节流

`refresh=1` 时增加 30 秒节流，粒度为 `resolved_email + folder`。命中节流时不再访问上游，只使用当前列表读取结果继续解析验证码。

节流状态使用进程内字典即可，格式类似：

```python
EXTERNAL_VERIFICATION_REFRESH_STATE = {
    "resolved@example.com:all": datetime(...)
}
```

该状态只用于保护上游，不作为强一致业务状态。应用重启后丢失可以接受。

## 安全与隐私

- 接口只返回验证码和命中邮件元信息，不返回正文。
- 详情读取中的异常统一收敛，避免外部调用方看到 token、代理、账号配置或 provider 细节。
- 保持 API Key 鉴权，不允许未授权访问。
- `top` 限制最大 `20`，避免一次请求读取大量邮件详情。
- 不支持附件和原始邮件。

## 测试计划

新增或扩展外部 API 测试，覆盖：

- 缺少 API Key 返回 401。
- 未配置外部 API Key 返回 403。
- API Key 错误返回 401。
- 缺少 `email` 返回 400。
- 邮箱不存在返回 404。
- `folder` 无效返回 400。
- 默认从最新候选邮件完整正文提取 6 位数字验证码。
- ChatGPT 风格 HTML 正文示例可提取 `051949`，即使 `body_preview` 不含验证码。
- 当正文无验证码但主题或 `body_preview` 有验证码时可兜底提取，并返回正确 `source`。
- 未找到验证码返回 HTTP 200 且 `found=false`。
- `subject_contains`、`from_contains`、`keyword` 能过滤候选邮件。
- `refresh=1` 调用现有 `fetch_account_emails`，重复请求在 30 秒内节流。
- Outlook Graph、Outlook OAuth IMAP、普通 IMAP 至少各覆盖一条详情读取路径，全部使用 mock，避免真实上游请求。
- 响应体不包含凭据、代理字段、完整正文或上游错误细节。

## 文档更新

实现时同步更新：

- `docs/api.md`：新增外部验证码 API 章节。
- `README.md`：在外部 API 或自动化接入部分增加简短说明。

## 已确认决策

- 接口形态：验证码专用接口，只返回验证码和命中邮件元信息。
- 搜索策略：从最新候选邮件的完整正文解析验证码，不能依赖 `body_preview`。
- 刷新策略：默认不刷新，`refresh=1` 可选刷新，并做 30 秒节流。
- 验证码规则：默认 6 位数字，并优先匹配验证码上下文附近的 HTML 代码块。
- 候选邮件范围：默认最新 5 封，支持过滤参数，最大 20 封。
- 未找到语义：HTTP 200 + `found=false`。
