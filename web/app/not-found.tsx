import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-start gap-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
        404 — page not found
      </h1>
      <p className="text-slate-500">
        That route is not part of the RedactGate admin console.
      </p>
      <Link href="/" className="btn-primary">
        Back to dashboard
      </Link>
    </div>
  );
}
