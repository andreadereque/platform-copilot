import { z } from "zod";
import {
  accessRequestSchema,
  chatEventSchema,
  requestsPageSchema,
  type AccessRequest,
  type ChatEvent,
  type RequestsPage,
} from "./schemas";
import { splitSse } from "./sse";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    const parsed = z.object({ detail: z.string() }).safeParse(body);
    if (parsed.success) {
      return parsed.data.detail;
    }
  } catch {
    // not JSON: fall back to the status text
  }
  return `HTTP ${response.status}`;
}

async function request<T>(
  url: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return schema.parse(await response.json());
}

export const REQUESTS_PAGE_SIZE = 10;

/** The OIDC access token goes in every request; the API validates it (RS256, iss, aud, exp). */
function bearer(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

export function fetchRequests(
  token: string,
  cursor: string | null,
): Promise<RequestsPage> {
  const params = new URLSearchParams({ limit: String(REQUESTS_PAGE_SIZE) });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request(`/api/access-requests?${params}`, requestsPageSchema, {
    headers: bearer(token),
  });
}

export function decideRequest(
  token: string,
  requestId: number,
  decision: "approve" | "reject",
): Promise<AccessRequest> {
  return request(
    `/api/access-requests/${requestId}/${decision}`,
    accessRequestSchema,
    {
      method: "POST",
      headers: bearer(token),
    },
  );
}

export type HistoryTurn = { role: "user" | "assistant"; content: string };

export type ChatRequest = {
  token: string;
  message: string;
  history: HistoryTurn[];
  conversationId: string;
  onEvent: (event: ChatEvent) => void;
};

/** POST the question and call onEvent for every Server-Sent Event as it arrives. */
export async function streamChat(req: ChatRequest): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...bearer(req.token) },
    body: JSON.stringify({
      message: req.message,
      history: req.history,
      conversation_id: req.conversationId || null,
    }),
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = splitSse(buffer);
    buffer = rest;
    for (const raw of events) {
      req.onEvent(
        chatEventSchema.parse({ event: raw.event, data: JSON.parse(raw.data) }),
      );
    }
  }
}
