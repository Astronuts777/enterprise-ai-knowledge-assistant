"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  getDocumentAccess, listEmployees, saveEmployee, setDocumentAccess, setDocumentGrant,
  type DocumentAccess, type DocumentSummary, type Employee, type Tenant,
} from "../lib/api";

const roles = { enterprise_admin: "企业管理员", knowledge_admin: "知识管理员", member: "普通成员" };

export default function EmployeeCenter({ actor, tenant, documents, onChanged }: {
  actor: string; tenant: Tenant; documents: DocumentSummary[]; onChanged: () => Promise<void>;
}) {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<Employee | null>(null);
  const [documentId, setDocumentId] = useState("");
  const [confirmation, setConfirmation] = useState<{ message: string; execute: () => Promise<void> } | null>(null);
  const [access, setAccess] = useState<DocumentAccess | null>(null);
  const owner = tenant.owner_user_id === actor;
  const manager = owner || employees.find((e) => e.user_id === actor)?.role === "enterprise_admin";

  useEffect(() => {
    let active = true;
    listEmployees(actor, tenant.id).then((rows) => {
      if (active) { setEmployees(rows); setMore(rows.length === 100); }
    }).catch((error) => { if (active) setNotice(String(error)); });
    return () => { active = false; };
  }, [actor, tenant.id]);

  async function run(work: () => Promise<void>) {
    setBusy(true); setNotice("");
    try { await work(); } catch (error) { setNotice(error instanceof Error ? error.message : "操作失败"); }
    finally { setBusy(false); }
  }
  async function profile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    await run(async () => {
      const saved = await saveEmployee(actor, tenant.id, selected.user_id,
        String(form.get("name") ?? ""), String(form.get("department") ?? ""));
      setEmployees((rows) => rows.map((e) => e.user_id === saved.user_id ? saved : e));
      setSelected(saved); setNotice("员工资料已保存；姓名和部门在数据库中加密存储。");
    });
  }
  async function chooseDocument(id: string) {
    setDocumentId(id); setAccess(null); setConfirmation(null);
    if (id) await run(async () => { setAccess(await getDocumentAccess(actor, tenant.id, id)); });
  }
  async function changeMode(mode: "space" | "restricted") {
    const message = mode === "space"
      ? "恢复空间权限后，空间成员和企业管理员可能获得访问权；现有逐人授权会清空。确认继续？"
      : "改为仅所有者和指定成员访问后，其他空间成员及管理员将失去访问权。确认继续？";
    setConfirmation({ message, execute: async () => { await run(async () => {
      await setDocumentAccess(actor, tenant.id, documentId, mode);
      setAccess(await getDocumentAccess(actor, tenant.id, documentId));
      await onChanged(); setNotice("文档访问范围已更新。");
    }); } });
  }
  async function grant(role: "reader" | "editor" | "none") {
    if (!selected) return;
    setConfirmation({ message: "确认更新这名员工对所选文档的权限？此操作将记录到审计日志。", execute: async () => { await run(async () => {
      await setDocumentGrant(actor, tenant.id, documentId, selected.user_id, role);
      setAccess(await getDocumentAccess(actor, tenant.id, documentId));
      await onChanged(); setNotice(role === "none" ? "授权已撤销，后续访问及历史引用将重新检查权限。" : "文档授权已保存，不改变员工的企业角色。");
    }); } });
  }
  const currentGrant = access?.grants.find((g) => g.user_id === selected?.user_id)?.role;
  return <section className="employee-center">
    <div className="page-heading"><div><p className="kicker">企业协作 · 第一阶段</p><h1>员工中心</h1>
      <p>同企业成员目录、员工资料和文档级授权。文件审批请前往“文件审批”页面；聊天尚未开放。</p></div></div>
    <div className="knowledge-status">
      <h2>{owner ? "你是本企业所有者" : "企业所有者与管理员是不同权限"}</h2>
      <p>{tenant.owner_user_id
        ? "机密文档由本企业所有者逐人授权；管理员身份不会绕过机密文档权限。所有者身份不绑定任何固定账号。"
        : "本企业为旧版本创建，尚未核验所有者。不会自动提升现有账号权限；文档级授权暂不可用，原空间权限保持不变。"}</p>
    </div>
    {notice && <p role="status" className="form-notice">{notice}</p>}
    {confirmation && <div className="publication-confirm" role="alert"><p>{confirmation.message}</p><div className="heading-actions"><button disabled={busy} onClick={() => setConfirmation(null)}>取消变更</button><button className="primary-button" disabled={busy} onClick={() => { const action = confirmation.execute; setConfirmation(null); void action(); }}>确认变更权限</button></div></div>}
    <div className="employee-grid">
      <section className="employee-list" aria-label="员工目录">
        {employees.map((employee) => <button key={employee.user_id} disabled={busy}
          className={selected?.user_id === employee.user_id ? "employee-item selected" : "employee-item"}
          onClick={() => { setSelected(employee); setNotice(""); setConfirmation(null); }}>
          <strong>{employee.display_name || (employee.user_id === actor ? "当前账号" : "未设置姓名")}</strong>
          <span>{employee.department || "未设置部门"} · {employee.is_owner ? "企业所有者" : roles[employee.role]}</span>
          <small>{employee.user_id} · {employee.active ? "已启用" : "已停用"}</small>
        </button>)}
        {more && <button disabled={busy} onClick={() => void run(async () => {
          const rows = await listEmployees(actor, tenant.id, employees.length);
          setEmployees((current) => [...current, ...rows]); setMore(rows.length === 100);
        })}>加载更多员工</button>}
      </section>
      <section className="employee-detail">
        {!selected ? <p>选择左侧员工，查看资料或配置文档权限。</p> : <>
          <h2>{selected.display_name || "员工资料"}</h2>
          <p className="upload-hint">身份标识：{selected.user_id}</p>
          <form key={selected.user_id} className="dialog-form" onSubmit={profile}>
            <label>姓名<input name="name" maxLength={100} defaultValue={selected.display_name}
              disabled={busy || !(selected.user_id === actor || (manager && !selected.is_owner))} /></label>
            <label>部门<input name="department" maxLength={100} defaultValue={selected.department}
              disabled={busy || !(selected.user_id === actor || (manager && !selected.is_owner))} /></label>
            {(selected.user_id === actor || (manager && !selected.is_owner)) &&
              <button className="secondary-button" disabled={busy} type="submit">保存员工资料</button>}
          </form>
          {owner && <div className="dialog-form document-grant-panel">
            <h3>文档级授权</h3>
            <label>选择文档<select value={documentId} disabled={busy} onChange={(e) => void chooseDocument(e.target.value)}>
              <option value="">请选择</option>{documents.map((d) =>
                <option value={d.document.id} key={d.document.id}>{d.document.title} · {d.document.access_mode === "restricted" ? "指定成员" : "空间权限"}</option>)}
            </select></label>
            {access && <>
              <p>访问范围：{access.access_mode === "space" ? "继承空间权限" : "仅所有者与指定成员"}</p>
              <button className="secondary-button" disabled={busy} onClick={() => void changeMode(access.access_mode === "space" ? "restricted" : "space")}>
                {access.access_mode === "space" ? "设为仅指定成员可访问" : "恢复空间权限"}
              </button>
              {access.access_mode === "restricted" && <><p>该员工权限：{selected.is_owner ? "所有者（不可撤销）" : currentGrant === "editor" ? "阅读及修改" : currentGrant === "reader" ? "只读" : "无权限"}</p>
                <p className="upload-hint">只读仅查看已发布版本；修改允许查看草稿和上传新版。发布、撤回及再授权仍由所有者操作。</p>
                {!selected.is_owner && <div className="heading-actions">
                  <button disabled={busy || !selected.active} onClick={() => void grant("reader")}>授予只读</button>
                  <button disabled={busy || !selected.active} onClick={() => void grant("editor")}>授予阅读及修改</button>
                  <button disabled={busy || !currentGrant} onClick={() => void grant("none")}>撤销授权</button>
                </div>}</>}
            </>}
          </div>}
        </>}
      </section>
    </div>
  </section>;
}
