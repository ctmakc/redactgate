import type { ReactNode } from "react";

import { SiteFooter } from "@/components/SiteFooter";
import { Wordmark } from "@/components/Wordmark";

/**
 * Sign-in / sign-up / firm pages. Left: a synthetic client file note whose sensitive
 * values get sealed into tokens on load (the product in one glance). Right: the Clerk
 * card. Asymmetric 5/7 split; stacks on phones with the specimen below the form.
 */

const LINES: (string | { raw: string; token: string })[][] = [
  ["File note, 14 Oct 2026. Client ", { raw: "Daniel Okafor", token: "[[PERSON_7b71b4]]" }, ","],
  ["SIN ", { raw: "046 454 286", token: "[[SIN_3c1999]]" }, ", OHIP ", { raw: "1234-567-890-AB", token: "[[HEALTH_CARD_780ee2]]" }, "."],
  ["Deposits to account ", { raw: "5021234", token: "[[BANK_ACCOUNT_94b33a]]" }, " at transit ", { raw: "00412", token: "[[BANK_TRANSIT_e406b8]]" }, "."],
  ["Asks whether to open an FHSA this year."],
];

function Specimen() {
  let n = 0;
  return (
    <div className="specimen panel px-6 py-6 sm:px-8 sm:py-8">
      <div className="eyebrow mb-5 flex items-center justify-between">
        <span>what leaves your office</span>
        <span className="stamp-seal">sealed</span>
      </div>
      <div className="space-y-2 font-mono text-[13.5px] leading-[26px] text-carbon">
        {LINES.map((line, i) => (
          <p key={i}>
            {line.map((part, j) => {
              if (typeof part === "string") return <span key={j}>{part}</span>;
              const delay = 500 + n++ * 260;
              return (
                <span key={j} className="seal-swap" style={{ ["--d" as string]: `${delay}ms` }}>
                  <span className="seal-raw">{part.raw}</span>
                  <span className="seal-token">
                    <span className="text-seal">[[</span>
                    {part.token.slice(2, -2)}
                    <span className="text-seal">]]</span>
                  </span>
                  <span className="seal-bar" aria-hidden />
                </span>
              );
            })}
          </p>
        ))}
      </div>
      <div className="mt-6 border-t border-rule pt-4 font-mono text-micro leading-relaxed text-graphite">
        the model reasons over tokens. your staff get the real values back. the audit keeps
        counts, never values.
      </div>
    </div>
  );
}

export function AuthShell({
  title,
  lede,
  children,
}: {
  title: string;
  lede: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-8 sm:px-6 md:px-9">
      <header className="flex items-center justify-between">
        <div className="w-[190px]">
          <Wordmark />
        </div>
        <a href="https://redactgate.pages.dev" className="font-mono text-micro text-graphite hover:text-carbon">
          about redactgate →
        </a>
      </header>
      <div className="grid flex-1 grid-cols-1 items-center gap-10 py-10 lg:grid-cols-12 lg:gap-14">
        <section className="order-2 lg:order-1 lg:col-span-5">
          <h1 className="auth-title max-w-md font-sans text-display font-semibold tracking-tight text-carbon">
            {title}
          </h1>
          <p className="mt-3 max-w-md text-body text-graphite">{lede}</p>
          <div className="mt-8">
            <Specimen />
          </div>
        </section>
        <section className="order-1 flex justify-center lg:order-2 lg:col-span-7 lg:justify-end">
          {children}
        </section>
      </div>
      <SiteFooter />
    </div>
  );
}
