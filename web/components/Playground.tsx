"use client";

import { useActionState, useMemo, useState } from "react";
import { useFormStatus } from "react-dom";

import { runPrompt, type RunState } from "@/app/(console)/playground/actions";
import { EntityCounts } from "@/components/EntityCounts";
import type { ExplainSpan, PlaygroundResult } from "@/lib/api";
import type { Sample } from "@/lib/samples";

const INITIAL: RunState = { status: "idle", prompt: "", result: null, audit: null, error: null };
const PH = /\[\[[A-Z][A-Z0-9_]*_[0-9a-f]{4,12}\]\]/g;

// ── text helpers ────────────────────────────────────────────────────────────────

type Seg = { kind: "text"; text: string } | { kind: "entity"; text: string; type: string };

function segmentInput(text: string, spans: ExplainSpan[]): Seg[] {
  const out: Seg[] = [];
  let at = 0;
  for (const sp of [...spans].sort((a, b) => a.start - b.start)) {
    if (sp.start < at) continue;
    if (sp.start > at) out.push({ kind: "text", text: text.slice(at, sp.start) });
    out.push({ kind: "entity", text: text.slice(sp.start, sp.end), type: sp.type });
    at = sp.end;
  }
  if (at < text.length) out.push({ kind: "text", text: text.slice(at) });
  return out;
}

/** placeholder -> original value, by walking the sanitized text in step with the spans. */
function placeholderMap(result: PlaygroundResult): Map<string, string> {
  const map = new Map<string, string>();
  const input = result.explain.inputs.at(-1);
  const sent = result.explain.upstream_messages.filter((m) => m.role === "user").at(-1)?.content ?? "";
  if (!input) return map;
  const spans = [...input.spans].sort((a, b) => a.start - b.start);
  const phs = sent.match(PH) ?? [];
  spans.forEach((sp, i) => {
    const ph = phs[i];
    if (ph && !map.has(ph)) map.set(ph, input.text.slice(sp.start, sp.end));
  });
  return map;
}

function splitPlaceholders(text: string): { ph: string | null; text: string }[] {
  const parts: { ph: string | null; text: string }[] = [];
  let last = 0;
  for (const m of text.matchAll(PH)) {
    if (m.index! > last) parts.push({ ph: null, text: text.slice(last, m.index) });
    parts.push({ ph: m[0], text: m[0] });
    last = m.index! + m[0].length;
  }
  if (last < text.length) parts.push({ ph: null, text: text.slice(last) });
  return parts;
}

/** Minimal markdown: **bold**, "- " bullets and blank-line paragraphs. */
function Inline({ text, render }: { text: string; render: (t: string, k: string) => React.ReactNode }) {
  const bits = text.split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g);
  return (
    <>
      {bits.map((b, i) =>
        b.startsWith("**") && b.endsWith("**") ? (
          <strong key={i} className="font-semibold text-carbon">
            {render(b.slice(2, -2), `b${i}`)}
          </strong>
        ) : b.length > 2 && b.startsWith("*") && b.endsWith("*") ? (
          <em key={i}>{render(b.slice(1, -1), `e${i}`)}</em>
        ) : (
          <span key={i}>{render(b, `t${i}`)}</span>
        ),
      )}
    </>
  );
}

