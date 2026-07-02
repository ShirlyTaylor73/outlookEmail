# iCloud Hide My Email 邮箱集成设计

## 目标

把 iCloud Hide My Email（以下简称 HME）地址作为本项目的一等邮箱实体接入现有邮箱体系。每个 HME 地址在界面、分组、外部 API、领取、分享和邮件查看上都应像当前 Outlook / IMAP 普通邮箱一样独立使用。

本设计覆盖阶段 1 和阶段 2：

- **阶段 1：HME 独立邮箱与共享接收源。** 用户配置一个接收邮箱 IMAP 源，并在该源下导入多个 HME 地址。每个 HME 地址创建为独立 `accounts` 记录，邮件读取时通过共享接收源 IMAP 拉取并按 HME 地址过滤。
- **阶段 2：同步已有 HME 地址。** 用户可选配置 iCloud Cookie，系统通过从 `hidemyemail-generator` 迁移的 HME 列表逻辑同步已有 HME 地址到本项目。

本设计不覆盖 HME 地址生成、停用、恢复和删除。这些能力依赖 Apple 未公开的网页接口，后续单独设计。

## 背景依据

Apple 官方资料确认 HME 地址会转发到用户选择的个人邮箱。Apple 还提供 iCloud Mail 的 IMAP 设置：`imap.mail.me.com:993`，需要 app-specific password。Apple 没有公开 HME 管理 API，`maildomainws`、`/v1/hme/generate`、`/v1/hme/reserve`、`/v2/hme/list` 属于网页端内部接口，稳定性不能等同官方公开 API。

`hidemyemail-generator` 的相关实现为本项目提供了可复用参考：

- `InboxConfig` 保存接收邮箱 IMAP 配置：host、port、username、password、folder、use_ssl。
- 本地收件台通过 IMAP 登录接收邮箱，按 UID 拉取邮件。
- 邮件归属通过 `To`、`Delivered-To`、`X-Original-To`、`Envelope-To`、`Apparently-To`、`Original-Recipient`、`Resent-To`、`Cc` 和正文中的 HME 地址匹配。
- `sync-hme` 通过 Cookie、region 和 maildomain host 调用 HME list 接口，同步已有 HME 地址。

参考资料：

- Apple HME iPhone 用户指南：`https://support.apple.com/guide/iphone/create-and-manage-hide-my-email-addresses-iphcb02e76f7/ios`
- Apple HME Mac 用户指南：`https://support.apple.com/guide/mac-help/use-hide-my-email-mchle62f7f45/mac`
- iCloud Mail IMAP 设置：`https://support.apple.com/en-us/102525`
- Apple app-specific password：`https://support.apple.com/en-us/102654`
- 本地参考项目：`D:\WorkSpace\code\full-stack-workspace\hidemyemail-generator`

## 核心决策

采用方案 B：每个 HME 地址是 `accounts` 独立记录，但共享一个 HME 接收源配置。

### 为什么不是账号别名

HME 地址必须支持完整邮箱功能：独立分组、独立邮件查看、独立外部 API 查询、独立领取、独立分享。别名模型更适合「多个地址指向同一个账号」的查询回退，不适合把每个地址作为完整邮箱实体管理。

### 为什么不让用户填写 `source_id`

`source_id` 是内部数据库主键，不是用户概念，也不是 Apple 返回的 ID。用户创建 HME 接收源时，系统自动生成 source ID；用户在该源下导入 HME 地址时，系统自动绑定。

用户导入 HME 地址时不支持 `hme@icloud.com----source_id`。正确格式为：

```text
abc@icloud.com
def@icloud.com----备注
```

如果只有一个 HME 接收源，纯邮箱导入默认绑定到该源。如果当前界面已经选中某个源，导入默认绑定当前源。如果没有源，前端应先引导用户创建源。

### HME 地址唯一性

HME 地址全局唯一。系统不允许同一个 HME 地址对应多个 `accounts` 记录。

