import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { UserManager } from "oidc-client-ts";
import { App } from "./App";
import { createAuth, oidcSettings } from "./auth/oidc";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { mutations: { retry: 0 } },
});

const auth = createAuth(
  new UserManager(
    oidcSettings(import.meta.env.VITE_OIDC_AUTHORITY, window.location.origin),
  ),
  window.location,
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App auth={auth} />
    </QueryClientProvider>
  </StrictMode>,
);
