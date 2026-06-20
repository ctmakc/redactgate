import type { ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-sans text-h1 font-semibold tracking-tight text-carbon">{title}</h1>
        {subtitle ? (
          <p className="mt-1.5 max-w-2xl font-mono text-micro leading-relaxed text-graphite">
            {subtitle}
          </p>
        ) : null}
      </div>
      {action ? <div>{action}</div> : null}
    </div>
  );
}
