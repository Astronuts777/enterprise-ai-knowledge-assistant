"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  Classification,
  Membership,
  MembershipRole,
  Space,
  Tenant,
  createSpace,
  createTenant,
  getTenant,
  listMembers,
  listSpaces,
  saveMember,
} from "../lib/api";

type View = "overview" | "spaces" | "members";

const ACTOR_KEY = "enterprise-ai.dev-actor-id";
const TENANT_KEY = "enterprise-ai.active-tenant-id";

const roleLabels: Record<MembershipRole, string> = {
  enterprise_admin: "企业管理员",
  knowledge_admin: "知识管理员",
  member: "普通成员",
};

const classificationLabels: Record<Classification, string> = {
  internal: "内部",
  confidential: "机密",
  restricted: "严格受限",
};

function Icon({ name }: { name: "home" | "spaces" | "members" | "settings" | "plus" | "arrow" | "shield" }) {
  const paths: Record<typeof name, ReactNode> = {
    home: <><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5M9 21v-7h6v7" /></>,
    spaces: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M9 9v11" /></>,
    members: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.09A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.09 14H3v-4h.09A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63h.02A1.7 1.7 0 0 0 10 3.09V3h4v.09a1.7 1.7 0 0 0 1 1.54 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9v.02A1.7 1.7 0 0 0 20.91 10H21v4h-.09A1.7 1.7 0 0 0 19.4 15Z" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="m9 18 6-6-6-6" />,
    shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Zm-3-10 2 2 4-4" />,
  };
  return <svg aria-hidden="true" className="icon" viewBox="0 0 24 24">{paths[name]}</svg>;
}

function shortId(id: string): string {
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(new Date(value));
}

