# 临时邮箱分享功能设计

## 背景

当前项目支持 GPTMail、DuckMail 和 Cloudflare Temp Email 三类临时邮箱，登录用户可以生成、导入、读取和删除临时邮箱。所有临时邮箱接口目前都需要 Web 登录态。

目标是迁移 MoeMail 的临时邮箱分享能力：登录用户可以把本系统中的临时邮箱生成分享链接，其他人无需登录即可通过 URL 查看该邮箱收到的邮件。

## 范围

本次实现临时邮箱级分享，不实现单封邮件独立分享。

包含：

- 全部临时邮箱均可分享，包括生成和导入的 GPTMail、DuckMail、Cloudflare 临时邮箱。
- 同一个临时邮箱可以创建多条分享链接。
- 分享链接路径为 `/shared/<token>`。
- 未登录访问者可以查看邮箱地址、分享过期时间、邮件列表和邮件详情。
- 未登录访问者可以手动刷新邮件，但刷新必须在后端按 token 节流。
- 登录用户可以创建、复制和删除分享链接。

不包含：

- 不支持 `/shared/message/<token>` 单封邮件分享。
- 不允许公开访问者删除邮箱、删除邮件、编辑标签、查看 provider token、查看 JWT、查看 DuckMail 密码或执行任何管理操作。
- 不支持批量创建分享链接。

## 用户体验

登录后，在临时邮箱列表的三点菜单中新增「分享」操作。点击后打开分享弹窗。

分享弹窗包含：

- 有效期下拉菜单：`1小时`、`24小时`、`3天`、`1个月`、`永久`。
- 默认有效期：`1个月`。
- 「创建链接」按钮。
- 已有分享链接列表，展示链接、创建时间、过期时间和过期状态。
- 每条链接支持复制和删除。

公开分享页使用独立只读页面，不复用登录后的四栏管理界面。页面包含：

- 顶部邮箱地址和分享过期时间。
- 刷新按钮。
- 邮件列表。
- 邮件详情区域，支持 HTML 正文和纯文本正文。
- 链接不存在、已过期、邮箱已删除时展示失效提示。

## 数据模型

新增 SQLite 表 `temp_email_shares`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 分享记录 ID |
| `temp_email_id` | INTEGER NOT NULL | 关联 `temp_emails.id` |
| `token` | TEXT NOT NULL UNIQUE | 公开分享 token |
| `expires_at` | TIMESTAMP NULL | 过期时间，NULL 表示永久 |
| `last_refreshed_at` | TIMESTAMP NULL | 公开刷新节流时间 |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 更新时间 |

索引：

- `idx_temp_email_shares_temp_email_id`：按邮箱查询分享链接。
- `idx_temp_email_shares_token`：按 token 查询公开链接。

删除临时邮箱时，相关分享链接必须同步删除。可以通过显式删除实现，不依赖 SQLite 外键级联。

## 登录态 API

### 获取分享链接

`GET /api/temp-emails/<int:temp_email_id>/shares`

鉴权：Web 登录态。

返回当前临时邮箱的全部分享链接，按创建时间倒序。

### 创建分享链接

`POST /api/temp-emails/<int:temp_email_id>/shares`

鉴权：Web 登录态。

请求体：

```json
{
  "expires_in": 2592000000
}
```

`expires_in` 单位为毫秒。`0` 表示永久。服务端只接受预设值：1 小时、24 小时、3 天、30 天、永久。

服务端使用 `secrets.token_urlsafe(24)` 生成强随机 token。若 token 冲突，最多重试 5 次。

### 删除分享链接

`DELETE /api/temp-emails/<int:temp_email_id>/shares/<int:share_id>`

鉴权：Web 登录态。

只能删除属于该临时邮箱的分享链接。

## 公开 API

公开 API 不需要登录态，也不需要 API Key。

### 获取分享邮箱信息

`GET /api/shared/<token>`

校验：

- token 存在。
- 分享未过期。
- 关联临时邮箱存在。

返回：

- 邮箱地址。
- provider 展示名。
- 分享创建时间。
- 分享过期时间。

不返回 provider 凭据或内部管理字段。

### 获取邮件列表

`GET /api/shared/<token>/messages`

返回当前缓存中的邮件列表。列表字段包括：

