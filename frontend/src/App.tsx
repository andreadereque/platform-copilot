import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { Auth, Session } from "./auth/oidc";
import { ApprovalsPanel } from "./components/ApprovalsPanel";
import { ChatPanel } from "./components/ChatPanel";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "failed"; message: string }
  | { status: "signed-in"; session: Session };

function LoginScreen({ auth }: { auth: Auth }) {
  return (
    <div className="login">
      <h1>platform-copilot</h1>
      <p className="meta">Proyecto de práctica · usuarios y datos inventados</p>
      <button type="button" onClick={() => void auth.login()}>
        Entrar con Keycloak
      </button>
    </div>
  );
}

export function App({ auth }: { auth: Auth }) {
  const client = useQueryClient();
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    auth.restore().then(
      (session) => {
        if (session) {
          setState({ status: "signed-in", session });
          return;
        }
        setState({ status: "anonymous" });
      },
      (error: unknown) =>
        setState({ status: "failed", message: String(error) }),
    );
    // A renewed access token replaces the old one; the user and their queries stay.
    return auth.onRenewed((session) =>
      setState({ status: "signed-in", session }),
    );
  }, [auth]);

  if (state.status === "loading") {
    return <p className="loading">Cargando…</p>;
  }
  if (state.status === "failed") {
    return (
      <div className="login">
        <p className="error" role="alert">
          No se pudo completar el login: {state.message}
        </p>
        <LoginScreen auth={auth} />
      </div>
    );
  }
  if (state.status === "anonymous") {
    return <LoginScreen auth={auth} />;
  }
  const { session } = state;

  return (
    <div className="app">
      <header className="bar">
        <strong>platform-copilot</strong>
        <span className="meta">Proyecto de práctica · datos inventados</span>
        <span className="sel">
          <span>
            {session.user.name} · {session.user.groups.join(", ")}
          </span>
          <button
            type="button"
            className="ghost"
            onClick={() => void auth.logout()}
          >
            Salir
          </button>
        </span>
      </header>
      <main className="cols">
        <ChatPanel
          key={session.user.id}
          session={session}
          onApprovalPending={() =>
            void client.invalidateQueries({ queryKey: ["access-requests"] })
          }
        />
        <ApprovalsPanel session={session} />
      </main>
    </div>
  );
}
