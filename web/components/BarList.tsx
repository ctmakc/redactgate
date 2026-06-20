import type { CSSProperties } from "react";

/** Horizontal redaction-bar list — carbon bars that wipe open. Pure CSS, no chart dep. */
export function BarList({
  items,
  emptyLabel = "no data yet.",
}: {
  items: { label: string; value: number; mono?: boolean }[];
  emptyLabel?: string;
  accent?: string;
}) {
  if (items.length === 0) {
    return <p className="py-4 font-mono text-small text-graphite">{emptyLabel}</p>;
  }
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ul className="flex flex-col gap-3">
      {items.map((item, i) => {
        const w = Math.max(12, Math.round((item.value / max) * 220));
        return (
          <li key={item.label} className="flex items-center gap-3">
            <span className="w-24 shrink-0 truncate font-mono text-small text-carbon">
              {item.label}
            </span>
            <span className="redbar" style={{ width: `${w}px` }}>
              <span
                className="redbar-fill"
                style={{ animationDelay: `${i * 60}ms` } as CSSProperties}
              />
            </span>
            <span className="ml-auto font-mono text-small tabular-nums text-carbon">
              {item.value.toLocaleString("en-US")}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
