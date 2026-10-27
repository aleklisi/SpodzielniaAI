import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import i18next from "eslint-plugin-i18next";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // No user-visible text in JSX outside the message catalogues (14.4); attributes a person
  // reads or hears (aria-label, title, placeholder …) are checked by src/i18n/hardcoded.test.ts.
  {
    files: ["src/**/*.tsx"],
    ignores: ["src/**/*.test.tsx", "src/test/**"],
    plugins: { i18next },
    rules: {
      "i18next/no-literal-string": [
        "error",
        { mode: "jsx-text-only", words: { exclude: ["[0-9!-/:-@[-`{-~\\s·→—–…×%−✓✗●▲◆■]+", "[A-Z_-]+"] } },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Playwright's own output (reports with the bundled trace viewer, artefacts).
    "playwright-report/**",
    "test-results/**",
  ]),
]);

export default eslintConfig;
