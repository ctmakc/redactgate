# RedactGate — design notes

Identity: **Redaction Field** (June 2026, admin console). The black redaction bar is the
material, the `[[TYPE_hex]]` token is the atom. Locked; Build North (Oct 2026) extends it.

| Axis | Decision |
|---|---|
| Background | office-stock vellum `#ECE7DD` / night "vault" `#14130F` (prefers-color-scheme) |
| Type | Geist Sans for prose and numbers, Geist Mono for labels, tokens, printer-output eyebrows |
| Accent | seal-cyan `#1B6E78` (lit `#4FB3BC`) = sealed & reversible; vermillion `#A8331F` = blocked/tamper only |
| Signature | the token chip and the carbon redaction bar; `raw values never cross this line` perimeter |
| Motion | wipe/clip-path seals (bar sweeps over a value, leaves the token), no fade-up as the only motion |

Tokens live as RGB CSS variables in `web/app/globals.css` (`--c-*`), consumed by Tailwind
(`rgb(var(--c-x) / <alpha-value>)`) and by Clerk's `appearance.variables`
(`web/lib/clerk-appearance.ts`), so Clerk's sign-in card, firm switcher and user menu flip
with the palette.

## Build North additions
- **Sign-in / sign-up / firm pages** — asymmetric 5/7 split. Left: a synthetic file note
  whose values get swept by a carbon bar and replaced by tokens on load (one orchestrated
  pass, `seal-swap`). Right: Clerk card.
- **Playground** — three panes read left to right as custody: *your note* (values on a
  carbon rule with an inline type tag) → dashed vertical perimeter "raw values stop here" →
  *what Claude received* (tokens seal in, staggered 70 ms) → *answer* (restored values get a
  seal underline that draws in). Below: the single audit row that was written.
- **Role-aware shell** — firm switcher (Clerk organization), role stamp, nav filtered by role.
- Reduced motion: every seal/unseal shows its end state.
