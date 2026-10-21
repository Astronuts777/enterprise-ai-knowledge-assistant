export type Tenant = {
  id: string;
  name: string;
  status: string;
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

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

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
    headers: {
      "Content-Type": "application/json",
      "X-Dev-User-Id": actorId,
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw await apiError(response);
  }

  return response.json() as Promise<T>;
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

export function listMembers(actorId: string, tenantId: string): Promise<Membership[]> {
  return request<Membership[]>(`/v1/tenants/${tenantId}/members`, actorId);
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
