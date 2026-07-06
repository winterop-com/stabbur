import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { initAuth } from "./lib/auth";
import "./index.css";

// Install the bearer-token fetch wrapper before anything makes a request (V-14).
initAuth();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
