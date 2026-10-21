"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  Account,
  AccountSession,
  AuditEvent,
  Classification,
  DocumentContent,
  DocumentSummary,
  KnowledgeAnswer,
  Membership,
  MembershipRole,
  Space,
  SpaceMembership,
  SpaceRole,
  Tenant,
  acceptInvitation,
  askKnowledgeQuestion,
  createInvitation,
  createSpace,
  createTenant,
  getCurrentAccount,
  getDocumentContent,
  getTenant,
  loginAccount,
  listDocuments,
  listAuditEvents,
  listAccountSessions,
  listMembers,
  listQuestionHistory,
  listSpaceMembers,
  listSpaces,
  openBrowserSession,
  publishDocumentVersion,
  registerAccount,
  revokeAccountSession,
  revokeMemberSessions,
  revokeOtherAccountSessions,
  removeSpaceMember,
  requestPasswordReset,
  resendVerification,
  resetPassword,
  logoutAccount,
  saveMember,
  saveSpaceMember,
  uploadDocument,
  uploadDocumentVersion,
  verifyEmail,
} from "../lib/api";

type View = "overview" | "assistant" | "spaces" | "documents" | "members" | "audit" | "settings";

const ACTOR_KEY = "enterprise-ai.dev-actor-id";
const TENANT_KEY = "enterprise-ai.active-tenant-id";
const PUBLIC_DEMO = process.env.NEXT_PUBLIC_APP_MODE === "public-demo";
const ACCOUNT_MODE = process.env.NEXT_PUBLIC_APP_MODE === "accounts";

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

const auditActionLabels: Record<string, string> = {
  "account.registered": "账号注册",
  "account.login": "账号登录",
  "account.logout": "安全退出",
  "account.session_revoked": "撤销账号会话",
  "account.sessions_revoked": "退出其他设备",
  "account.sessions_revoked_by_admin": "管理员强制下线",
  "account.email_verified": "邮箱验证",
  "account.verification_requested": "请求验证邮件",
  "account.password_reset_requested": "请求密码重置",
  "account.password_reset": "完成密码重置",
  "invitation.created": "创建企业邀请",
  "invitation.accepted": "接受企业邀请",
  "tenant.created": "创建企业",
  "membership.saved": "更新企业成员",
  "space.created": "创建知识空间",
  "space_access.saved": "更新空间权限",
  "space_access.removed": "撤销空间权限",
  "document.created": "上传文档",
  "document.version_uploaded": "上传文档版本",
  "document.published": "发布文档版本",
  "document.viewed": "查看文档内容",
  "knowledge.question_answered": "知识问答",
  "authorization.denied": "访问被拒绝",
};

function Icon({ name }: { name: "home" | "assistant" | "spaces" | "members" | "settings" | "plus" | "arrow" | "shield" | "file" | "upload" | "back" | "send" | "quote" }) {
  const paths: Record<typeof name, ReactNode> = {
    home: <><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5M9 21v-7h6v7" /></>,
    assistant: <><path d="m12 3 1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4L12 3Z" /><path d="m19 14 .8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8L19 14Z" /><path d="M5 15v6M2 18h6" /></>,
    spaces: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M9 9v11" /></>,
    members: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.09A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.09 14H3v-4h.09A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63h.02A1.7 1.7 0 0 0 10 3.09V3h4v.09a1.7 1.7 0 0 0 1 1.54 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9v.02A1.7 1.7 0 0 0 20.91 10H21v4h-.09A1.7 1.7 0 0 0 19.4 15Z" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="m9 18 6-6-6-6" />,
    shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Zm-3-10 2 2 4-4" />,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h6" /></>,
    upload: <><path d="M12 16V4M7 9l5-5 5 5" /><path d="M5 20h14" /></>,
    back: <path d="m15 18-6-6 6-6" />,
    send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
    quote: <><path d="M10 11H5a4 4 0 0 0 4 4v4H5v-4a8 8 0 0 1 5-8v4ZM22 11h-5a4 4 0 0 0 4 4v4h-4v-4a8 8 0 0 1 5-8v4Z" /></>,
  };
  return <svg aria-hidden="true" className="icon" viewBox="0 0 24 24">{paths[name]}</svg>;
}

function shortId(id: string): string {
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(new Date(value));
}

function formatAuditDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatLocator(value: string): string {
  const [kind, position] = value.split(":");
  if (kind === "page") return `第 ${position} 页`;
  if (kind === "paragraph") return `第 ${position} 段`;
  return value;
}

