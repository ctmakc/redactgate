"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

export function AuditControls() {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [provider, setProvider] = useState(params.get("provider") ?? "");
  const [blocked, setBlocked] = useState(params.get("blocked") ?? "");

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const sp = new URLSearchParams();
    if (q) sp.set("q", q);
    if (provider) sp.set("provider", provider);
    if (blocked) sp.set("blocked", blocked);
    sp.set("page", "1");
    router.push(`/audit?${sp.toString()}`);
  }

  function reset() {
    setQ("");
    setProvider("");
    setBlocked("");
    router.push("/audit");
  }

  return (
    <form
      onSubmit={apply}
      className="mb-4 flex flex-wrap items-end gap-3 rounded-xl border border-rule bg-leaf p-4 shadow-card"
    >
      <label className="flex-1 min-w-[180px]">
        <span className="mb-1 block text-xs font-medium text-graphite">
          Search route / provider
        </span>
        <input
          className="input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="/v1/chat/completions"
        />
      </label>
      <label className="min-w-[140px]">
        <span className="mb-1 block text-xs font-medium text-graphite">
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
        <span className="mb-1 block text-xs font-medium text-graphite">
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
