const capabilities = [
  { title: "权限先于检索", detail: "未获授权的内容不会进入候选集或模型上下文。" },
  { title: "回答带证据", detail: "事实性主张关联不可变文档版本和稳定原文位置。" },
  { title: "风险显式呈现", detail: "冲突、过期和证据不足会成为正式回答状态。" },
  { title: "重要决定由人确认", detail: "保留 AI 草稿、人工修改、批准或拒绝的完整链路。" },
];

const workflow = ["导入企业资料", "权限内检索", "生成引用回答", "形成证据包", "人工确认"];

export default function Home() {
  return (
    <main>
      <section className="hero">
        <p className="eyebrow">Enterprise AI Knowledge Assistant</p>
        <h1>让企业知识成为<br />可核验的决策依据</h1>
        <p className="lede">
          一个不绑定行业的企业知识库与 AI 决策助手。所有回答都尊重权限、显示证据，并为重要决定保留人工责任。
        </p>
        <div className="status"><span />MVP 架构骨架已建立</div>
      </section>

      <section aria-labelledby="workflow-heading" className="panel">
        <p className="section-label" id="workflow-heading">首个完整工作流</p>
        <ol className="workflow">
          {workflow.map((step, index) => (
            <li key={step}><span>{String(index + 1).padStart(2, "0")}</span>{step}</li>
          ))}
        </ol>
      </section>

      <section aria-label="产品能力" className="grid">
        {capabilities.map((capability) => (
          <article key={capability.title}>
            <h2>{capability.title}</h2>
            <p>{capability.detail}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
