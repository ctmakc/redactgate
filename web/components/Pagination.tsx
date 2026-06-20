import Link from "next/link";

export function Pagination({
  page,
  pageSize,
  total,
  baseQuery,
}: {
  page: number;
  pageSize: number;
  total: number;
  /** existing query params WITHOUT the page key, already serialized (no leading ?). */
  baseQuery: string;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const prev = Math.max(1, page - 1);
  const next = Math.min(totalPages, page + 1);
  const href = (p: number) =>
    `/audit?${baseQuery ? `${baseQuery}&` : ""}page=${p}`;

  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);

  return (
    <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
      <span>
        {from}–{to} of {total.toLocaleString("en-US")}
      </span>
      <div className="flex items-center gap-2">
        <Link
          href={href(prev)}
          aria-disabled={page <= 1}
          className={`btn-ghost ${page <= 1 ? "pointer-events-none opacity-40" : ""}`}
        >
          Prev
        </Link>
        <span className="tabular-nums">
          Page {page} / {totalPages}
        </span>
        <Link
          href={href(next)}
          aria-disabled={page >= totalPages}
          className={`btn-ghost ${page >= totalPages ? "pointer-events-none opacity-40" : ""}`}
        >
          Next
        </Link>
      </div>
    </div>
  );
}