export default function Dashboard() {
  const [actorId, setActorId] = useState("");
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [members, setMembers] = useState<Membership[]>([]);
  const [canManageMembers, setCanManageMembers] = useState(true);
  const [view, setView] = useState<View>("overview");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [spaceDialog, setSpaceDialog] = useState(false);
  const [memberDialog, setMemberDialog] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const loadWorkspace = useCallback(async (userId: string, organizationId: string) => {
    const [loadedTenant, loadedSpaces] = await Promise.all([
      getTenant(userId, organizationId),
      listSpaces(userId, organizationId),
    ]);
    setTenant(loadedTenant);
    setSpaces(loadedSpaces);
    try {
      setMembers(await listMembers(userId, organizationId));
      setCanManageMembers(true);
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setMembers([]);
        setCanManageMembers(false);
      } else {
        throw error;
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      const storedActor = window.localStorage.getItem(ACTOR_KEY) ?? crypto.randomUUID();
      const storedTenant = window.localStorage.getItem(TENANT_KEY);
      window.localStorage.setItem(ACTOR_KEY, storedActor);
      setActorId(storedActor);
      setTenantId(storedTenant);

      if (!storedTenant) {
        setReady(true);
        return;
      }

      loadWorkspace(storedActor, storedTenant)
        .catch((error: unknown) => {
          if (error instanceof ApiError && error.status === 404) {
            window.localStorage.removeItem(TENANT_KEY);
            setTenantId(null);
            setTenant(null);
            return;
          }
          setNotice(error instanceof Error ? error.message : "无法载入工作区");
        })
        .finally(() => {
          if (!cancelled) setReady(true);
        });
    });

    return () => {
      cancelled = true;
    };
  }, [loadWorkspace]);

  const actorMembership = useMemo(
    () => members.find((member) => member.user_id === actorId),
    [actorId, members],
  );

  async function handleCreateTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    if (!name) return;
    setBusy(true);
    setNotice(null);
    try {
      const created = await createTenant(actorId, name);
      window.localStorage.setItem(TENANT_KEY, created.id);
      setTenantId(created.id);
      await loadWorkspace(actorId, created.id);
      setNotice("企业工作区已创建");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建企业失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateSpace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!tenantId) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice(null);
    try {
      await createSpace(actorId, tenantId, {
        name: String(form.get("name") ?? "").trim(),
        classification: String(form.get("classification")) as Classification,
      });
      setSpaces(await listSpaces(actorId, tenantId));
      setSpaceDialog(false);
      setNotice("知识空间已创建");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建知识空间失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!tenantId) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice(null);
    try {
      await saveMember(actorId, tenantId, {
        user_id: String(form.get("user_id") ?? "").trim(),
        role: String(form.get("role")) as MembershipRole,
      });
      setMembers(await listMembers(actorId, tenantId));
      setMemberDialog(false);
      setNotice("成员权限已保存");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存成员失败");
    } finally {
      setBusy(false);
    }
  }

  function resetDemo() {
    window.localStorage.removeItem(TENANT_KEY);
    setTenantId(null);
    setTenant(null);
    setSpaces([]);
    setMembers([]);
    setView("overview");
  }

  if (!ready) {
    return <div className="loading-screen"><span className="spinner" />正在载入安全工作区</div>;
  }

  if (!tenant) {
    return (
      <main className="onboarding">
        <section className="onboarding-story">
          <div className="brand-mark"><span>EA</span> Enterprise AI</div>
          <div>
            <p className="kicker">可信知识，从边界开始</p>
            <h1>建立你的第一个<br />企业知识工作区</h1>
            <p className="onboarding-copy">把内部政策、项目资料与决策证据放进同一个有权限、有出处、可追溯的工作空间。</p>
          </div>
          <div className="trust-list">
            <span><Icon name="shield" />企业数据默认隔离</span>
            <span><Icon name="shield" />每个回答保留证据链</span>
            <span><Icon name="shield" />重要决定由人确认</span>
          </div>
        </section>
        <section className="onboarding-form-wrap">
          <form className="onboarding-form" onSubmit={handleCreateTenant}>
            <span className="step-label">步骤 1 / 1</span>
            <h2>创建企业</h2>
            <p>创建后，你将自动成为企业管理员。</p>
            <label htmlFor="tenant-name">企业名称</label>
            <input id="tenant-name" name="name" minLength={2} maxLength={160} placeholder="例如：远景科技" required />
            <button className="primary-button wide" disabled={busy} type="submit">
              {busy ? "正在创建…" : "进入工作区"}<Icon name="arrow" />
            </button>
            <div className="dev-identity"><span />本地演示身份：{shortId(actorId)}</div>
            {notice && <p className="form-error">{notice}</p>}
          </form>
        </section>
      </main>
    );
  }

  const navItems: { key: View; label: string; icon: "home" | "spaces" | "members" }[] = [
    { key: "overview", label: "总览", icon: "home" },
    { key: "spaces", label: "知识空间", icon: "spaces" },
    { key: "members", label: "成员与权限", icon: "members" },
  ];

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark compact"><span>EA</span><b>Enterprise AI</b></div>
        <nav aria-label="主导航">
          <p>工作区</p>
          {navItems.map((item) => (
            <button className={view === item.key ? "active" : ""} key={item.key} onClick={() => setView(item.key)}>
              <Icon name={item.icon} />{item.label}
              {item.key === "spaces" && spaces.length > 0 && <em>{spaces.length}</em>}
            </button>
          ))}
          <p>系统</p>
          <button className="disabled-nav" disabled><Icon name="settings" />企业设置<span>即将开放</span></button>
        </nav>
        <div className="sidebar-foot">
          <span className="security-dot" />权限隔离已启用
          <small>开发环境 · SQLite</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <button className="tenant-switcher"><span>{tenant.name.slice(0, 1)}</span><div><small>当前企业</small><b>{tenant.name}</b></div></button>
          <div className="topbar-user"><div><b>本地演示管理员</b><span>{actorMembership ? roleLabels[actorMembership.role] : "已授权成员"}</span></div><i>{actorId.slice(0, 2).toUpperCase()}</i></div>
        </header>

        <div className="content">
          {notice && <div className="toast" role="status">{notice}<button onClick={() => setNotice(null)}>×</button></div>}
          {view === "overview" && (
            <Overview tenant={tenant} spaces={spaces} members={members} onCreateSpace={() => setSpaceDialog(true)} onViewSpaces={() => setView("spaces")} />
          )}
          {view === "spaces" && (
            <SpacesView spaces={spaces} onCreate={() => setSpaceDialog(true)} />
          )}
          {view === "members" && (
            <MembersView members={members} actorId={actorId} canManage={canManageMembers} onAdd={() => setMemberDialog(true)} />
          )}
          <footer className="workspace-footer">企业 ID {shortId(tenant.id)}<button onClick={resetDemo}>切换演示企业</button></footer>
        </div>
      </section>

      {spaceDialog && <Dialog title="创建知识空间" description="空间决定资料的权限边界和敏感等级。" onClose={() => setSpaceDialog(false)}>
        <form className="dialog-form" onSubmit={handleCreateSpace}>
          <label>空间名称<input name="name" minLength={2} maxLength={160} placeholder="例如：公司政策" autoFocus required /></label>
          <label>保密级别<select name="classification" defaultValue="internal"><option value="internal">内部</option><option value="confidential">机密</option><option value="restricted">严格受限</option></select></label>
          <div className="dialog-actions"><button type="button" onClick={() => setSpaceDialog(false)}>取消</button><button className="primary-button" disabled={busy} type="submit">{busy ? "创建中…" : "创建空间"}</button></div>
        </form>
      </Dialog>}

      {memberDialog && <Dialog title="添加企业成员" description="当前阶段使用用户 UUID 模拟来自企业身份系统的用户。" onClose={() => setMemberDialog(false)}>
        <form className="dialog-form" onSubmit={handleAddMember}>
          <label>用户 UUID<div className="input-action"><input name="user_id" defaultValue={crypto.randomUUID()} pattern="[0-9a-fA-F-]{36}" required /><button type="button" onClick={(event) => { const input = event.currentTarget.previousElementSibling as HTMLInputElement; input.value = crypto.randomUUID(); }}>重新生成</button></div></label>
          <label>企业角色<select name="role" defaultValue="member"><option value="member">普通成员</option><option value="knowledge_admin">知识管理员</option><option value="enterprise_admin">企业管理员</option></select></label>
          <div className="dialog-actions"><button type="button" onClick={() => setMemberDialog(false)}>取消</button><button className="primary-button" disabled={busy} type="submit">{busy ? "保存中…" : "添加成员"}</button></div>
        </form>
      </Dialog>}
    </main>
  );
}