- `id`
- `from`
- `subject`
- `body_preview`
- `timestamp`
- `has_html`

默认按时间倒序。首版返回当前已缓存的全部列表，不新增游标分页；如果后续邮件量增长，再单独设计分页。

### 刷新邮件

`POST /api/shared/<token>/refresh`

公开访问者可以触发刷新，但服务端必须按 token 节流。节流窗口固定为 30 秒。

行为：

- 未超过节流窗口时，不访问上游 provider，直接返回当前缓存列表，并标记 `throttled: true`。
- 超过节流窗口时，复用现有临时邮箱刷新逻辑访问上游 provider，保存最新邮件后返回列表，并更新 `last_refreshed_at`。
- 刷新失败时返回错误，但不得泄露 provider token、JWT、密码或完整上游响应。

### 获取邮件详情

`GET /api/shared/<token>/messages/<path:message_id>`

校验：

- token 有效。
- message 属于该分享链接关联的临时邮箱。

返回：

- `id`
- `from`
- `to`
- `subject`
- `body`
- `body_type`
- `timestamp`

HTML 正文必须在公开页使用 DOMPurify 净化后再渲染，避免 XSS。

## 后端设计

新增分享相关 helper，放在 `outlook_web/segments/06_routes_temp_email.py` 附近，保持临时邮箱逻辑集中：

- `create_temp_email_share(temp_email_id, expires_in)`
- `list_temp_email_shares(temp_email_id)`
- `delete_temp_email_share(temp_email_id, share_id)`
- `get_valid_temp_email_share(token)`
- `serialize_temp_email_share(share)`
- `refresh_shared_temp_email_messages(share)`

公开刷新逻辑应复用现有 provider 分支：

- GPTMail：调用 `get_temp_emails_from_api` 后保存。
- DuckMail：通过已保存的加密密码或 token 获取邮件。
- Cloudflare：通过已保存的 JWT 获取邮件。

公开接口只能返回统一格式数据，不返回上游原始错误详情。

## 前端设计

在 `static/js/index/03-temp-emails.js` 中：

- 临时邮箱菜单新增「分享」按钮。
- 新增分享弹窗创建、加载、复制、删除逻辑。
- 复制链接使用 `${window.location.origin}/shared/${token}`。

新增公开分享页面模板 `templates/shared_temp_email.html`，并配套新增静态 JS/CSS：

- 页面不依赖登录态和 CSRF token。
- 使用公开 API 加载邮箱、邮件列表和邮件详情。
- 刷新按钮调用 `POST /api/shared/<token>/refresh`。
- HTML 正文渲染前使用现有 DOMPurify 资源净化。

## 安全要求

- 分享 token 必须是高熵随机值，不能包含邮箱地址或自增 ID。
- 公开 API 不设置登录态，不读取或修改 session。
- 所有公开响应都必须禁止返回敏感字段。
- 公开刷新必须节流，避免公开链接被频繁请求拖垮上游服务。
- 公开 HTML 正文必须净化。
- 过期链接返回 410 或展示明确的过期状态。
- 不存在或已删除的链接返回 404。

## 测试计划

新增后端 pytest 覆盖：

- 创建分享链接默认 30 天有效期。
- 仅接受预设有效期。
- 同一临时邮箱可创建多条分享链接。
- 删除分享链接后公开访问失效。
- 过期分享链接不可访问。
- 删除临时邮箱后相关分享链接不可访问。
- 公开邮件详情只能读取关联邮箱下的 message。
- 公开刷新在节流窗口内不重复调用上游 provider。
- 公开 API 不返回 DuckMail 密码、DuckMail token、Cloudflare JWT 等敏感字段。

前端验证：

- 临时邮箱菜单出现「分享」。
- 分享弹窗可创建、复制、删除链接。
- `/shared/<token>` 无登录态可打开。
- 公开页可刷新、查看列表和详情。
- 过期或删除后的链接展示失效状态。

## 成功标准

- 登录用户可以为任意临时邮箱创建多条分享链接。
- 未登录访问者打开 `/shared/<token>` 可以查看该邮箱邮件。
- 未登录访问者可以刷新邮件，但刷新被后端节流保护。
- 分享链接可以按条删除，删除后立即失效。
- 公开接口不泄露任何邮箱凭据或管理字段。
- 相关后端测试通过。
