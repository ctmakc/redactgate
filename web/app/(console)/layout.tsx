import type { ReactNode } from "react";

import { AccountButton, FirmSwitcher } from "@/components/AccountBar";
import { NavLink } from "@/components/NavLink";
import { SiteFooter } from "@/components/SiteFooter";
import { Wordmark } from "@/components/Wordmark";
import { requireSession, type ConsoleSession } from "@/lib/session";

export const dynamic = "force-dynamic";

function navFor(s: ConsoleSession) {
  const items = [{ href: "/", label: "dashboard" }];
  if (s.can.runPlayground) items.push({ href: "/playground", label: "playground" });
  if (s.can.readAudit) items.push({ href: "/audit", label: "audit" });
  if (s.can.readPolicies) items.push({ href: "/policies", label: "policies" });
  items.push({ href: "/benchmark", label: "benchmark" });
  return items;
}

export default async function ConsoleLayout({ children }: { children: ReactNode }) {
  const session = await requireSession();
  const nav = navFor(session);

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-rule bg-vellum px-5 py-6 md:flex">
        <Wordmark />
        <div className="mt-7">
          <div className="eyebrow mb-1.5">firm</div>
          <FirmSwitcher />
          <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-micro text-graphite">
            <span>your role</span>
            <span className={session.role === "compliance" ? "stamp-seal" : "stamp-outline"}>
              {session.roleLabel}
            </span>
          </div>
        </div>
        <nav className="mt-7 flex flex-col gap-0.5" aria-label="console">
          {nav.map((n) => (
            <NavLink key={n.href} href={n.href}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex items-end justify-between gap-3 border-t border-rule pt-4">
          <p className="font-mono text-micro leading-relaxed text-graphite">
            raw entity values are <span className="text-carbon">never stored</span>. the audit
            keeps type counts only.
          </p>
          <AccountButton />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-b border-rule bg-vellum px-4 py-3 md:hidden">
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <Wordmark />
            </div>
            <AccountButton />
          </div>
          <div className="mt-3">
            <FirmSwitcher />
          </div>
          <nav className="-mx-4 mt-2 flex overflow-x-auto px-2" aria-label="console">
            {nav.map((n) => (
              <NavLink key={n.href} href={n.href}>
                {n.label}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 py-8 sm:px-6 md:px-9 md:py-10">
          <div className="flex-1">{children}</div>
          <SiteFooter />
        </main>
      </div>
    </div>
  );
}
