import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/** Unit tests live in tests/, mirroring the source folders. Run with `npm test`. */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    include: ["tests/**/*.test.{ts,tsx}"],
    environment: "node",
  },
});
