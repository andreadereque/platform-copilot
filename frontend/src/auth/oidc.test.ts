import type { User as OidcUser, UserManager } from "oidc-client-ts";
import { describe, expect, it, vi } from "vitest";
import { createAuth, oidcSettings, toSession } from "./oidc";

function oidcUser(profile: Record<string, unknown>, expired = false): OidcUser {
  return {
    access_token: "jwt",
    expired,
    profile: { sub: "8f0c", ...profile },
  } as unknown as OidcUser;
}

const MARTA = oidcUser({
  preferred_username: "marta@finance",
  name: "Marta Ruiz",
  groups: ["finance", "public"],
});

function fakeManager(stored: OidcUser | null = null) {
  let loaded: ((user: OidcUser) => void) | undefined;
  const manager = {
    signinRedirect: vi.fn(() => Promise.resolve()),
    signinRedirectCallback: vi.fn(() => Promise.resolve(MARTA)),
    signoutRedirect: vi.fn(() => Promise.resolve()),
    getUser: vi.fn(() => Promise.resolve(stored)),
    events: {
      addUserLoaded: vi.fn((callback: (user: OidcUser) => void) => {
        loaded = callback;
        return () => {};
      }),
    },
  };
  return {
    manager: manager as unknown as UserManager,
    spies: manager,
    load: (user: OidcUser) => loaded?.(user),
  };
}

function at(url: string): Location {
  return new URL(url) as unknown as Location;
}

describe("oidc", () => {
  it("configures authorization code with the local Keycloak by default", () => {
    const settings = oidcSettings(undefined, "http://localhost:5173");
    expect(settings).toMatchObject({
      authority: "http://localhost:8080/realms/platform-copilot",
      client_id: "platform-copilot-web",
      redirect_uri: "http://localhost:5173/",
      response_type: "code",
    });
    expect(oidcSettings("https://sso.example/realms/x", "o").authority).toBe(
      "https://sso.example/realms/x",
    );
  });

  it("maps the token's claims to the user", () => {
    expect(toSession(MARTA)).toEqual({
      token: "jwt",
      user: {
        id: "marta@finance",
        name: "Marta Ruiz",
        groups: ["finance", "public"],
      },
    });
    expect(toSession(oidcUser({ name: "" })).user).toEqual({
      id: "8f0c",
      name: "8f0c",
      groups: [],
    });
  });

  it("finishes the redirect once, even if asked twice, and cleans the URL", async () => {
    const replace = vi.spyOn(window.history, "replaceState");
    const { manager, spies } = fakeManager();
    const auth = createAuth(
      manager,
      at("http://localhost:5173/?code=c&state=s"),
    );
    const [first, second] = await Promise.all([auth.restore(), auth.restore()]);
    expect(first?.user.id).toBe("marta@finance");
    expect(second).toBe(first);
    expect(spies.signinRedirectCallback).toHaveBeenCalledTimes(1);
    expect(spies.signinRedirectCallback).toHaveBeenCalledWith(
      "http://localhost:5173/?code=c&state=s",
    );
    expect(replace).toHaveBeenCalledWith({}, "", "/");
  });

  it("returns the stored session, or null when there is none or it expired", async () => {
    const page = at("http://localhost:5173/");
    expect(
      (await createAuth(fakeManager(MARTA).manager, page).restore())?.token,
    ).toBe("jwt");
    expect(await createAuth(fakeManager().manager, page).restore()).toBeNull();
    const expired = oidcUser({ preferred_username: "x" }, true);
    expect(
      await createAuth(fakeManager(expired).manager, page).restore(),
    ).toBeNull();
  });

  it("logs in, logs out and reports renewed tokens", async () => {
    const { manager, spies, load } = fakeManager();
    const auth = createAuth(manager, at("http://localhost:5173/"));
    await auth.login();
    await auth.logout();
    expect(spies.signinRedirect).toHaveBeenCalled();
    expect(spies.signoutRedirect).toHaveBeenCalled();
    const listener = vi.fn();
    auth.onRenewed(listener);
    load(MARTA);
    expect(listener).toHaveBeenCalledWith(toSession(MARTA));
  });
});
