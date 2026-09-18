import type { Config } from "tailwindcss";

/**
 * The palette lives in CSS custom properties (app/globals.css) because the
 * dashboard reads them as `var(--accent)` inline. This config only needs to
 * know where the classes are and to make the mono stack the default.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
};
export default config;