function Prose({ text, render }: { text: string; render: (t: string, k: string) => React.ReactNode }) {
  const lines = text.replace(/\r/g, "").split("\n");
  return (
    <div className="space-y-2">
      {lines.map((line, i) => {
        if (!line.trim()) return null;
        if (/^-{3,}$/.test(line.trim())) return <hr key={i} className="border-rule" />;
        const bullet = /^\s*(?:[-*]|\d+\.)\s+/.exec(line);
        const body = bullet ? line.slice(bullet[0].length) : line.replace(/^#+\s*/, "");
        return (
          <p key={i} className={bullet ? "relative pl-4" : undefined}>
            {bullet ? (
              <span aria-hidden className="absolute left-0 top-[11px] h-px w-2 bg-graphite" />
            ) : null}
            <Inline text={body} render={render} />
          </p>
        );
      })}
    </div>
  );
}

function TokenChip({ ph, i }: { ph: string; i: number }) {
  return (
    <span className="token tok-seal whitespace-nowrap" style={{ ["--i" as string]: i }}>
      <span className="br">[[</span>
      {ph.slice(2, -2)}
      <span className="br">]]</span>
    </span>
  );
}

// ── panes ─────────────────────────────────────────────────────────────────────

function Pane({
  step,
  title,
  note,
  tone,
  children,
}: {
  step: string;
  title: string;
  note: string;
  tone?: "seal";
  children: React.ReactNode;
}) {
  return (
    <section className="panel flex min-w-0 flex-col">
      <header className="flex items-baseline justify-between gap-3 border-b border-rule px-4 py-3">
        <h2 className="font-mono text-small font-medium text-carbon">
          <span className={tone === "seal" ? "text-seal" : "text-graphite"}>{step}</span> {title}
        </h2>
        <span className="font-mono text-micro text-graphite">{note}</span>
      </header>
      <div className="min-w-0 flex-1 px-4 py-4 text-small leading-[24px] text-carbon">{children}</div>
    </section>
  );
}

function Results({ state }: { state: RunState }) {
  const r = state.result!;
  const ex = r.explain;
  const input = ex.inputs.at(-1);
  const sent = ex.upstream_messages.filter((m) => m.role === "user").at(-1)?.content ?? "";
  const phMap = useMemo(() => placeholderMap(r), [r]);
  const upstreamAnswer = ex.upstream_answer ?? r.answer;
  let tokenIndex = 0;
  let restoredIndex = 0;

  const renderSent = (t: string, k: string) =>
    splitPlaceholders(t).map((p, j) =>
      p.ph ? <TokenChip key={`${k}-${j}`} ph={p.ph} i={tokenIndex++} /> : <span key={`${k}-${j}`}>{p.text}</span>,
    );
  const renderRestored = (t: string, k: string) =>
    splitPlaceholders(t).map((p, j) => {
      if (!p.ph) return <span key={`${k}-${j}`}>{p.text}</span>;
      const value = phMap.get(p.ph);
      return value ? (
        <span
          key={`${k}-${j}`}
          className="restored"
          title={`restored from ${p.ph}`}
          style={{ ["--i" as string]: restoredIndex++ }}
        >
          {value}
        </span>
      ) : (
        <TokenChip key={`${k}-${j}`} ph={p.ph} i={0} />
      );
    });

  const total = Object.values(ex.entity_counts).reduce((a, b) => a + b, 0);
  const fallback = ex.fallback_from?.length ? ex.fallback_from : null;

  return (
    <div className="mt-8">
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-micro text-graphite">
        <span>
          <span className="tabular-nums text-carbon">{total}</span> values sealed
        </span>
        <span className="text-rule">·</span>
        <span>
          model <span className="text-carbon">{ex.model ?? ex.provider}</span> via{" "}
          <span className="text-carbon">{ex.provider}</span>
        </span>
        <span className="text-rule">·</span>
        <span className="tabular-nums">{(ex.latency_ms / 1000).toFixed(1)} s</span>
        {r.remaining !== null ? (
          <>
            <span className="text-rule">·</span>
            <span className="tabular-nums">{r.remaining} runs left today</span>
          </>
        ) : null}
      </div>

      {fallback ? (
        <div className="mb-4 border border-vermillion/40 bg-vermillion/5 px-3 py-2 font-mono text-micro text-carbon">
          {fallback.map((f) => f.provider).join(", ")} failed ({fallback[0].status}); the policy allowed{" "}
          {ex.provider}, which answered instead.
        </div>
      ) : null}

      <div className="relative grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22px_minmax(0,1fr)_minmax(0,1.2fr)]">
        <Pane step="1" title="your note" note="stays in the firm">
          <p className="whitespace-pre-wrap break-words">
            {input
              ? segmentInput(input.text, input.spans).map((s, i) =>
                  s.kind === "text" ? (
                    <span key={i}>{s.text}</span>
                  ) : (
                    <mark key={i} className="ent">
                      {s.text}
                      <span className="ent-tag">{s.type.toLowerCase().replace(/_/g, " ")}</span>
                    </mark>
                  ),
                )
              : state.prompt}
          </p>
        </Pane>

        <div className="perimeter-v" aria-hidden>
          <span>raw values stop here</span>
        </div>

        <Pane step="2" title="what Claude received" note="tokens only" tone="seal">
          <p className="whitespace-pre-wrap break-words font-mono text-[12.5px] leading-[24px]">{renderSent(sent, "s")}</p>
          <details className="mt-4 border-t border-rule pt-3">
            <summary className="cursor-pointer font-mono text-micro text-graphite hover:text-carbon">
              Claude&apos;s raw answer, before re-inflation
            </summary>
            <div className="mt-3 font-mono text-[12.5px] leading-[22px] text-graphite">
              <Prose text={upstreamAnswer} render={renderSent} />
            </div>
          </details>
        </Pane>

        <Pane step="3" title="answer" note="values restored on return">
          <Prose text={upstreamAnswer} render={renderRestored} />
        </Pane>
      </div>

      {state.audit ? (
        <div className="panel mt-4 overflow-x-auto">
          <table className="ledger">
            <thead>
              <tr>
                <th>audit event</th>
                <th>route</th>
                <th>provider</th>
                <th>what was recorded</th>
                <th>tokens in / out</th>
                <th>chain link</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="whitespace-nowrap font-mono text-micro">
                  <span className="text-carbon">#{state.audit.id}</span> ·{" "}
                  {new Date(state.audit.created_at).toLocaleTimeString()}
                </td>
                <td>
                  <code className="token">{state.audit.route}</code>
                </td>
                <td className="font-mono text-micro">{state.audit.provider}</td>
                <td>
                  <EntityCounts counts={state.audit.entity_counts} />
                </td>
                <td className="whitespace-nowrap font-mono text-micro tabular-nums">
                  {state.audit.prompt_tokens ?? "—"} / {state.audit.completion_tokens ?? "—"}
                </td>
                <td className="font-mono text-micro text-graphite">
                  {state.audit.event_hash ? `${state.audit.event_hash.slice(0, 10)}…` : "—"}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="border-t border-rule px-3 py-2 font-mono text-micro text-graphite">
            counts and types only. the note, the payload and the answer are not stored.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function RunButton() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className="btn-primary run-btn min-w-[180px]" disabled={pending} data-pending={pending || undefined}>
      {pending ? (
        <span className="flex items-center gap-2">
          <span aria-hidden className="run-bar" />
          sealing · asking Claude
        </span>
      ) : (
        "Seal and send to Claude"
      )}
    </button>
  );
}

export function Playground({
  samples,
  policyLabel,
}: {
  samples: Sample[];
  policyLabel: string;
}) {
  const [state, action] = useActionState(runPrompt, INITIAL);
  const [text, setText] = useState(samples[0]?.text ?? "");
  const [picked, setPicked] = useState(samples[0]?.id ?? "");

  return (
    <div>
      <form action={action} className="panel">
        <div className="flex flex-wrap items-center gap-2 border-b border-rule px-4 py-3">
          <span className="eyebrow mr-1">synthetic sample</span>
          {samples.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setText(s.text);
                setPicked(s.id);
              }}
              className={`sample-chip ${picked === s.id ? "sample-chip-on" : ""}`}
              aria-pressed={picked === s.id}
            >
              {s.label} <span className="text-graphite">· {s.who}</span>
            </button>
          ))}
        </div>
        <label className="block px-4 pt-4">
          <span className="sr-only">Note to send</span>
          <textarea
            name="prompt"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setPicked("");
            }}
            rows={6}
            maxLength={8000}
            className="input min-h-[150px] resize-y font-mono text-[13px] leading-[22px]"
            spellCheck={false}
          />
        </label>
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <p className="font-mono text-micro text-graphite">
            enforcing <span className="text-carbon">{policyLabel}</span>. use invented data only.
          </p>
          <RunButton />
        </div>
      </form>

      {state.status === "error" ? (
        <div className="mt-4 border border-vermillion/50 bg-vermillion/5 px-4 py-3 font-mono text-small text-carbon" role="alert">
          <span className="stamp-block mr-2">refused</span>
          {state.error}
        </div>
      ) : null}

      {state.status === "ok" && state.result ? <Results key={state.result.explain.session_id} state={state} /> : null}
    </div>
  );
}
