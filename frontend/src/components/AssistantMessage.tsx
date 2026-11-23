import type { Turn } from "../chat/reducer";
import { ToolChip } from "./ToolChip";

/** "[2]" in the answer becomes a superscript that matches the sources list below. */
function withCitations(text: string) {
  return text.split(/(\[\d+\])/).map((part, index) => {
    if (/^\[\d+\]$/.test(part)) {
      return <sup key={index}>{part}</sup>;
    }
    return <span key={index}>{part}</span>;
  });
}

function sourceLabel(source: string): string {
  return source.replace("#", " › ").replace("catalog:", "catálogo › ");
}

export function AssistantMessage({ turn }: { turn: Turn }) {
  return (
    <div className="msg bot">
      {turn.tools.length > 0 && (
        <div className="chips">
          {turn.tools.map((step) => (
            <ToolChip key={step.id} step={step} />
          ))}
        </div>
      )}
      <div className="answer">
        {withCitations(turn.text)}
        {turn.streaming && <span className="cursor" aria-hidden="true" />}
      </div>
      {turn.streaming && turn.step && <div className="meta">{turn.step}</div>}
      {turn.error && (
        <div className="error" role="alert">
          {turn.error}
        </div>
      )}
      {turn.citations.length > 0 && (
        <ol className="sources">
          {turn.citations.map((citation) => (
            <li key={citation.n}>
              [{citation.n}] {sourceLabel(citation.source)}
            </li>
          ))}
        </ol>
      )}
      {turn.meta && <div className="meta">{turn.meta}</div>}
    </div>
  );
}
