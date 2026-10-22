# Enterprise AI Knowledge Assistant

面向不同企业的可信知识库与 AI 决策证据平台。它不绑定某个确定行业，而是用统一的企业、成员、
知识空间、文档版本、权限和引用模型，为不同组织建立自己的知识工作台。

- **公开演示**：[enterprise-ai-knowledge-assistant-y39d.onrender.com](https://enterprise-ai-knowledge-assistant-y39d.onrender.com)
- **API 文档（本地）**：[http://localhost:8000/docs](http://localhost:8000/docs)
- **Web（本地）**：[http://localhost:3000](http://localhost:3000)

> **当前推荐：用户在自己的电脑上运行完整本地版。** 不需要购买云服务器、对象存储或模型 API，
> 也不需要配置付费 API 密钥。首次安装需联网下载依赖和模型，运行消耗自己的硬件、电力和磁盘空间。
> 请从下面的[本地 AI 配置指南](#local-ai-setup)开始；普通使用无需启动 Docker 或创建 Render 资源。
>
> 公开地址仅作免费演示，不是正式生产系统，也不会调用访问者电脑里的模型。请勿上传真实机密资料。

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
10. 可选启用 Ollama 本地多语言语义检索与模型综合回答，也保留外部服务适配器；模型引用由服务端校验并重新编号。
11. 在资料不足时明确拒答，不用没有来源的内容补全答案。
12. 保存当前用户自己的问答历史，并阻止跨企业、跨空间读取数据。
13. 由企业管理员查看身份、权限、文档阅读和问答操作的追加式审计日志，并导出防公式注入的合规 CSV 快照。
14. 在“账号安全”中启用身份验证器双重验证、更新一次性恢复码，并查看或撤销登录设备。
15. 由企业管理员强制要求管理员或全部成员启用 MFA；未满足策略的账号不能读取企业资料。
16. 企业管理员可在成员页停用/恢复成员；停用后旧会话也无法读取该企业资料，不影响其他企业权限。
17. 可选接入本机 ClamAV，在上传原件、解析和建索引之前扫描；启用后扫描服务故障也会阻止上传。
18. 本地版可用独立密钥加密备份数据库、原件和应用主密钥，并验证后恢复到新目录。

适合用来验证以下场景：

- 公司制度、员工手册和标准流程查询；
- 项目文档、交付材料和会议纪要整理；
- 产品、运营、客服或销售知识库；
- 对结论来源、版本和证据链有要求的内部问答；
- 跨行业企业知识助手的产品原型和用户测试。

### 当前能力边界

- 默认问答使用透明、可重复的本地关键词检索和原文摘录，不向外部模型发送资料；管理员可选配置语义嵌入和生成模型。
- 已实现持久化异步 Worker：加密隔离上传、扫描/解析/OCR/索引、租约接管、有限重试、取消及状态刷新。通用环境默认同步；本地 `start` 使用异步模式。
- 公网账号模式使用服务端会话和 HttpOnly Cookie；本地默认仍可用 `X-Dev-User-Id` 快速调试。
- 邮箱验证、邀请邮件和密码重置已经接入 SMTP 适配器；公开演示环境尚未配置邮件服务，因此暂不强制邮箱验证，邀请仍会返回可手动发送的链接。
- 登录按账号和网络来源进行数据库级限流；支持 TOTP 双重验证、恢复码、会话撤销与企业 MFA 策略。可选 OIDC SSO 与 SCIM 用户/组同步已实现，默认关闭；真实企业身份平台尚需配置和联调，异常检测尚未接入。
- 当前语义检索在授权后的加密向量候选集中由应用计算，适合现阶段规模；数据库内 pgvector 检索仍在路线图中。
- PDF 支持本地中英文 OCR，DOCX 表格保留表头与行定位；OCR 仍可能误识别数字、复杂表格和低清扫描件，必须在发布前核对解析结果。云盘连接器、决策证据包、审批和不可变审计归档仍在路线图中。
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
    A --> Q[(持久文件任务)]
    Q --> K[Worker: 扫描/解析/索引]
    K --> D
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
services/worker/                  持久异步文件处理 Worker
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

<a id="local-ai-setup"></a>

#### 本地 AI 完整运行（Ollama，不发送资料给外部模型）

**适用方式：每位用户在自己的电脑上启动网页、API、数据库、文件存储和模型。**
浏览器访问的是自己的 `http://127.0.0.1:8201`，不是 Render 公网地址。
这条路径不使用付费云资源；后文的云部署、S3 和外部模型说明只是可选技术参考，不是安装前提。

##### 第一步：准备电脑和代码

- macOS 或 Linux：使用系统终端，执行下方 Bash 命令。
- Windows：当前启动器使用 Unix 路径；请在 **WSL2 的 Ubuntu 环境**内安装 Python、Node.js、pnpm、
  Ollama，并把仓库克隆到 WSL 的 Linux 主目录。所有命令都在 WSL 终端执行，不要混用 Windows
  原生 Python/Ollama 或直接把 `.venv/bin/...` 粘贴到 PowerShell。Windows/WSL 全流程尚未实机验证。
- Python 3.12–3.14、Node.js 22、pnpm 11.25.0、Git；安装方法见上方“准备环境”。
- 建议至少 16 GB 内存、预留 15 GB 空闲磁盘作为试用起点；文档和备份会额外占空间。
  这不是性能保证：CPU 可以运行但可能较慢，大文件、长问题和同时提问会增加资源需求。
- 不需要 Docker、SMTP 邮箱服务、云存储账号或外部模型 API 密钥。

首次安装（已在本机完成的步骤不必重复）：

```bash
git clone https://github.com/Astronuts777/enterprise-ai-knowledge-assistant.git
cd enterprise-ai-knowledge-assistant
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
pnpm install --frozen-lockfile
```

后续所有终端都先进入这个仓库目录。不要复制别人的 `.venv`、`node_modules`、账号数据库或密钥来代替安装。

##### 第二步：启动并下载本地模型

这里的 `AI_PROVIDER=local` 指**关键词检索＋原文摘录**，不是本地大模型。
真正的本地模型模式是 `AI_PROVIDER=ollama` 和 `EMBEDDING_PROVIDER=ollama`。

本地组合为 [Qwen3 4B](https://ollama.com/library/qwen3:4b)（整理答案）和
[BGE-M3](https://ollama.com/library/bge-m3)（多语言语义检索）。下载模型需要联网，
运行时问题、正文、向量与推理留在本机；关闭云端模型，并且应用只接受回环地址、禁用 HTTP 代理及重定向。
这不代表模型答案绝对正确，引用校验也不能证明每个结论都忠实于原文。

1. 安装 [Ollama](https://ollama.com/download)（本次验证版本 0.40.8）。本机已安装的独立版本也可位于
   `work/ollama-runtime/ollama`，启动器会优先使用它。
2. 终端一启动专用本地模型服务（端口 11435，与常用默认端口分开）：

   ```bash
   .venv/bin/python scripts/local_ai.py model
   ```

   如果运行环境无法访问 GPU，改用 `.venv/bin/python scripts/local_ai.py model --cpu`。
   启动器设置 `OLLAMA_NO_CLOUD=1`、仅监听 `127.0.0.1`，模型保存在 `work/ollama-models/`。
   保持终端一运行，**另开终端二**下载两个模型（首次需要，合计约 3.7 GB，随模型版本可能变化）：

   ```bash
   curl http://127.0.0.1:11435/api/pull -d '{"model":"qwen3:4b"}'
   curl http://127.0.0.1:11435/api/pull -d '{"model":"bge-m3"}'
   ```

   等待两个下载都显示 `"status":"success"`，再检查服务中的模型列表：

   ```bash
   curl --fail http://127.0.0.1:11435/api/tags
   ```

   结果应同时包含 `qwen3:4b` 和 `bge-m3`。本启动器使用 **11435**，不能只在默认 11434 服务下载模型；
   也不要把模型名改成云端模型。下载失败时先解决网络或磁盘问题，不要先上传企业资料。

##### 第三步：启动完整网页

3. 在终端二构建同源账号版网页，然后启动 Web＋API：

   ```bash
   pnpm build:local-ai
   .venv/bin/python scripts/local_ai.py web
   ```

4. 访问 **http://127.0.0.1:8201**，注册本地账号及企业，创建空间，上传资料，
   核对正文并发布，再进入 AI 决策助手提问。PyCharm 也可分别运行
   **AI: Local Ollama** 和 **Web: Local AI**；同一服务不要重复启动。

本地启动器会自动创建并迁移 SQLite 数据库、生成加密密钥，开启邮箱＋密码账号模式；
本地配置不要求验证邮件，也不会发送邮件。没有通用的预设账号，请自己注册。
无需另外启动 `pnpm dev:web` 或 8000 端口的开发 API，也无需手工填写 `.env`；
这个启动器明确设置本地所需环境变量，而不是自动读取 `.env`。

先用一份不含机密信息的小型 TXT/Markdown 验证：上传 → 核对解析正文 → 发布 → 提问 → 打开引用核对。
如果模型服务未运行或下载未完成，请先处理错误，不要将“已有索引”当成模型在线的证明。

##### 日常使用：一条命令启动，诊断问题

首次依赖安装、模型下载和网页构建完成后，先用 `Ctrl+C` 停止上面手工启动的两个服务。
以后每天只需在仓库根目录运行：

```bash
.venv/bin/python scripts/local_ai.py start
```

它会检查 Python 依赖、网页构建、Ollama、数据密钥与端口，依次启动模型、Web＋API 和 Worker，
确认模型文件齐全及数据库就绪后打印网页地址。保持这个终端运行；按 `Ctrl+C` 一起停止本次启动的服务。
若一个服务启动失败或运行中退出，启动器会停止本次启动的其他服务，资料仍然保留。
**这不是自动安装器**：不会自动安装依赖、下载模型、购买资源或调用付费 API。

- GPU 不可用：`start --cpu`。
- 自定义端口：`start --port 8202 --model-port 11436`，启动器会统一配置模型、Web/API 和 Worker。
- 端口已占用时会停止启动并提示，不会接管或关闭已有进程；先停止自己旧的手工服务，或改用空闲端口。

只检查环境、不更改数据：

```bash
.venv/bin/python scripts/local_ai.py doctor
```

`doctor` 会输出中文检查结果，检查依赖、模型文件、密钥和可选的中英文 OCR 工具。
模型列表检查需要对应模型服务已经运行；尚未运行时提示“未就绪”是正常的，按提示单独启动 `model` 再检查。
自定义端口时加 `doctor --model-port 11436`。OCR 缺失只会警告，不妨碍纯文本使用。
检查通过不等于已验证模型推理质量或并发性能，仍需用测试资料完成一次上传和问答。

需要分别调试时，仍可使用原来的两个终端方式；不要与 `start` 重复运行：

```bash
# 终端一：保持运行
.venv/bin/python scripts/local_ai.py model

# 终端二：保持运行
.venv/bin/python scripts/local_ai.py web
```

停止时在对应终端按 `Ctrl+C`。电脑休眠、关机或服务停止后网页/AI 不可用；重新启动后仍使用原来的账号和资料。
代码升级前先按下文备份；升级依赖后重新运行 `pnpm build:local-ai`，再启动服务。

##### 第四步：了解文件保存位置与备份

| 仓库内路径 | 内容 | 是否需要备份 |
| --- | --- | --- |
| `data/local/ollama/enterprise.db` | 账号、权限、文档记录、解析正文、向量和问答记录 | 是 |
| `data/local/ollama/uploads/` | 加密上传原件 | 是，不能只备份数据库 |
| `data/local/ollama/master.key` | 解密所需主密钥 | 是，必须安全保存 |
| `work/ollama-models/` | 下载的模型 | 可选，丢失后可重新下载 |

本地 AI 使用独立的 `data/local/ollama/enterprise.db` 和 `uploads/`，不会自动导入线上账号或资料。
密钥自动保存到 `data/local/ollama/master.key`（权限 0600），不要删除、公开或提交 Git。
加密涵盖敏感字段、向量和上传文件，不是整个 SQLite 文件的透明加密；应同时启用磁盘加密、访问控制并保管密钥备份。
OCR 使用短暂的私有明文临时文件，处理后自动清理；它们仍受本机磁盘安全保护的约束。
这些文件位于本机普通磁盘目录，不是 Render 临时磁盘，重启服务或电脑不会主动清空它们。
但删除项目目录、清理 `data/local/`、硬盘损坏或丢失密钥仍会导致数据丢失或无法解密。

本地备份步骤：

1. 停止 Web＋API，确保没有上传或数据库写入。
2. 用文件管理器完整复制 `data/local/ollama/` 到自己已有的加密备份介质，保留数据库可能伴随的文件。
3. 该目录包含主密钥，备份也必须受加密和访问控制保护；不要上传公共网盘、发给他人或提交到 GitHub。
   主密钥另外保存一份受保护的离线副本，不要只留在同一块硬盘上。
4. 恢复时先停止服务，保留现有目录的安全副本，再恢复同一时间点的完整目录及其原密钥。
   启动后检查登录、文档和问答；不要为旧数据库生成新密钥。

本地目录已被 Git 忽略，**推送代码到 GitHub 不等于备份资料**。
除了手工复制，现在也提供会校验原件、数据库和密钥的[本地加密备份与隔离恢复命令](docs/09-closed-pilot-readiness.md)。
目前未安排定时任务，仍需自行定期执行；备份密钥不能与备份一起公开。

##### 可选：让扫描 PDF 可以读取（本地 OCR）

扫描 PDF 额外需要 Poppler、Tesseract 和 `eng`、`chi_sim` 语言包。
macOS 可在自己的终端运行 `brew install poppler tesseract tesseract-lang`。
Ubuntu / WSL Ubuntu 可运行：

```bash
sudo apt-get update
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim
```

安装后用 `pdftoppm -v`、`tesseract --list-langs` 检查，语言列表应包含 `eng` 和 `chi_sim`，
然后重新启动 Web＋API。TXT、Markdown 和 DOCX 不需要这套 OCR 依赖。
本项目这台机器也支持已安装在 `work/ocr/` 的独立 OCR 环境，启动器会自动加入路径。
Docker 镜像已声明对应系统依赖。PDF 最多 100 页，其中需要 OCR 的图片页面最多 10 页，
OCR 总时间预算 90 秒；当前采取保守策略，含图片的页面会做 OCR，包含图片的封面也可能需要人工处理。
未安装 OCR、识别失败或超限时标记解析失败，不把部分正文伪装成解析成功。
复杂表格的单元格关系仍不能保证完整恢复。

已有资料的升级方式：

- 正文完整但没有向量：空间管理员在文档列表展开“语义检索索引管理”，点击“建立语义索引”。
- 原来漏掉了表格/扫描正文：先上传新版本并检查解析结果，再发布；重建向量不会重新解析原始文件。
- 助手页面显示当前检索配置和已索引片段数，**这不是模型实时健康检查**；模型服务故障返回错误，不误报“资料中没有答案”。
- 当前候选检索最多处理授权范围内最近 5,000 个片段，并取有限相关片段及相邻正文；不能保证对整库做穷尽总结。
- 本地问答关闭自动截断和上下文滑动；超过模型上下文限制时返回错误，请缩小问题/空间范围，不会悄悄丢掉证据后继续回答。

##### 不同电脑如何使用？

- 每个人按本指南安装后，访问自己电脑的 `http://127.0.0.1:8201` 即可使用；不依赖项目作者的电脑开机。
- **不同电脑是独立实例，账号、企业、文件和问答不会自动共享。** 输入同一个企业名或邮箱也不会连接到同一份数据库。
- 这不是“打开公共链接就使用本机模型”的实现；只安装 Ollama、仍访问 Render 网站，不会启用本机 AI。
- 如果之后需要全公司共享同一知识库，可以另行评估使用企业已有、持续在线的内网电脑统一运行整套系统。
  当前启动器仅监听回环地址，尚未提供安全的局域网共享配置；不能直接把监听地址改为 `0.0.0.0` 就当作正式部署。

本地模型服务不会自动接到 Render 公网网站，Render 的 `127.0.0.1` 不是你的电脑。
不要暴露 Ollama 端口，也不要把公网配置直接改成你电脑的回环地址。
共享部署仍需规划 HTTPS、网络访问控制、权限隔离、备份和运维，不在当前个人本地配置范围内。

##### 本地启动常见问题

| 现象 | 检查方式 |
| --- | --- |
| 找不到 `pnpm`、`python3` 或 `ollama` | 先安装对应工具、重新打开终端，确认命令在当前环境的 PATH 中；WSL 内需要独立安装 |
| 提示 `Build first` | 在仓库根目录运行 `pnpm build:local-ai`，构建成功后再启动 `web` |
| `8201` 网页打不开 | 确认终端二仍在运行、无报错；访问本机 `http://127.0.0.1:8201`，不是 `/docs` 或 Render 地址 |
| 模型连接失败或找不到模型 | 检查终端一和 `curl --fail http://127.0.0.1:11435/api/tags`，确认两个模型下载在 11435 服务中 |
| 端口已占用 | 不要重复启动；确认已有进程归属后停止自己的旧进程，或同时为 `model` 与 `web` 指定相同的 `--model-port`；网页端口可用 `web --port 8202` |
| GPU 不可用 | 停止自己的模型服务后改用 `model --cpu`；CPU 较慢，不保证长问题在超时前完成 |
| 已上传但无法找到内容 | 检查解析正文是否完整、版本是否发布、账号是否有权限，以及“语义检索索引管理”中是否建立索引 |
| 原来的账号或文件不见了 | 确认使用原仓库与 `data/local/ollama/`，不是新克隆的目录、普通开发版或 Render 网站；不要删除密钥重试 |

可复现验证（只使用合成测试资料）：

```bash
.venv/bin/pytest
RUN_LOCAL_AI_TESTS=1 OLLAMA_BASE_URL=http://127.0.0.1:11435 \
  .venv/bin/pytest services/api/tests/test_local_ai_live.py -s
PATH="$PWD/work/ocr/bin:$PATH" TESSDATA_PREFIX="$PWD/work/ocr/share/tessdata" \
  RUN_LOCAL_OCR_TESTS=1 .venv/bin/pytest services/api/tests/test_ocr_live.py -s
```

真实模型验证涵盖中文问英文资料、跨文件答案、同义表达和无依据拒答；OCR 验证使用真正的图片型 PDF。
它们是冒烟测试，不代替使用企业实际文件做准确率、安全及压力评测。

### 5. 使用 PyCharm 启动

1. 用 PyCharm 打开仓库根目录。
2. 将 Python Interpreter 设为仓库中的 `.venv/bin/python`。
3. 在运行配置中选择 **Local: Web + API**。
4. 点击运行后访问 `http://localhost:3000`。

也可以分别运行 **API: FastAPI** 和 **Web: Dev**。新 Worker 是持续运行的后台服务，不提供网页。
最简单的本地完整启动方式是 `.venv/bin/python scripts/local_ai.py start`，它同时启动模型、Web/API 和 Worker。
升级、单独运行 Worker 及 SSO/SCIM 配置详见[后台处理与企业身份接入](docs/10-worker-sso-scim.md)。

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
| `FILE_SCAN_BACKEND` | 上传扫描：`disabled` 或本机 `clamav` | `disabled`，不是已扫描 |
| `FILE_SCAN_REQUIRED` | 为 `true` 时拒绝禁用扫描的配置 | `false`；企业试点应启用 |
| `CLAMAV_HOST` / `CLAMAV_PORT` | 扫描器回环 IP 与端口；禁止公网地址 | `127.0.0.1` / `3310` |
| `CLAMAV_TIMEOUT_SECONDS` | 扫描总超时，范围 1–120 秒；超时拒绝上传 | `30` |
| `OBJECT_STORAGE_ENDPOINT` | S3 兼容 API 地址；AWS S3 留空 | 无 |
| `OBJECT_STORAGE_ACCESS_KEY` | 对象存储访问密钥 ID | 无 |
| `OBJECT_STORAGE_SECRET_KEY` | 对象存储访问密钥 Secret | 无 |
| `OBJECT_STORAGE_BUCKET` | 私有文件存储桶 | 无 |
| `OBJECT_STORAGE_REGION` | 存储区域 | `us-east-1` |
| `OBJECT_STORAGE_PREFIX` | 同一存储桶内的环境隔离前缀 | 无 |
| `OBJECT_STORAGE_FORCE_PATH_STYLE` | MinIO 等服务是否强制 path-style URL | `false` |
| `OBJECT_STORAGE_SSE` | 可选服务端加密算法，例如 AWS 的 `AES256` | 无 |
| `AI_PROVIDER` | 回答生成：`local`、`ollama` 或 `openai` | `local` |
| `EMBEDDING_PROVIDER` | 语义嵌入：`disabled`、`ollama` 或 `openai` | `disabled` |
| `AI_MODEL` | 回答模型名称；启用 `openai` 时必填 | Ollama：`qwen3:4b` |
| `EMBEDDING_MODEL` | 嵌入模型名称；启用 `openai` 时必填 | Ollama：`bge-m3` |
| `OLLAMA_BASE_URL` | 仅允许 API 所在机器的回环 HTTP(S) 地址 | `http://127.0.0.1:11434`；本地启动器使用 11435 |
| `OLLAMA_TIMEOUT_SECONDS` | 本地模型请求超时，范围 1–600 秒 | `180` |
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

> 当前无付费资源方案采用上文的完整本地版。以下 Render、云存储及生产运维内容保留作为技术参考，
> 不要求本地用户执行，也不意味着已开通或批准购买任何服务。

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

这是旧 Worker 占位入口的输出。新版 Worker 常驻轮询，`--once` 只执行一个周期；它不提供网页。
完整本地模型网站使用 `.venv/bin/python scripts/local_ai.py start`；升级前备份、安装依赖并重新构建网页。

### Render 页面显示 `API 请求失败`

先查看 Web Service 的 **Logs**，找到同一时间的 Python 或 PostgreSQL 异常。不要只看前端提示；
修复后应增加回归测试、推送新提交并验证线上业务操作。

### 文档上传后为什么只有已发布版本能被问答使用

这是证据完整性设计。`ready` 只表示解析完成，只有知识管理员明确发布的版本才能进入默认检索。
普通空间查看者的文档列表和阅读器也只返回已发布版本；空间管理者和企业管理员可以在发布前预览
最新版本。每次阅读都会记录文档、版本和访问级别，但不会把正文写入审计日志。

### 如何启用语义检索和外部生成模型

当前不产生模型 API 费用的方案请使用[本地 Ollama 配置](#local-ai-setup)。下面的外部服务适配器仅作参考，
可能产生费用，不属于当前推荐安装流程，不需要申请或填写外部 API 密钥。

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

以下勾选代表功能代码已实现，不代表线上配置或真实企业验收已完成。
十项试点计划的实现、部署与验收差距见[企业封闭试点状态](docs/09-closed-pilot-readiness.md)。

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
- [x] 企业级成员停用/恢复、旧会话访问拦截及成员页操作
- [x] 本地 SQLite、原件和应用密钥的加密备份、校验与隔离恢复工具
- [x] 本机 ClamAV 上传前扫描适配、故障拒绝和审计（需另行启动扫描服务）
- [x] TOTP 双重验证、一次性恢复码与验证码防重放
- [x] 企业级 MFA 强制策略与恢复码安全轮换
- [x] 企业管理员审计 CSV 导出、行数门禁与导出行为追踪
- [x] 授权后加密向量与关键词混合检索（当前规模）
- [x] 可配置的大模型生成、严格结构化输出与引用校验
- [ ] PostgreSQL pgvector 规模化检索与租户过滤召回评测
- [ ] 决策证据包和人工审批
- [x] 本地中英文 PDF OCR 与 DOCX 表格提取（复杂文件需人工核对）
- [x] 持久异步 Worker、租约接管、有限重试、取消与管理界面
- [x] 可选 OIDC SSO / SCIM 用户和组同步、停用隔离与 MFA 保留（待真实 IdP 联调）
- [ ] 批量导入、分布式任务容量验收与外部知识连接器
- [ ] 评测集、安全门禁、备份和生产运维

## 文档版本管理与撤回发布

空间管理者（同时具备企业文档管理权限）或企业管理员可在文档列表点击“版本管理”：

- 查看所有版本、预览指定版本；即使最新版本是草稿，也能管理仍在生效的旧版本。
- “撤回发布”将指定已发布版本退回待发布，保留加密原件、文本与索引，不删除文件。
- 撤回后，普通成员不能再通过列表、详情或阅读接口读取该版本，AI 新问题不再检索它。
- 只要历史回答有任一引用版本已撤回或被替代，历史接口就隐藏整条回答，避免答案正文泄露旧资料。
  原回答保留在数据库；重新发布同一版本且权限符合时可再次显示。无引用的拒答不受影响。
- 可重新发布待发布或已替代的版本；同一文档的发布变更串行执行，当前已发布版本会被替代。
- 模型生成结束后再次核对来源发布状态与空间权限；来源变化时拒绝返回该次答案，请重新提问。
- 发布与撤回都会记录审计事件，不记录正文。已经下载、截屏、显示的内容无法远程收回，
  已发出的在途响应也不是远程擦除范围。Render 临时磁盘的原件持久性限制仍然存在。

API：`POST /v1/tenants/{tenant_id}/document-versions/{version_id}/unpublish`；
指定版本预览：`GET /v1/tenants/{tenant_id}/documents/{document_id}/content?version_id=...`。
无新增迁移或付费服务。回归测试：`services/api/tests/test_document_publication.py`。

开发交付流程：每轮功能修改 → 自动化测试与前端构建 → 检查提交内容无密钥/企业数据 →
提交并推送 GitHub → Render 手动部署 → 核对部署提交、健康检查和新接口。
推送成功不等于部署成功；构建或健康检查失败时先修复，不宣称上线。
每次部署前须保留重要原件副本，避免临时磁盘清空造成不可恢复的数据损失。

## 项目文档

- [后台处理、SSO 与 SCIM 配置和验收](docs/10-worker-sso-scim.md)

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
- [企业封闭试点状态、本地备份与文件安全扫描](docs/09-closed-pilot-readiness.md)