export default function Dashboard() {
  const [actorId, setActorId] = useState("");
  const [accountEmail, setAccountEmail] = useState("");
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [documentsBySpace, setDocumentsBySpace] = useState<Record<string, DocumentSummary[]>>({});
  const [answers, setAnswers] = useState<KnowledgeAnswer[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [accountSessions, setAccountSessions] = useState<AccountSession[]>([]);
  const [activeSpace, setActiveSpace] = useState<Space | null>(null);
  const [members, setMembers] = useState<Membership[]>([]);
  const [spaceMembers, setSpaceMembers] = useState<SpaceMembership[]>([]);
  const [canManageActiveSpace, setCanManageActiveSpace] = useState(false);
  const [canManageMembers, setCanManageMembers] = useState(true);
  const [view, setView] = useState<View>("overview");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [spaceDialog, setSpaceDialog] = useState(false);
  const [memberDialog, setMemberDialog] = useState(false);
  const [spaceAccessDialog, setSpaceAccessDialog] = useState(false);
  const [uploadDialog, setUploadDialog] = useState(false);
  const [versionTarget, setVersionTarget] = useState<DocumentSummary | null>(null);
  const [documentReader, setDocumentReader] = useState<DocumentContent | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadWorkspace = useCallback(async (userId: string, organizationId: string) => {
    const [loadedTenant, loadedSpaces, loadedAnswers] = await Promise.all([
      getTenant(userId, organizationId),
      listSpaces(userId, organizationId),
      listQuestionHistory(userId, organizationId),
    ]);
    setTenant(loadedTenant);
    setSpaces(loadedSpaces);
    setAnswers(loadedAnswers);
    const documentEntries = await Promise.all(
      loadedSpaces.map(async (space) => [
        space.id,
        await listDocuments(userId, organizationId, space.id),
      ] as const),
    );
    setDocumentsBySpace(Object.fromEntries(documentEntries));
    try {
      const loadedMembers = await listMembers(userId, organizationId);
      setMembers(loadedMembers);
      setCanManageMembers(true);
      const actorRole = loadedMembers.find((member) => member.user_id === userId)?.role;
      setAuditEvents(
        actorRole === "enterprise_admin"
          ? await listAuditEvents(userId, organizationId)
          : [],
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setMembers([]);
        setAuditEvents([]);
        setCanManageMembers(false);
      } else {
        throw error;
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(async () => {
      if (cancelled) return;
      const storedActor = window.localStorage.getItem(ACTOR_KEY) ?? crypto.randomUUID();
      const storedTenant = window.localStorage.getItem(TENANT_KEY);
      try {
        if (ACCOUNT_MODE) {
          const account = await getCurrentAccount();
          if (cancelled) return;
          setActorId(account.user_id);
          setAccountEmail(account.email);
          setTenantId(account.tenant_id);
          if (account.tenant_id) {
            window.localStorage.setItem(TENANT_KEY, account.tenant_id);
            await loadWorkspace(account.user_id, account.tenant_id);
          }
          return;
        }
        const session = await openBrowserSession(storedActor);
        if (cancelled) return;
        window.localStorage.setItem(ACTOR_KEY, session.user_id);
        setActorId(session.user_id);
        setTenantId(storedTenant);

        if (!storedTenant) return;
        await loadWorkspace(session.user_id, storedTenant);
      } catch (error: unknown) {
          if (ACCOUNT_MODE && error instanceof ApiError && error.status === 401) {
            setActorId("");
            setAccountEmail("");
            setTenantId(null);
            return;
          }
          if (error instanceof ApiError && error.status === 404) {
            window.localStorage.removeItem(TENANT_KEY);
            setTenantId(null);
            setTenant(null);
            return;
          }
          setNotice(error instanceof Error ? error.message : "无法载入工作区");
      } finally {
        if (!cancelled) setReady(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [loadWorkspace]);

  const actorMembership = useMemo(
    () => members.find((member) => member.user_id === actorId),
    [actorId, members],
  );

  const finishAccountSignIn = useCallback(async (account: Account) => {
    setActorId(account.user_id);
    setAccountEmail(account.email);
    setTenantId(account.tenant_id);
    if (account.tenant_id) {
      window.localStorage.setItem(TENANT_KEY, account.tenant_id);
      await loadWorkspace(account.user_id, account.tenant_id);
    }
  }, [loadWorkspace]);

  async function handleAcceptAccess(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice(null);
    try {
      const account = await acceptInvitation(
        String(form.get("tenant_id") ?? "").trim(),
        String(form.get("invite_token") ?? "").trim(),
      );
      await finishAccountSignIn(account);
      setNotice("企业邀请已接受");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "接受邀请失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    if (ACCOUNT_MODE) await logoutAccount();
    window.localStorage.removeItem(TENANT_KEY);
    setActorId("");
    setAccountEmail("");
    setTenantId(null);
    setTenant(null);
    setSpaces([]);
    setDocumentsBySpace({});
    setAnswers([]);
    setMembers([]);
    setAuditEvents([]);
    setAccountSessions([]);
    setDocumentReader(null);
  }

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
      const loadedSpaces = await listSpaces(actorId, tenantId);
      setSpaces(loadedSpaces);
      setDocumentsBySpace((current) => ({
        ...current,
        ...Object.fromEntries(loadedSpaces.map((space) => [space.id, current[space.id] ?? []])),
      }));
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
      if (ACCOUNT_MODE) {
        const invitation = await createInvitation(actorId, tenantId, {
          email: String(form.get("email") ?? "").trim(),
          role: String(form.get("role")) as MembershipRole,
        });
        const link = `${window.location.origin}/?tenant=${tenantId}&invite=${invitation.invite_token}`;
        let copied = false;
        try {
          await navigator.clipboard?.writeText(link);
          copied = Boolean(navigator.clipboard);
        } catch {
          copied = false;
        }
        setNotice(invitation.delivery_status === "sent"
          ? `邀请邮件已发送，有效期至 ${formatDate(invitation.expires_at)}`
          : copied ? `邀请链接已复制，有效期至 ${formatDate(invitation.expires_at)}` : `请复制邀请链接：${link}`);
      } else {
        await saveMember(actorId, tenantId, {
          user_id: String(form.get("user_id") ?? "").trim(),
          role: String(form.get("role")) as MembershipRole,
        });
        setMembers(await listMembers(actorId, tenantId));
        setNotice("成员权限已保存");
      }
      setMemberDialog(false);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存成员失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveSpaceMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!tenantId || !activeSpace) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice(null);
    try {
      await saveSpaceMember(actorId, tenantId, activeSpace.id, {
        user_id: String(form.get("user_id") ?? "").trim(),
        role: String(form.get("role")) as SpaceRole,
      });
      setSpaceMembers(await listSpaceMembers(actorId, tenantId, activeSpace.id));
      setNotice("空间访问权限已保存");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存空间权限失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveSpaceMember(userId: string) {
    if (!tenantId || !activeSpace) return;
    setBusy(true);
    setNotice(null);
    try {
      await removeSpaceMember(actorId, tenantId, activeSpace.id, userId);
      setSpaceMembers(await listSpaceMembers(actorId, tenantId, activeSpace.id));
      setNotice("空间访问权限已撤销");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "撤销空间权限失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleUploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!tenantId || !activeSpace) return;
    const form = new FormData(event.currentTarget);
    const file = form.get("file");
    if (!(file instanceof File) || file.size === 0) return;
    setBusy(true);
    setNotice(null);
    try {
      if (versionTarget) {
        await uploadDocumentVersion(actorId, tenantId, versionTarget.document.id, file);
        setNotice("新版本已上传，确认内容后即可发布");
      } else {
        await uploadDocument(actorId, tenantId, activeSpace.id, {
          title: String(form.get("title") ?? "").trim() || file.name.replace(/\.[^.]+$/, ""),
          file,
        });
        setNotice("文档已解析，确认内容后即可发布");
      }
      const documents = await listDocuments(actorId, tenantId, activeSpace.id);
      setDocumentsBySpace((current) => ({ ...current, [activeSpace.id]: documents }));
      setUploadDialog(false);
      setVersionTarget(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "上传文档失败");
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish(summary: DocumentSummary) {
    if (!tenantId || !activeSpace) return;
    setBusy(true);
    setNotice(null);
    try {
      await publishDocumentVersion(actorId, tenantId, summary.latest_version.id);
      const documents = await listDocuments(actorId, tenantId, activeSpace.id);
      setDocumentsBySpace((current) => ({ ...current, [activeSpace.id]: documents }));
      setNotice(`《${summary.document.title}》已发布`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "发布文档失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleOpenDocument(summary: DocumentSummary) {
    if (!tenantId) return;
    setBusy(true);
    setNotice(null);
    try {
      setDocumentReader(
        await getDocumentContent(actorId, tenantId, summary.document.id),
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法读取文档");
    } finally {
      setBusy(false);
    }
  }

  async function handleAskQuestion(question: string, spaceId?: string) {
    if (!tenantId) return;
    setBusy(true);
    setNotice(null);
    try {
      const answer = await askKnowledgeQuestion(actorId, tenantId, {
        question,
        ...(spaceId ? { space_ids: [spaceId] } : {}),
      });
      setAnswers((current) => [answer, ...current]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "获取回答失败");
    } finally {
      setBusy(false);
    }
  }

  async function openSpace(space: Space) {
    setActiveSpace(space);
    setView("documents");
    if (!tenantId) return;
    try {
      setSpaceMembers(await listSpaceMembers(actorId, tenantId, space.id));
      setCanManageActiveSpace(true);
    } catch (error) {
      if (error instanceof ApiError && [403, 404].includes(error.status)) {
        setSpaceMembers([]);
        setCanManageActiveSpace(false);
      } else {
        setNotice(error instanceof Error ? error.message : "无法读取空间权限");
      }
    }
  }

  async function openAudit() {
    if (!tenantId) return;
    setBusy(true);
    setNotice(null);
    try {
      setAuditEvents(await listAuditEvents(actorId, tenantId));
      setView("audit");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法读取审计日志");
    } finally {
      setBusy(false);
    }
  }

  async function openSettings() {
    if (!ACCOUNT_MODE) return;
    setBusy(true);
    setNotice(null);
    try {
      setAccountSessions(await listAccountSessions());
      setView("settings");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法读取登录设备");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevokeSession(session: AccountSession) {
    setBusy(true);
    setNotice(null);
    try {
      await revokeAccountSession(session.id);
      if (session.is_current) {
        await handleLogout();
        return;
      }
      setAccountSessions(await listAccountSessions());
      setNotice("该设备的登录会话已撤销");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "撤销会话失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevokeOtherSessions() {
    setBusy(true);
    setNotice(null);
    try {
      const result = await revokeOtherAccountSessions();
      setAccountSessions(await listAccountSessions());
      setNotice(result.revoked_count > 0 ? `已退出 ${result.revoked_count} 个其他设备` : "没有其他登录设备");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "退出其他设备失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevokeMemberSessions(userId: string) {
    if (!tenantId) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await revokeMemberSessions(actorId, tenantId, userId);
      setNotice(result.revoked_count > 0 ? `已强制退出该成员的 ${result.revoked_count} 个会话` : "该成员当前没有登录会话");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "强制下线失败");
    } finally {
      setBusy(false);
    }
  }

  function resetDemo() {
    window.localStorage.removeItem(TENANT_KEY);
    setTenantId(null);
    setTenant(null);
    setSpaces([]);
    setDocumentsBySpace({});
    setAnswers([]);
    setAuditEvents([]);
    setAccountSessions([]);
    setActiveSpace(null);
    setMembers([]);
    setDocumentReader(null);
    setView("overview");
  }

  if (!ready) {
    return <div className="loading-screen"><span className="spinner" />正在载入安全工作区</div>;
  }

  if (ACCOUNT_MODE && !actorId) {
    return <AuthScreen busy={busy} onBusy={setBusy} onReady={finishAccountSignIn} />;
  }

  if (!tenant) {
    if (ACCOUNT_MODE) {
      return <AccountAccessSetup
        email={accountEmail}
        busy={busy}
        notice={notice}
        onCreate={handleCreateTenant}
        onAccept={handleAcceptAccess}
        onLogout={handleLogout}
      />;
    }
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
            <div className="dev-identity"><span />{PUBLIC_DEMO ? "安全演示会话" : "本地演示身份"}：{shortId(actorId)}</div>
            {notice && <p className="form-error">{notice}</p>}
          </form>
        </section>
      </main>
    );
  }

  const navItems: { key: View; label: string; icon: "home" | "assistant" | "spaces" | "members" }[] = [
    { key: "overview", label: "总览", icon: "home" },
    { key: "assistant", label: "AI 决策助手", icon: "assistant" },
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
            <button className={view === item.key || (item.key === "spaces" && view === "documents") ? "active" : ""} key={item.key} onClick={() => setView(item.key)}>
              <Icon name={item.icon} />{item.label}
              {item.key === "spaces" && spaces.length > 0 && <em>{spaces.length}</em>}
            </button>
          ))}
          <p>系统</p>
          {actorMembership?.role === "enterprise_admin" && <button className={view === "audit" ? "active" : ""} onClick={openAudit}><Icon name="shield" />审计日志<em>{auditEvents.length}</em></button>}
          {ACCOUNT_MODE ? <button className={view === "settings" ? "active" : ""} onClick={openSettings}><Icon name="settings" />账号安全</button> : <button className="disabled-nav" disabled><Icon name="settings" />账号安全<span>账号模式</span></button>}
        </nav>
        <div className="sidebar-foot">
          <span className="security-dot" />权限隔离已启用
          <small>{ACCOUNT_MODE ? "账号与加密已启用" : PUBLIC_DEMO ? "公开演示环境" : "开发环境 · SQLite"}</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <button className="tenant-switcher"><span>{tenant.name.slice(0, 1)}</span><div><small>当前企业</small><b>{tenant.name}</b></div></button>
          <div className="topbar-user"><div><b>{ACCOUNT_MODE ? accountEmail : PUBLIC_DEMO ? "演示管理员" : "本地演示管理员"}</b><span>{actorMembership ? roleLabels[actorMembership.role] : "已授权成员"}</span></div><i>{(accountEmail || actorId).slice(0, 2).toUpperCase()}</i></div>
        </header>

        <div className="content">
          {notice && <div className="toast" role="status">{notice}<button onClick={() => setNotice(null)}>×</button></div>}
          {view === "overview" && (
            <Overview tenant={tenant} spaces={spaces} members={members} answers={answers} documentsBySpace={documentsBySpace} onCreateSpace={() => setSpaceDialog(true)} onViewSpaces={() => setView("spaces")} onOpenSpace={openSpace} onOpenAssistant={() => setView("assistant")} />
          )}
          {view === "assistant" && (
            <AssistantView
              answers={answers}
              spaces={spaces}
              documentsBySpace={documentsBySpace}
              busy={busy}
              onAsk={handleAskQuestion}
            />
          )}
          {view === "spaces" && (
            <SpacesView spaces={spaces} documentsBySpace={documentsBySpace} onCreate={() => setSpaceDialog(true)} onOpen={openSpace} />
          )}
          {view === "documents" && activeSpace && (
            <DocumentsView
              space={activeSpace}
              documents={documentsBySpace[activeSpace.id] ?? []}
              busy={busy}
              onBack={() => setView("spaces")}
              onUpload={() => { setVersionTarget(null); setUploadDialog(true); }}
              onNewVersion={(document) => { setVersionTarget(document); setUploadDialog(true); }}
              onPublish={handlePublish}
              onOpen={handleOpenDocument}
              canManage={canManageActiveSpace}
              onManageAccess={() => setSpaceAccessDialog(true)}
            />
          )}
          {view === "members" && (
            <MembersView members={members} actorId={actorId} busy={busy} canManage={canManageMembers} onAdd={() => setMemberDialog(true)} onRevokeSessions={handleRevokeMemberSessions} />
          )}
          {view === "audit" && actorMembership?.role === "enterprise_admin" && (
            <AuditView events={auditEvents} />
          )}
          {view === "settings" && ACCOUNT_MODE && (
            <AccountSecurityView sessions={accountSessions} busy={busy} onRevoke={handleRevokeSession} onRevokeOthers={handleRevokeOtherSessions} />
          )}
          <footer className="workspace-footer">企业 ID {shortId(tenant.id)}<button onClick={ACCOUNT_MODE ? handleLogout : resetDemo}>{ACCOUNT_MODE ? "安全退出" : "切换演示企业"}</button></footer>
        </div>
      </section>

      {spaceDialog && <Dialog title="创建知识空间" description="空间决定资料的权限边界和敏感等级。" onClose={() => setSpaceDialog(false)}>
        <form className="dialog-form" onSubmit={handleCreateSpace}>
          <label>空间名称<input name="name" minLength={2} maxLength={160} placeholder="例如：公司政策" autoFocus required /></label>
          <label>保密级别<select name="classification" defaultValue="internal"><option value="internal">内部</option><option value="confidential">机密</option><option value="restricted">严格受限</option></select></label>
          <div className="dialog-actions"><button type="button" onClick={() => setSpaceDialog(false)}>取消</button><button className="primary-button" disabled={busy} type="submit">{busy ? "创建中…" : "创建空间"}</button></div>
        </form>
      </Dialog>}

      {memberDialog && <Dialog title={ACCOUNT_MODE ? "邀请企业成员" : "添加企业成员"} description={ACCOUNT_MODE ? "成员必须使用被邀请的邮箱注册或登录后接受邀请。" : "当前阶段使用用户 UUID 模拟来自企业身份系统的用户。"} onClose={() => setMemberDialog(false)}>
        <form className="dialog-form" onSubmit={handleAddMember}>
          {ACCOUNT_MODE
            ? <label>企业邮箱<input name="email" type="email" autoComplete="email" placeholder="name@company.com" required /></label>
            : <label>用户 UUID<div className="input-action"><input name="user_id" defaultValue={crypto.randomUUID()} pattern="[0-9a-fA-F-]{36}" required /><button type="button" onClick={(event) => { const input = event.currentTarget.previousElementSibling as HTMLInputElement; input.value = crypto.randomUUID(); }}>重新生成</button></div></label>}
          <label>企业角色<select name="role" defaultValue="member"><option value="member">普通成员</option><option value="knowledge_admin">知识管理员</option><option value="enterprise_admin">企业管理员</option></select></label>
          <div className="dialog-actions"><button type="button" onClick={() => setMemberDialog(false)}>取消</button><button className="primary-button" disabled={busy} type="submit">{busy ? "保存中…" : "添加成员"}</button></div>
        </form>
      </Dialog>}

      {spaceAccessDialog && activeSpace && <Dialog title="管理空间访问" description={`只有这里列出的成员能够访问“${activeSpace.name}”；企业管理员始终拥有管理权限。`} onClose={() => setSpaceAccessDialog(false)}>
        <form className="dialog-form" onSubmit={handleSaveSpaceMember}>
          <label>企业成员
            {members.length > 0
              ? <select name="user_id" required>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.user_id === actorId ? "当前账号" : shortId(member.user_id)} · {roleLabels[member.role]}</option>)}</select>
              : <input name="user_id" placeholder="成员 UUID" required />}
          </label>
          <label>空间权限<select name="role" defaultValue="viewer"><option value="viewer">查看者：读取与提问</option><option value="manager">管理者：上传、发布与授权</option></select></label>
          <div className="dialog-actions"><button type="button" onClick={() => setSpaceAccessDialog(false)}>关闭</button><button className="primary-button" disabled={busy} type="submit">保存权限</button></div>
        </form>
        {spaceMembers.length > 0 && <div className="space-access-list">{spaceMembers.map((member) => <div key={member.user_id}><span><b>{member.user_id === actorId ? "当前账号" : shortId(member.user_id)}</b><small>{member.role === "manager" ? "空间管理者" : "空间查看者"}</small></span><button disabled={busy} onClick={() => handleRemoveSpaceMember(member.user_id)} type="button">撤销</button></div>)}</div>}
      </Dialog>}

      {uploadDialog && activeSpace && <Dialog
        title={versionTarget ? "上传新版本" : "导入企业资料"}
        description={versionTarget ? `为《${versionTarget.document.title}》添加不可变的新版本。` : `文件将进入“${activeSpace.name}”并自动解析定位。`}
        onClose={() => { setUploadDialog(false); setVersionTarget(null); }}
      >
        <form className="dialog-form" onSubmit={handleUploadDocument}>
          {!versionTarget && <label>文档标题<input name="title" maxLength={255} placeholder="默认使用文件名" /></label>}
          <label>选择文件<input className="file-input" name="file" type="file" accept=".pdf,.docx,.txt,.md" required /></label>
          <p className="upload-hint">支持 PDF、DOCX、TXT、Markdown，最大 10 MB。上传后需人工发布。</p>
          <div className="dialog-actions"><button type="button" onClick={() => { setUploadDialog(false); setVersionTarget(null); }}>取消</button><button className="primary-button" disabled={busy} type="submit"><Icon name="upload" />{busy ? "解析中…" : "上传并解析"}</button></div>
        </form>
      </Dialog>}
      {documentReader && (
        <DocumentReader content={documentReader} onClose={() => setDocumentReader(null)} />
      )}
    </main>
  );
}

function AuthScreen({ busy, onBusy, onReady }: { busy: boolean; onBusy: (value: boolean) => void; onReady: (account: Account) => Promise<void> }) {
  type AuthMode = "login" | "create" | "join" | "forgot" | "verify" | "reset";
  const [parameters] = useState(() => new URLSearchParams(window.location.search));
  const [invitation] = useState(() => parameters.get("invite") ?? "");
  const [invitationTenant] = useState(() => parameters.get("tenant") ?? "");
  const [linkToken] = useState(() => parameters.get("token") ?? "");
  const [mode, setMode] = useState<AuthMode>(() => {
    if (parameters.get("mode") === "verify") return "verify";
    if (parameters.get("mode") === "reset") return "reset";
    return invitation ? "join" : "login";
  });
  const [pendingEmail, setPendingEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    onBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (mode === "login") {
        await onReady(await loginAccount(email, password));
      } else if (mode === "create") {
        const account = await registerAccount({
          email,
          password,
          organization_name: String(form.get("organization_name") ?? "").trim(),
        });
        if (account.email_verified) await onReady(account);
        else {
          setPendingEmail(email);
          setMode("verify");
          setMessage("验证邮件已发送，请在 24 小时内完成验证。");
        }
      } else if (mode === "join") {
        let account: Account;
        try {
          account = await registerAccount({ email, password });
        } catch (registrationError) {
          if (!(registrationError instanceof ApiError) || registrationError.status !== 409) throw registrationError;
          account = await loginAccount(email, password);
        }
        if (!account.email_verified) {
          setPendingEmail(email);
          setMode("verify");
          setMessage("请先验证受邀邮箱，再接受企业邀请。");
          return;
        }
        const accepted = await acceptInvitation(
          String(form.get("tenant_id") ?? "").trim(),
          String(form.get("invite_token") ?? "").trim(),
        );
        await onReady({ ...accepted, email: account.email });
      } else if (mode === "forgot") {
        await requestPasswordReset(email);
        setPendingEmail(email);
        setMessage("如果账号存在，密码重置邮件已经发送。");
      } else if (mode === "verify") {
        const account = await verifyEmail(String(form.get("token") ?? linkToken).trim());
        if (invitation && invitationTenant) {
          const accepted = await acceptInvitation(invitationTenant, invitation);
          await onReady({ ...accepted, email: account.email });
        } else {
          await onReady(account);
        }
      } else if (mode === "reset") {
        await onReady(await resetPassword(
          String(form.get("token") ?? linkToken).trim(),
          password,
        ));
      }
    } catch (submitError) {
      if (mode === "login" && submitError instanceof ApiError && submitError.status === 403) {
        setPendingEmail(email);
        setMode("verify");
        setMessage("该账号需要先完成邮箱验证。");
      } else {
        setError(submitError instanceof Error ? submitError.message : "认证失败");
      }
    } finally {
      onBusy(false);
    }
  }

  async function resend() {
    if (!pendingEmail) return;
    onBusy(true);
    setError(null);
    try {
      await resendVerification(pendingEmail);
      setMessage("如果账号仍待验证，新的验证邮件已经发送。");
    } catch (resendError) {
      setError(resendError instanceof Error ? resendError.message : "重新发送失败");
    } finally {
      onBusy(false);
    }
  }

  const title = mode === "login" ? "欢迎回来" : mode === "create" ? "创建企业账号" : mode === "join" ? "加入企业" : mode === "forgot" ? "找回密码" : mode === "verify" ? "验证邮箱" : "设置新密码";
  const description = mode === "login" ? "使用已授权的企业账号进入。" : mode === "create" ? "创建者将成为首位企业管理员。" : mode === "join" ? "邀请邮箱必须与注册邮箱一致。" : mode === "forgot" ? "我们会向账号邮箱发送一个 30 分钟有效的链接。" : mode === "verify" ? "验证完成前不会建立企业访问会话。" : "重置成功后，所有旧会话都会失效。";

  return <main className="onboarding auth-page">
    <section className="onboarding-story">
      <div className="brand-mark"><span>EA</span> Enterprise AI</div>
      <div><p className="kicker">身份先于知识</p><h1>企业知识，<br />只对授权成员开放</h1><p className="onboarding-copy">账号、企业成员关系和知识权限由服务端共同校验。输入企业名称不能进入他人的工作区。</p></div>
      <div className="trust-list"><span><Icon name="shield" />服务端安全会话</span><span><Icon name="shield" />数据库字段加密</span><span><Icon name="shield" />上传原件认证加密</span></div>
    </section>
    <section className="onboarding-form-wrap">
      <form className="onboarding-form" onSubmit={submit}>
        {(["login", "create", "join"] as AuthMode[]).includes(mode) && <div className="auth-tabs">
          <button className={mode === "login" ? "active" : ""} type="button" onClick={() => setMode("login")}>登录</button>
          <button className={mode === "create" ? "active" : ""} type="button" onClick={() => setMode("create")}>创建企业</button>
          <button className={mode === "join" ? "active" : ""} type="button" onClick={() => setMode("join")}>接受邀请</button>
        </div>}
        <h2>{title}</h2>
        <p>{description}</p>
        {["login", "create", "join", "forgot"].includes(mode) && <label>邮箱<input name="email" type="email" autoComplete="email" defaultValue={pendingEmail} placeholder="name@company.com" required /></label>}
        {["login", "create", "join", "reset"].includes(mode) && <label>{mode === "reset" ? "新密码" : "密码"}<input name="password" type="password" minLength={12} autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="至少 12 个字符" required /></label>}
        {mode === "create" && <label>企业名称<input name="organization_name" minLength={2} maxLength={160} placeholder="例如：远景科技" required /></label>}
        {mode === "join" && <><label>企业 ID<input name="tenant_id" defaultValue={invitationTenant} required /></label><label>邀请令牌<input name="invite_token" defaultValue={invitation} required /></label></>}
        {mode === "verify" && <label>验证令牌<input name="token" defaultValue={linkToken} required /></label>}
        {mode === "reset" && <label>重置令牌<input name="token" defaultValue={linkToken} required /></label>}
        <button className="primary-button wide" disabled={busy} type="submit">{busy ? "正在处理…" : mode === "login" ? "安全登录" : mode === "create" ? "创建账号" : mode === "join" ? "验证邀请并加入" : mode === "forgot" ? "发送重置邮件" : mode === "verify" ? "完成邮箱验证" : "保存新密码"}<Icon name="arrow" /></button>
        {mode === "login" && <button className="text-button" type="button" onClick={() => setMode("forgot")}>忘记密码？</button>}
        {mode === "verify" && pendingEmail && <button className="text-button" disabled={busy} type="button" onClick={resend}>重新发送验证邮件</button>}
        {!["login", "create", "join"].includes(mode) && <button className="text-button" type="button" onClick={() => { setMode("login"); setError(null); setMessage(null); }}>返回登录</button>}
        <p className="security-note"><Icon name="shield" />密码使用 scrypt 加盐哈希，系统不会保存明文密码。</p>
        {message && <p className="form-success">{message}</p>}
        {error && <p className="form-error">{error}</p>}
      </form>
    </section>
  </main>;
}

function AccountAccessSetup({ email, busy, notice, onCreate, onAccept, onLogout }: { email: string; busy: boolean; notice: string | null; onCreate: (event: FormEvent<HTMLFormElement>) => Promise<void>; onAccept: (event: FormEvent<HTMLFormElement>) => Promise<void>; onLogout: () => Promise<void> }) {
  return <main className="onboarding auth-page">
    <section className="onboarding-story"><div className="brand-mark"><span>EA</span> Enterprise AI</div><div><p className="kicker">账号已登录</p><h1>选择你的<br />企业访问方式</h1><p className="onboarding-copy">你可以创建新的企业工作区，或使用管理员发给你的邀请加入已有企业。</p></div><div className="trust-list"><span><Icon name="shield" />{email}</span></div></section>
    <section className="onboarding-form-wrap"><div className="access-options">
      <form className="onboarding-form compact-form" onSubmit={onCreate}><span className="step-label">企业创建者</span><h2>创建新企业</h2><p>你将自动成为企业管理员。</p><label>企业名称<input name="name" minLength={2} maxLength={160} required /></label><button className="primary-button wide" disabled={busy}>创建工作区</button></form>
      <div className="option-divider">或者</div>
      <form className="onboarding-form compact-form" onSubmit={onAccept}><span className="step-label">受邀成员</span><h2>接受邀请</h2><p>权限由邀请你的管理员指定。</p><label>企业 ID<input name="tenant_id" required /></label><label>邀请令牌<input name="invite_token" required /></label><button className="primary-button wide" disabled={busy}>加入企业</button></form>
      {notice && <p className="form-error">{notice}</p>}<button className="text-button" type="button" onClick={onLogout}>退出当前账号</button>
    </div></section>
  </main>;
}

function Overview({ tenant, spaces, members, answers, documentsBySpace, onCreateSpace, onViewSpaces, onOpenSpace, onOpenAssistant }: { tenant: Tenant; spaces: Space[]; members: Membership[]; answers: KnowledgeAnswer[]; documentsBySpace: Record<string, DocumentSummary[]>; onCreateSpace: () => void; onViewSpaces: () => void; onOpenSpace: (space: Space) => void; onOpenAssistant: () => void }) {
  const documents = Object.values(documentsBySpace).flat();
  const publishedCount = documents.filter((item) => item.latest_version.status === "published").length;
  const completedSteps = (spaces.length > 0 ? 1 : 0) + (documents.length > 0 ? 1 : 0) + (answers.length > 0 ? 1 : 0);
  return <>
    <div className="page-heading"><div><p className="kicker">工作区总览</p><h1>早上好，{tenant.name}</h1><p>你的可信知识基础已经建立。下一步，从创建空间并导入第一份企业资料开始。</p></div><button className="primary-button" onClick={onCreateSpace}><Icon name="plus" />创建知识空间</button></div>
    <section className="stats-grid" aria-label="工作区统计">
      <article><span>知识空间</span><strong>{spaces.length}</strong><small>{spaces.length ? "权限边界运行正常" : "等待创建第一个空间"}</small></article>
      <article><span>企业成员</span><strong>{members.length || "—"}</strong><small>按企业角色授权</small></article>
      <article><span>已发布文档</span><strong>{publishedCount}</strong><small>{documents.length ? `共 ${documents.length} 份资料` : "等待导入第一份资料"}</small></article>
      <article className="health-card"><span>知识健康度</span><strong>{publishedCount ? "可检索" : "准备中"}</strong><small><i />{publishedCount ? "已发布知识可用" : "权限层已就绪"}</small></article>
    </section>
    <section className="dashboard-grid">
      <article className="card getting-started"><div className="card-head"><div><p className="kicker">快速开始</p><h2>建立可信知识基础</h2></div><span>{completedSteps} / 3</span></div>
        <ol>
          <li className={spaces.length > 0 ? "done" : "current"}><i>{spaces.length > 0 ? "✓" : "1"}</i><div><b>创建知识空间</b><span>为政策、项目或部门划分安全边界</span></div>{spaces.length === 0 && <button onClick={onCreateSpace}>现在创建</button>}</li>
          <li className={documents.length > 0 ? "done" : spaces.length > 0 ? "current" : ""}><i>{documents.length > 0 ? "✓" : "2"}</i><div><b>导入企业资料</b><span>上传 PDF、DOCX、Markdown 或文本</span></div>{spaces.length > 0 && documents.length === 0 ? <button onClick={() => onOpenSpace(spaces[0])}>现在导入</button> : <em>{documents.length ? "已完成" : "等待空间"}</em>}</li>
          <li className={answers.length > 0 ? "done" : publishedCount > 0 ? "current" : ""}><i>{answers.length > 0 ? "✓" : "3"}</i><div><b>提出第一个问题</b><span>获得带原文引用的可信回答</span></div>{publishedCount > 0 && answers.length === 0 ? <button onClick={onOpenAssistant}>现在提问</button> : <em>{answers.length ? "已完成" : "等待发布资料"}</em>}</li>
        </ol>
      </article>
      <article className="card spaces-summary"><div className="card-head"><div><p className="kicker">知识空间</p><h2>最近空间</h2></div><button onClick={onViewSpaces}>查看全部</button></div>
        {spaces.length === 0 ? <div className="empty-mini"><span><Icon name="spaces" /></span><b>还没有知识空间</b><p>创建一个空间，开始组织企业知识。</p></div> : <ul>{spaces.slice(0, 4).map((space) => <li key={space.id} onClick={() => onOpenSpace(space)}><span className={`classification ${space.classification}`}>{space.name.slice(0, 1)}</span><div><b>{space.name}</b><small>{classificationLabels[space.classification]} · {documentsBySpace[space.id]?.length ?? 0} 份资料</small></div><Icon name="arrow" /></li>)}</ul>}
      </article>
    </section>
  </>;
}

function AssistantView({ answers, spaces, documentsBySpace, busy, onAsk }: { answers: KnowledgeAnswer[]; spaces: Space[]; documentsBySpace: Record<string, DocumentSummary[]>; busy: boolean; onAsk: (question: string, spaceId?: string) => Promise<void> }) {
  const [question, setQuestion] = useState("");
  const [spaceId, setSpaceId] = useState("");
  const publishedCount = Object.values(documentsBySpace)
    .flat()
    .filter((item) => item.latest_version.status === "published").length;
  const suggestions = ["公司的远程办公政策是什么？", "哪些事项需要审批？", "请总结已发布资料中的关键要求。"];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = question.trim();
    if (!value) return;
    await onAsk(value, spaceId || undefined);
    setQuestion("");
  }

  return <>
    <div className="page-heading assistant-heading"><div><p className="kicker">证据优先</p><h1>AI 决策助手</h1><p>只基于已发布的企业资料回答，并为每项结论保留原文位置。</p></div><span className="assistant-mode"><i />本地证据模式</span></div>
    <section className="assistant-layout">
      <div className="assistant-main">
        <form className="question-composer" onSubmit={submit}>
          <div className="composer-top"><span><Icon name="assistant" />向企业知识提问</span><select aria-label="限定知识空间" value={spaceId} onChange={(event) => setSpaceId(event.target.value)}><option value="">全部知识空间</option>{spaces.map((space) => <option key={space.id} value={space.id}>{space.name}</option>)}</select></div>
          <textarea aria-label="问题" maxLength={1000} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：远程办公需要谁批准？" rows={4} value={question} />
          <div className="composer-actions"><small>{publishedCount ? `正在检索 ${publishedCount} 份已发布资料` : "尚无已发布资料，助手会明确提示依据不足"}</small><button className="primary-button" disabled={busy || question.trim().length < 2} type="submit">{busy ? "检索证据中…" : "获取可信回答"}<Icon name="send" /></button></div>
        </form>

        {answers.length === 0 ? <section className="assistant-empty"><span><Icon name="assistant" /></span><h2>从一个具体问题开始</h2><p>问题越明确，原文定位越准确。只有已发布版本会进入检索范围。</p><div>{suggestions.map((suggestion) => <button key={suggestion} onClick={() => setQuestion(suggestion)} type="button">{suggestion}</button>)}</div></section> : <section className="answer-history" aria-label="问答历史">{answers.map((item) => <article className="answer-card" key={item.id}><div className="question-row"><span>你</span><div><small>{formatDate(item.created_at)}</small><h2>{item.question}</h2></div></div><div className="answer-row"><span><Icon name="assistant" /></span><div><div className="answer-meta"><b>{item.generator === "openai" ? "AI 证据式回答" : "本地证据式回答"}</b><em className={item.status}>{item.status === "grounded" ? `${item.citations.length} 条引用` : "依据不足"}</em></div><p>{item.answer}</p>{item.citations.length > 0 && <div className="citations"><h3><Icon name="quote" />原文依据</h3>{item.citations.map((citation) => <blockquote key={citation.citation_number}><span>[{citation.citation_number}]</span><div><b>{citation.document_title}</b><small>v{citation.version_number} · {formatLocator(citation.locator)} · {citation.filename}</small><p>{citation.excerpt}</p></div></blockquote>)}</div>}</div></div></article>)}</section>}
      </div>
      <aside className="assistant-guide"><p className="kicker">可信边界</p><h2>这个助手会做什么</h2><ul><li><Icon name="shield" /><div><b>只读已发布资料</b><span>草稿、失败版本和已替代版本不会参与回答。</span></div></li><li><Icon name="quote" /><div><b>保留原文位置</b><span>每条引用显示文件、版本、页码或段落。</span></div></li><li><Icon name="assistant" /><div><b>没有证据就拒答</b><span>外部模型即使已配置，也只能使用本次获准访问的证据。</span></div></li></ul></aside>
    </section>
  </>;
}

function SpacesView({ spaces, documentsBySpace, onCreate, onOpen }: { spaces: Space[]; documentsBySpace: Record<string, DocumentSummary[]>; onCreate: () => void; onOpen: (space: Space) => void }) {
  return <><div className="page-heading"><div><p className="kicker">知识治理</p><h1>知识空间</h1><p>每个空间都是独立的权限和资料边界。</p></div><button className="primary-button" onClick={onCreate}><Icon name="plus" />创建空间</button></div>
    {spaces.length === 0 ? <section className="empty-state"><span><Icon name="spaces" /></span><h2>创建第一个知识空间</h2><p>可以按部门、项目或知识主题划分，例如“公司政策”“项目 A”“客户交付”。</p><button className="primary-button" onClick={onCreate}>创建知识空间</button></section> : <section className="space-grid">{spaces.map((space) => <article key={space.id} onClick={() => onOpen(space)}><div className={`space-icon ${space.classification}`}>{space.name.slice(0, 1)}</div><span className={`badge ${space.classification}`}>{classificationLabels[space.classification]}</span><h2>{space.name}</h2><p>{documentsBySpace[space.id]?.length ?? 0} 份资料 · 创建于 {formatDate(space.created_at)}</p><div><span><Icon name="shield" />权限隔离</span><button>进入空间 <Icon name="arrow" /></button></div></article>)}</section>}
  </>;
}

function DocumentsView({ space, documents, busy, canManage, onBack, onUpload, onNewVersion, onPublish, onOpen, onManageAccess }: { space: Space; documents: DocumentSummary[]; busy: boolean; canManage: boolean; onBack: () => void; onUpload: () => void; onNewVersion: (document: DocumentSummary) => void; onPublish: (document: DocumentSummary) => void; onOpen: (document: DocumentSummary) => void; onManageAccess: () => void }) {
  const statusLabels = { ready: "待发布", published: "已发布", superseded: "已替代", failed: "解析失败" };
  return <>
    <button className="back-button" onClick={onBack}><Icon name="back" />返回知识空间</button>
    <div className="page-heading document-heading"><div><p className="kicker">{classificationLabels[space.classification]}空间</p><h1>{space.name}</h1><p>只有获得该空间授权的成员可以读取资料和参与检索。</p></div><div className="heading-actions">{canManage && <button className="secondary-button" onClick={onManageAccess}><Icon name="shield" />管理访问</button>}{canManage && <button className="primary-button" onClick={onUpload}><Icon name="upload" />导入资料</button>}</div></div>
    {documents.length === 0 ? <section className="empty-state document-empty"><span><Icon name="file" /></span><h2>{canManage ? "导入第一份企业资料" : "这个空间还没有已发布资料"}</h2><p>{canManage ? "支持 PDF、DOCX、TXT 和 Markdown。系统会保存加密原件并生成带定位的文本块。" : "你拥有查看权限；空间管理者发布资料后会显示在这里。"}</p>{canManage && <button className="primary-button" onClick={onUpload}><Icon name="upload" />选择文件</button>}</section> : <section className="documents-card"><div className="document-table-head"><span>文档</span><span>版本</span><span>大小</span><span>状态</span><span>操作</span></div>{documents.map((item) => <article className="document-row" key={item.document.id}><button className="document-open" disabled={busy} onClick={() => onOpen(item)}><i><Icon name="file" /></i><p><b>{item.document.title}</b><span>{item.latest_version.filename} · {formatDate(item.latest_version.created_at)}</span>{item.latest_version.error_message && <em>{item.latest_version.error_message}</em>}</p></button><span>v{item.latest_version.version_number}</span><span>{formatBytes(item.latest_version.byte_size)}</span><span className={`document-status ${item.latest_version.status}`}>{statusLabels[item.latest_version.status]}</span><div className="document-actions"><button className="view-action" disabled={busy} onClick={() => onOpen(item)}>查看</button>{canManage && item.latest_version.status === "ready" && <button className="publish-action" disabled={busy} onClick={() => onPublish(item)}>发布</button>}{canManage && <button disabled={busy} onClick={() => onNewVersion(item)}>新版本</button>}{!canManage && <span>只读</span>}</div></article>)}</section>}
  </>;
}

function DocumentReader({ content, onClose }: { content: DocumentContent; onClose: () => void }) {
  const statusLabel = content.version.status === "published" ? "已发布版本" : content.version.status === "ready" ? "待发布预览" : content.version.status === "superseded" ? "已替代版本" : "解析失败";
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section aria-modal="true" className="dialog document-reader" role="dialog"><button aria-label="关闭文档" className="dialog-close" onClick={onClose}>×</button><p className="kicker">安全文档阅读器</p><h2>{content.document.title}</h2><div className="reader-meta"><span>{content.version.filename}</span><span>v{content.version.version_number}</span><span>{statusLabel}</span><span>{formatBytes(content.version.byte_size)}</span></div><div className="reader-content">{content.chunks.length === 0 ? <div className="reader-empty"><Icon name="file" /><p>这个版本没有可显示的解析内容。</p></div> : content.chunks.map((chunk) => <article key={chunk.id}><small>{formatLocator(chunk.locator)}</small><p>{chunk.text}</p></article>)}</div><footer><Icon name="shield" />内容已通过企业、空间和版本权限校验；本次查看已写入审计日志。</footer></section></div>;
}

function MembersView({ members, actorId, busy, canManage, onAdd, onRevokeSessions }: { members: Membership[]; actorId: string; busy: boolean; canManage: boolean; onAdd: () => void; onRevokeSessions: (userId: string) => void }) {
  return <><div className="page-heading"><div><p className="kicker">访问控制</p><h1>成员与权限</h1><p>企业角色决定成员能够管理和访问哪些内容。</p></div>{canManage && <button className="primary-button" onClick={onAdd}><Icon name="plus" />添加成员</button>}</div>
    {!canManage ? <section className="empty-state"><span><Icon name="shield" /></span><h2>仅企业管理员可查看</h2><p>你的资料访问权限不受影响，但成员目录受到额外保护。</p></section> : <section className="members-card"><div className="table-head"><span>成员</span><span>角色</span><span>加入时间</span><span>状态</span><span>会话</span></div>{members.map((member) => <div className="member-row" key={member.user_id}><div><i>{member.user_id.slice(0, 2).toUpperCase()}</i><p><b>{member.user_id === actorId ? (ACCOUNT_MODE ? "当前账号" : PUBLIC_DEMO ? "演示管理员" : "本地演示管理员") : `用户 ${shortId(member.user_id)}`}</b><span>{shortId(member.user_id)}</span></p></div><span className="role-pill">{roleLabels[member.role]}</span><span>{formatDate(member.created_at)}</span><span className="active-status"><i />已启用</span><span className="member-session-action">{ACCOUNT_MODE && member.user_id !== actorId ? <button disabled={busy} onClick={() => onRevokeSessions(member.user_id)}>强制下线</button> : "当前设备"}</span></div>)}</section>}
  </>;
}

function AccountSecurityView({ sessions, busy, onRevoke, onRevokeOthers }: { sessions: AccountSession[]; busy: boolean; onRevoke: (session: AccountSession) => void; onRevokeOthers: () => void }) {
  const otherSessionCount = sessions.filter((session) => !session.is_current).length;
  return <>
    <div className="page-heading"><div><p className="kicker">账号安全</p><h1>登录设备与会话</h1><p>查看当前账号的有效登录会话，并立即撤销不认识的设备。</p></div><button className="secondary-button" disabled={busy || otherSessionCount === 0} onClick={onRevokeOthers}><Icon name="shield" />退出全部其他设备</button></div>
    <section className="security-summary"><div><Icon name="shield" /><span><b>{sessions.length}</b><small>有效会话</small></span></div><p>系统只保留粗粒度浏览器和操作系统标签，不保存完整 User-Agent 或原始 IP 地址。会话最多持续 12 小时。</p></section>
    <section className="session-list">
      {sessions.map((session) => <article key={session.id}><div className="session-device"><i><Icon name="settings" /></i><span><b>{session.client_label}</b><small>{session.is_current ? "当前设备" : `登录于 ${formatAuditDate(session.created_at)}`}</small></span></div><div className="session-expiry"><small>有效期至</small><time dateTime={session.expires_at}>{formatAuditDate(session.expires_at)}</time></div>{session.is_current ? <span className="current-session"><i />正在使用</span> : <button disabled={busy} onClick={() => onRevoke(session)}>退出此设备</button>}</article>)}
    </section>
  </>;
}

function AuditView({ events }: { events: AuditEvent[] }) {
  return <>
    <div className="page-heading"><div><p className="kicker">安全追溯</p><h1>企业审计日志</h1><p>记录关键身份、权限、文档和问答操作。正文、问题、答案、邮箱和令牌不会写入审计元数据。</p></div><span className="assistant-mode"><i />仅企业管理员可见</span></div>
    {events.length === 0
      ? <section className="empty-state"><span><Icon name="shield" /></span><h2>尚无审计事件</h2><p>后续关键操作会按时间倒序显示在这里。</p></section>
      : <section className="audit-card">
        <div className="audit-table-head"><span>事件</span><span>操作者</span><span>资源</span><span>结果</span><span>时间</span></div>
        {events.map((event) => <article className="audit-row" key={event.id}>
          <div><b>{auditActionLabels[event.action] ?? event.action}</b><small>{Object.entries(event.details).map(([key, value]) => `${key}: ${String(value)}`).join(" · ") || "无敏感正文"}</small></div>
          <span>{event.actor_id ? shortId(event.actor_id) : "系统"}</span>
          <span>{event.resource_type}{event.resource_id ? ` · ${shortId(event.resource_id)}` : ""}</span>
          <em className={`audit-outcome ${event.outcome}`}>{event.outcome === "success" ? "成功" : event.outcome === "denied" ? "已拒绝" : "失败"}</em>
          <time dateTime={event.created_at}>{formatAuditDate(event.created_at)}</time>
        </article>)}
      </section>}
  </>;
}

function Dialog({ title, description, onClose, children }: { title: string; description: string; onClose: () => void; children: ReactNode }) {
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section aria-modal="true" className="dialog" role="dialog"><button aria-label="关闭" className="dialog-close" onClick={onClose}>×</button><p className="kicker">Enterprise AI</p><h2>{title}</h2><p>{description}</p>{children}</section></div>;
}
