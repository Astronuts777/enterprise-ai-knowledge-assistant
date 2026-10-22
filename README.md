# Enterprise AI Knowledge Assistant

面向不同企业的可信知识库与 AI 决策证据平台。它不绑定某个确定行业，而是用统一的企业、成员、
知识空间、文档版本、权限和引用模型，为不同组织建立自己的知识工作台。

- **公开演示**：[enterprise-ai-knowledge-assistant-y39d.onrender.com](https://enterprise-ai-knowledge-assistant-y39d.onrender.com)
- **API 文档（本地）**：[http://localhost:8000/docs](http://localhost:8000/docs)
- **Web（本地）**：[http://localhost:3000](http://localhost:3000)

> 公开地址是免费演示环境，不是正式生产系统。请勿上传真实机密资料。

## 这个 Web 可以做什么

当前版本已经打通一条可操作的企业知识闭环：

1. 使用邮箱和密码注册，创建独立企业工作区，创建者自动成为企业管理员。
2. 通过验证邮件激活邮箱；支持忘记密码和限时、一次性的安全重置链接。
3. 由管理员向指定邮箱发送限时、一次性邀请，成员不能通过输入企业名称自行加入。
4. 按部门、项目或业务主题创建知识空间，并设置内部、机密或严格受限等级。
5. 为受邀成员分配企业角色，并为每个知识空间单独授予只读者或管理员权限。
6. 上传并解析不超过 10 MB 的 PDF、DOCX、TXT 和 Markdown 文件。
7. 预览解析结果，发布可检索版本，并为同一文档上传后续版本。
8. 点击文档打开安全阅读器；空间查看者只能读取已发布版本，空间管理者可以预览待发布版本。
9. 只针对当前用户获准访问的已发布资料提问，得到带文件名、版本、页码或段落位置的证据式回答。
10. 可选启用语义向量混合检索和外部生成模型；模型引用会由服务端校验并重新编号。
11. 在资料不足时明确拒答，不用没有来源的内容补全答案。
12. 保存当前用户自己的问答历史，并阻止跨企业、跨空间读取数据。
13. 由企业管理员查看身份、权限、文档阅读和问答操作的追加式审计日志。
14. 在“账号安全”中启用身份验证器双重验证、更新一次性恢复码，并查看或撤销登录设备。
15. 由企业管理员强制要求管理员或全部成员启用 MFA；未满足策略的账号不能读取企业资料。

适合用来验证以下场景：

- 公司制度、员工手册和标准流程查询；
- 项目文档、交付材料和会议纪要整理；
- 产品、运营、客服或销售知识库；
- 对结论来源、版本和证据链有要求的内部问答；
- 跨行业企业知识助手的产品原型和用户测试。

### 当前能力边界

- 默认问答使用透明、可重复的本地关键词检索和原文摘录，不向外部模型发送资料；管理员可选配置语义嵌入和生成模型。
- 文档解析在 API 请求内同步完成；Worker 当前只有可测试的任务边界，尚未处理异步任务。
- 公网账号模式使用服务端会话和 HttpOnly Cookie；本地默认仍可用 `X-Dev-User-Id` 快速调试。
- 邮箱验证、邀请邮件和密码重置已经接入 SMTP 适配器；公开演示环境尚未配置邮件服务，因此暂不强制邮箱验证，邀请仍会返回可手动发送的链接。
- 登录按账号和网络来源进行数据库级限流；支持 TOTP 身份验证器双重验证、一次性恢复码、恢复码轮换、验证码防重放和限时 MFA 挑战；企业可对管理员或全员强制执行 MFA。用户可查看并撤销登录设备，企业管理员可强制成员下线。企业 SSO 和异常检测尚未接入。
- 当前语义检索在授权后的加密向量候选集中由应用计算，适合现阶段规模；数据库内 pgvector 检索仍在路线图中。
- SCIM、OCR、云盘连接器、决策证据包、审批和审计导出仍在路线图中。
- Render 免费环境的数据库和文件存储不适合作为正式企业数据基础设施。

## 产品原则

- **有证据**：回答必须关联原始文档、不可变版本和稳定位置。
- **有权限**：检索和回答必须在企业与角色边界内完成。
- **可追溯**：保存数据来源、文档版本、问题、答案和引用快照。
- **诚实拒答**：证据不足时返回资料不足，不生成伪确定结论。
- **人工负责**：AI 提供证据，不替代高风险业务决定的负责人。
- **行业无关**：核心数据结构不绑定行业，差异通过配置和知识内容扩展。

## 系统结构

```mermaid
flowchart LR
    U[企业用户] --> W[Next.js Web]
    W --> A[FastAPI]
    A --> P[身份与权限检查]
    P --> D[(SQLite 或 PostgreSQL)]
    A --> F[文件存储]
    A --> R[权限感知混合检索]
    R --> D
    R -. 可选 .-> M[模型适配器]
    R --> C[回答与不可变引用]
    K[Worker 边界] -. 后续异步解析 .-> A
```

当前部署把静态导出的 Next.js Web 和 FastAPI 放在同一个 Docker 服务中，对外只暴露一个
HTTPS 地址。生产目标架构会将 Web、API、Worker、PostgreSQL、对象存储和模型适配器拆分。

### 技术栈

| 层级 | 当前实现 |
| --- | --- |
| Web | Next.js 16、React 19、TypeScript |
| API | FastAPI、Pydantic、Uvicorn |
| 数据访问 | SQLAlchemy 2、Alembic |
| 本地数据库 | 持久化 SQLite |
| 部署数据库 | PostgreSQL，包含行级安全策略 |
| 文档解析 | pypdf、python-docx、纯文本解析 |
| 包管理 | pnpm 11、Python 锁定依赖 |
| 部署 | Docker、Render Blueprint |

## 仓库结构

```text
apps/web/                         Next.js 企业知识工作台
services/api/                     FastAPI、权限、文档和问答接口
services/worker/                  后续异步解析与索引 Worker 边界
packages/core/                    共享权限与领域逻辑
migrations/                       Alembic 数据库迁移
infra/                            本地 PostgreSQL 与 MinIO
docs/                             产品、架构、用户故事和 ADR
.run/                             PyCharm 运行配置
Dockerfile                        公网部署镜像
render.yaml                       Render Web 与 PostgreSQL Blueprint
requirements.lock                锁定的 Python 依赖
pnpm-lock.yaml                    锁定的前端依赖
```

## 完整开发流程

### 1. 准备环境

建议使用：

- Python 3.12–3.14；
- Node.js 22；
- pnpm 11.25.0；
- Git；
- Docker Desktop（仅在使用 PostgreSQL 和 MinIO 时需要）。

如果终端提示 `pnpm: command not found`：

```bash
corepack enable
corepack prepare pnpm@11.25.0 --activate
pnpm --version
```

如果安装的 Node.js 没有 Corepack，可以改用：

```bash
npm install --global pnpm@11.25.0
```

### 2. 获取代码

```bash
git clone https://github.com/Astronuts777/enterprise-ai-knowledge-assistant.git
cd enterprise-ai-knowledge-assistant
```

### 3. 安装依赖

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
pnpm install --frozen-lockfile
```

Windows PowerShell 中，Python 路径通常是 `.venv\Scripts\python.exe`。

### 4. 启动本地开发环境

终端一启动 API：

```bash
PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/uvicorn enterprise_ai_api.main:app --reload --host 127.0.0.1 --port 8000
```

终端二启动 Web：

```bash
pnpm dev:web
```

然后打开：

- Web：`http://localhost:3000`
- Swagger API：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/healthz`

Web 默认连接 `http://localhost:8000`。如需修改地址：

```bash
cp apps/web/.env.example apps/web/.env.local
```

再修改 `apps/web/.env.local` 中的 `NEXT_PUBLIC_API_URL`。

#### 本地验证正式账号模式

先生成并妥善保存一个本地加密密钥：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

使用该输出分别启动 API 和账号版 Web：

```bash
APP_ENV=development ACCOUNT_AUTH_MODE=true ENCRYPTION_MASTER_KEY='<刚生成的密钥>' \
  PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/uvicorn enterprise_ai_api.main:app --reload --host 127.0.0.1 --port 8000

NEXT_PUBLIC_APP_MODE=accounts pnpm dev:web
```

同一个本地数据库必须持续使用同一密钥；密钥变化后，之前加密的数据将无法解密。

### 5. 使用 PyCharm 启动

1. 用 PyCharm 打开仓库根目录。
2. 将 Python Interpreter 设为仓库中的 `.venv/bin/python`。
3. 在运行配置中选择 **Local: Web + API**。
4. 点击运行后访问 `http://localhost:3000`。

也可以分别运行 **API: FastAPI** 和 **Web: Dev**。`Worker` 输出 `No jobs available` 后正常退出，
代表当前没有异步任务，不是网站启动失败。

### 6. 完成一次本地业务验证

建议每次功能开发后走一遍最短主链路：

1. 创建一个企业。
2. 创建知识空间。
3. 上传一份 TXT 或 Markdown 文档。
4. 查看解析结果并发布版本。
5. 在 AI 决策助手中提出文档能够回答的问题。
6. 检查回答是否包含文件、版本、位置和原文片段。
7. 再询问一个资料中不存在的问题，确认系统明确拒答。

### 7. 运行质量检查

提交代码前执行：

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest
pnpm lint:web
pnpm test:web
pnpm build:web
```

当前测试会启用 SQLite 外键约束，覆盖企业、成员、空间、文档版本、文本块、问答、引用和跨租户边界。

### 8. 建议的代码提交过程

```bash
git switch -c codex/feature-name
# 修改代码并运行全部质量检查
git status
git diff --check
git add <相关文件>
git commit -m "feat: describe the change"
git push -u origin codex/feature-name
```

创建 Pull Request 后先检查测试结果和部署预览，再合并到 `main`。紧急演示修复也应补回归测试，
避免只修复线上现象而没有覆盖数据库约束。

## 数据库与文件存储

### 加密策略

- 密码使用带随机盐的 scrypt 单向哈希，数据库中没有可还原的明文密码。
- TOTP 密钥使用 AES-256-GCM 加密保存，恢复码只保存带密钥单向摘要；密码重置不会绕过已启用的双重验证。
- 邮箱使用 AES-256-GCM 加密保存，另用带密钥 HMAC 摘要完成精确账号查询。
- 文档解析正文、问题、回答和引用摘录在进入数据库前使用 AES-256-GCM 认证加密。
- 语义嵌入向量在进入数据库前同样使用 AES-256-GCM 认证加密，数据库不会保存可直接读取的向量。
- 上传原件只在内存中完成首轮解析，落盘前加密为 `payload.enc`，不保留明文原件。
- 文件重复检测使用带密钥内容摘要，避免公开 SHA-256 暴露已知文件是否存在。
- 数据库中的 UUID、角色、状态和时间等索引元数据保持明文，以支持授权、关联和运维查询。
- PostgreSQL 或磁盘的整盘加密由部署平台负责；应用层加密是额外保护，不能替代数据库备份和访问控制。

`ENCRYPTION_MASTER_KEY` 必须是部署平台生成并安全保存的高熵密钥。不要把它写入 Git；不要随意更换或删除，
否则已有密文和加密文件将无法读取。正式轮换需要先增加密钥版本和重加密流程。

### 默认 SQLite 模式

不设置 `DATABASE_URL` 时：

- 数据库：`data/local/enterprise_ai.db`
- 上传文件：`data/local/uploads/`
- API 启动时自动执行 Alembic 迁移；
- 两个位置都被 Git 忽略。

备份时先停止 API，再复制整个 `data/local/`。需要全新环境时，将该目录移动到备份位置后重启 API。

### PostgreSQL 与 MinIO 持久对象存储模式

安装 Docker 后启动依赖：

```bash
docker compose -f infra/docker-compose.yml up -d
```

Compose 会自动创建私有的 `enterprise-ai-documents` 存储桶。执行迁移并启动 API：

```bash
MIGRATION_DATABASE_URL='postgresql+psycopg://app_owner:local-development-only@localhost:5432/enterprise_ai' \
  .venv/bin/alembic upgrade head

DATABASE_URL='postgresql+psycopg://app_runtime:local-development-only@localhost:5432/enterprise_ai' \
  OBJECT_STORAGE_BACKEND=s3 \
  OBJECT_STORAGE_ENDPOINT='http://localhost:9000' \
  OBJECT_STORAGE_ACCESS_KEY='enterprise-ai-local' \
  OBJECT_STORAGE_SECRET_KEY='local-development-only' \
  OBJECT_STORAGE_BUCKET='enterprise-ai-documents' \
  OBJECT_STORAGE_REGION='us-east-1' \
  OBJECT_STORAGE_FORCE_PATH_STYLE=true \
  PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/uvicorn enterprise_ai_api.main:app --reload
```

PostgreSQL 迁移会启用并强制执行租户行级安全。应用层仍执行显式授权，形成应用和数据库两层隔离。
上传对象在进入 MinIO 前已经由应用使用 AES-256-GCM 加密；存储桶必须保持私有。数据库只保存稳定的
对象键，不保存供应商 URL，因此正式环境可以使用 AWS S3、Cloudflare R2、Backblaze B2 或其他
S3 兼容服务，而不改变业务数据模型。

### 正式环境对象存储切换

1. 在对象存储服务商创建私有存储桶和最小权限服务账号。
2. 权限只授予目标存储桶的读取、写入和删除对象能力，不授予公开访问或账号管理权限。
3. 在部署平台设置 `OBJECT_STORAGE_BACKEND=s3` 以及下表中的 S3 参数；密钥只存于平台 Secret。
4. 先在测试环境上传文件，确认数据库 `object_key` 对应的对象存在且内容以 `EAKA1` 加密头开头。
5. 再切换生产环境。切换前已有的本地对象必须先复制到目标存储桶，并保持对象键不变。

应用在 S3 配置缺失时拒绝启动；上传失败时返回 503 并回滚数据库，数据库提交失败时会尽力删除
刚写入的对象。对象存储不可用不会产生一个“数据库显示成功但原件不存在”的新文档版本。

## 环境变量

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `APP_ENV` | 环境标识 | `development` |
| `ACCOUNT_AUTH_MODE` | 启用邮箱密码账号、服务端会话和企业邀请 | `false` |
| `APP_PUBLIC_URL` | 验证、重置和邀请邮件中的 Web 根地址 | `http://localhost:3000` |
| `REQUIRE_EMAIL_VERIFICATION` | 注册后是否必须验证邮箱才能登录 | `false` |
| `EMAIL_DELIVERY_BACKEND` | 邮件后端：`disabled` 或 `smtp` | `disabled` |
| `SMTP_HOST` / `SMTP_PORT` | SMTP 服务地址和端口 | 无 / `587` |
| `SMTP_FROM_EMAIL` | 验证、重置和邀请邮件的发件地址 | 无 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 可选 SMTP 登录凭据 | 无 |
| `SMTP_USE_TLS` / `SMTP_USE_SSL` | STARTTLS 或隐式 TLS；不能同时启用 | `true` / `false` |
| `ENCRYPTION_MASTER_KEY` | 数据字段、令牌摘要和上传文件的主密钥 | 本地使用开发密钥；公网必须设置 |
| `DATABASE_URL` | API 运行数据库 | 本地 SQLite |
| `MIGRATION_DATABASE_URL` | Alembic 迁移数据库 | `alembic.ini` 中的本地 PostgreSQL |
| `LOCAL_STORAGE_PATH` | 上传文件目录 | `data/local/uploads` |
| `OBJECT_STORAGE_BACKEND` | 文件后端：`local` 或 `s3` | `local` |
| `OBJECT_STORAGE_ENDPOINT` | S3 兼容 API 地址；AWS S3 留空 | 无 |
| `OBJECT_STORAGE_ACCESS_KEY` | 对象存储访问密钥 ID | 无 |
| `OBJECT_STORAGE_SECRET_KEY` | 对象存储访问密钥 Secret | 无 |
| `OBJECT_STORAGE_BUCKET` | 私有文件存储桶 | 无 |
| `OBJECT_STORAGE_REGION` | 存储区域 | `us-east-1` |
| `OBJECT_STORAGE_PREFIX` | 同一存储桶内的环境隔离前缀 | 无 |
| `OBJECT_STORAGE_FORCE_PATH_STYLE` | MinIO 等服务是否强制 path-style URL | `false` |
| `OBJECT_STORAGE_SSE` | 可选服务端加密算法，例如 AWS 的 `AES256` | 无 |
| `AI_PROVIDER` | 回答生成：`local` 或 `openai` | `local` |
| `EMBEDDING_PROVIDER` | 语义嵌入：`disabled` 或 `openai` | `disabled` |
| `AI_MODEL` | 外部回答模型名称；启用 `openai` 回答时必填 | 无 |
| `EMBEDDING_MODEL` | 外部嵌入模型名称；启用 `openai` 嵌入时必填 | 无 |
| `EMBEDDING_DIMENSIONS` | 可选嵌入维度，范围 1–4096 | 由模型决定 |
| `OPENAI_API_KEY` | OpenAI API 密钥，只能保存于 Secret 管理器 | 无 |
| `OPENAI_BASE_URL` / `OPENAI_PROJECT` | OpenAI API 根地址和可选 Project | 官方 API / 无 |
| `AI_REQUEST_TIMEOUT_SECONDS` | 外部模型请求超时，范围 1–120 秒 | `30` |
| `AI_ALLOW_RESTRICTED` | 是否允许严格受限空间调用外部模型 | `false` |
| `BACKUP_ENCRYPTION_KEY` | 独立的 32 字节 Base64 数据库备份密钥 | 无；执行备份时必须设置 |
| `BACKUP_STORAGE_BACKEND` | 备份后端：`local` 或 `s3`；生产只允许 `s3` | `local` |
| `BACKUP_STORAGE_*` | 独立备份桶的 endpoint、凭据、bucket、region、prefix、SSE | 无 |
| `BACKUP_RETENTION_DAYS` | 数据库备份保留天数，不能小于 35 | `35` |
| `BACKUP_MIN_RECOVERY_POINTS` | 生命周期策略必须保留的最少恢复点 | `7` |
| `AUDIT_RETENTION_DAYS` | 审计记录最低保留天数，不能小于 365 | `2555` |
| `CORS_ORIGINS` | 允许访问 API 的 Web 来源，逗号分隔 | 本地 3000–3002 端口 |
| `PUBLIC_DEMO_MODE` | 启用签名浏览器演示会话 | `false` |
| `SESSION_SECRET` | 公开演示会话签名密钥 | 无；演示模式必须设置 |
| `WEB_STATIC_DIR` | 由 FastAPI 托管的 Web 静态导出目录 | 无 |
| `NEXT_PUBLIC_API_URL` | 浏览器请求的 API 地址 | `http://localhost:8000` |

不要提交 `.env`、访问密钥、数据库密码或真实企业资料。

## API 概览

```text
POST /v1/session
POST /v1/auth/register
POST /v1/auth/login
POST /v1/auth/logout
GET  /v1/auth/me
GET  /v1/auth/sessions
DELETE /v1/auth/sessions
DELETE /v1/auth/sessions/{session_id}
POST /v1/auth/email/verify
POST /v1/auth/email/resend
POST /v1/auth/password/forgot
POST /v1/auth/password/reset
POST /v1/auth/invitations/{tenant_id}/accept
POST /v1/tenants
GET  /v1/tenants/{tenant_id}
POST /v1/tenants/{tenant_id}/members
GET  /v1/tenants/{tenant_id}/members
DELETE /v1/tenants/{tenant_id}/members/{user_id}/sessions
POST /v1/tenants/{tenant_id}/invitations
POST /v1/tenants/{tenant_id}/spaces
GET  /v1/tenants/{tenant_id}/spaces
GET  /v1/tenants/{tenant_id}/spaces/{space_id}/members
POST /v1/tenants/{tenant_id}/spaces/{space_id}/members
DELETE /v1/tenants/{tenant_id}/spaces/{space_id}/members/{user_id}
GET  /v1/tenants/{tenant_id}/audit-events
POST /v1/tenants/{tenant_id}/spaces/{space_id}/documents
GET  /v1/tenants/{tenant_id}/spaces/{space_id}/documents
GET  /v1/tenants/{tenant_id}/documents/{document_id}
GET  /v1/tenants/{tenant_id}/documents/{document_id}/content
POST /v1/tenants/{tenant_id}/documents/{document_id}/versions
GET  /v1/tenants/{tenant_id}/document-versions/{version_id}/chunks
POST /v1/tenants/{tenant_id}/document-versions/{version_id}/publish
POST /v1/tenants/{tenant_id}/document-versions/{version_id}/semantic-index
POST /v1/tenants/{tenant_id}/questions
GET  /v1/tenants/{tenant_id}/questions
GET  /healthz
GET  /readyz
```

默认本地开发模式可使用 `X-Dev-User-Id` 模拟身份：

```bash
curl -X POST http://localhost:8000/v1/tenants \
  -H 'Content-Type: application/json' \
  -H 'X-Dev-User-Id: 00000000-0000-0000-0000-000000000001' \
  -d '{"name":"示例企业"}'
```

这个请求头只能用于本地开发，不能作为生产身份验证。

## 部署流程

### Render 公开演示环境

`Dockerfile` 会构建账号登录版 Next.js 页面，并把它与 FastAPI 放入同一镜像。`render.yaml` 会创建：

- 一个 Docker Web Service；
- 一个 PostgreSQL 数据库；
- `ACCOUNT_AUTH_MODE` 和独立生成的 `ENCRYPTION_MASTER_KEY`；
- `/healthz` 健康检查。

仓库已经支持 S3 兼容持久对象存储，但 Render Blueprint 默认仍保留 `OBJECT_STORAGE_BACKEND=local`
作为不保存真实资料的公开演示模式。正式使用前必须在 Render Secret 中设置对象存储凭据并把后端改为
`s3`；不要把访问密钥提交到 `render.yaml` 或 Git。

一键创建：

[在 Render 创建 Blueprint](https://dashboard.render.com/blueprint/new?repo=https%3A%2F%2Fgithub.com%2FAstronuts777%2Fenterprise-ai-knowledge-assistant)

发布步骤：

1. 将通过检查的提交推送到 GitHub `main`。
2. 等待 Render 自动构建该提交。
3. 如果未自动触发，在 Render 选择 **Manual Deploy → Deploy latest commit**。
4. 确认状态为 **Deploy succeeded | Live**。
5. 确认 `/healthz` 与实际查询数据库的 `/readyz` 都返回 HTTP 200。
6. 打开公开地址，实际完成一次企业创建和知识空间操作。
7. 检查 Render 日志中没有新的 500、数据库约束或迁移错误。

免费演示环境的限制：

- 闲置 15 分钟后休眠，首次唤醒可能需要约一分钟；
- 免费 PostgreSQL 创建 30 天后到期；
- Blueprint 默认的本地文件后端是临时的，上传原件可能在休眠、重启或重新部署后消失；
- 已解析的文本块保存在 PostgreSQL，但缺少原件时不能视为完整的生产证据链。

正式生产部署必须使用持久对象存储和长期数据库，并接入企业 SSO、备份恢复、密钥管理、审计、
监控告警和数据保留策略。

### 加密备份与恢复演练

仓库提供 `python -m enterprise_ai_api.backup` 运维命令，可创建独立密钥加密的 PostgreSQL 备份，
校验清单后恢复到命名含 `_restore` 或 `_drill` 的隔离数据库，并验证 Alembic 版本和关键表。
生产环境会拒绝把备份写入临时本地磁盘。完整的 Secret、定时任务、保留策略和演练步骤见
[备份、恢复演练与数据保留](docs/06-backup-and-recovery.md)。Blueprint 没有自动创建可能产生费用的
Render Cron Job；配置定时任务和私有备份桶之前，不能把公开演示环境视为已有可恢复备份。

## 常见问题

### `zsh: command not found: pnpm`

先执行 `corepack enable` 和 `corepack prepare pnpm@11.25.0 --activate`，再重新打开终端。

### `http://localhost:8000/docs` 能打开，但完整网页打不开

`8000/docs` 只是 API 文档。还需要运行 `pnpm dev:web`，然后访问 `http://localhost:3000`。

### PyCharm 显示 `No jobs available` 并正常退出

运行的是 Worker 占位入口，不是 Web 或 API。请选择 **Local: Web + API**。

### Render 页面显示 `API 请求失败`

先查看 Web Service 的 **Logs**，找到同一时间的 Python 或 PostgreSQL 异常。不要只看前端提示；
修复后应增加回归测试、推送新提交并验证线上业务操作。

### 文档上传后为什么只有已发布版本能被问答使用

这是证据完整性设计。`ready` 只表示解析完成，只有知识管理员明确发布的版本才能进入默认检索。
普通空间查看者的文档列表和阅读器也只返回已发布版本；空间管理者和企业管理员可以在发布前预览
最新版本。每次阅读都会记录文档、版本和访问级别，但不会把正文写入审计日志。

### 如何启用语义检索和外部生成模型

默认配置是 `AI_PROVIDER=local`、`EMBEDDING_PROVIDER=disabled`。完成数据出境、供应商保留策略和
密钥管理评审后，可在部署平台的 Secret 管理器设置 `OPENAI_API_KEY`、`AI_MODEL` 和
`EMBEDDING_MODEL`，再把两个 provider 分别改为 `openai`。启用后，新上传文档会建立加密语义索引；
已有版本可由知识管理员调用 `semantic-index` 接口补建。`restricted` 空间默认始终使用本地回答，
除非企业明确设置 `AI_ALLOW_RESTRICTED=true`。

外部生成只接收本次用户有权访问、已经发布并被检索选中的证据片段，请求明确使用 `store=false`；
模型必须按严格 JSON Schema 返回主张与引用编号，服务端会拒绝越界引用并重新生成显示编号。详细威胁
边界和扩展方案见[混合检索与可配置 AI](docs/08-hybrid-retrieval-and-ai.md)。实现依据 OpenAI 官方的
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
和 [Embeddings API](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create)。

## 路线图

- [x] 企业、成员、角色和知识空间
- [x] 文档上传、解析、版本和发布
- [x] 权限感知的证据问答、引用与拒答
- [x] SQLite 本地开发与 PostgreSQL 演示部署
- [x] 邮箱密码账号、服务端会话和限时企业邀请
- [x] 敏感数据库字段与上传原件的 AES-256-GCM 加密
- [x] 邮箱验证、密码重置、邀请邮件和数据库级登录限流
- [x] 知识空间只读者/管理员 ACL 与检索隔离
- [x] 企业管理员审计日志、拒绝事件记录与 PostgreSQL 追加式保护
- [x] 独立密钥加密的 PostgreSQL 备份与隔离恢复演练工具
- [x] 数据库就绪检查、请求 ID、结构化访问日志与生产配置审计
- [x] 基于空间最低权限和发布状态的安全文档阅读器
- [x] 用户设备会话管理与企业管理员强制下线
- [x] TOTP 双重验证、一次性恢复码与验证码防重放
- [x] 企业级 MFA 强制策略与恢复码安全轮换
- [x] 授权后加密向量与关键词混合检索（当前规模）
- [x] 可配置的大模型生成、严格结构化输出与引用校验
- [ ] PostgreSQL pgvector 规模化检索与租户过滤召回评测
- [ ] 决策证据包、人工审批和审计导出
- [ ] 异步 Worker、OCR 和批量导入
- [ ] 企业 SSO、SCIM 与外部知识连接器
- [ ] 评测集、安全门禁、备份和生产运维

## 项目文档

- [项目章程](docs/00-project-charter.md)
- [产品定位](docs/01-product-positioning.md)
- [MVP 范围与验收标准](docs/02-mvp-scope.md)
- [用户故事与核心流程](docs/03-user-stories.md)
- [MVP 系统架构](docs/04-system-architecture.md)
- [AI 协作记录](docs/ai-development-log.md)
- [横向产品策略决策](docs/decisions/ADR-0001-horizontal-product-strategy.md)
- [初始技术栈提案](docs/decisions/ADR-0002-initial-technology-stack.md)
- [安全、身份与加密设计](docs/05-security-and-access-control.md)
- [备份、恢复演练与数据保留](docs/06-backup-and-recovery.md)
- [生产运行、监控与配置审计](docs/07-production-operations.md)
- [混合检索与可配置 AI](docs/08-hybrid-retrieval-and-ai.md)