function Overview({ tenant, spaces, members, onCreateSpace, onViewSpaces }: { tenant: Tenant; spaces: Space[]; members: Membership[]; onCreateSpace: () => void; onViewSpaces: () => void }) {
  return <>
    <div className="page-heading"><div><p className="kicker">工作区总览</p><h1>早上好，{tenant.name}</h1><p>你的可信知识基础已经建立。下一步，从创建空间并导入第一份企业资料开始。</p></div><button className="primary-button" onClick={onCreateSpace}><Icon name="plus" />创建知识空间</button></div>
    <section className="stats-grid" aria-label="工作区统计">
      <article><span>知识空间</span><strong>{spaces.length}</strong><small>{spaces.length ? "权限边界运行正常" : "等待创建第一个空间"}</small></article>
      <article><span>企业成员</span><strong>{members.length || "—"}</strong><small>按企业角色授权</small></article>
      <article><span>已发布文档</span><strong>0</strong><small>文档流程即将接入</small></article>
      <article className="health-card"><span>知识健康度</span><strong>准备中</strong><small><i />权限层已就绪</small></article>
    </section>
    <section className="dashboard-grid">
      <article className="card getting-started"><div className="card-head"><div><p className="kicker">快速开始</p><h2>建立可信知识基础</h2></div><span>{spaces.length > 0 ? "1" : "0"} / 3</span></div>
        <ol>
          <li className={spaces.length > 0 ? "done" : "current"}><i>{spaces.length > 0 ? "✓" : "1"}</i><div><b>创建知识空间</b><span>为政策、项目或部门划分安全边界</span></div>{spaces.length === 0 && <button onClick={onCreateSpace}>现在创建</button>}</li>
          <li><i>2</i><div><b>导入企业资料</b><span>上传 PDF、DOCX、Markdown 或文本</span></div><em>下一阶段</em></li>
          <li><i>3</i><div><b>提出第一个问题</b><span>获得带原文引用的可信回答</span></div><em>下一阶段</em></li>
        </ol>
      </article>
      <article className="card spaces-summary"><div className="card-head"><div><p className="kicker">知识空间</p><h2>最近空间</h2></div><button onClick={onViewSpaces}>查看全部</button></div>
        {spaces.length === 0 ? <div className="empty-mini"><span><Icon name="spaces" /></span><b>还没有知识空间</b><p>创建一个空间，开始组织企业知识。</p></div> : <ul>{spaces.slice(0, 4).map((space) => <li key={space.id}><span className={`classification ${space.classification}`}>{space.name.slice(0, 1)}</span><div><b>{space.name}</b><small>{classificationLabels[space.classification]} · {formatDate(space.created_at)}</small></div><Icon name="arrow" /></li>)}</ul>}
      </article>
    </section>
  </>;
}

