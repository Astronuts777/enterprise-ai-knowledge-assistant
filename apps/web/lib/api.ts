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
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(body?.detail ?? "API 请求失败", response.status);
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
