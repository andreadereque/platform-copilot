import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { journey, pendingRequest, sseStream, users } from "../test/fixtures";
import { server } from "../test/server";
import {
  ApiError,
  decideRequest,
  fetchRequests,
  fetchUsers,
  streamChat,
} from "./client";
import type { ChatEvent } from "./schemas";

describe("api client", () => {
  it("loads users and requests with the user header", async () => {
    let header = "";
    server.use(
      http.get("/api/users", () => HttpResponse.json(users)),
      http.get("/api/access-requests", ({ request }) => {
        header = request.headers.get("X-User-Id") ?? "";
        return HttpResponse.json([pendingRequest]);
      }),
    );
    expect(await fetchUsers()).toEqual(users);
    expect(await fetchRequests("marta@finance")).toEqual([pendingRequest]);
    expect(header).toBe("marta@finance");
  });

  it("posts decisions and surfaces the backend's detail on errors", async () => {
    server.use(
      http.post("/api/access-requests/42/approve", () =>
        HttpResponse.json({
          ...pendingRequest,
          status: "approved",
          decided_by: "marta@finance",
        }),
      ),
      http.post("/api/access-requests/42/reject", () =>
        HttpResponse.json(
          { detail: "Only the owner can decide" },
          { status: 403 },
        ),
      ),
    );
    expect((await decideRequest("marta@finance", 42, "approve")).status).toBe(
      "approved",
    );
    const error = await decideRequest("laura@sales", 42, "reject").catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 403,
      message: "Only the owner can decide",
    });
  });

  it("falls back to the status when the error body is not JSON", async () => {
    server.use(
      http.get("/api/users", () => new HttpResponse("boom", { status: 502 })),
    );
    await expect(fetchUsers()).rejects.toThrow("HTTP 502");
  });

  it("falls back to the status when the error JSON has no detail", async () => {
    server.use(
      http.get("/api/users", () =>
        HttpResponse.json({ nope: 1 }, { status: 500 }),
      ),
    );
    await expect(fetchUsers()).rejects.toThrow("HTTP 500");
  });

  it("streams chat events in order, even when a chunk cuts an event in half", async () => {
    let body: unknown;
    server.use(
      http.post("/api/chat", async ({ request }) => {
        body = await request.json();
        return new HttpResponse(sseStream(journey), {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );
    const events: ChatEvent[] = [];
    await streamChat({
      userId: "laura@sales",
      message: "hola",
      history: [{ role: "user", content: "antes" }],
      conversationId: "",
      onEvent: (event) => events.push(event),
    });
    expect(events.map((e) => e.event)).toEqual(journey.map(([name]) => name));
    expect(body).toEqual({
      message: "hola",
      history: [{ role: "user", content: "antes" }],
      conversation_id: null,
    });
  });

  it("rejects when the chat request fails", async () => {
    server.use(
      http.post("/api/chat", () =>
        HttpResponse.json({ detail: "Unknown user x" }, { status: 401 }),
      ),
    );
    await expect(
      streamChat({
        userId: "x",
        message: "hola",
        history: [],
        conversationId: "c",
        onEvent: () => {},
      }),
    ).rejects.toThrow("Unknown user x");
  });
});
