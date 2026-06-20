import type { Metadata } from "next";
import type { ReactNode } from "react";

import { NavLink } from "@/components/NavLink";
import { Wordmark } from "@/components/Wordmark";
import {
  IconAudit,
  IconBenchmark,
  IconDashboard,
  IconPolicies,
} from "@/components/icons";

import "./globals.css";

export const metadata: Metadata = {
  title: "RedactGate — Admin Console",
  description:
    "Self-hosted PII/financial redaction firewall. Detect, reversibly tokenize, and audit sensitive entities before any cloud-LLM call.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen">
          <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white px-4 py-5 md:flex">
            <div className="px-2">
              <Wordmark />
            </div>
            <nav className="mt-8 flex flex-col gap-1">
              <NavLink href="/" icon={<IconDashboard />}>
                Dashboard
              </NavLink>
              <NavLink href="/audit" icon={<IconAudit />}>
                Audit
              </NavLink>
              <NavLink href="/policies" icon={<IconPolicies />}>
                Policies
              </NavLink>
              <NavLink href="/benchmark" icon={<IconBenchmark />}>
                Benchmark
              </NavLink>
            </nav>
            <div className="mt-auto rounded-lg border border-slate-200 bg-slate-50 p-3 text-[0.7rem] leading-relaxed text-slate-500">
              Raw entity values are{" "}
              <span className="font-semibold text-slate-700">never stored</span>.
              Audit &amp; metrics keep type counts only.
            </div>
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3 md:hidden">
              <Wordmark />
            </header>
            <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
