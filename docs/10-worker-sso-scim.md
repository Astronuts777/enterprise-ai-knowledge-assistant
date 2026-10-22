# 后台文件处理与企业身份接入

本轮实现本地可运行的异步处理，以及可选 OIDC / SCIM 接口。不购买服务器、模型 API 或身份服务。
协议测试使用测试签名密钥与模拟身份端点；**尚未完成真实企业 IdP、PostgreSQL 并发及真实 ClamAV 的联调验收**。

## 1. 升级与启动

先停止自己的 Web/API/Worker，按 `09-closed-pilot-readiness.md` 做加密备份。不要删除或重新生成旧主密钥。
在仓库根目录执行：

```bash
.venv/bin/python -m pip install -r requirements.lock
pnpm build:local-ai
.venv/bin/python scripts/local_ai.py start
```

`start` 现在启动 Ollama、Web/API、Worker 三个子进程，统一使用 `data/local/ollama` 的数据库、主密钥和加密原件。
不会自动下载模型；任一子进程退出时，启动器停止本次启动的其他子进程，不接管已有服务。
本地 SQLite 自动升级至 `20261022_0012`；PostgreSQL 必须先按部署流程执行 Alembic 升级。
迁移保留原文档、文本块、成员和权限；新增表启用 PostgreSQL 强制租户 RLS。
这次迁移不提供破坏性降级：回退需要用验证过的升级前备份恢复整套实例。

调试时也可分别运行（不要再同时运行 `start`）：

```bash
.venv/bin/python scripts/local_ai.py model
# 另一个终端
.venv/bin/python scripts/local_ai.py web --async-processing
# 第三个终端：等 Web 完成数据库迁移后启动
.venv/bin/python scripts/local_ai.py worker
```

`web` 不带 `--async-processing` 仍使用同步模式，兼容原来的双终端流程。通用部署以
`DOCUMENT_PROCESSING_MODE=async` 开启队列，使用相同数据库、密钥、存储和扫描/模型配置运行：

```bash
export PYTHONPATH=services/api/src:services/worker/src:packages/core/src
.venv/bin/python -m enterprise_ai_worker.main
# 只跑一个轮询周期（无任务时正常退出）
.venv/bin/python -m enterprise_ai_worker.main --once
```

PostgreSQL Worker 还需设置 `WORKER_TENANT_IDS` 为逗号分隔的企业 UUID。只处理明确列出的企业，
不会绕过 RLS。新增企业需更新此配置并重启 Worker。不要将异步模式用于没有常驻 Worker 的 Render 单服务演示。

## 2. 文件生命周期

上传权限检查 → 加密保存原件与持久队列 → Worker 领取租约 → 解密/完整性验证 → 扫描 → 解析/OCR → 索引 → 待发布。

- 异步模式下，未扫描原件仅作为加密隔离对象保存，不会提供下载或进入问答；扫描后才允许解析。
- 网页显示“后台排队中/后台处理中/待发布/处理失败”，在文档页每 5 秒刷新。
- `FILE_SCAN_BACKEND=disabled` **不等于扫描成功**。启用扫描见前一章；扫描不可用时会重试并保持不可发布。
- Worker 每个文件最多自动处理 3 次，暂时失败退避 30、60 秒；模型已启用但不可用时，不把缺失索引伪装成完成。
- 任务租约 15 分钟；崩溃后租约到期可接管，旧任务结果不能覆盖新结果。单次处理超过租约不会提交，需要调小文件或改善本机性能。
- 文件页提供取消和手动重试；检测到恶意文件的版本不能直接重试，应清理源文件后上传新版本。
- 取消不是立即杀死解析/模型调用，也不删除加密原件；正在执行的结果会被丢弃，不会发布。
- 每次重试重新检查上传者权限；上传者被停用/撤权后不会完成入库。处理成功仍需管理员检查内容并手动发布。
- 任务可追踪：`GET /v1/tenants/{tenant}/document-versions/{version}/job`；管理操作是末尾加 `/retry` 或 `/cancel` 的 POST。
  仅具有该空间文档管理权限的成员可调用，操作写入审计。旧同步版本没有任务，需上传新版本。

