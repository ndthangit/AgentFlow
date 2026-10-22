import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { initializeAuth } from "./shared/auth";
import "./styles/index.css";

const root = createRoot(document.getElementById("root")!);

initializeAuth()
  .then(() => root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  ))
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "Could not connect to Keycloak";
    root.render(
      <main className="boot-error">
        <h1>Could not sign in</h1>
        <p>{message}</p>
      </main>,
    );
  });
