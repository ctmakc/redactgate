export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      {/* the seal — a filled square, the mark of 'sealed & reversible' */}
      <span aria-hidden className="h-3 w-3 shrink-0 bg-seal" />
      <div className="font-mono text-[15px] font-semibold leading-none text-carbon">
        <span className="text-seal">[[</span>redactgate
        <span className="text-seal">]]</span>
      </div>
      {/* live status dot — the only pill in the system */}
      <span
        aria-label="online"
        title="sealed · online"
        className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-seal"
      />
    </div>
  );
}
