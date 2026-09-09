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
  icons: {
    icon: "/favicon.png",
    apple: "/apple-touch-icon.png",
  },
  openGraph: {
    title: "redactgate — console",
    description:
      "Self-hosted PII/financial redaction firewall. Detect, reversibly tokenize, and audit sensitive entities before any cloud-LLM call.",
    images: ["/images/og-image.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: "redactgate — console",
    images: ["/images/og-image.png"],
  },
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
                {/* Agency credit — house standard (uafest.ca): wording + MMIX logo on a
                    light chip, grayscale until hover, so it sits on light and dark footers. */}
                <div style={{ width: "100%", display: "flex", justifyContent: "center", padding: "22px 24px 0" }}>
                  <a
                    href="https://mmix.ua/"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ display: "inline-flex", alignItems: "center", gap: 10, fontSize: 12.5, color: "inherit", opacity: 0.7, textDecoration: "none" }}
                  >
                    Development & promotion —
                    <span style={{ display: "inline-flex", alignItems: "center", background: "rgba(255,255,255,0.9)", borderRadius: 6, padding: "4px 8px" }}>
                      <img src="/mmix-logo.png" alt="MMIX — Marketing Mix" width={62} height={60} style={{ height: 36, width: "auto" }} />
                    </span>
                  </a>
                </div>
              </footer>
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
