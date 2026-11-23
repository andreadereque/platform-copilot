import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Turn } from "../chat/reducer";
import { AssistantMessage } from "./AssistantMessage";

const base: Turn = {
  role: "assistant",
  text: "",
  tools: [],
  citations: [],
  step: "",
  meta: "",
  error: "",
  streaming: true,
};

describe("AssistantMessage", () => {
  it("shows only the cursor before the first step arrives", () => {
    const { container } = render(<AssistantMessage turn={base} />);
    expect(container.querySelector(".cursor")).not.toBeNull();
    expect(container.querySelector(".meta")).toBeNull();
  });

  it("shows the current step while streaming", () => {
    render(<AssistantMessage turn={{ ...base, step: "Paso 2 de 5" }} />);
    expect(screen.getByText("Paso 2 de 5")).toBeInTheDocument();
  });

  it("labels document sources by file and section", () => {
    render(
      <AssistantMessage
        turn={{
          ...base,
          streaming: false,
          text: "Hace falta motivo [1].",
          citations: [{ n: 1, source: "access-policy.md#datos-con-pii" }],
        }}
      />,
    );
    expect(
      screen.getByText("[1] access-policy.md › datos-con-pii"),
    ).toBeInTheDocument();
  });
});
