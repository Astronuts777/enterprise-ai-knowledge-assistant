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

访问 `http://localhost:3000`。

### API 与测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/uvicorn enterprise_ai_api.main:app --app-dir services/api/src --reload
```

API 健康检查位于 `http://localhost:8000/healthz`。

### 本地依赖服务

安装 Docker 后运行：

```bash
docker compose -f infra/docker-compose.yml up -d
```

这将启动带 pgvector 的 PostgreSQL 和 S3 兼容对象存储。`.env.example` 中仅包含本地开发占位凭据，不得用于共享或生产环境。

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

