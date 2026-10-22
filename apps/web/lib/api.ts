export type Tenant = {
  id: string;
  name: string;
  status: string;
  mfa_policy: "off" | "admins" | "all";
  mfa_required_for_current_user: boolean;
  created_at: string;
};

export type MembershipRole = "enterprise_admin" | "knowledge_admin" | "member";

export type Membership = {
  tenant_id: string;
  user_id: string;
  role: MembershipRole;
  created_at: string;
};

export type Classification = "internal" | "confidential" | "restricted";

export type Space = {
  id: string;
  tenant_id: string;
  name: string;
  classification: Classification;
  created_at: string;
};

export type SpaceRole = "manager" | "viewer";

export type SpaceMembership = {
  tenant_id: string;
  space_id: string;
  user_id: string;
  role: SpaceRole;
  created_at: string;
};

export type AuditOutcome = "success" | "denied" | "failure";

export type AuditEvent = {
  id: string;
  tenant_id: string;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: AuditOutcome;
  details: Record<string, string | number | boolean | null>;
  created_at: string;
};

export type DocumentStatus = "ready" | "published" | "superseded" | "failed";

export type KnowledgeDocument = {
  id: string;
  tenant_id: string;
  space_id: string;
  title: string;
  created_by: string;
  created_at: string;
};

export type DocumentVersion = {
  id: string;
  tenant_id: string;
  document_id: string;
  version_number: number;
  filename: string;
  media_type: string;
  byte_size: number;
  content_hash: string;
  status: DocumentStatus;
  error_message: string | null;
  created_by: string;
  created_at: string;
  published_at: string | null;
};

export type DocumentSummary = {
  document: KnowledgeDocument;
  latest_version: DocumentVersion;
};

export type DocumentUpload = {
  document: KnowledgeDocument;
  version: DocumentVersion;
};

export type DocumentChunk = {
  id: string;
  document_version_id: string;
  position: number;
  locator: string;
  text: string;
};

export type DocumentContent = {
  document: KnowledgeDocument;
  version: DocumentVersion;
  chunks: DocumentChunk[];
};

export type AnswerStatus = "grounded" | "insufficient_evidence";

export type KnowledgeStatus = {
  semantic_enabled: boolean;
  generation_enabled: boolean;
  restricted_by_policy: boolean;
  published_chunks: number;
  indexed_chunks: number;
  embedding_model: string | null;
  answer_model: string | null;
};

export function getKnowledgeStatus(actorId: string, tenantId: string, spaceId?: string): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>(`/v1/tenants/${tenantId}/knowledge-status${spaceId ? `?space_id=${encodeURIComponent(spaceId)}` : ""}`, actorId);
}

export function rebuildSemanticIndex(actorId: string, tenantId: string, versionId: string): Promise<DocumentVersion> {
  return request<DocumentVersion>(`/v1/tenants/${tenantId}/document-versions/${versionId}/semantic-index`, actorId, { method: "POST" });
}

export type AnswerCitation = {
  citation_number: number;
  document_id: string;
  document_version_id: string;
  document_chunk_id: string;
  document_title: string;
  filename: string;
  version_number: number;
  locator: string;
  excerpt: string;
  relevance_score: number;
};

export type KnowledgeAnswer = {
  id: string;
  tenant_id: string;
  created_by: string;
  question: string;
  answer: string;
  status: AnswerStatus;
  generator: "local" | "openai" | "ollama";
  model_name: string | null;
  citations: AnswerCitation[];
  created_at: string;
};

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export type BrowserSession = {
  user_id: string;
  mode: "development" | "public_demo";
};

export type Account = {
  mfa_required: false;
  user_id: string;
  email: string;
  tenant_id: string | null;
  email_verified: boolean;
  mfa_enabled: boolean;
};

export type MfaChallenge = {
  mfa_required: true;
  challenge_token: string;
};

export type LoginResult = Account | MfaChallenge;

export type MfaSetup = {
  secret: string;
  otpauth_uri: string;
};

export type AccountSession = {
  id: string;
  client_label: string;
  is_current: boolean;
  created_at: string;
  expires_at: string;
};

