import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Sama dengan paths "@/*" di tsconfig.json. Tanpa ini, test komponen
    // tidak bisa mengimpor lewat alias yang dipakai aplikasi.
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    // Test murni (lib/) tetap di Node; test halaman/komponen butuh DOM.
    environmentMatchGlobs: [["**/*.test.tsx", "jsdom"]],
    include: ["**/*.test.{ts,tsx}"],
    setupFiles: ["./test/setup.ts"],
  },
});
