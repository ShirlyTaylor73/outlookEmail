# Typed Mailbox Groups And External Claim API Design

## Summary

本设计把当前依赖 `name == '临时邮箱'` 的特殊分组逻辑改为显式分组类型，并新增面向外部进程的单个邮箱领取 API。升级后普通导入账号和临时邮箱都通过真实 `group_id` 管理；默认临时邮箱组继续存在，但不再固定排最前，也不再依赖 `TEMP_EMAIL_GROUP_ID = -1` 这类虚拟 ID。

外部程序通过 API Key 从某个源分组领取一个邮箱，服务端用事务保证并发进程不会领到同一个邮箱。处理成功后，调用方带 `claim_token` 完成任务并把邮箱移动到目标分组；处理失败则释放占用，邮箱留在源分组等待重试。

## Goals

- 删除或废弃 `TEMP_EMAIL_GROUP_ID = -1` 的虚拟分组概念。
- 让所有普通账号和临时邮箱都通过真实 `groups.id` 管理。
- 一个分组只能管理一种邮箱资源：普通账号或临时邮箱。
- 支持多个临时邮箱分组，并允许按正常分组排序管理。
- 提供 API Key 鉴权的单个邮箱领取、完成、释放接口。
- 通过数据库事务和 claim token 防止并行进程重复领取同一个邮箱。
- 支持旧数据无缝迁移并保留普通账号、临时邮箱、分享链接和邮件缓存。

## Non-Goals

- 本次不做批量 claim 或批量 complete。
- 本次不合并 `accounts` 和 `temp_emails` 两张表。
- 本次不引入多用户权限体系。
- 本次不改变现有邮件读取、验证码 API、分享链接的核心语义。
- 本次不允许一个分组同时包含普通账号和临时邮箱。

## Data Model

### groups

新增字段：

```sql
mailbox_type TEXT NOT NULL DEFAULT 'account'
```

取值：

- `account`：普通导入账号分组，包含 Outlook、IMAP、Gmail、QQ、163 等 `accounts` 表资源。
- `temp_email`：临时邮箱分组，包含 GPTMail、DuckMail、Cloudflare Temp Email 等 `temp_emails` 表资源。

迁移规则：

- 现有名为 `临时邮箱` 的系统组标记为 `mailbox_type='temp_email'`。
- 其他现有分组标记为 `mailbox_type='account'`。
- 保留现有显示顺序；升级后不再强制 `临时邮箱` 排最前。
- 默认 `临时邮箱` 组继续 `is_system=1`，不可删除，但允许参与排序。

### temp_emails

新增字段：

```sql
group_id INTEGER
```

迁移规则：

- 所有已有临时邮箱的 `group_id` 设置为默认临时邮箱组的真实 ID。当前用户环境中该组显示为 `id=2`，代码迁移时必须按组名或系统属性查询真实 ID，不能硬编码。
- 新生成或导入的临时邮箱如果未传 `group_id`，默认写入默认临时邮箱组。

### mailbox_claims

