"use server";

import { revalidatePath } from "next/cache";

import { activatePolicy, createTeamPolicy, type PolicyMode } from "@/lib/api";
import { requireSession } from "@/lib/session";

export interface PolicyFormState {
  ok: boolean;
  error: string | null;
}

const MODES: PolicyMode[] = ["tokenize", "mask", "hard_block"];

function csv(v: FormDataEntryValue | null): string[] {
  if (typeof v !== "string") return [];
  return v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Create a policy for the caller's firm and make it the enforced one. Admins only. */
export async function submitPolicy(
  _prev: PolicyFormState,
  form: FormData,
): Promise<PolicyFormState> {
  const session = await requireSession();
  if (!session.can.managePolicies) {
    return { ok: false, error: "Only firm admins can change policies." };
  }
  const name = String(form.get("name") ?? "").trim();
  const modeRaw = String(form.get("mode") ?? "tokenize");
  const mode: PolicyMode = MODES.includes(modeRaw as PolicyMode)
    ? (modeRaw as PolicyMode)
    : "tokenize";
  if (!name) return { ok: false, error: "Policy name is required." };

  try {
    await createTeamPolicy(session.teamId, {
      name,
      mode,
      blocked_types: csv(form.get("blocked_types")).map((t) => t.toUpperCase()),
      allowed_providers: csv(form.get("allowed_providers")).map((p) => p.toLowerCase()),
      activate: true,
    });
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Failed to create policy." };
  }
  revalidatePath("/policies");
  return { ok: true, error: null };
}

/** Switch the enforced policy. Admins only; the backend checks the policy is this firm's. */
export async function activate(form: FormData): Promise<void> {
  const session = await requireSession();
  if (!session.can.managePolicies) return;
  const policyId = String(form.get("policy_id") ?? "");
  if (policyId) await activatePolicy(session.teamId, policyId);
  revalidatePath("/policies");
}