当前是小规模数据库队列，不是高吞吐分布式导入系统。尚无批量导入、资源隔离的解析沙箱、长期任务心跳或死信告警。
备份/恢复时同时停止 Worker，避免恢复副本继续处理任务；恢复后的运行中任务需等待旧租约过期。

## 3. SSO 的配置与边界

使用 **OIDC Authorization Code + PKCE S256**，支持 RS256 签名，检查 issuer、audience、azp、nonce、有效期与已验证邮箱。
服务端保存加密 PKCE verifier；state 绑定浏览器 HttpOnly Cookie，五分钟有效且一次性消费。
应用不保存 IdP access/refresh token。令牌/JWKS 请求不跟随跳转、不使用环境代理，限制超时和响应大小。
端点必须使用配置 issuer 的同一 HTTPS 源。暂不支持 SAML、跨源端点、非 RS256、`private_key_jwt`、前/后通道单点退出。
客户端认证当前为 `client_secret_post`；选择身份服务时须确认兼容。

先通过原账号方式建立企业并保存一个受保护的本地管理员作为应急账号。复制
`infra/sso.providers.example.json` 到受控的本地路径（例如忽略版本管理的 `data/local/sso.providers.json`），修改：

1. `tenant_id`：现有企业 UUID，不是企业名。
2. `issuer`、三个端点、`client_id`：来自管理员控制的身份系统配置，不接受用户登录时提供任意 URL。
3. `key`：稳定、唯一的英文小写标识，例如 `company`；绑定后不要指向另一个 issuer。
4. `client_secret_env` 与 `scim_token_env`：只写环境变量名称，不把密钥写入配置示例或 Git。

配置 API 进程环境变量（项目不会自动加载 `.env`）：

```bash
export SSO_CONFIG_FILE=/absolute/path/to/data/local/sso.providers.json
export ACCOUNT_AUTH_MODE=true
export APP_PUBLIC_URL=https://knowledge.your-company.example
# 在服务管理器的私密环境中设置 COMPANY_OIDC_CLIENT_SECRET、COMPANY_SCIM_TOKEN。
# SCIM token 至少 32 个字符，使用密码管理器生成随机值；不要发到聊天或提交到 Git。
```

在 IdP 的客户端配置中，注册**精确**回调 URL：

```text
https://knowledge.your-company.example/v1/auth/sso/company/callback
```

配置有效后重启 API，登录页会显示企业单点登录按钮；未配置时保持原来的账号登录。
纯本机实验可在 `APP_ENV=development` 下显式设置 `SSO_ALLOW_LOOPBACK_HTTP=true`，
使用数字回环地址的 IdP 与 `APP_PUBLIC_URL=http://127.0.0.1:8201`；生产禁止此例外。
本地启动器仅绑定回环地址，不自动提供企业内网服务。企业已有服务器/域名可单独部署；不为此购买资源。

可以用企业已有 IdP 或在已有设备上自托管 OIDC 服务。示例端点采用 Keycloak 风格，**不表示 Keycloak 原生带 SCIM 推送**；
用户同步需要身份平台的 SCIM 客户端、企业已有连接器，或管理员调用下面的接口。没有 IdP 时无需启用这部分。
不要为了试用本功能购买身份平台套餐。

### 账号与 MFA 安全规则

- 必须先经 SCIM 预配用户；`externalId` 必须精确等于 OIDC `sub`，大小写敏感，`userName` 必须与已验证的邮箱一致。
- 不按相同邮箱自动关联旧本地账号，不自动加入企业；冲突返回 409。现有账号保留原登录方式。
- 新用户只有 `member` 角色，无任何空间权限。管理员通过现有成员/空间页面或授权 API 分配必要权限；IdP 不能传管理员角色。
- SSO 不绕过本地 MFA。已启用 TOTP 的账号仍需验证码/恢复码。SSO-only 用户可在成功 SSO 登录五分钟内设置 MFA；
  更新恢复码/关闭 MFA 同时需要最近登录和有效 TOTP/恢复码。企业强制策略继续生效。
