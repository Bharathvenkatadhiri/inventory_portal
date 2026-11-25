/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Django runs on :8000. In development Vite serves the app at /app/ and passes
// every other path (sign-in, the API, static files, checkout) to Django, so the
// browser only ever talks to one origin, as it does in production. The Host
// header is left as the browser sent it, so Django's CSRF origin check passes.
const DJANGO = process.env.DJANGO_URL ?? "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Built files are served by Django under /static/app/ (core/settings.py
  // STATICFILES_DIRS); the page itself under /app/ (organizations.views.app_shell).
  base: command === "build" ? "/static/app/" : "/app/",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "^/(?!app(/|$)).*": { target: DJANGO, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
}));
