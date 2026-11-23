import type { HistoryTurn } from "../api/client";
import type { ChatEvent } from "../api/schemas";

export type ToolStep = {
  id: string;
  tool: string;
  status: "running" | "ok" | "error";
  summary: string;
};

export type Citation = { n: number; source: string };

export type Turn = {
  role: "user" | "assistant";
  text: string;
  tools: ToolStep[];
  citations: Citation[];
  step: string;
  meta: string;
  error: string;
  streaming: boolean;
};

export type ChatState = {
  turns: Turn[];
  conversationId: string;
  busy: boolean;
};

export type ChatAction =
  | { type: "ask"; text: string }
  | { type: "event"; event: ChatEvent }
  | { type: "failed"; message: string };

export const initialChat: ChatState = {
  turns: [],
  conversationId: "",
  busy: false,
};

function newTurn(role: Turn["role"], text: string, streaming: boolean): Turn {
  return {
    role,
    text,
    tools: [],
    citations: [],
    step: "",
    meta: "",
    error: "",
    streaming,
  };
}

function updateLast(
  state: ChatState,
  change: (turn: Turn) => Partial<Turn>,
): ChatState {
  const turns = state.turns.slice();
  const last = turns[turns.length - 1];
  turns[turns.length - 1] = { ...last, ...change(last) };
  return { ...state, turns };
}

function finish(state: ChatState, change: Partial<Turn>): ChatState {
  return {
    ...updateLast(state, () => ({ ...change, streaming: false, step: "" })),
    busy: false,
  };
}

function applyEvent(state: ChatState, event: ChatEvent): ChatState {
  switch (event.event) {
    case "start":
      return { ...state, conversationId: event.data.conversation_id };
    case "step":
      return updateLast(state, () => ({
        step: `Paso ${event.data.n} de ${event.data.max}`,
      }));
    case "tool_call":
      return updateLast(state, (turn) => ({
        tools: [
          ...turn.tools,
          {
            id: event.data.id,
            tool: event.data.tool,
            status: "running",
            summary: "…",
          },
        ],
      }));
    case "tool_result":
      return updateLast(state, (turn) => ({
        tools: turn.tools.map((tool) => {
          if (tool.id !== event.data.id || tool.status !== "running") {
            return tool;
          }
          let status: ToolStep["status"] = "error";
          if (event.data.ok) {
            status = "ok";
          }
          return { ...tool, status, summary: event.data.summary };
        }),
      }));
    case "token":
      return updateLast(state, (turn) => ({
        text: turn.text + event.data.text,
      }));
    case "citation":
      return updateLast(state, (turn) => ({
        citations: [...turn.citations, event.data],
      }));
    case "approval_pending":
      return state;
    case "done": {
      const seconds = (event.data.ms / 1000).toFixed(1);
      const meta = `${event.data.steps} pasos · ${event.data.tokens} tokens · ${seconds} s · ${event.data.model}`;
      return finish(state, { meta });
    }
    case "error":
      return finish(state, { error: event.data.message });
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  if (action.type === "ask") {
    const turns = [
      ...state.turns,
      newTurn("user", action.text, false),
      newTurn("assistant", "", true),
    ];
    return { ...state, turns, busy: true };
  }
  if (action.type === "failed") {
    return finish(state, { error: action.message });
  }
  return applyEvent(state, action.event);
}

/** The last answered turns, sent back so the model keeps the conversation's context. */
export function toHistory(turns: Turn[], limit = 10): HistoryTurn[] {
  return turns
    .filter((turn) => !turn.streaming && !turn.error && turn.text)
    .map((turn) => ({ role: turn.role, content: turn.text }))
    .slice(-limit);
}
