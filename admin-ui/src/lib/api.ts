const BASE = "";

function token(): string | null {
  return sessionStorage.getItem("samvit_token");
}

export function setToken(t: string) {
  sessionStorage.setItem("samvit_token", t);
}

export function clearToken() {
  sessionStorage.removeItem("samvit_token");
}

export function isAuthenticated(): boolean {
  return !!token();
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const headers: Record<string, string> = {};
  const t = token();
  if (t) headers["Authorization"] = `Bearer ${t}`;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 || res.status === 403) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.error || res.statusText, data);
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.error || res.statusText, data);
  }
  return res.json();
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public data?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
};

export interface Agent {
  id: string;
  handle: string;
  provider: string;
  role: string;
  suspended_at: string | null;
  created_at: string;
  tasks_created: number;
  tasks_claimed: number;
}

export interface Task {
  id: string;
  title: string;
  status: string;
  priority: number;
  tags: string[];
  worker_type: string | null;
  created_by: string;
  claimed_by: string | null;
  claimed_at: string | null;
  created_at: string;
}

export interface GuardViolation {
  id: string;
  direction: string;
  tool: string;
  pattern: string;
  category: string;
  severity: string;
  snippet: string;
  at: string;
}

export interface SystemStatus {
  agents: {
    total: number;
    active: number;
    suspended: number;
  };
  tasks: {
    pending: number;
    claimed: number;
    done: number;
    failed: number;
    cancelled: number;
  };
  storage: {
    kv_count: number;
    vector_count: number;
  };
  guard: {
    violations_24h: number;
  };
  events: {
    published: number;
    failed: number;
    connected: boolean;
    degraded: boolean;
  };
}

export interface Settings {
  guard_mode?: string;
  maintenance_mode?: boolean;
  [key: string]: unknown;
}

export interface GraphNode {
  id: string;
  handle: string;
  provider: string;
  role: string;
  suspended: boolean;
  created_at: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  created_at: string;
}

export interface TaskEdge extends GraphEdge {
  title: string;
  status: string;
}

export interface MessageEdge extends GraphEdge {
  topic: string;
  preview: string;
}

export interface TimelineEvent {
  type: string;
  summary: string;
  ref_id: string | null;
  at: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: {
    tasks: TaskEdge[];
    messages: MessageEdge[];
  };
  timeline: TimelineEvent[];
}