- 导入时发现同一 source 下已存在：更新备注、标签或状态，不重复创建。
- 导入时发现其他 source 下已存在：跳过并返回冲突明细。
- 外部 API 使用 `email=<hme>` 时必须稳定解析到唯一邮箱。

## 数据模型

### 新增表：`icloud_hme_sources`

```sql
CREATE TABLE IF NOT EXISTS icloud_hme_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'global',
    receiver_email TEXT NOT NULL,
    receiver_provider TEXT NOT NULL DEFAULT 'custom',
    receiver_imap_host TEXT NOT NULL,
    receiver_imap_port INTEGER NOT NULL DEFAULT 993,
    receiver_imap_password TEXT NOT NULL,
    receiver_folder TEXT NOT NULL DEFAULT 'INBOX',
    use_ssl INTEGER NOT NULL DEFAULT 1,
    cookie TEXT,
    maildomain_host TEXT,
    last_sync_at TIMESTAMP,
    last_sync_status TEXT DEFAULT 'never',
    last_sync_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

字段说明：

- `name`：用户可读名称，例如「个人 Gmail 接收源」。
- `region`：`global` 或 `china`，用于阶段 2 同步。
- `receiver_*`：接收 HME 转发邮件的真实 IMAP 邮箱配置。它可以是 iCloud Mail、Gmail 或自定义 IMAP。
- `receiver_imap_password`：加密保存，复用现有 `encrypt_data` / `decrypt_data`。
- `cookie`：可选，加密保存。只有阶段 2 同步需要。
- `maildomain_host`：可选，允许从 Cookie 捕获内容或账号检测结果中保存，例如 `p68-maildomainws.icloud.com`。
- `last_sync_*`：记录阶段 2 同步结果。

### 扩展表：`accounts`

新增字段：

```sql
ALTER TABLE accounts ADD COLUMN icloud_hme_source_id INTEGER;
```

HME 邮箱记录约定：

```text
accounts.email = HME 地址
accounts.account_type = 'icloud_hme'
accounts.provider = 'icloud_hme'
accounts.icloud_hme_source_id = icloud_hme_sources.id
accounts.imap_host / imap_port / imap_password = 留空或仅作兼容展示，不保存重复接收源凭据
```

新增 provider 元数据：

```python
MAIL_PROVIDERS["icloud_hme"] = {
    "label": "iCloud Hide My Email",
    "imap_host": "",
    "imap_port": 993,
    "account_type": "icloud_hme",
}
```

HME 仍然使用普通邮箱分组，即 `groups.mailbox_type='account'`。这样分组、排序、批量移动、状态、标签、外部领取和分享都能最大化复用现有普通账号能力。

## 阶段 1：收信与邮件查看

### 邮件列表流程

在现有 `fetch_account_emails(account, folder, skip, top)` 增加分支：

```text
if account.account_type == 'icloud_hme':
    return fetch_icloud_hme_account_emails(account, folder, skip, top)
