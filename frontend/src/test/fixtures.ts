import { vi } from "vitest";
import type { AccessRequest, User } from "../api/schemas";
import type { Auth, Session } from "../auth/oidc";

export const users: User[] = [
  { id: "laura@sales", name: "Laura Gil", groups: ["sales", "public"] },
  { id: "marta@finance", name: "Marta Ruiz", groups: ["finance", "public"] },
];

/** In the tests the access token is just "token-<user id>"; MSW handlers read it back. */
export function sessionFor(user: User): Session {
  return { token: `token-${user.id}`, user };
}

export function userFromAuthorization(request: Request): string {
  return (request.headers.get("Authorization") ?? "").replace(
    "Bearer token-",
    "",
  );
}

/** An Auth that never redirects: restore() resolves with the given session. */
export function fakeAuth(
  session: Session | null,
  overrides: Partial<Auth> = {},
) {
  const renewed: Array<(session: Session) => void> = [];
  const auth = {
    restore: vi.fn(() => Promise.resolve(session)),
    login: vi.fn(() => Promise.resolve()),
    logout: vi.fn(() => Promise.resolve()),
    onRenewed: vi.fn((listener: (session: Session) => void) => {
      renewed.push(listener);
      return () => {};
    }),
    ...overrides,
  };
  return { auth, renew: (next: Session) => renewed.forEach((l) => l(next)) };
}

export const pendingRequest: AccessRequest = {
  id: 42,
  product: "sales_es_monthly",
  owner: "marta@finance",
  requester: "laura@sales",
  reason: "informe trimestral",
  status: "pending",
  pii: true,
  created_at: "2026-11-23T10:00:00Z",
  decided_by: null,
  decided_at: null,
};

export const decidedRequest: AccessRequest = {
  ...pendingRequest,
  id: 39,
  product: "hr_headcount_eu",
  pii: false,
  status: "approved",
  decided_by: "marta@finance",
  decided_at: "2026-11-22T10:00:00Z",
};

/** An SSE body that arrives in two network chunks, the first one cut mid-event. */
export function sseStream(
  events: Array<[string, unknown]>,
): ReadableStream<Uint8Array> {
  const text = events
    .map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
  const cut = Math.floor(text.length / 2);
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text.slice(0, cut)));
      controller.enqueue(encoder.encode(text.slice(cut)));
      controller.close();
    },
  });
}

export const journey: Array<[string, unknown]> = [
  ["start", { conversation_id: "abc" }],
  ["step", { n: 1, max: 5 }],
  ["tool_call", { id: "c1", tool: "query_catalog", args: { sql: "SELECT 1" } }],
  [
    "tool_result",
    { id: "c1", tool: "query_catalog", ok: true, summary: "1 filas" },
  ],
  ["tool_call", { id: "c2", tool: "create_access_request", args: {} }],
  [
    "tool_result",
    { id: "c2", tool: "create_access_request", ok: true, summary: "#42" },
  ],
  ["approval_pending", { request_id: 42 }],
  ["step", { n: 2, max: 5 }],
  ["token", { text: "La dueña es marta [1]. " }],
  ["token", { text: "Petición creada." }],
  ["citation", { n: 1, source: "catalog:data_products" }],
  ["done", { steps: 2, tokens: 1200, ms: 3400, model: "qwen2.5:3b" }],
];
