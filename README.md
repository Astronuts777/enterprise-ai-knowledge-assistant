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
2. 由管理员向指定邮箱签发限时、一次性邀请，成员不能通过输入企业名称自行加入。
3. 按部门、项目或业务主题创建知识空间，并设置内部、机密或严格受限等级。
4. 为受邀成员分配企业管理员、知识管理员或普通成员角色。
5. 上传并解析不超过 10 MB 的 PDF、DOCX、TXT 和 Markdown 文件。
6. 预览解析结果，发布可检索版本，并为同一文档上传后续版本。
7. 只针对已发布资料提问，得到带文件名、版本、页码或段落位置的证据式回答。
8. 在资料不足时明确拒答，不用没有来源的内容补全答案。
9. 保存当前用户自己的问答历史，并阻止跨企业读取数据。

适合用来验证以下场景：

- 公司制度、员工手册和标准流程查询；
- 项目文档、交付材料和会议纪要整理；
- 产品、运营、客服或销售知识库；
- 对结论来源、版本和证据链有要求的内部问答；
- 跨行业企业知识助手的产品原型和用户测试。

### 当前能力边界

- 问答使用透明、可重复的本地关键词检索和原文摘录，尚未调用外部大模型。
- 文档解析在 API 请求内同步完成；Worker 当前只有可测试的任务边界，尚未处理异步任务。
- 公网账号模式使用服务端会话和 HttpOnly Cookie；本地默认仍可用 `X-Dev-User-Id` 快速调试。
- 当前管理员需要手动把邀请链接安全地发送给成员；自动邮件验证、密码重置和企业 SSO 尚未接入。
- SCIM、OCR、云盘连接器、向量检索、决策证据包、审批和完整审计仍在路线图中。
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
    A --> R[权限感知检索]
    R --> D
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
- 邮箱使用 AES-256-GCM 加密保存，另用带密钥 HMAC 摘要完成精确账号查询。
- 文档解析正文、问题、回答和引用摘录在进入数据库前使用 AES-256-GCM 认证加密。
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

### PostgreSQL 与 MinIO 模式

安装 Docker 后启动依赖：

```bash
docker compose -f infra/docker-compose.yml up -d
```

执行迁移并启动 API：

```bash
MIGRATION_DATABASE_URL='postgresql+psycopg://app_owner:local-development-only@localhost:5432/enterprise_ai' \
  .venv/bin/alembic upgrade head

DATABASE_URL='postgresql+psycopg://app_runtime:local-development-only@localhost:5432/enterprise_ai' \
  PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/uvicorn enterprise_ai_api.main:app --reload
```

PostgreSQL 迁移会启用并强制执行租户行级安全。应用层仍执行显式授权，形成应用和数据库两层隔离。
当前业务上传仍使用 `LOCAL_STORAGE_PATH`；MinIO 是后续对象存储接入的本地依赖边界。

## 环境变量

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `APP_ENV` | 环境标识 | `development` |
| `ACCOUNT_AUTH_MODE` | 启用邮箱密码账号、服务端会话和企业邀请 | `false` |
| `ENCRYPTION_MASTER_KEY` | 数据字段、令牌摘要和上传文件的主密钥 | 本地使用开发密钥；公网必须设置 |
| `DATABASE_URL` | API 运行数据库 | 本地 SQLite |
| `MIGRATION_DATABASE_URL` | Alembic 迁移数据库 | `alembic.ini` 中的本地 PostgreSQL |
| `LOCAL_STORAGE_PATH` | 上传文件目录 | `data/local/uploads` |
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
POST /v1/auth/invitations/{tenant_id}/accept
POST /v1/tenants
GET  /v1/tenants/{tenant_id}
POST /v1/tenants/{tenant_id}/members
GET  /v1/tenants/{tenant_id}/members
POST /v1/tenants/{tenant_id}/invitations
POST /v1/tenants/{tenant_id}/spaces
GET  /v1/tenants/{tenant_id}/spaces
POST /v1/tenants/{tenant_id}/spaces/{space_id}/documents
GET  /v1/tenants/{tenant_id}/spaces/{space_id}/documents
GET  /v1/tenants/{tenant_id}/documents/{document_id}
POST /v1/tenants/{tenant_id}/documents/{document_id}/versions
GET  /v1/tenants/{tenant_id}/document-versions/{version_id}/chunks
POST /v1/tenants/{tenant_id}/document-versions/{version_id}/publish
POST /v1/tenants/{tenant_id}/questions
GET  /v1/tenants/{tenant_id}/questions
GET  /healthz
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

一键创建：

[在 Render 创建 Blueprint](https://dashboard.render.com/blueprint/new?repo=https%3A%2F%2Fgithub.com%2FAstronuts777%2Fenterprise-ai-knowledge-assistant)

发布步骤：

1. 将通过检查的提交推送到 GitHub `main`。
2. 等待 Render 自动构建该提交。
3. 如果未自动触发，在 Render 选择 **Manual Deploy → Deploy latest commit**。
4. 确认状态为 **Deploy succeeded | Live**。
5. 打开公开地址，实际完成一次企业创建和知识空间操作。
6. 检查 Render 日志中没有新的 500、数据库约束或迁移错误。

免费演示环境的限制：

- 闲置 15 分钟后休眠，首次唤醒可能需要约一分钟；
- 免费 PostgreSQL 创建 30 天后到期；
- Web 服务文件系统是临时的，上传原件可能在休眠、重启或重新部署后消失；
- 已解析的文本块保存在 PostgreSQL，但缺少原件时不能视为完整的生产证据链。

正式生产部署必须使用持久对象存储和长期数据库，并接入企业 SSO、备份恢复、密钥管理、审计、
监控告警和数据保留策略。

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

## 路线图

- [x] 企业、成员、角色和知识空间
- [x] 文档上传、解析、版本和发布
- [x] 权限感知的证据问答、引用与拒答
- [x] SQLite 本地开发与 PostgreSQL 演示部署
- [x] 邮箱密码账号、服务端会话和限时企业邀请
- [x] 敏感数据库字段与上传原件的 AES-256-GCM 加密
- [ ] 邮箱验证、密码重置、邀请邮件和登录限流
- [ ] pgvector 关键词与语义混合检索
- [ ] 可配置的大模型生成与引用校验
- [ ] 决策证据包、人工审批和审计事件
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
