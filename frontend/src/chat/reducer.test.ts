import { describe, expect, it } from "vitest";
import type { ChatEvent } from "../api/schemas";
import { journey } from "../test/fixtures";
import { chatReducer, initialChat, toHistory, type ChatState } from "./reducer";

function play(
  events: Array<[string, unknown]>,
  state: ChatState = initialChat,
): ChatState {
  let next = chatReducer(state, { type: "ask", text: "¿Dueño?" });
  for (const [event, data] of events) {
    next = chatReducer(next, {
      type: "event",
      event: { event, data } as ChatEvent,
    });
  }
  return next;
}

describe("chatReducer", () => {
  it("builds the assistant turn from the event stream", () => {
    const state = play(journey);
    const answer = state.turns[1];
    expect(state.conversationId).toBe("abc");
    expect(state.busy).toBe(false);
    expect(answer.text).toBe("La dueña es marta [1]. Petición creada.");
    expect(answer.tools.map((t) => [t.tool, t.status, t.summary])).toEqual([
      ["query_catalog", "ok", "1 filas"],
      ["create_access_request", "ok", "#42"],
    ]);
    expect(answer.citations).toEqual([
      { n: 1, source: "catalog:data_products" },
    ]);
    expect(answer.meta).toBe("2 pasos · 1200 tokens · 3.4 s · qwen2.5:3b");
    expect(answer.streaming).toBe(false);
  });

  it("shows progress while streaming and marks failed tools", () => {
    const state = play([
      ["step", { n: 1, max: 5 }],
      ["tool_call", { id: "a", tool: "search_docs", args: {} }],
      ["tool_call", { id: "b", tool: "query_catalog", args: {} }],
      [
        "tool_result",
        { id: "b", tool: "query_catalog", ok: false, summary: "error" },
      ],
    ]);
    const answer = state.turns[1];
    expect(state.busy).toBe(true);
    expect(answer.step).toBe("Paso 1 de 5");
    expect(answer.tools.map((t) => t.status)).toEqual(["running", "error"]);
  });

  it("ends the turn with the error message", () => {
    const fromServer = play([
      ["error", { code: "max_steps", message: "Stopped" }],
    ]);
    expect(fromServer.turns[1]).toMatchObject({
      error: "Stopped",
      streaming: false,
    });
    const fromNetwork = chatReducer(play([]), {
      type: "failed",
      message: "offline",
    });
    expect(fromNetwork.turns[1].error).toBe("offline");
    expect(fromNetwork.busy).toBe(false);
  });

  it("keeps only answered turns as history", () => {
    const answered = play(journey);
    const failed = chatReducer(
      chatReducer(answered, { type: "ask", text: "otra" }),
      {
        type: "failed",
        message: "x",
      },
    );
    expect(toHistory(failed.turns)).toEqual([
      { role: "user", content: "¿Dueño?" },
      { role: "assistant", content: "La dueña es marta [1]. Petición creada." },
      { role: "user", content: "otra" },
    ]);
    expect(toHistory(failed.turns, 1)).toEqual([
      { role: "user", content: "otra" },
    ]);
  });
});
