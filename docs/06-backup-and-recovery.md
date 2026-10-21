# 备份、恢复演练与数据保留

## 当前能力与边界

仓库提供 PostgreSQL 逻辑备份工具。工具调用 `pg_dump` 生成 custom-format dump，使用独立的
`BACKUP_ENCRYPTION_KEY` 做 AES-256-GCM 认证加密，再把密文和 JSON 完整性清单写入本地开发目录或
私有 S3 兼容存储桶。清单记录密文大小、SHA-256、创建时间和预计删除时间。

恢复工具只接受 PostgreSQL 数据库，而且目标库名必须包含 `_restore` 或 `_drill`，还必须通过
`--confirm-target` 再次准确确认。恢复后会检查 Alembic 版本和四张关键表。它不会创建、删除数据库，
也不会自动对线上数据库执行恢复。

这套工具不能代替云数据库快照。正式环境应同时启用托管 PostgreSQL 的时间点恢复、跨区域私有备份、
对象存储版本控制和服务商磁盘加密。

## 保留策略基线

| 数据 | 默认策略 | 约束 |
| --- | --- | --- |
| 数据库加密备份 | 每日一次，保留 35 天 | 至少保留 7 个可恢复点 |
| 企业审计事件 | 在线保留 2555 天（约 7 年） | 配置不得短于 365 天 |
| 恢复演练 | 每月至少一次 | 只能恢复到隔离数据库，并记录结果 |

应用不会自动删除追加式审计事件。`AUDIT_RETENTION_DAYS` 表示组织的最低保留承诺；在实现 WORM
审计归档、归档校验和独立数据库维护角色之前，超过期限的记录仍保留在 PostgreSQL 中。这样避免一个
被攻陷的应用运行账户通过“保留期任务”擦除审计证据。

对象存储生命周期应由云平台配置：仅删除已超过 `delete_after` 的备份，并保证日备份正常运行时至少
存在 `BACKUP_MIN_RECOVERY_POINTS` 个恢复点。建议对备份桶启用版本控制、对象锁和与应用文件不同的
最小权限服务账号。

## 配置

生成一个与应用主密钥不同的备份密钥：

```bash
openssl rand -base64 32
```

将结果存入部署平台 Secret `BACKUP_ENCRYPTION_KEY`。不要把它写入 `.env`、日志、工单或 Git。
生产环境还需要：

```dotenv
BACKUP_STORAGE_BACKEND=s3
BACKUP_STORAGE_BUCKET=enterprise-ai-backups
BACKUP_STORAGE_ACCESS_KEY=...
BACKUP_STORAGE_SECRET_KEY=...
BACKUP_STORAGE_REGION=us-east-1
BACKUP_STORAGE_PREFIX=production
BACKUP_STORAGE_SSE=AES256
BACKUP_RETENTION_DAYS=35
BACKUP_MIN_RECOVERY_POINTS=7
AUDIT_RETENTION_DAYS=2555
```

AWS S3 可省略 endpoint；R2、B2 或 MinIO 需要设置 `BACKUP_STORAGE_ENDPOINT`，必要时设置
`BACKUP_STORAGE_FORCE_PATH_STYLE=true`。生产环境如果选择本地备份后端，工具会拒绝运行。

## 创建备份

容器镜像已经包含 PostgreSQL client。在具有数据库只读备份权限和备份桶写入权限的独立定时任务中运行：

```bash
PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/python -m enterprise_ai_api.backup policy

PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/python -m enterprise_ai_api.backup create
```

第二个命令读取 `DATABASE_URL` 和全部 `BACKUP_*` Secret，成功后输出 `backup_id`、
`manifest_object_key`、校验值和删除日期。数据库密码只通过 `PG*` 环境变量传给 `pg_dump`，不会进入
命令参数或 JSON 清单。

建议在 Render Cron Job、云调度器或等价的私有运行环境中每天执行一次。调度器必须在失败时告警，且
每次至少检查：命令退出码为 0、两个对象都存在、清单校验值与密文一致。当前 Blueprint 不会自动创建
付费定时任务，也不会伪装成已经启用了线上备份。

## 隔离恢复演练

1. 创建临时 PostgreSQL 数据库，例如 `enterprise_ai_drill`，并确保它不接收线上流量。
2. 给演练进程配置备份桶只读权限、`BACKUP_ENCRYPTION_KEY` 和目标数据库连接串。
3. 使用备份命令返回的 `manifest_object_key` 执行恢复：

```bash
PYTHONPATH=services/api/src:packages/core/src \
  .venv/bin/python -m enterprise_ai_api.backup restore-drill \
  --manifest-key 'postgres/2026/10/21/<backup-id>.manifest.json' \
  --target-url "$RESTORE_DATABASE_URL" \
  --confirm-target enterprise_ai_drill
```

4. 保存命令输出中的备份 ID、Alembic revision、演练时间和负责人，并另外抽查登录、空间权限、文档
   元数据和审计事件数量。
5. 完成检查后，由数据库平台管理员删除隔离数据库；应用工具本身不提供删除命令。

恢复演练会对目标库执行 `pg_restore --clean --if-exists`，所以目标库中的原有内容会被替换。命名限制和
确认参数是防误操作门禁，不是权限边界；生产恢复仍应使用双人审批和平台级访问控制。

## 密钥与灾难恢复

- 应用加密主密钥和备份加密密钥都必须进入独立的机密管理系统，并有受控的离线恢复副本。
- 丢失 `ENCRYPTION_MASTER_KEY` 会使数据库中的字段及上传原件不可读；丢失
  `BACKUP_ENCRYPTION_KEY` 会使数据库备份不可解密。
- 轮换前先实现密钥版本标记和双密钥解密窗口，不能直接覆盖旧值。
- 每次变更数据库结构、加密格式或对象存储供应商后，都应额外执行一次恢复演练。
