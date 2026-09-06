import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { initializeAuth } from "./auth";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

initializeAuth()
  .then(() => root.render(<StrictMode><App /></StrictMode>))
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "Không thể kết nối Keycloak";
    root.render(<main className="boot-error"><h1>Không thể đăng nhập</h1><p>{message}</p></main>);
  });
