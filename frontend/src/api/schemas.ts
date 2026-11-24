import { z } from "zod";

// Everything that comes from the backend is validated at runtime: TypeScript types
// disappear when the code runs, so a changed API fails loudly here instead of deep in the UI.

export const userSchema = z.object({
  id: z.string(),
  name: z.string(),
  groups: z.array(z.string()),
});
export type User = z.infer<typeof userSchema>;

export const accessRequestSchema = z.object({
  id: z.number(),
  product: z.string(),
  owner: z.string(),
  requester: z.string(),
  reason: z.string(),
  status: z.enum(["pending", "approved", "rejected"]),
  pii: z.boolean(),
  created_at: z.string(),
  decided_by: z.string().nullable(),
  decided_at: z.string().nullable(),
});
export type AccessRequest = z.infer<typeof accessRequestSchema>;

/** One keyset page: pass `next_cursor` back to get the next one; null means the last page. */
export const requestsPageSchema = z.object({
  items: z.array(accessRequestSchema),
  next_cursor: z.string().nullable(),
});
export type RequestsPage = z.infer<typeof requestsPageSchema>;

export const chatEventSchema = z.discriminatedUnion("event", [
  z.object({
    event: z.literal("start"),
    data: z.object({ conversation_id: z.string() }),
  }),
  z.object({
    event: z.literal("step"),
    data: z.object({ n: z.number(), max: z.number() }),
  }),
  z.object({
    event: z.literal("tool_call"),
    data: z.object({
      id: z.string(),
      tool: z.string(),
      args: z.record(z.string(), z.unknown()),
    }),
  }),
  z.object({
    event: z.literal("tool_result"),
    data: z.object({
      id: z.string(),
      tool: z.string(),
      ok: z.boolean(),
      summary: z.string(),
    }),
  }),
  z.object({ event: z.literal("token"), data: z.object({ text: z.string() }) }),
  z.object({
    event: z.literal("citation"),
    data: z.object({ n: z.number(), source: z.string() }),
  }),
  z.object({
    event: z.literal("approval_pending"),
    data: z.object({ request_id: z.number() }),
  }),
  z.object({
    event: z.literal("done"),
    data: z.object({
      steps: z.number(),
      tokens: z.number(),
      ms: z.number(),
      model: z.string(),
    }),
  }),
  z.object({
    event: z.literal("error"),
    data: z.object({ code: z.string(), message: z.string() }),
  }),
]);
export type ChatEvent = z.infer<typeof chatEventSchema>;
