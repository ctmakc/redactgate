"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

export function AuditControls() {
  const router = useRouter();
  const params = useSearchParams();
  const [provider, setProvider] = useState(params.get("provider") ?? "");
  const [blocked, setBlocked] = useState(params.get("blocked") ?? "");

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const sp = new URLSearchParams();
    if (provider) sp.set("provider", provider);
    if (blocked) sp.set("blocked", blocked);
    sp.set("page", "1");
    router.push(`/audit?${sp.toString()}`);
  }

  function reset() {
    setProvider("");
    setBlocked("");
    router.push("/audit");
  }

  return (
    <form
      onSubmit={apply}
      className="panel mb-4 flex flex-wrap items-end gap-3 p-4"
    >
      <label className="min-w-[140px]">
        <span className="eyebrow mb-1 block">
          Provider
        </span>
        <input
          className="input"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          placeholder="anthropic"
        />
      </label>
      <label className="min-w-[120px]">
        <span className="eyebrow mb-1 block">
          Blocked
        </span>
        <select
          className="input"
          value={blocked}
          onChange={(e) => setBlocked(e.target.value)}
        >
          <option value="">Any</option>
          <option value="true">Blocked</option>
          <option value="false">Allowed</option>
        </select>
      </label>
      <div className="flex gap-2">
        <button type="submit" className="btn-primary">
          Filter
        </button>
        <button type="button" className="btn-ghost" onClick={reset}>
          Reset
        </button>
      </div>
    </form>
  );
}
