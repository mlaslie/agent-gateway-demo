import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { httpApi, type Api } from "./api/client";
import "./styles.css";

// Mock backend: build/dev with VITE_MOCK=1, or append ?mock=1 to the URL (handy for offline previews).
const wantMock = import.meta.env.VITE_MOCK === "1" || new URLSearchParams(location.search).get("mock") === "1";

const load: Promise<Api> = wantMock ? import("./api/mock").then((m) => m.mockApi) : Promise.resolve(httpApi);

load.then((api) => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App api={api} />
    </StrictMode>,
  );
});