- SSO-only 用户不能通过本地“忘记密码”另设密码来绕过 IdP；登录故障应由身份管理员处理。
- OIDC 无法主动获知 IdP 离职事件：必须配置 SCIM 停用；否则现有应用会话在到期/撤销前仍可能有效。
- 通用 Uvicorn 部署建议关闭含查询串的访问日志（`--no-access-log`），使用应用已脱敏的路径/请求 ID 日志；
  反向代理也不要记录 OAuth 回调查询参数、Cookie、Authorization 头或请求体。

## 4. SCIM 2.0 支持范围

服务基址：`https://<站点>/scim/v2/company`。每个 provider 使用自己的 Bearer token，只作用于绑定企业。
服务接口不接受普通用户 Cookie 代替同步凭证。令牌轮换后新请求立即使用新环境值（常规部署需重启进程）。

| 接口 | 实现 |
| --- | --- |
| `ServiceProviderConfig`、`Schemas`、`ResourceTypes` | 能力与模式发现 |
| `Users` | 创建、单条读取、分页列表、PUT、PATCH `active/userName`、DELETE |
| `Groups` | 创建、读取、分页、PUT、PATCH `displayName/members`、DELETE；每组最多 500 个用户，无嵌套组 |
| 过滤 | 用户 `userName eq` / `externalId eq`；组 `displayName eq` / `externalId eq` |
| 分页与限制 | `startIndex` 从 1 起；`count` 0–100；请求体最大 64 KiB，PATCH 最多 20 个操作 |

不支持 Bulk、排序、ETag 条件更新、密码同步、任意复杂过滤或企业扩展属性。组同步是目录同步，
**不自动映射角色/空间权限**，避免同步意外扩大访问范围。管理员继续管理现有空间 ACL。
这是一套明确限制的 SCIM 实现，不代表已经通过所有商业 IdP 的兼容性认证；接入前需按下面清单联调。

创建用户的请求内容（由身份平台或管理员客户端发送，Bearer token 放请求头，不写入文件）：

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
  "externalId": "EXACT_OIDC_SUBJECT",
  "userName": "member@example.com",
  "active": true
}
```

停用：`PATCH /Users/{返回的SCIM资源id}`：

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [{"op": "replace", "path": "active", "value": false}]
}
```

管理员手动停用 `active` 与身份同步状态 `provisioning_active` 分开保存，两者都启用才可访问。
SCIM 重新启用不能撤销管理员的手动停用；管理员恢复也不能绕过 IdP 停用。SCIM 用户修改撤销该账号全部设备会话和未完成的认证令牌。
DELETE 保留审计/文档作者记录，但撤销访问、隐藏旧目录资源并释放登录标识；重新创建是全新成员，不继承旧角色/空间授权。
组删除只移除同步目录组，不删除用户、文件或人工空间授权。

## 5. 接入验收清单

- [ ] 正常 SSO、错误签名/issuer/audience、未预配账号、邮箱不匹配、state 过期/重放均符合预期。
- [ ] 新成员看不到任何文档；授予指定空间后仅能阅读该空间；不能通过组名取得管理员角色。
- [ ] SCIM 停用后旧 Cookie 与新 SSO 登录都失效；重新启用不会撤销手动停用。
- [ ] MFA 强制启用、SSO 后 TOTP、恢复码、全员离职与应急管理员恢复完成演练。
- [ ] 实际 IdP 验证用户/组增删改、externalId 到 sub 的映射、分页/过滤及属性支持范围。
- [ ] 异步上传后关闭并重启 Worker，任务仍能继续；扫描/模型离线时不可发布；取消后结果不入库。
- [ ] 真实 PostgreSQL RLS 与并发 Worker、备份恢复、数据保留/删除策略与值守告警完成验收。

目前这些真实环境验收没有自动视为通过。仓库自动化测试不替代企业试点与安全评审。