新增表：

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
);
```

约束语义：

- `resource_type` 取值为 `account` 或 `temp_email`。
- `status` 取值为 `claiming`、`completed`、`released`、`expired`。
- 同一资源同一时间只能有一个有效 `claiming` 记录。
- SQLite 条件唯一索引可用于保护未释放的领取状态，例如对 `resource_type, resource_id, status` 建索引；最终一致性仍由 `BEGIN IMMEDIATE` 事务保证。

## Backend Behavior

### Group APIs

`GET /api/groups`：

- 返回每个分组的 `mailbox_type`。
- 统计数量时按类型分流：
  - `account`：统计 `accounts.group_id = groups.id`。
  - `temp_email`：统计 `temp_emails.group_id = groups.id`。
- 不再依赖 `name == '临时邮箱'` 判断临时邮箱分组。

创建或更新分组：

- 新建分组时必须确定 `mailbox_type`，默认 `account`。
- `mailbox_type` 一旦分组内存在资源，不允许直接改成另一种类型。
- 默认临时邮箱系统组不可删除，但可以排序。

移动分组：

- 普通账号只能移动到 `mailbox_type='account'` 的分组。
- 临时邮箱只能移动到 `mailbox_type='temp_email'` 的分组。
- 类型不匹配返回 `400`。

导出分组：

- 按 `mailbox_type` 选择导出逻辑。
- `temp_email` 分组只导出该分组下的临时邮箱，不再导出全部临时邮箱。

### Temp Email APIs

`load_temp_emails()` 扩展为支持可选 `group_id`。

`GET /api/temp-emails`：

- 支持 `group_id` 查询参数。
- 如果不传 `group_id`，为了兼容现有页面，可以返回全部临时邮箱；分组视图应传入当前临时邮箱分组 ID 以获得精确结果。

`POST /api/temp-emails/import` 和 `POST /api/temp-emails/generate`：

- 支持可选 `group_id`。
- 未传时写入默认临时邮箱组。
- 传入普通账号分组时返回 `400`。

## External Claim API

全部接口使用现有 `api_key_required`，通过 `X-API-Key`、`api_key` 或 `apikey` 鉴权。

### Claim One Mailbox

```http
POST /api/external/mailboxes/claim
```

请求：

```json
{
  "source_group_id": 1,
  "caller_id": "worker-01",
  "task_id": "batch-001",
  "lease_seconds": 600
}
```

规则：

- `source_group_id` 必填。
- `caller_id` 和 `task_id` 必填，用于日志和排查。
- `lease_seconds` 默认 600，限制在 1 到 3600 秒。
- 服务端按源分组 `mailbox_type` 选择资源表。
- 只领取 `status='active'` 的邮箱资源。
- 按资源 ID 升序分配。
- 领取时先回收过期 `claiming` 记录。
- 事务使用 `BEGIN IMMEDIATE`，保证多个并发进程不会领取到同一个资源。

成功返回：

```json
{
  "success": true,
  "mailbox": {
    "resource_type": "account",
    "resource_id": 123,
    "email": "user@example.com",
    "group_id": 1,
    "claim_token": "mclm_xxx",
    "lease_expires_at": "2026-06-02T12:00:00Z"
  }
}
```

无可领取资源返回：

```json
{
  "success": true,
  "mailbox": null
}
```

### Complete Claim And Move

```http
POST /api/external/mailboxes/complete
```

请求：

```json
{
  "resource_type": "account",
  "resource_id": 123,
  "claim_token": "mclm_xxx",
  "target_group_id": 5,
  "caller_id": "worker-01",
  "task_id": "batch-001",
  "detail": "done"
}
```

规则：

- `claim_token` 必须匹配当前 `claiming` 记录。
- 即使租约已过期，只要资源未被其他 claim 重新领取且 token 仍匹配，仍允许完成。
- `target_group_id` 的 `mailbox_type` 必须与 `resource_type` 一致，否则返回 `400`。
- 完成时在同一个事务里移动资源分组并把 claim 标记为 `completed`。
- 成功后普通账号更新 `accounts.group_id`，临时邮箱更新 `temp_emails.group_id`。

### Release Claim

```http
POST /api/external/mailboxes/release
```

请求：

```json
{
  "resource_type": "account",
  "resource_id": 123,
  "claim_token": "mclm_xxx",
  "caller_id": "worker-01",
  "task_id": "batch-001",
  "detail": "local task failed"
}
```

规则：

- `claim_token` 必须匹配当前 `claiming` 记录。
- 释放后不移动分组。
- 资源留在源分组，后续可重新领取。

## Error Semantics

- `400`：参数缺失、分组类型不匹配、目标分组类型不允许。
- `401`：API Key 缺失或错误。
- `403`：服务端未配置对外 API Key。
- `404`：源分组、目标分组或资源不存在。
- `409`：claim token 不匹配、claim 已完成、已释放或已被重新领取。
- `200` 且 `mailbox=null`：没有可领取资源，不视为错误。

## Frontend Impact

- 新建分组弹窗增加“分组类型”选择，默认 `普通邮箱`。
- 分组列表显示仍保持当前视觉风格，可根据 `mailbox_type` 决定右侧面板和操作集合。
- 临时邮箱分组不再固定最前；迁移后保持当前显示顺序，但之后可排序。
- 普通账号导入、移动只显示普通邮箱分组。
- 临时邮箱生成、导入、移动只显示临时邮箱分组。
- 临时邮箱列表按当前选中的临时邮箱分组加载，不再无条件显示全部临时邮箱。

## Migration Plan

启动时在 `init_db()` 中执行幂等迁移：

1. `groups` 缺少 `mailbox_type` 时增加字段，默认 `account`。
2. 确保默认临时邮箱系统组存在。
3. 将默认临时邮箱系统组设为 `mailbox_type='temp_email'`。
4. 将其他缺省或空值分组设为 `mailbox_type='account'`。
5. `temp_emails` 缺少 `group_id` 时增加字段。
6. 将 `temp_emails.group_id IS NULL` 的旧数据迁移到默认临时邮箱系统组。
7. 创建 `temp_emails(group_id, created_at)` 和 `mailbox_claims` 相关索引。
8. 保留现有 `groups.id`、`accounts.group_id`、临时邮箱 ID、分享链接和邮件记录。

升级后不需要清库，旧版本数据应完整保留。

## Security Defaults

- 外部 claim API 不返回账号密码、refresh token、IMAP 密码、代理、临时邮箱 token、Cloudflare JWT、DuckMail 密码等敏感字段。
- claim token 使用高熵随机值，只用于完成或释放当前领取。
- 完成和释放接口必须同时校验 `resource_type`、`resource_id`、`claim_token`。
- 公开错误信息不暴露数据库细节或上游服务细节。

## Test Plan

后端测试：

- 旧库迁移后，已有临时邮箱全部写入默认临时邮箱组。
- `groups.mailbox_type` 默认值和默认临时邮箱组类型正确。
- `/api/groups` 对普通账号组和临时邮箱组分别统计数量。
- 临时邮箱生成、导入未传 `group_id` 时进入默认临时邮箱组。
- 临时邮箱生成、导入传普通账号组时返回 `400`。
- 普通账号移动到临时邮箱组返回 `400`。
- 临时邮箱移动到普通账号组返回 `400`。
- claim 普通账号组返回 `resource_type=account`。
- claim 临时邮箱组返回 `resource_type=temp_email`。
- 并发 claim 不会返回重复资源，使用事务或线程模拟。
- complete 成功后资源移动到目标组，claim 状态变为 `completed`。
- release 后资源留在源组，后续可以重新 claim。
- token 不匹配返回 `409`。
- 租约过期后未被重新领取时，token 匹配仍允许 complete。
- 外部 claim 响应不包含凭据、代理、临时邮箱密钥。

静态和编译检查：

```bash
python -m pytest tests/test_external_mailbox_claim.py tests/test_temp_email_groups.py -q -p no:cacheprovider
python -m compileall web_outlook_app.py outlook_web
```

## Final Decisions

- 新建分组默认类型为 `account`。
- 默认临时邮箱组保留 `is_system=1`，不可删除，但允许排序。
- 迁移后保留当前分组显示顺序。
- 临时邮箱未传 `group_id` 时默认进入默认临时邮箱系统组。
- 不再使用 `TEMP_EMAIL_GROUP_ID = -1` 作为后端分组模型。