function SpacesView({ spaces, onCreate }: { spaces: Space[]; onCreate: () => void }) {
  return <><div className="page-heading"><div><p className="kicker">知识治理</p><h1>知识空间</h1><p>每个空间都是独立的权限和资料边界。</p></div><button className="primary-button" onClick={onCreate}><Icon name="plus" />创建空间</button></div>
    {spaces.length === 0 ? <section className="empty-state"><span><Icon name="spaces" /></span><h2>创建第一个知识空间</h2><p>可以按部门、项目或知识主题划分，例如“公司政策”“项目 A”“客户交付”。</p><button className="primary-button" onClick={onCreate}>创建知识空间</button></section> : <section className="space-grid">{spaces.map((space) => <article key={space.id}><div className={`space-icon ${space.classification}`}>{space.name.slice(0, 1)}</div><span className={`badge ${space.classification}`}>{classificationLabels[space.classification]}</span><h2>{space.name}</h2><p>暂无文档 · 创建于 {formatDate(space.created_at)}</p><div><span><Icon name="shield" />权限隔离</span><button disabled>进入空间 <Icon name="arrow" /></button></div></article>)}</section>}
  </>;
}

function MembersView({ members, actorId, canManage, onAdd }: { members: Membership[]; actorId: string; canManage: boolean; onAdd: () => void }) {
  return <><div className="page-heading"><div><p className="kicker">访问控制</p><h1>成员与权限</h1><p>企业角色决定成员能够管理和访问哪些内容。</p></div>{canManage && <button className="primary-button" onClick={onAdd}><Icon name="plus" />添加成员</button>}</div>
    {!canManage ? <section className="empty-state"><span><Icon name="shield" /></span><h2>仅企业管理员可查看</h2><p>你的资料访问权限不受影响，但成员目录受到额外保护。</p></section> : <section className="members-card"><div className="table-head"><span>成员</span><span>角色</span><span>加入时间</span><span>状态</span></div>{members.map((member) => <div className="member-row" key={member.user_id}><div><i>{member.user_id.slice(0, 2).toUpperCase()}</i><p><b>{member.user_id === actorId ? "本地演示管理员" : `用户 ${shortId(member.user_id)}`}</b><span>{shortId(member.user_id)}</span></p></div><span className="role-pill">{roleLabels[member.role]}</span><span>{formatDate(member.created_at)}</span><span className="active-status"><i />已启用</span></div>)}</section>}
  </>;
}

function Dialog({ title, description, onClose, children }: { title: string; description: string; onClose: () => void; children: ReactNode }) {
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section aria-modal="true" className="dialog" role="dialog"><button aria-label="关闭" className="dialog-close" onClick={onClose}>×</button><p className="kicker">Enterprise AI</p><h2>{title}</h2><p>{description}</p>{children}</section></div>;
}
