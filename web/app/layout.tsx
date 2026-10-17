import { ClerkProvider } from "@clerk/nextjs";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { clerkAppearance } from "@/lib/clerk-appearance";

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

const clerkOn = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY,
);

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="font-sans">
        {clerkOn ? (
          <ClerkProvider
            appearance={clerkAppearance}
            signInUrl="/sign-in"
            signUpUrl="/sign-up"
            signInFallbackRedirectUrl="/playground"
            signUpFallbackRedirectUrl="/playground"
          >
            {children}
          </ClerkProvider>
        ) : (
          children
        )}
      </body>
    </html>
  );
}
