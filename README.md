# Enterprise AI Knowledge Assistant

一个面向不同企业的可信知识库与 AI 决策证据平台。

系统将帮助企业导入和管理 PDF、网页、会议记录及内部文档，在权限边界内提供带精确引用的回答，并处理版本冲突、信息过期、敏感信息、质量评测与人工审批。

## 产品原则

- **有证据**：回答必须关联原始文档、版本和位置。
- **有权限**：检索和生成均遵循企业、空间、文档和用户权限。
- **可追溯**：记录数据来源、检索过程、AI 输出、人工修改与批准结果。
- **诚实拒答**：证据不足、权限不足或来源冲突时，不生成确定性结论。
- **人工负责**：高风险或重要决策必须由获得授权的人确认。
- **行业无关**：核心平台不绑定单一行业，行业差异通过配置和知识包扩展。

## 当前阶段

项目已进入 MVP 基础开发阶段。首个跨行业工作流、P0 范围、用户故事、系统架构和初始技术栈已经确认，当前正在建立权限优先的纵向功能切片。

## 本地开发

### Web

```bash
pnpm install
pnpm dev:web
```

访问 `http://localhost:3000`。当前 Web 控制台已支持：

- 首次创建企业工作区，并将本地演示用户设为企业管理员。
- 查看工作区总览和权限基础状态。
- 创建不同保密等级的知识空间。
- 添加企业成员并分配企业管理员、知识管理员或普通成员角色。
- 在知识空间中上传、解析和发布 PDF、DOCX、TXT 与 Markdown。
- 为同一逻辑文档上传新版本，旧发布版本会保留并标记为已替代。
- 在前端直接显示 API 权限错误和跨租户拒绝结果。

Web 默认连接 `http://localhost:8000`。需要修改 API 地址时，复制
`apps/web/.env.example` 为 `apps/web/.env.local` 并修改 `NEXT_PUBLIC_API_URL`。
本地运行可在 PyCharm 中选择 `Local: Web + API`，或分别启动 `Web: Dev` 和
`API: FastAPI`。

### API 与测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
.venv/bin/ruff check .
PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/uvicorn enterprise_ai_api.main:app --reload
```

API 健康检查位于 `http://localhost:8000/healthz`。

首次纵向功能切片已经提供以下接口：

```text
POST /v1/tenants
GET  /v1/tenants/{tenant_id}
POST /v1/tenants/{tenant_id}/members
GET  /v1/tenants/{tenant_id}/members
POST /v1/tenants/{tenant_id}/spaces
GET  /v1/tenants/{tenant_id}/spaces
POST /v1/tenants/{tenant_id}/spaces/{space_id}/documents
GET  /v1/tenants/{tenant_id}/spaces/{space_id}/documents
GET  /v1/tenants/{tenant_id}/documents/{document_id}
POST /v1/tenants/{tenant_id}/documents/{document_id}/versions
GET  /v1/tenants/{tenant_id}/document-versions/{version_id}/chunks
POST /v1/tenants/{tenant_id}/document-versions/{version_id}/publish
```

未设置 `DATABASE_URL` 时，API 使用持久化 SQLite：业务记录保存在
`data/local/enterprise_ai.db`，上传原件保存在 `data/local/uploads/`。两个位置都被 Git 忽略，
重启 API 后数据不会丢失。API 启动时会自动执行本地 Alembic 迁移。打开
`http://localhost:8000/docs` 可在 Swagger UI 中调用接口。当前身份适配器使用
`X-Dev-User-Id` 请求头模拟已验证用户，例如：

```bash
curl -X POST http://localhost:8000/v1/tenants \
  -H 'Content-Type: application/json' \
  -H 'X-Dev-User-Id: 00000000-0000-0000-0000-000000000001' \
  -d '{"name":"示例企业"}'
```

`X-Dev-User-Id` 仅用于本地纵向切片；接入企业 SSO 后必须由服务端验证的身份声明替代，
不能在生产环境信任客户端自报的用户 ID。

本地备份前先停止 API，然后复制整个 `data/local/` 目录。需要全新演示环境时，停止 API，
将该目录移到其他位置保存，再重新启动；系统会创建新的数据库和上传目录。

当前 10 MB 以内的文档在请求内完成首轮解析，以便验证完整链路。进入大文件、OCR 和批量导入
阶段后，解析将迁移到已有 Worker 边界，避免阻塞 API。

### 本地依赖服务

安装 Docker 后运行：

```bash
docker compose -f infra/docker-compose.yml up -d
```

这将启动带 pgvector 的 PostgreSQL 和 S3 兼容对象存储。`.env.example` 中仅包含本地开发占位凭据，不得用于共享或生产环境。

PostgreSQL 启动后，先以表所有者执行迁移，再以不具备 `BYPASSRLS` 权限的运行账户启动 API：

```bash
MIGRATION_DATABASE_URL='postgresql+psycopg://app_owner:local-development-only@localhost:5432/enterprise_ai' \
  .venv/bin/alembic upgrade head
DATABASE_URL='postgresql+psycopg://app_runtime:local-development-only@localhost:5432/enterprise_ai' \
  PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/uvicorn enterprise_ai_api.main:app --reload
```

迁移会为企业、成员、空间、文档、版本和文本块表启用并强制执行 PostgreSQL 行级安全。
应用层仍会在每个用例执行显式授权，构成两层隔离。

`requirements-dev.txt` 记录直接 Python 依赖，`requirements.lock` 和 `pnpm-lock.yaml` 固定完整依赖解析结果。

## 文档

- [项目章程](docs/00-project-charter.md)
- [产品定位](docs/01-product-positioning.md)
- [MVP 范围与验收标准](docs/02-mvp-scope.md)
- [用户故事与核心流程](docs/03-user-stories.md)
- [MVP 系统架构](docs/04-system-architecture.md)
- [AI 协作记录](docs/ai-development-log.md)
- [横向产品策略决策](docs/decisions/ADR-0001-horizontal-product-strategy.md)
- [初始技术栈提案](docs/decisions/ADR-0002-initial-technology-stack.md)

## 计划阶段

1. 实现企业、成员、知识空间和数据库行级安全
2. 完成文档上传、解析、版本和发布流程
3. 完成权限感知检索、引用问答和拒答
4. 完成决策证据包、审批和审计
5. 建立评测集、安全门禁和持续集成
6. 部署测试环境并开展用户验证

