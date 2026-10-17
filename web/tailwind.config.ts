import type { Config } from "tailwindcss";

// ── REDACTION FIELD ──────────────────────────────────────────────────────────
// The black redaction bar is the material. Warm office-stock paper, carbon ink,
// one seal-cyan signal (sealed & reversible), one vermillion stamp (blocked/tamper).
// Greens/emeralds and indigo are banned. Hairline-ruled, flat (an appliance, not an app).

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Every surface/ink colour is a CSS variable (RGB channels) so the whole console
        // flips to the "vault" night palette under prefers-color-scheme: dark.
        vellum: "rgb(var(--c-vellum) / <alpha-value>)", // bg — warm paper / vault
        leaf: "rgb(var(--c-leaf) / <alpha-value>)", // raised records-paper surface / casing
        carbon: "rgb(var(--c-carbon) / <alpha-value>)", // ink + the redaction bar / bleach
        graphite: "rgb(var(--c-graphite) / <alpha-value>)", // secondary ink
        rule: "rgb(var(--c-rule) / <alpha-value>)", // 1px hairline / seam
        seal: "rgb(var(--c-seal) / <alpha-value>)", // SIGNAL: sealed & reversible
        vermillion: "rgb(var(--c-vermillion) / <alpha-value>)", // hard-block / tamper stamp
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: {
        DEFAULT: "4px",
        sm: "3px",
        none: "0",
      },
      fontSize: {
        micro: ["12px", { lineHeight: "16px", letterSpacing: "0" }],
        small: ["13px", { lineHeight: "20px" }],
        body: ["14px", { lineHeight: "22px" }],
        h2: ["18px", { lineHeight: "24px" }],
        h1: ["24px", { lineHeight: "30px" }],
        display: ["30px", { lineHeight: "36px" }],
      },
      boxShadow: {
        panel: "inset 0 1px 0 rgb(var(--c-highlight) / 0.5)",
        modal: "0 8px 24px rgb(0 0 0 / 0.18)",
      },
      keyframes: {
        unseal: {
          "0%": { clipPath: "inset(0 100% 0 0)" },
          "100%": { clipPath: "inset(0 0 0 0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
