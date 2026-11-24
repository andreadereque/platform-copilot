import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { AccessRequest } from "./api/schemas";
import { App } from "./App";
import {
  decidedRequest,
  journey,
  fakeAuth,
  pendingRequest,
  sessionFor,
  sseStream,
  userFromAuthorization,
  users,
} from "./test/fixtures";
import { renderWithQuery } from "./test/render";
import { server } from "./test/server";

function chatReturns(events: Array<[string, unknown]>) {
  return http.post(
    "/api/chat",
    () =>
      new HttpResponse(sseStream(events), {
        headers: { "Content-Type": "text/event-stream" },
      }),
  );
}

function page(items: unknown[], nextCursor: string | null = null) {
  return HttpResponse.json({ items, next_cursor: nextCursor });
}

function requestsFor(lists: Record<string, unknown[]>) {
  return http.get("/api/access-requests", ({ request }) =>
    page(lists[userFromAuthorization(request)] ?? []),
  );
}

const laura = fakeAuth(sessionFor(users[0]));

describe("App", () => {
  it("asks a question and shows tools, answer, sources and stats", async () => {
    server.use(requestsFor({}), chatReturns(journey));
    renderWithQuery(<App auth={laura.auth} />);
    expect(
      await screen.findByText("No tienes peticiones."),
    ).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("Pregunta"),
      "¿Dueño de sales_es_monthly?",
    );
    await userEvent.click(screen.getByRole("button", { name: "Enviar" }));
    expect(
      await screen.findByText("2 pasos · 1200 tokens · 3.4 s · qwen2.5:3b"),
    ).toBeInTheDocument();
    expect(screen.getByText("¿Dueño de sales_es_monthly?")).toBeInTheDocument();
    const chips = screen
      .getAllByTestId("tool-chip")
      .map((chip) => chip.textContent);
    expect(chips).toEqual([
      "query_catalog · 1 filas",
      "create_access_request · #42",
    ]);
    expect(screen.getByText("[1]").tagName).toBe("SUP");
    expect(
      screen.getByText("[1] catálogo › data_products"),
    ).toBeInTheDocument();
  });

  it("starts from an example question and shows server errors", async () => {
    server.use(
      requestsFor({}),
      chatReturns([
        ["step", { n: 1, max: 5 }],
        ["error", { code: "llm_error", message: "Ollama is down" }],
      ]),
    );
    renderWithQuery(<App auth={laura.auth} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Consumidor de Kafka/i }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ollama is down",
    );
  });

  it("explains when the chat cannot be reached", async () => {
    server.use(
      requestsFor({}),
      http.post("/api/chat", () =>
        HttpResponse.json({ detail: "down" }, { status: 503 }),
      ),
    );
    renderWithQuery(<App auth={laura.auth} />);
    await userEvent.type(
      await screen.findByLabelText("Pregunta"),
      "hola{Enter}",
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se pudo contactar con el copilot",
    );
  });

  it("ignores empty questions", async () => {
    server.use(requestsFor({}));
    renderWithQuery(<App auth={laura.auth} />);
    await userEvent.type(
      await screen.findByLabelText("Pregunta"),
      "   {Enter}",
    );
    expect(screen.getByRole("button", { name: "Enviar" })).toBeDisabled();
    expect(screen.queryByText("Paso 1 de 5")).not.toBeInTheDocument();
  });

  it("lets the owner approve and reject", async () => {
    let lists: Record<string, AccessRequest[]> = {
      "marta@finance": [
        pendingRequest,
        { ...pendingRequest, id: 43 },
        decidedRequest,
      ],
    };
    server.use(
      http.get("/api/access-requests", ({ request }) =>
        page(lists[userFromAuthorization(request)] ?? []),
      ),
      http.post("/api/access-requests/42/approve", () => {
        lists = {
          "marta@finance": [
            {
              ...pendingRequest,
              status: "approved",
              decided_by: "marta@finance",
            },
          ],
        };
        return HttpResponse.json(lists["marta@finance"][0]);
      }),
      http.post("/api/access-requests/43/reject", () =>
        HttpResponse.json({ detail: "already rejected" }, { status: 409 }),
      ),
    );
    renderWithQuery(<App auth={fakeAuth(sessionFor(users[1])).auth} />);
    const cards = await screen.findAllByText(/^#\d+ · /);
    expect(cards).toHaveLength(3);
    expect(screen.getByText("aprobada por marta@finance")).toBeInTheDocument();
    expect(screen.getAllByText("PII")).toHaveLength(2);
    const second = screen
      .getByText("#43 · sales_es_monthly")
      .closest("div") as HTMLElement;
    await userEvent.click(
      within(second).getByRole("button", { name: "Rechazar" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "already rejected",
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Aprobar" })[0],
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Aprobar" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("refreshes the approvals panel when the agent creates a request", async () => {
    let calls = 0;
    server.use(
      http.get("/api/access-requests", () => {
        calls += 1;
        return page([]);
      }),
      chatReturns(journey),
    );
    renderWithQuery(<App auth={laura.auth} />);
    await userEvent.type(
      await screen.findByLabelText("Pregunta"),
      "pide acceso{Enter}",
    );
    await screen.findByText(/2 pasos/);
    await waitFor(() => expect(calls).toBeGreaterThan(1));
  });

  it("loads the next page of requests with the cursor", async () => {
    const cursors: Array<string | null> = [];
    server.use(
      http.get("/api/access-requests", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        cursors.push(cursor);
        if (cursor === "c2") {
          return page([decidedRequest]);
        }
        return page([pendingRequest], "c2");
      }),
    );
    renderWithQuery(<App auth={laura.auth} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Cargar más" }),
    );
    expect(
      await screen.findByText("#39 · hr_headcount_eu"),
    ).toBeInTheDocument();
    expect(screen.getByText("#42 · sales_es_monthly")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cargar más" }),
    ).not.toBeInTheDocument();
    expect(cursors).toEqual([null, "c2"]);
  });

  it("shows who is logged in and logs out", async () => {
    const { auth } = fakeAuth(sessionFor(users[1]));
    server.use(requestsFor({}));
    renderWithQuery(<App auth={auth} />);
    expect(
      await screen.findByText("Marta Ruiz · finance, public"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Salir" }));
    expect(auth.logout).toHaveBeenCalled();
  });

  it("sends the renewed access token on the next request", async () => {
    const tokens: string[] = [];
    server.use(
      http.get("/api/access-requests", ({ request }) => {
        tokens.push(request.headers.get("Authorization") ?? "");
        return page([pendingRequest], "next");
      }),
    );
    const { auth, renew } = fakeAuth(sessionFor(users[1]));
    renderWithQuery(<App auth={auth} />);
    await screen.findByText("#42 · sales_es_monthly");
    act(() => renew({ ...sessionFor(users[1]), token: "renewed" }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await waitFor(() =>
      expect(tokens).toEqual(["Bearer token-marta@finance", "Bearer renewed"]),
    );
  });

  it("asks anonymous users to log in with Keycloak", async () => {
    const { auth } = fakeAuth(null);
    renderWithQuery(<App auth={auth} />);
    expect(screen.getByText("Cargando…")).toBeInTheDocument();
    await userEvent.click(
      await screen.findByRole("button", { name: "Entrar con Keycloak" }),
    );
    expect(auth.login).toHaveBeenCalled();
  });

  it("explains a failed login and offers to try again", async () => {
    const { auth } = fakeAuth(null, {
      restore: () => Promise.reject(new Error("invalid_grant")),
    });
    renderWithQuery(<App auth={auth} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se pudo completar el login: Error: invalid_grant",
    );
    expect(
      screen.getByRole("button", { name: "Entrar con Keycloak" }),
    ).toBeInTheDocument();
  });

  it("shows loading errors from the API", async () => {
    server.use(
      http.get("/api/access-requests", () =>
        HttpResponse.json({ detail: "db down" }, { status: 500 }),
      ),
    );
    renderWithQuery(<App auth={laura.auth} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("db down");
  });
});
