"use server";

import { getAudit, runPlayground, type AuditEvent, type PlaygroundResult } from "@/lib/api";
import { requireSession } from "@/lib/session";

export interface RunState {
  status: "idle" | "ok" | "error";
  prompt: string;
  result: PlaygroundResult | null;
  audit: AuditEvent | null;
  error: string | null;
}

/** Run one prompt through the firm's gateway. Admins and members only (server-checked). */
export async function runPrompt(_prev: RunState, form: FormData): Promise<RunState> {
  const session = await requireSession();
  const prompt = String(form.get("prompt") ?? "").slice(0, 8000);
  if (!session.can.runPlayground) {
    return { status: "error", prompt, result: null, audit: null, error: "Your role can read the audit trail but cannot send prompts." };
  }
  if (!prompt.trim()) {
    return { status: "error", prompt, result: null, audit: null, error: "Paste a note first." };
  }
  try {
    const result = await runPlayground(session.teamId, prompt);
    let audit: AuditEvent | null = null;
    try {
      audit = (await getAudit({ teamId: session.teamId, pageSize: 1 })).items[0] ?? null;
    } catch {
      audit = null;
    }
    return { status: "ok", prompt, result, audit, error: null };
  } catch (err) {
    return {
      status: "error",
      prompt,
      result: null,
      audit: null,
      error: err instanceof Error ? err.message : "The gateway did not answer.",
    };
  }
}