export type Invitation = {
  id: string;
  tenant_id: string;
  email: string;
  role: MembershipRole;
  expires_at: string;
  invite_token: string;
  delivery_status: "sent" | "manual";
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function apiError(response: Response): Promise<ApiError> {
  const body = (await response.json().catch(() => null)) as { detail?: string } | null;
  return new ApiError(body?.detail ?? "API 请求失败", response.status);
}

async function request<T>(path: string, actorId: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Dev-User-Id": actorId,
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw await apiError(response);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function accountRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) throw await apiError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function getCurrentAccount(): Promise<Account> {
  return accountRequest<Account>("/v1/auth/me");
}

export function registerAccount(input: {
  email: string;
  password: string;
  organization_name?: string;
}): Promise<Account> {
  return accountRequest<Account>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function loginAccount(email: string, password: string): Promise<LoginResult> {
  return accountRequest<LoginResult>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function completeMfaLogin(challengeToken: string, code: string): Promise<Account> {
  return accountRequest<Account>("/v1/auth/mfa/login", {
    method: "POST",
    body: JSON.stringify({ challenge_token: challengeToken, code }),
  });
}

export function startMfaSetup(password: string): Promise<MfaSetup> {
  return accountRequest<MfaSetup>("/v1/auth/mfa/setup", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function confirmMfaSetup(code: string): Promise<{ recovery_codes: string[] }> {
  return accountRequest<{ recovery_codes: string[] }>("/v1/auth/mfa/confirm", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function regenerateMfaRecoveryCodes(
  password: string,
  code: string,
): Promise<{ recovery_codes: string[] }> {
  return accountRequest<{ recovery_codes: string[] }>("/v1/auth/mfa/recovery-codes", {
    method: "POST",
    body: JSON.stringify({ password, code }),
  });
}

export function disableMfa(password: string, code: string): Promise<Account> {
  return accountRequest<Account>("/v1/auth/mfa", {
    method: "DELETE",
    body: JSON.stringify({ password, code }),
  });
}

export function logoutAccount(): Promise<void> {
  return accountRequest<void>("/v1/auth/logout", { method: "POST" });
}

export function listAccountSessions(): Promise<AccountSession[]> {
  return accountRequest<AccountSession[]>("/v1/auth/sessions");
}

export function revokeAccountSession(sessionId: string): Promise<void> {
  return accountRequest<void>(`/v1/auth/sessions/${sessionId}`, { method: "DELETE" });
}

export function revokeOtherAccountSessions(): Promise<{ revoked_count: number }> {
  return accountRequest<{ revoked_count: number }>("/v1/auth/sessions", {
    method: "DELETE",
  });
}

export function revokeMemberSessions(
  actorId: string,
  tenantId: string,
  userId: string,
): Promise<{ revoked_count: number }> {
  return request<{ revoked_count: number }>(
    `/v1/tenants/${tenantId}/members/${userId}/sessions`,
    actorId,
    { method: "DELETE" },
  );
}

export function verifyEmail(token: string): Promise<Account> {
  return accountRequest<Account>("/v1/auth/email/verify", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function resendVerification(email: string): Promise<{ message: string }> {
  return accountRequest<{ message: string }>("/v1/auth/email/resend", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function requestPasswordReset(email: string): Promise<{ message: string }> {
  return accountRequest<{ message: string }>("/v1/auth/password/forgot", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, password: string): Promise<LoginResult> {
  return accountRequest<LoginResult>("/v1/auth/password/reset", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });
}

export function createInvitation(
  actorId: string,
  tenantId: string,
  input: { email: string; role: MembershipRole },
): Promise<Invitation> {
  return request<Invitation>(`/v1/tenants/${tenantId}/invitations`, actorId, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function acceptInvitation(tenantId: string, token: string): Promise<Account> {
  return accountRequest<Account>(`/v1/auth/invitations/${tenantId}/accept`, {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function openBrowserSession(actorId: string): Promise<BrowserSession> {
  const response = await fetch(`${API_BASE_URL}/v1/session`, {
    method: "POST",
    cache: "no-store",
    credentials: "include",
    headers: { "X-Dev-User-Id": actorId },
  });
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<BrowserSession>;
}

export function createTenant(actorId: string, name: string): Promise<Tenant> {
  return request<Tenant>("/v1/tenants", actorId, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function getTenant(actorId: string, tenantId: string): Promise<Tenant> {
  return request<Tenant>(`/v1/tenants/${tenantId}`, actorId);
}

export function updateTenantSecurityPolicy(
  actorId: string,
  tenantId: string,
  mfaPolicy: Tenant["mfa_policy"],
): Promise<Tenant> {
  return request<Tenant>(`/v1/tenants/${tenantId}/security-policy`, actorId, {
    method: "PATCH",
    body: JSON.stringify({ mfa_policy: mfaPolicy }),
  });
}

export function listSpaces(actorId: string, tenantId: string): Promise<Space[]> {
  return request<Space[]>(`/v1/tenants/${tenantId}/spaces`, actorId);
}

export function createSpace(
  actorId: string,
  tenantId: string,
  input: { name: string; classification: Classification },
): Promise<Space> {
  return request<Space>(`/v1/tenants/${tenantId}/spaces`, actorId, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listSpaceMembers(
  actorId: string,
  tenantId: string,
  spaceId: string,
): Promise<SpaceMembership[]> {
  return request<SpaceMembership[]>(
    `/v1/tenants/${tenantId}/spaces/${spaceId}/members`,
    actorId,
  );
}

export function saveSpaceMember(
  actorId: string,
  tenantId: string,
  spaceId: string,
  input: { user_id: string; role: SpaceRole },
): Promise<SpaceMembership> {
  return request<SpaceMembership>(
    `/v1/tenants/${tenantId}/spaces/${spaceId}/members`,
    actorId,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function removeSpaceMember(
  actorId: string,
  tenantId: string,
  spaceId: string,
  userId: string,
): Promise<void> {
  return request<void>(
    `/v1/tenants/${tenantId}/spaces/${spaceId}/members/${userId}`,
    actorId,
    { method: "DELETE" },
  );
}

export function listMembers(actorId: string, tenantId: string): Promise<Membership[]> {
  return request<Membership[]>(`/v1/tenants/${tenantId}/members`, actorId);
}

export function listAuditEvents(
  actorId: string,
  tenantId: string,
  limit = 100,
): Promise<AuditEvent[]> {
  return request<AuditEvent[]>(
    `/v1/tenants/${tenantId}/audit-events?limit=${limit}`,
    actorId,
  );
}

export async function downloadAuditExport(
  actorId: string,
  tenantId: string,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(
    `${API_BASE_URL}/v1/tenants/${tenantId}/audit-events/export.csv`,
    {
      cache: "no-store",
      credentials: "include",
      headers: { "X-Dev-User-Id": actorId },
    },
  );
  if (!response.ok) throw await apiError(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? "audit-events.csv";
  return { blob: await response.blob(), filename };
}

export function saveMember(
  actorId: string,
  tenantId: string,
  input: { user_id: string; role: MembershipRole },
): Promise<Membership> {
  return request<Membership>(`/v1/tenants/${tenantId}/members`, actorId, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listDocuments(
  actorId: string,
  tenantId: string,
  spaceId: string,
): Promise<DocumentSummary[]> {
  return request<DocumentSummary[]>(
    `/v1/tenants/${tenantId}/spaces/${spaceId}/documents`,
    actorId,
  );
}

export function getDocumentContent(
  actorId: string,
  tenantId: string,
  documentId: string,
): Promise<DocumentContent> {
  return request<DocumentContent>(
    `/v1/tenants/${tenantId}/documents/${documentId}/content`,
    actorId,
  );
}

export async function uploadDocument(
  actorId: string,
  tenantId: string,
  spaceId: string,
  input: { title: string; file: File },
): Promise<DocumentUpload> {
  const form = new FormData();
  form.set("title", input.title);
  form.set("file", input.file);
  const response = await fetch(
    `${API_BASE_URL}/v1/tenants/${tenantId}/spaces/${spaceId}/documents`,
    {
      method: "POST",
      body: form,
      credentials: "include",
      headers: { "X-Dev-User-Id": actorId },
    },
  );
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<DocumentUpload>;
}

export async function uploadDocumentVersion(
  actorId: string,
  tenantId: string,
  documentId: string,
  file: File,
): Promise<DocumentVersion> {
  const form = new FormData();
  form.set("file", file);
  const response = await fetch(
    `${API_BASE_URL}/v1/tenants/${tenantId}/documents/${documentId}/versions`,
    {
      method: "POST",
      body: form,
      credentials: "include",
      headers: { "X-Dev-User-Id": actorId },
    },
  );
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<DocumentVersion>;
}

export function publishDocumentVersion(
  actorId: string,
  tenantId: string,
  versionId: string,
): Promise<DocumentVersion> {
  return request<DocumentVersion>(
    `/v1/tenants/${tenantId}/document-versions/${versionId}/publish`,
    actorId,
    { method: "POST" },
  );
}

export function askKnowledgeQuestion(
  actorId: string,
  tenantId: string,
  input: { question: string; space_ids?: string[] },
): Promise<KnowledgeAnswer> {
  return request<KnowledgeAnswer>(`/v1/tenants/${tenantId}/questions`, actorId, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listQuestionHistory(
  actorId: string,
  tenantId: string,
): Promise<KnowledgeAnswer[]> {
  return request<KnowledgeAnswer[]>(`/v1/tenants/${tenantId}/questions`, actorId);
}
