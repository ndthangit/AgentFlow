import Keycloak from "keycloak-js";

export const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL ?? "http://localhost:8080",
  realm: import.meta.env.VITE_KEYCLOAK_REALM ?? "agentflow",
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? "agentflow-web",
});

export async function initializeAuth(): Promise<boolean> {
  return keycloak.init({
    onLoad: "login-required",
    pkceMethod: "S256",
    checkLoginIframe: false,
  });
}

export async function accessToken(): Promise<string> {
  await keycloak.updateToken(30);
  if (!keycloak.token) throw new Error("Không có access token");
  return keycloak.token;
}
