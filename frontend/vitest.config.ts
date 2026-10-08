import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary", "html"],
      // Périmètre mesuré : la logique métier front réellement testable
      // (fonctions pures). Les composants/pages React restent hors scope
      // tant qu'il n'y a pas de tests de composants.
      include: ["app/**/*.ts", "lib/**/*.ts"],
      exclude: ["**/*.test.ts", "**/*.d.ts"],
      // Plancher anti-régression sous la baseline mesurée (~87 %).
      thresholds: {
        statements: 80,
        branches: 80,
        functions: 80,
        lines: 80,
      },
    },
  },
});
