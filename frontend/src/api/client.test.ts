import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { journey, pendingRequest, sseStream } from "../test/fixtures";
import { server } from "../test/server";
import { ApiError, decideRequest, fetchRequests, streamChat } from "./client";
import type { ChatEvent } from "./schemas";

describe("api client", () => {
  it("loads pages of requests with the bearer token", async () => {
    let header = "";
    const urls: string[] = [];
    server.use(
      http.get("/api/access-requests", ({ request }) => {
        header = request.headers.get("Authorization") ?? "";
        urls.push(new URL(request.url).search);
        return HttpResponse.json({ items: [pendingRequest], next_cursor: "n" });
      }),
    );
    expect(await fetchRequests("jwt-marta", null)).toEqual({
      items: [pendingRequest],
      next_cursor: "n",
    });
    await fetchRequests("jwt-marta", "abc");
    expect(header).toBe("Bearer jwt-marta");
    expect(urls).toEqual(["?limit=10", "?limit=10&cursor=abc"]);
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
    expect((await decideRequest("jwt-marta", 42, "approve")).status).toBe(
      "approved",
    );
    const error = await decideRequest("jwt-laura", 42, "reject").catch(
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
      http.get(
        "/api/access-requests",
        () => new HttpResponse("boom", { status: 502 }),
      ),
    );
    await expect(fetchRequests("t", null)).rejects.toThrow("HTTP 502");
  });

  it("falls back to the status when the error JSON has no detail", async () => {
    server.use(
      http.get("/api/access-requests", () =>
        HttpResponse.json({ nope: 1 }, { status: 500 }),
      ),
    );
    await expect(fetchRequests("t", null)).rejects.toThrow("HTTP 500");
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
      token: "jwt-laura",
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
        HttpResponse.json({ detail: "Invalid token" }, { status: 401 }),
      ),
    );
    await expect(
      streamChat({
        token: "expired",
        message: "hola",
        history: [],
        conversationId: "c",
        onEvent: () => {},
      }),
    ).rejects.toThrow("Invalid token");
  });
});
