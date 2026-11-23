import { describe, expect, it } from "vitest";
import { splitSse } from "./sse";

describe("splitSse", () => {
  it("returns complete events and keeps the unfinished rest", () => {
    const { events, rest } = splitSse(
      'event: token\ndata: {"text":"a"}\n\nevent: done\ndata: {',
    );
    expect(events).toEqual([{ event: "token", data: '{"text":"a"}' }]);
    expect(rest).toBe("event: done\ndata: {");
  });

  it("defaults the event name and skips blocks without data", () => {
    const { events, rest } = splitSse("data: 1\n\n: comment\n\n");
    expect(events).toEqual([{ event: "message", data: "1" }]);
    expect(rest).toBe("");
  });
});
