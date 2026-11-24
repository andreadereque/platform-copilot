import type {
  User as OidcUser,
  UserManager,
  UserManagerSettings,
} from "oidc-client-ts";
import type { User } from "../api/schemas";

// Login with OAuth 2.0 authorization code + PKCE, through oidc-client-ts:
// 1. login(): the library creates a random code_verifier, keeps it in sessionStorage and
//    redirects to Keycloak with code_challenge = BASE64URL(SHA256(code_verifier)).
// 2. The user types their password on Keycloak's page (never on ours).
// 3. Keycloak redirects back with ?code=...&state=...; restore() exchanges the code, together
//    with the code_verifier, for the tokens. A stolen code is useless without the verifier.
// 4. Every API call sends the access token as "Authorization: Bearer <jwt>".

export type Session = { token: string; user: User };

export interface Auth {
  /** Finish a login redirect if the URL carries one; otherwise the stored session, or null. */
  restore(): Promise<Session | null>;
  login(): Promise<void>;
  logout(): Promise<void>;
  /** Calls `listener` when the token is renewed in the background. Returns an unsubscribe. */
  onRenewed(listener: (session: Session) => void): () => void;
}

type Manager = Pick<
  UserManager,
  | "signinRedirect"
  | "signinRedirectCallback"
  | "getUser"
  | "signoutRedirect"
  | "events"
>;

export const CLIENT_ID = "platform-copilot-web";

export function oidcSettings(
  authority: string | undefined,
  origin: string,
): UserManagerSettings {
  return {
    authority: authority ?? "http://localhost:8080/realms/platform-copilot",
    client_id: CLIENT_ID, // a public client: a browser cannot keep a secret, PKCE replaces it
    redirect_uri: `${origin}/`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: "code", // authorization code; oidc-client-ts always adds PKCE (S256)
    scope: "openid profile",
    automaticSilentRenew: true, // renews the 5-minute access token with the refresh token
  };
}

function groupsClaim(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(String);
  }
  return [];
}

function stringClaim(value: unknown): string | undefined {
  if (typeof value === "string" && value) {
    return value;
  }
  return undefined;
}

export function toSession(oidcUser: OidcUser): Session {
  const profile = oidcUser.profile;
  const id = stringClaim(profile.preferred_username) ?? profile.sub;
  return {
    token: oidcUser.access_token,
    user: {
      id,
      name: stringClaim(profile.name) ?? id,
      groups: groupsClaim(profile.groups),
    },
  };
}

export function createAuth(manager: Manager, location: Location): Auth {
  // React StrictMode runs effects twice; the authorization code can only be redeemed once.
  let restoring: Promise<Session | null> | undefined;

  async function finishLogin(): Promise<Session> {
    const user = await manager.signinRedirectCallback(location.href);
    window.history.replaceState({}, "", location.pathname); // drop ?code=&state=
    return toSession(user);
  }

  async function stored(): Promise<Session | null> {
    const user = await manager.getUser();
    if (!user || user.expired) {
      return null;
    }
    return toSession(user);
  }

  return {
    restore() {
      const params = new URLSearchParams(location.search);
      if (!restoring && params.has("code") && params.has("state")) {
        restoring = finishLogin();
      }
      restoring ??= stored();
      return restoring;
    },
    login: () => manager.signinRedirect(),
    logout: () => manager.signoutRedirect(),
    onRenewed(listener) {
      return manager.events.addUserLoaded((user) => listener(toSession(user)));
    },
  };
}
