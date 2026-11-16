import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend port. 8000 is often taken by other developer tools, so we default to 8321.
// Override with BACKEND_PORT=xxxx npm run dev (and start uvicorn with the same --port).
const backendPort = process.env.BACKEND_PORT ?? "8321";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": `http://127.0.0.1:${backendPort}` } },
});
