"use client";

import { useCallback, useEffect, useState } from "react";
import {
  cancelDocumentApproval, decideDocumentApproval, enableDocumentApproval, getDocumentDetail,
  listApprovals, publishDocumentVersion, submitDocumentApproval,
  type ApprovalStatus, type DocumentApproval, type DocumentDetail,
} from "../lib/api";

const labels: Record<ApprovalStatus, string> = {
  pending: "待审批", approved: "已通过 · 待发布", rejected: "已驳回",
  cancelled: "已取消", published: "已发布 · 审批已使用",
};
const short = (id: string) => id.slice(0, 8);

export function ApprovalInbox({ actor, tenant, revision, onOpen }: {
  actor: string; tenant: string; revision: number;
  onOpen: (document: string, version: string) => void;
}) {
  const [rows, setRows] = useState<DocumentApproval[]>([]);
  const [filter, setFilter] = useState<ApprovalStatus | "all">("pending");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    queueMicrotask(() => { if (active) { setBusy(true); setRows([]); setError(""); } });
    listApprovals(actor, tenant, { offset, status: filter === "all" ? undefined : filter })
      .then((items) => { if (active) setRows(items); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "无法读取审批"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [actor, tenant, filter, offset, revision, refresh]);
  return <>
    <div className="page-heading"><div><p className="kicker">企业协作 · 第二阶段</p>
      <h1>文件审批</h1><p>只显示你当前有编辑权限的文档审批。通过审批不等于发布，也不增加阅读权限。</p></div>
      <button className="secondary-button" disabled={busy} onClick={() => setRefresh((v) => v + 1)}>刷新审批</button>
    </div>
    <div className="heading-actions approval-filters">
      <label>审批状态 <select value={filter} onChange={(e) => { setFilter(e.target.value as ApprovalStatus | "all"); setOffset(0); }}>
        <option value="all">全部记录</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <span>从知识空间的“版本管理 → 审批与发布”发起申请。</span>
    </div>
    {error && <p role="alert">{error}</p>}
    <section className="approval-list" aria-label="审批列表">
      {busy ? <p>正在核验权限并加载…</p> : rows.length === 0 ? <p>当前没有可见的审批记录。</p> : rows.map((row) =>
        <article key={row.id} className="approval-card">
          <div><h2>{row.document_title}</h2><p>v{row.version_number} · 第 {row.attempt} 次申请 · {labels[row.status]}</p>
            <small>提交人 {short(row.submitted_by)} · {new Date(row.created_at).toLocaleString()}</small></div>
          <button className="secondary-button" onClick={() => onOpen(row.document_id, row.version_id)}>
            {row.can_decide ? "查看并审批" : "查看审批记录"}
          </button>
        </article>)}
    </section>
    <div className="heading-actions">
      <button disabled={busy || offset === 0} onClick={() => setOffset((v) => Math.max(0, v - 50))}>上一页</button>
      <span>第 {Math.floor(offset / 50) + 1} 页</span>
      <button disabled={busy || rows.length < 50} onClick={() => setOffset((v) => v + 50)}>下一页</button>
    </div>
  </>;
}

export function ApprovalPanel({ actor, tenant, documentId, versionId, onClose, onChanged, onPreview }: {
  actor: string; tenant: string; documentId: string; versionId: string;
  onClose: () => void; onChanged: () => Promise<void>;
  onPreview: (document: string, version: string) => Promise<void>;
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [rows, setRows] = useState<DocumentApproval[]>([]);
  const [note, setNote] = useState("");
  const [review, setReview] = useState("");
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState("");
  const [more, setMore] = useState(false);
  const [confirmation, setConfirmation] = useState<{ label: string; run: () => Promise<unknown> } | null>(null);
  const refresh = useCallback(async () => {
    const [document, approvals] = await Promise.all([
      getDocumentDetail(actor, tenant, documentId),
      listApprovals(actor, tenant, { version_id: versionId }),
    ]);
    setDetail(document); setRows(approvals); setMore(approvals.length === 50);
  }, [actor, tenant, documentId, versionId]);
  useEffect(() => {
    let active = true;
    Promise.all([getDocumentDetail(actor, tenant, documentId), listApprovals(actor, tenant, { version_id: versionId })])
      .then(([document, approvals]) => { if (active) { setDetail(document); setRows(approvals); setMore(approvals.length === 50); } })
      .catch((err) => { if (active) setNotice(err instanceof Error ? err.message : "无法读取审批"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [actor, tenant, documentId, versionId]);
  async function execute(action: () => Promise<unknown>, success: string) {
    setBusy(true); setNotice(""); setConfirmation(null);
    try {
      await action(); await refresh(); await onChanged();
      setNote(""); setReview(""); setNotice(success);
    } catch (err) {
      // Drop old notes and capabilities on errors, including revoked access.
      setRows([]); setDetail(null);
      setNotice(err instanceof Error ? err.message : "操作失败，请刷新检查状态");
      try { await refresh(); } catch { /* Remain fail-closed after access revocation. */ }
    } finally { setBusy(false); }
  }
  const version = detail?.versions.find((v) => v.id === versionId);
  const latest = [...rows].sort((a, b) => b.attempt - a.attempt)[0];
  const reviewable = version && ["ready", "superseded"].includes(version.status);
  async function preview() {
    setBusy(true); setNotice("");
    try { await onPreview(documentId, versionId); }
    catch (err) {
      setRows([]); setDetail(null);
      setNotice(err instanceof Error ? err.message : "无法读取此版本");
    } finally { setBusy(false); }
  }
  return <div className="dialog-backdrop"><section className="dialog document-reader approval-panel"
    role="dialog" aria-modal="true" aria-labelledby="approval-title">
    <button className="dialog-close" aria-label="关闭审批" disabled={busy} onClick={onClose}>×</button>
    <p className="kicker">具体版本 · 双人审批</p><h2 id="approval-title">{detail?.document.title ?? "文件审批"}</h2>
    {notice && <p role="status" className="form-notice">{notice}</p>}
    {version && <>
      <p>v{version.version_number} · {version.filename} · {version.status === "published" ? "已发布" : "未发布版本"}</p>
      <button className="secondary-button" disabled={busy} onClick={() => void preview()}>预览此版本原文</button>
      <p className="upload-hint">提交者和上传者不能审批本版本。审批人须已有发布权限；机密文档仅所有者可审批。
        若所有者上传了机密版本，需先由获授权的其他编辑成员提交实际修改后的新版本，不能自批。</p>
      {confirmation && <div role="alert" className="publication-confirm"><b>{confirmation.label}</b>
        <div className="heading-actions"><button disabled={busy} onClick={() => setConfirmation(null)}>取消操作</button>
          <button className="primary-button" disabled={busy} onClick={() => void execute(confirmation.run, "操作已完成并记录到审计日志。")}>确认操作</button></div>
      </div>}
      {!detail?.document.approval_required ? <div className="knowledge-status"><h3>此文档尚未启用发布审批</h3>
        <p>启用后所有版本的后续发布必须先审批，不影响当前已发布版本。本阶段不提供关闭审批的入口。</p>
        {detail?.can_publish && <button className="primary-button" disabled={busy} onClick={() => setConfirmation({
          label: "确定为此文档及后续版本启用强制发布审批？启用后不能关闭。",
          run: () => enableDocumentApproval(actor, tenant, versionId),
        })}>启用发布审批</button>}
      </div> : <div className="knowledge-status"><h3>已启用强制发布审批</h3>
        <p>通过后仍需发布负责人发布；审批不会自动扩大权限。撤回后重新发布须再次审批。</p>
        {reviewable && detail.can_edit && !["pending", "approved"].includes(latest?.status ?? "") && <form className="dialog-form"
          onSubmit={(event) => { event.preventDefault(); void execute(
            () => submitDocumentApproval(actor, tenant, versionId, note), "审批已提交，等待有发布权限的其他成员处理。",
          ); }}>
          <label>提交说明<textarea maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="说明修改内容和希望审核的事项" /></label>
          <button className="primary-button" disabled={busy} type="submit">提交此版本审批</button>
        </form>}
        {latest?.status === "approved" && detail.can_publish && reviewable &&
          <button className="primary-button" disabled={busy} onClick={() => setConfirmation({
            label: "确认发布这个已审批版本？它会替代当前发布版本；服务端将再次核验审批和权限。",
            run: () => publishDocumentVersion(actor, tenant, versionId),
          })}>发布已审批版本</button>}
      </div>}
      <h3>本版本审批记录</h3>
      {rows.length === 0 ? <p>尚无审批记录。</p> : rows.map((row) => <article className="approval-record" key={row.id}>
        <h4>第 {row.attempt} 次申请 · {labels[row.status]}</h4>
        <small>提交人 {short(row.submitted_by)} · {new Date(row.created_at).toLocaleString()}</small>
        <p className="approval-note">{row.submission_note || "未填写提交说明"}</p>
        {row.reviewed_by && <><small>审批人 {short(row.reviewed_by)}</small><p className="approval-note">{row.review_note}</p></>}
        {row.can_decide && <div className="dialog-form">
          <label>审批意见<textarea maxLength={2000} required value={review} onChange={(e) => setReview(e.target.value)} /></label>
          <div className="heading-actions">{(["approved", "rejected"] as const).map((decision) =>
            <button key={decision} disabled={busy || !review.trim()} onClick={() => setConfirmation({
              label: decision === "approved" ? "确认通过此版本审批？不会自动发布。" : "确认驳回此版本？原已发布版本不受影响。",
              run: () => decideDocumentApproval(actor, tenant, row.id, decision, review),
            })}>{decision === "approved" ? "通过审批" : "驳回审批"}</button>)}</div>
        </div>}
        {row.can_cancel && <button disabled={busy} onClick={() => setConfirmation({
          label: "确认取消此审批？已通过但未发布的审批也将失效。",
          run: () => cancelDocumentApproval(actor, tenant, row.id),
        })}>取消本次审批</button>}
      </article>)}
      {more && <button disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          const older = await listApprovals(actor, tenant, { version_id: versionId, offset: rows.length });
          setRows((current) => [...current, ...older]); setMore(older.length === 50);
        } catch (err) { setRows([]); setDetail(null); setNotice(String(err)); }
        finally { setBusy(false); }
      }}>加载更早记录</button>}
    </>}
  </section></div>;
}
