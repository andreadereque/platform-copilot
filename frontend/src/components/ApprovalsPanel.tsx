import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { decideRequest, fetchRequests } from "../api/client";
import type { AccessRequest } from "../api/schemas";
import type { Session } from "../auth/oidc";

const STATUS_LABEL = {
  pending: "pendiente",
  approved: "aprobada",
  rejected: "rechazada",
};

export function requestsKey(userId: string) {
  return ["access-requests", userId];
}

function Decided({ request }: { request: AccessRequest }) {
  return (
    <span className="meta">
      {STATUS_LABEL[request.status]} por {request.decided_by}
    </span>
  );
}

export function ApprovalsPanel({ session }: { session: Session }) {
  const { user, token } = session;
  const client = useQueryClient();
  // Keyset pagination: each page brings the cursor for the next one ("Cargar más").
  const requests = useInfiniteQuery({
    queryKey: requestsKey(user.id),
    queryFn: ({ pageParam }) => fetchRequests(token, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
  const items = requests.data?.pages.flatMap((page) => page.items);
  const decide = useMutation({
    mutationFn: (input: { id: number; decision: "approve" | "reject" }) =>
      decideRequest(token, input.id, input.decision),
    onSettled: () =>
      client.invalidateQueries({ queryKey: requestsKey(user.id) }),
  });

  return (
    <aside className="side" aria-label="Peticiones de acceso">
      <h2>Peticiones a tus data products</h2>
      <p className="meta">
        Solo la persona dueña de un data product puede aprobar o rechazar.
      </p>
      {requests.isPending && <p className="meta">Cargando…</p>}
      {requests.isError && (
        <p className="error" role="alert">
          {requests.error.message}
        </p>
      )}
      {decide.isError && (
        <p className="error" role="alert">
          {decide.error.message}
        </p>
      )}
      {items?.length === 0 && <p className="meta">No tienes peticiones.</p>}
      {items?.map((request) => (
        <div key={request.id} className={`req req-${request.status}`}>
          <b>
            #{request.id} · {request.product}
          </b>
          <div>
            {request.requester} · «{request.reason}»
          </div>
          {request.pii && <span className="pii">PII</span>}
          {request.status === "pending" && (
            <div className="acts">
              <button
                type="button"
                disabled={decide.isPending}
                onClick={() =>
                  decide.mutate({ id: request.id, decision: "approve" })
                }
              >
                Aprobar
              </button>
              <button
                type="button"
                className="ghost"
                disabled={decide.isPending}
                onClick={() =>
                  decide.mutate({ id: request.id, decision: "reject" })
                }
              >
                Rechazar
              </button>
            </div>
          )}
          {request.status !== "pending" && <Decided request={request} />}
        </div>
      ))}
      {requests.hasNextPage && (
        <button
          type="button"
          className="ghost more"
          disabled={requests.isFetchingNextPage}
          onClick={() => void requests.fetchNextPage()}
        >
          Cargar más
        </button>
      )}
    </aside>
  );
}
