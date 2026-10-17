import "server-only";

import { cache } from "react";

/**
 * Who is looking at the console, which firm they act for, and what they may do.
 *
 * Identity comes from Clerk (verified session token). The firm is the user's ACTIVE
 * Clerk organization; it is mapped server-side to a RedactGate tenant through
 * `POST /admin/tenants/resolve`. The browser never sends a tenant id: every admin call
 * below is scoped by the team id derived here.
 *
 * Roles (Clerk organization roles):
 *   org:admin       runs prompts, edits policies, reads the audit trail
 *   org:compliance  reads the audit trail, verifies the chain, reviews policies (read-only)
 *   org:member      runs prompts in the playground, sees the dashboard
 */

export type Role = "admin" | "compliance" | "member";

export interface Capabilities {
  runPlayground: boolean;
  readAudit: boolean;
  managePolicies: boolean;
  readPolicies: boolean;
}

export interface ConsoleSession {
  userId: string;
  orgId: string;
  orgName: string;
  role: Role;
  roleLabel: string;
  teamId: string;
  can: Capabilities;
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: "admin",
  compliance: "compliance officer",
  member: "member",
};

export function capabilitiesFor(role: Role): Capabilities {
  return {
    runPlayground: role === "admin" || role === "member",
    readAudit: role === "admin" || role === "compliance",
    managePolicies: role === "admin",
    readPolicies: role === "admin" || role === "compliance",
  };
}

export function clerkConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY,
  );
}

function roleFromClerk(orgRole: string | null | undefined): Role {
  if (orgRole === "org:admin") return "admin";
  if (orgRole === "org:compliance") return "compliance";
  return "member";
}

const API_BASE = (
  process.env.REDACTGATE_API_BASE ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8080"
).replace(/\/$/, "");

async function resolveTenant(externalId: string, name: string): Promise<string> {
  const res = await fetch(`${API_BASE}/admin/tenants/resolve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": process.env.REDACTGATE_ADMIN_TOKEN ?? "",
    },
    body: JSON.stringify({ external_id: externalId, name }),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`tenant resolve failed (${res.status})`);
  const body = (await res.json()) as { team_id: string };
  return body.team_id;
}

/**
 * The signed-in session, or `null` when there is no user / no active organization.
 * Cached per request so layout + page share one Clerk read and one tenant resolve.
 */
export const getConsoleSession = cache(async (): Promise<ConsoleSession | null> => {
  if (!clerkConfigured()) return null;
  const { auth, clerkClient } = await import("@clerk/nextjs/server");
  const { userId, orgId, orgRole } = await auth();
  if (!userId || !orgId) return null;

  const client = await clerkClient();
  const org = await client.organizations.getOrganization({ organizationId: orgId });
  const role = roleFromClerk(orgRole);
  const teamId = await resolveTenant(orgId, org.name);
  return {
    userId,
    orgId,
    orgName: org.name,
    role,
    roleLabel: ROLE_LABEL[role],
    teamId,
    can: capabilitiesFor(role),
  };
});

/** Use in pages: returns the session or throws a redirect to sign-in / org selection. */
export async function requireSession(): Promise<ConsoleSession> {
  const { redirect } = await import("next/navigation");
  if (!clerkConfigured()) redirect("/setup");
  const { auth } = await import("@clerk/nextjs/server");
  const { userId, orgId } = await auth();
  if (!userId) redirect("/sign-in");
  if (!orgId) redirect("/firm");
  const s = await getConsoleSession();
  if (!s) redirect("/firm");
  return s as ConsoleSession;
}
