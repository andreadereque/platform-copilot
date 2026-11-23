export type RawEvent = { event: string; data: string };

/** Split a growing SSE buffer into complete events and the unfinished rest. */
export function splitSse(buffer: string): { events: RawEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const [rest] = blocks.splice(blocks.length - 1, 1); // split always returns at least one item
  const events: RawEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) {
        event = line.slice("event: ".length);
      }
      if (line.startsWith("data: ")) {
        data += line.slice("data: ".length);
      }
    }
    if (data) {
      events.push({ event, data });
    }
  }
  return { events, rest };
}
