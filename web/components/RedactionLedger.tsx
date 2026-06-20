import type { CSSProperties } from "react";

import { Token } from "./Token";

const HEX = ["7f3a", "b1c2", "9d4e", "2a8f", "0c6b", "5e1d", "c4a9", "8b30"];
const SHORT: Record<string, string> = {
  CREDIT_CARD: "CC",
  IP_ADDRESS: "IP",
  BANK_ACCOUNT: "ACCT",
  PHONE: "PHONE",
};

export interface LedgerRow {
  entity_type: string;
  count: number;
}

/** The dashboard hero — not a big-number card. Each detected entity type is a row whose
 * carbon redaction bar (length ∝ volume) wipes open to reveal its token. States the whole
 * product thesis at a glance: this much was sealed, none of it is here. */
export function RedactionLedger({ items, total }: { items: LedgerRow[]; total: number }) {
  const max = Math.max(1, ...items.map((i) => i.count));
  return (
    <section className="panel p-5">
      <div className="flex items-baseline justify-between">
        <h2 className="eyebrow">redaction ledger</h2>
        <div className="font-mono text-micro text-graphite">
          <span className="tabular-nums text-carbon">{total.toLocaleString()}</span> sealed
          {" · "}
          <span className="text-carbon">0</span> raw stored
        </div>
      </div>

      <div className="mt-5 flex flex-col gap-3">
        {items.length === 0 ? (
          <div className="flex items-center gap-3 font-mono text-small text-graphite">
            <span className="redbar" style={{ width: 96 }}>
              <span className="redbar-fill" />
            </span>
            nothing sealed yet.
          </div>
        ) : (
          items.map((it, i) => (
            <div key={it.entity_type} className="flex items-center gap-3">
              <div className="w-28 shrink-0 truncate font-mono text-small text-carbon">
                {it.entity_type.toLowerCase()}
              </div>
              <span
                className="redbar"
                style={{ width: `${Math.max(12, Math.round((it.count / max) * 240))}px` }}
              >
                <span
                  className="redbar-fill"
                  style={{ animationDelay: `${i * 60}ms` } as CSSProperties}
                />
              </span>
              <Token
                type={SHORT[it.entity_type] ?? it.entity_type}
                hex={HEX[i % HEX.length]}
                delay={i * 60}
              />
              <div className="ml-auto w-20 shrink-0 text-right font-mono text-small tabular-nums text-carbon">
                {it.count.toLocaleString()}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
