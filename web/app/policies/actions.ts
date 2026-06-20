"use server";

import { revalidatePath } from "next/cache";

import { createPolicy, type PolicyMode } from "@/lib/api";

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

export async function submitPolicy(
  _prev: PolicyFormState,
  form: FormData,
): Promise<PolicyFormState> {
  const name = String(form.get("name") ?? "").trim();
  const modeRaw = String(form.get("mode") ?? "tokenize");
  const mode: PolicyMode = MODES.includes(modeRaw as PolicyMode)
    ? (modeRaw as PolicyMode)
    : "tokenize";

  if (!name) {
    return { ok: false, error: "Policy name is required." };
  }

  try {
    await createPolicy({
      name,
      mode,
      blocked_types: csv(form.get("blocked_types")).map((t) => t.toUpperCase()),
      allowed_providers: csv(form.get("allowed_providers")).map((p) =>
        p.toLowerCase(),
      ),
    });
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "Failed to create policy.",
    };
  }

  revalidatePath("/policies");
  return { ok: true, error: null };
}
