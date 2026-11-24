import { useMutation } from "@tanstack/react-query";
import { sendFeedback } from "../api/client";

type Props = { token: string; conversationId: string };

/** Thumbs up / down on the last answer. Thumbs down become candidate eval cases. */
export function FeedbackButtons({ token, conversationId }: Props) {
  const send = useMutation({
    mutationFn: (rating: 1 | -1) =>
      sendFeedback(token, { conversation_id: conversationId, rating }),
  });
  if (send.isSuccess) {
    return <p className="meta">Gracias por tu valoración.</p>;
  }
  return (
    <div className="feedback" aria-label="Valorar la respuesta">
      <span className="meta">¿Te ha servido?</span>
      <button
        type="button"
        className="ghost"
        aria-label="Me ha servido"
        disabled={send.isPending}
        onClick={() => send.mutate(1)}
      >
        👍
      </button>
      <button
        type="button"
        className="ghost"
        aria-label="No me ha servido"
        disabled={send.isPending}
        onClick={() => send.mutate(-1)}
      >
        👎
      </button>
      {send.isError && (
        <span className="error" role="alert">
          {send.error.message}
        </span>
      )}
    </div>
  );
}
