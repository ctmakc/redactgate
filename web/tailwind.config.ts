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
        // light
        vellum: "#ECE7DD", // bg — warm paper-neutral
        leaf: "#F6F2EA", // raised records-paper surface
        carbon: "#16140F", // near-black warm ink + the redaction bar
        graphite: "#4A463C", // secondary ink
        rule: "#C9C2B2", // 1px hairline
        seal: "#1B6E78", // SIGNAL: sealed & reversible (tokens, focus, active nav)
        vermillion: "#A8331F", // hard-block / tamper stamp
        // dark
        vault: "#14130F", // bg
        casing: "#1E1C16", // surface
        bleach: "#E8E3D6", // paper-white text
        seam: "#38352C", // hairline
        "seal-lit": "#4FB3BC",
        "vermillion-lit": "#D9583F",
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
        panel: "inset 0 1px 0 rgb(255 255 255 / 0.5)",
        modal: "0 8px 24px rgb(22 20 15 / 0.18)",
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
