import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { NavLink } from "@/components/NavLink";
import { Wordmark } from "@/components/Wordmark";

import "./globals.css";

export const metadata: Metadata = {
  title: "redactgate — console",
  description:
    "Self-hosted PII/financial redaction firewall. Detect, reversibly tokenize, and audit sensitive entities before any cloud-LLM call.",
};

const NAV = [
  { href: "/", label: "dashboard" },
  { href: "/audit", label: "audit" },
  { href: "/policies", label: "policies" },
  { href: "/benchmark", label: "benchmark" },
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="font-sans">
        <div className="flex min-h-screen">
          <aside className="hidden w-56 shrink-0 flex-col border-r border-rule bg-vellum px-5 py-6 md:flex">
            <Wordmark />
            <nav className="mt-9 flex flex-col gap-0.5">
              {NAV.map((n) => (
                <NavLink key={n.href} href={n.href}>
                  {n.label}
                </NavLink>
              ))}
            </nav>
            <div className="mt-auto border-t border-rule pt-4 font-mono text-micro leading-relaxed text-graphite">
              raw entity values are{" "}
              <span className="text-carbon">never stored</span>. the audit keeps
              type counts only.
            </div>
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex items-center justify-between border-b border-rule bg-vellum px-6 py-3 md:hidden">
              <Wordmark />
            </header>
            <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-6 py-8 md:px-9 md:py-10">
              <div className="flex-1">{children}</div>
              <footer className="perimeter mt-10 flex items-center font-mono text-micro text-graphite">
                raw values never cross this line
              </footer>
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
