import type { CSSProperties } from "react";

/** The hero atom: a [[TYPE_hex]] token under a carbon redaction bar that wipes open
 * on mount to reveal it — the product's "substitution moment" made literal. */
export function Token({
  type,
  hex = "7f3a",
  delay = 0,
}: {
  type: string;
  hex?: string;
  delay?: number;
}) {
  return (
    <span className="token reveal whitespace-nowrap">
      <span className="br">[[</span>
      {type}_{hex}
      <span className="br">]]</span>
      <span
        className="seal-cover"
        aria-hidden
        style={{ "--reveal-delay": `${delay}ms` } as CSSProperties}
      />
    </span>
  );
}
