import type { ToolStep } from "../chat/reducer";

export function ToolChip({ step }: { step: ToolStep }) {
  return (
    <span
      className={`chip chip-${step.tool} chip-${step.status}`}
      data-testid="tool-chip"
    >
      {step.tool} · {step.summary}
    </span>
  );
}
