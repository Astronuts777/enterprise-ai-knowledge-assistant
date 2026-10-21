# 混合检索与可配置 AI

## 1. 默认行为

系统默认使用本地关键词检索和原文摘录回答：

- `AI_PROVIDER=local`
- `EMBEDDING_PROVIDER=disabled`
- `AI_ALLOW_RESTRICTED=false`

因此，默认部署不会因问答或上传文档而调用外部模型。公开演示环境也固定使用这一模式。

## 2. 权限与数据流

问答先根据服务端身份计算用户可访问的知识空间，再查询其中已发布的文档版本。任何关键词匹配、
语义排序和生成模型调用都发生在这个授权候选集之后，不会先检索全企业数据再丢弃越权结果。

启用外部嵌入后，系统在上传或手动重建索引时向供应商发送解析后的文档块；启用外部回答后，系统
向供应商发送问题和本次检索到的少量证据片段。不会发送整库内容、未发布版本、其他企业数据、
API 密钥、数据库凭据或用户会话。Responses 请求设置 `store=false`，但企业仍需自行评审供应商的
合同、地域、日志、滥用监控和数据保留条款。

`restricted` 空间默认禁止外部嵌入和外部回答。即使同时查询多个空间，只要范围中包含严格受限空间，
本次问题就保守回退到本地检索与本地证据回答。只有企业完成风险评审并明确配置
`AI_ALLOW_RESTRICTED=true` 后才解除这一限制。

## 3. 混合检索

每个文档块可以保存一个模型名称和一组嵌入向量。向量使用与正文相同的 `EncryptedText` 类型，在
写入数据库前由 AES-256-GCM 认证加密。查询时，应用只读取当前租户、当前授权空间和已发布版本的
候选块，解密匹配模型的向量，计算余弦相似度，再与关键词覆盖分数组合。只有语义相似度达到最低
阈值的纯语义命中才会成为证据。

当前实现最多扫描 5,000 个授权候选块，返回 4 条证据，适合产品验证和中小知识空间。它刻意没有把
明文向量交给数据库索引，因此不能获得 pgvector 的大规模近邻检索性能。进入大规模生产前，需要在
以下方案中作出明确的数据保护决策：

1. 接受数据库可读取向量的风险，在 PostgreSQL RLS 条件内使用 pgvector；
2. 为每个租户部署隔离的向量服务或数据库；
3. 使用支持可验证访问控制的托管检索服务；
4. 保持应用层加密，并按空间或文档建立受限的缓存与分片。

无论选择哪一种，都必须先做租户过滤后的召回率、跨租户隔离和权限变更回归测试。

## 4. 生成与引用校验

外部回答适配器使用严格 JSON Schema，要求模型返回“证据是否充分”和一组“主张 + 引用编号”。
模型返回的文本不是可信数据，服务端会：

1. 拒绝未知字段、空主张和没有引用的主张；
2. 拒绝任何超出本次候选证据范围的编号；
3. 删除模型自行写入的 `[数字]` 标记；
4. 只保留实际使用的证据，并由服务端连续重新编号；
5. 在模型不可用或输出不合规时返回 503，并写入不含正文的失败审计事件。

本地模式不生成新事实，只拼接相关原文摘录。两种模式在证据不足时都明确拒答。

结构化输出和嵌入请求分别参照 OpenAI 官方的
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
与 [Embeddings API](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create)。

## 5. 配置与启用

在部署平台的 Secret 管理器中设置，不要写入 Git：

```dotenv
AI_PROVIDER=openai
AI_MODEL=<approved-response-model>
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=<approved-embedding-model>
OPENAI_API_KEY=<secret>
AI_ALLOW_RESTRICTED=false
```

可选设置 `OPENAI_PROJECT`、`OPENAI_BASE_URL`、`EMBEDDING_DIMENSIONS` 和
`AI_REQUEST_TIMEOUT_SECONDS`。生产环境的自定义 Base URL 必须使用 HTTPS。配置缺少密钥或模型名称
时应用拒绝启动，不会悄悄降级到未授权的供应商。

新上传文档会自动建立语义索引。已有文档由知识管理员调用：

```text
POST /v1/tenants/{tenant_id}/document-versions/{version_id}/semantic-index
```

重建会替换该版本所有文本块的向量和模型名称，并写入审计事件。嵌入失败不会写入部分结果。

## 6. 上线验收

- 用同义词问题验证纯关键词无法命中而语义检索可以命中；
- 查看数据库原始列，确认嵌入以 `enc:v1:` 密文保存；
- 验证无权用户、未发布版本和其他企业内容不会成为候选证据；
- 验证模型伪造或越界引用被拒绝；
- 验证 `restricted` 空间不会产生外部 provider 调用；
- 验证供应商超时返回通用错误，日志和审计中没有问题正文或证据正文；
- 建立包含可回答、不可回答、冲突和权限变化的离线评测集后再扩大使用范围。
