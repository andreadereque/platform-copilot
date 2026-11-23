import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { fetchUsers } from "./api/client";
import { ApprovalsPanel } from "./components/ApprovalsPanel";
import { ChatPanel } from "./components/ChatPanel";

const DEFAULT_USER = "laura@sales";

export function App() {
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: fetchUsers });
  const [selected, setSelected] = useState(DEFAULT_USER);

  if (users.isPending) {
    return <p className="loading">Cargando…</p>;
  }
  if (users.isError) {
    return (
      <p className="error" role="alert">
        No se pudo cargar la lista de usuarios: {users.error.message}
      </p>
    );
  }
  const user =
    users.data.find((candidate) => candidate.id === selected) ?? users.data[0];

  return (
    <div className="app">
      <header className="bar">
        <strong>platform-copilot</strong>
        <span className="meta">Proyecto de práctica · datos inventados</span>
        <label className="sel" htmlFor="user">
          Usuario
          <select
            id="user"
            value={user.id}
            onChange={(event) => setSelected(event.target.value)}
          >
            {users.data.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.id} · {candidate.groups.join(", ")}
              </option>
            ))}
          </select>
        </label>
      </header>
      <main className="cols">
        <ChatPanel
          key={user.id}
          user={user}
          onApprovalPending={() =>
            void client.invalidateQueries({ queryKey: ["access-requests"] })
          }
        />
        <ApprovalsPanel user={user} />
      </main>
    </div>
  );
}