```

`fetch_icloud_hme_account_emails` 的职责：

1. 根据 `account.icloud_hme_source_id` 读取 HME 接收源。
2. 解密 `receiver_imap_password`。
3. 按 `receiver_imap_host`、`receiver_imap_port`、`use_ssl` 登录接收邮箱。
4. 将本项目的 folder 参数解析为接收邮箱真实 IMAP 文件夹。
5. 使用 UID 搜索和分页拉取候选邮件。
6. 对候选邮件解析头部和正文，只保留归属当前 HME 地址的邮件。
7. 返回与普通 IMAP 邮箱一致的列表结构，包括 `id`、`id_mode='uid'`、`method='imap'`、`folder`、`subject`、`from`、`date`、`body_preview`、`has_attachments` 等。

### HME 地址匹配规则

匹配必须大小写不敏感，优先顺序如下：

1. 已知 HME 地址直接出现在收件相关头部：
   - `To`
   - `Delivered-To`
   - `X-Original-To`
   - `Envelope-To`
   - `Apparently-To`
   - `Original-Recipient`
   - `Resent-To`
   - `Cc`
2. 已知 HME 地址出现在正文或 HTML 清洗后的正文中。
3. 不应仅根据 `@icloud.com` 后缀自动归属到当前邮箱。该规则可以用于发现未知地址，但不能作为当前 HME 邮箱的授权依据。

列表和详情都必须执行归属校验。详情接口即使拿到了 UID，也要重新解析邮件，确认邮件属于当前 HME 地址；否则返回 404 或 403。

### 邮件详情流程

新增 `fetch_icloud_hme_account_detail_response(account, folder, message_id, method, id_mode, proxy_url)`。

流程：

1. 读取接收源并登录 IMAP。
2. 按 UID 优先拉取 `RFC822`。
3. 解析邮件正文、附件元数据和必要头部。
4. 调用同一套 HME 归属校验。
5. 归属成功后，返回与普通 IMAP 详情一致的只读结构。

公开分享场景不暴露原始 MIME、接收源凭据、Cookie、maildomain host 或附件二进制下载能力。

### 本地保留

HME 邮箱应按独立 `account_id` 写入普通邮箱本地保留表。这样每个 HME 的本地缓存、已缓存正文、外部 API 查询和分享刷新互不串扰。

缓存写入时使用 HME 邮箱自己的 `account_id`，而不是 source ID。source 只是凭据和同步配置，不是对外邮箱实体。

## 阶段 1：管理界面

### HME 接收源管理

新增「iCloud HME 源」管理入口，建议放在导入邮箱弹窗或设置页的普通邮箱相关区域。

每个源包含：

- 名称；
- 区域：`global` / `china`；
- 接收邮箱类型：iCloud Mail / Gmail / 自定义 IMAP；
- IMAP 主机、端口、用户名、应用密码或授权码、文件夹、SSL 开关；
- 可选 Cookie；
- 可选 maildomain host；
- 最后同步时间、同步状态、同步错误；
- 「测试 IMAP 连接」；
- 「同步已有 HME 地址」。

接收邮箱预设：

- iCloud Mail：`imap.mail.me.com`，端口 `993`，SSL，密码为 Apple app-specific password。
- Gmail：复用现有 Gmail IMAP 预设。
- 自定义 IMAP：用户填写主机和端口。

### 导入 HME 地址

导入入口应基于当前选择的 HME 接收源。格式：

```text
hme@icloud.com
hme@icloud.com----备注
```

导入行为：

- 如果没有 HME 接收源，先提示创建源。
- 如果只有一个源，默认使用该源。
- 如果有多个源，用户必须在界面选择源，不能通过文本输入 `source_id`。
- 创建 `accounts` 独立记录，继承当前选择的分组、标签、状态、备注。
- 同一个 HME 地址全局只允许存在一次。

### 列表展示

HME 邮箱在账号列表中显示为普通账号卡片：

- 邮箱地址：HME 地址；
- provider pill：`iCloud HME`；
- 刷新按钮：刷新当前 HME 的邮件；
- 分享按钮：复用普通账号分享；
- 编辑按钮：编辑备注、标签、状态、分组和所属 HME 源；
- 不显示 Outlook Token 刷新入口。

批量操作复用普通账号能力。对于不适用于 HME 的操作（例如刷新 Outlook Token），按钮应禁用或按现有 IMAP 账号逻辑隐藏。

## 阶段 1：外部 API 与领取

HME 地址作为 `resource_type='account'` 暴露，不新增第三种外部资源类型。

### `/api/external/accounts`

返回 HME 邮箱时包含：

```json
{
  "resource_type": "account",
  "account_type": "icloud_hme",
  "provider": "icloud_hme",
  "email": "abc@icloud.com",
  "group_id": 1
}
```

不得返回：

- 接收源 IMAP 密码；
- iCloud Cookie；
- maildomain host；
- proxy 配置；
- 任何源级敏感配置。

### `/api/external/emails`

`email=<hme-address>` 解析到该 HME 的 `accounts` 记录后，走 HME 邮件列表分支。`folder=all` 继续按现有语义聚合支持的文件夹。

### `/api/external/verification-code`

验证码接口复用现有提取策略，但详情读取走 HME 详情分支。接口仍只返回验证码和命中邮件元信息，不返回完整正文、凭据、Cookie、原始 MIME 或附件。

### 领取、完成和释放

HME 作为普通账号参与现有领取链路：

- `POST /api/external/mailboxes/claim`
- `POST /api/external/mailboxes/complete`
- `POST /api/external/mailboxes/release`

返回的 `resource_type` 仍为 `account`。完成后可移动到目标普通邮箱分组。

## 阶段 1：对外分享

HME 邮箱复用 `account_shares`：

- `POST /api/accounts/<account_id>/shares`
- `GET /shared/<token>`
- `GET /api/shared/<token>`
- `GET /api/shared/<token>/messages`
- `GET /api/shared/<token>/messages/<message_id>`
- `POST /api/shared/<token>/refresh`

分享要求：

- 公开信息显示 HME 邮箱地址和 provider label。
- 邮件列表只返回属于该 HME 地址的邮件。
- 邮件详情必须重新做 HME 归属校验。
- 公开刷新按现有账号分享节流。
- 不暴露接收源 IMAP、Cookie、maildomain host、原始 MIME 或附件下载能力。
- HTML 正文仍由前端 `DOMPurify.sanitize` 清洗。

## 阶段 2：同步已有 HME 地址

### 同步入口

在 HME 接收源管理中提供「同步已有 HME 地址」按钮。同步依赖该 source 的：

- `cookie`；
- `region`；
- 可选 `maildomain_host`。

如果未配置 Cookie，按钮应提示用户先配置 Cookie。阶段 1 收信不依赖 Cookie。

### 同步流程

1. 解密 source 的 Cookie。
2. 根据 `region` 选择默认 web origin 和默认 maildomain host：
   - `global`：`www.icloud.com`，默认 `p68-maildomainws.icloud.com`；
   - `china`：`www.icloud.com.cn`，默认 `p217-maildomainws.icloud.com.cn`。
3. 如果 source 保存了 `maildomain_host`，优先使用该 host。
4. 复用 `hidemyemail-generator` 的请求头和 `GET /v2/hme/list` 逻辑获取 HME 列表。
5. 对 `result.hmeEmails` 逐条处理：
   - 有 `hme` 且不存在：创建 `accounts` HME 记录，绑定当前 source；
   - 已存在且属于当前 source：更新备注、标签和状态映射；
   - 已存在但属于其他 source：跳过并记录冲突；
   - `isActive=false`：不删除，标记为 `inactive` 或写入备注，避免误删历史邮箱。
6. 更新 source 的 `last_sync_at`、`last_sync_status` 和 `last_sync_error`。

### 同步结果

同步接口返回：

```json
{
  "success": true,
  "created": 10,
  "updated": 3,
  "inactive": 2,
  "conflicts": [
    {
      "email": "abc@icloud.com",
      "existing_source_id": 1,
      "current_source_id": 2
    }
  ]
}
```

同步失败时不影响已有 HME 邮箱收信。失败信息保存到 source 并返回给前端。

## 错误处理

- 接收源不存在：返回「iCloud HME 接收源不存在」。
- 接收源 IMAP 登录失败：返回 provider block 或 auth error，保留现有账号列表状态。
- 文件夹不存在：返回可用文件夹列表，复用现有 IMAP 文件夹解析错误结构。
- HME 地址未命中任何邮件：返回空列表，不视为错误。
- 详情归属校验失败：返回 404 或 403。
- Cookie 失效：阶段 2 同步失败并写入 `last_sync_error`，不影响阶段 1 收信。
- HME 地址冲突：导入或同步时跳过冲突项，返回详细冲突列表。

## 安全要求

- `receiver_imap_password` 和 `cookie` 必须加密保存。
- 所有 API 响应不得返回接收源密码、Cookie、maildomain host 或代理配置。
- 公开分享保持只读。
- 详情接口必须校验邮件归属，不能只凭 UID 返回共享接收邮箱中的任意邮件。
- 日志中不得打印 Cookie、IMAP 密码或完整请求头。
- 导出功能如包含 HME，应只导出 HME 地址和业务元数据，不导出 source 凭据。

## 测试计划

### 单元测试

- `tests/test_icloud_hme_sources.py`
  - 创建、编辑、删除 HME source；
  - source 密码和 Cookie 加密保存；
  - 删除 source 时禁止留下悬挂 HME 账号，或要求先迁移/删除。

- `tests/test_icloud_hme_import.py`
  - `hme@icloud.com` 导入成功；
  - `hme@icloud.com----备注` 导入成功；
  - 不支持 `----source_id`；
  - 同一 HME 全局唯一；
  - 跨 source 冲突返回明细。

- `tests/test_icloud_hme_mail_fetch.py`
  - 按 `Delivered-To` 匹配；
  - 按 `X-Original-To` 匹配；
  - 按正文 fallback 匹配；
  - 不匹配的共享接收邮箱邮件不返回；
  - 详情接口对非归属 UID 返回 404/403。

- `tests/test_icloud_hme_external_api.py`
  - `/api/external/accounts` 返回 `account_type=icloud_hme`；
  - `/api/external/emails?email=<hme>` 只返回该 HME 邮件；
  - `/api/external/verification-code` 能从 HME 邮件详情提取验证码；
  - API 响应不暴露 source 凭据。

- `tests/test_icloud_hme_share.py`
  - HME 账号可创建分享；
  - 分享列表和详情只读；
  - 公开刷新节流；
  - 公开响应不暴露 source 凭据。

- `tests/test_icloud_hme_sync.py`
  - mock HME list 响应创建新账号；
  - 更新已有同 source HME；
  - 跨 source 冲突；
  - Cookie 失效写入 `last_sync_error`。

### 集成验证

完成实现后运行：

```bash
python -m pytest tests/test_icloud_hme_sources.py tests/test_icloud_hme_import.py tests/test_icloud_hme_mail_fetch.py tests/test_icloud_hme_external_api.py tests/test_icloud_hme_share.py tests/test_icloud_hme_sync.py -q -p no:cacheprovider
python -m pytest tests/test_account_share.py tests/test_external_verification_code_api.py tests/test_external_mailbox_claim.py -q -p no:cacheprovider
python -m compileall web_outlook_app.py outlook_web
```

由于本功能影响运行时邮件读取、外部 API、分享和数据库迁移，完成实现后还需要执行本项目规定的本地 HTTP smoke test。若本地 `.env` 有 `EXTERNAL_API_KEY`，使用：

```bash
python scripts/e2e_external_api_smoke.py --group-ids 1,2,49,50 --claim-group-id 49
```

## 非目标

- 不在阶段 1 / 2 实现 HME 地址生成。
- 不在阶段 1 / 2 实现 HME 停用、恢复或永久删除。
- 不新增第三种外部 `resource_type`。
- 不让用户在导入文本中填写 `source_id`。
- 不把 HME 地址作为 `account_aliases` 实现。

## 规格自检结果

- 无占位符、未完成章节或待定项。
- `source_id` 已限定为内部外键，用户导入格式不包含 `source_id`。
- HME 作为 `accounts` 独立记录，与分组、分享、外部 API、领取模型一致。
- 阶段 1 收信不依赖 Cookie；阶段 2 同步依赖 Cookie，并明确标记为 Apple 非公开网页接口能力。
- 本规格范围可由一份实现计划覆盖，不包含生成、停用、恢复等后续能力。
