import { useReducer, useState, type FormEvent } from "react";
import { streamChat } from "../api/client";
import type { Session } from "../auth/oidc";
import {
  chatReducer,
  initialChat,
  toHistory,
  type ChatState,
} from "../chat/reducer";
import { AssistantMessage } from "./AssistantMessage";
import { FeedbackButtons } from "./FeedbackButtons";

const EXAMPLES = [
  "¿Quién es el dueño de sales_es_monthly? Pídeme acceso para el informe trimestral",
  "¿Qué hago si un consumidor de Kafka va atrasado?",
  "¿Qué data products de finanzas tienen datos personales?",
];

/** Only a finished, successful answer can be rated. */
function canRate(state: ChatState): boolean {
  const last = state.turns.at(-1);
  return Boolean(
    last &&
    last.role === "assistant" &&
    !last.streaming &&
    !last.error &&
    state.conversationId,
  );
}

type Props = { session: Session; onApprovalPending: () => void };

export function ChatPanel({ session, onApprovalPending }: Props) {
  const [state, dispatch] = useReducer(chatReducer, initialChat);
  const [draft, setDraft] = useState("");

  async function ask(text: string) {
    // No empty or concurrent questions: the submit button is disabled in both cases,
    // which also blocks submitting with Enter.
    const question = text.trim();
    const history = toHistory(state.turns);
    dispatch({ type: "ask", text: question });
    setDraft("");
    try {
      await streamChat({
        token: session.token,
        message: question,
        history,
        conversationId: state.conversationId,
        onEvent: (event) => {
          dispatch({ type: "event", event });
          if (event.event === "approval_pending") {
            onApprovalPending();
          }
        },
      });
    } catch (error) {
      dispatch({
        type: "failed",
        message: `No se pudo contactar con el copilot: ${String(error)}`,
      });
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(draft);
  }

  return (
    <section className="chat" aria-label="Chat">
      <div className="turns">
        {state.turns.length === 0 && (
          <div className="empty">
            <p>
              Pregunta sobre la documentación, el catálogo de datos o pide
              acceso a un data product.
            </p>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                className="example"
                onClick={() => void ask(example)}
              >
                {example}
              </button>
            ))}
          </div>
        )}
        {state.turns.map((turn, index) => {
          if (turn.role === "user") {
            return (
              <div key={index} className="msg me">
                {turn.text}
              </div>
            );
          }
          return <AssistantMessage key={index} turn={turn} />;
        })}
        {canRate(state) && (
          <FeedbackButtons
            key={state.turns.length}
            token={session.token}
            conversationId={state.conversationId}
          />
        )}
      </div>
      <form className="input" onSubmit={submit}>
        <input
          id="question"
          aria-label="Pregunta"
          placeholder="Pregunta sobre la plataforma de datos"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" disabled={state.busy || !draft.trim()}>
          Enviar
        </button>
      </form>
    </section>
  );
}
