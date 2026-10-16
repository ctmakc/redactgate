/**
 * Clerk components dressed in the Redaction Field tokens. Values are CSS variables, so
 * the sign-in card, org switcher and user menu follow the light/dark palette with the
 * rest of the console.
 */
export const clerkAppearance = {
  variables: {
    colorPrimary: "rgb(var(--c-carbon))",
    colorPrimaryForeground: "rgb(var(--c-leaf))",
    colorBackground: "rgb(var(--c-leaf))",
    colorForeground: "rgb(var(--c-carbon))",
    colorMuted: "rgb(var(--c-vellum))",
    colorMutedForeground: "rgb(var(--c-graphite))",
    colorInput: "rgb(var(--c-leaf))",
    colorInputForeground: "rgb(var(--c-carbon))",
    colorBorder: "rgb(var(--c-rule))",
    colorNeutral: "rgb(var(--c-carbon))",
    colorDanger: "rgb(var(--c-vermillion))",
    colorRing: "rgb(var(--c-seal) / 0.35)",
    borderRadius: "4px",
    fontFamily: "var(--font-geist-sans), system-ui, sans-serif",
    fontFamilyButtons: "var(--font-geist-mono), ui-monospace, monospace",
  },
  elements: {
    card: { boxShadow: "none", border: "1px solid rgb(var(--c-rule))" },
    cardBox: { boxShadow: "none" },
    footer: { background: "rgb(var(--c-vellum))" },
    headerTitle: { fontWeight: 600, letterSpacing: "-0.01em" },
    formButtonPrimary: { textTransform: "none", fontWeight: 500 },
    formFieldInput: { border: "1px solid rgb(var(--c-graphite) / 0.45)", boxShadow: "none" },
    socialButtonsBlockButton: { border: "1px solid rgb(var(--c-graphite) / 0.45)", boxShadow: "none" },
    otpCodeFieldInput: { border: "1px solid rgb(var(--c-graphite) / 0.45)" },
    organizationPreviewAvatarContainer: { display: "none" },
    organizationSwitcherPopoverActionButton: { fontFamily: "var(--font-geist-mono), monospace" },
  },
} as const;
