export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <span
        aria-hidden
        className="inline-flex h-8 w-8 items-center justify-center rounded-lg
                   bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-card"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          className="h-5 w-5"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z" />
          <path d="M9.5 12.5 11 14l3.5-3.5" />
        </svg>
      </span>
      <div className="leading-tight">
        <div className="font-semibold tracking-tight text-slate-900">
          Redact<span className="text-brand-600">Gate</span>
        </div>
        <div className="text-[0.7rem] font-medium uppercase tracking-wider text-slate-400">
          Admin Console
        </div>
      </div>
    </div>
  );
}
