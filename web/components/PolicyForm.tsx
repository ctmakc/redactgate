"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";

import {
  submitPolicy,
  type PolicyFormState,
} from "@/app/policies/actions";

const INITIAL: PolicyFormState = { ok: false, error: null };

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className="btn-primary" disabled={pending}>
      {pending ? "Creating…" : "Create policy"}
    </button>
  );
}

export function PolicyForm() {
  const [state, action] = useActionState(submitPolicy, INITIAL);

  return (
    <form action={action} className="card flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-slate-900">New policy</h2>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-slate-500">
          Name
        </span>
        <input
          name="name"
          required
          className="input"
          placeholder="EU PII — tokenize"
        />
      </label>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-slate-500">
          Mode
        </span>
        <select name="mode" className="input" defaultValue="tokenize">
          <option value="tokenize">tokenize — reversible placeholders</option>
          <option value="mask">mask — irreversible masking</option>
          <option value="hard_block">hard_block — reject the request</option>
        </select>
      </label>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-slate-500">
          Blocked entity types{" "}
          <span className="text-slate-400">(comma-separated)</span>
        </span>
        <input
          name="blocked_types"
          className="input font-mono"
          placeholder="SIN, CREDIT_CARD, IBAN"
        />
      </label>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-slate-500">
          Allowed providers{" "}
          <span className="text-slate-400">(comma-separated, empty = all)</span>
        </span>
        <input
          name="allowed_providers"
          className="input font-mono"
          placeholder="anthropic, ollama"
        />
      </label>

      <div className="flex items-center gap-3">
        <SubmitButton />
        {state.ok ? (
          <span className="text-sm text-emerald-600">Policy created.</span>
        ) : null}
        {state.error ? (
          <span className="text-sm text-rose-600">{state.error}</span>
        ) : null}
      </div>
    </form>
  );
}
