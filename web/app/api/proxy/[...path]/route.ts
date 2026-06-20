import { NextRequest, NextResponse } from "next/server";

import { serverAdminToken, serverApiBase } from "@/lib/api";

/**
 * Server-side passthrough so client components can reach the RedactGate admin API
 * without the `X-Admin-Token` ever entering the browser bundle. Only `/admin/*`
 * paths are forwarded.
 */

export const dynamic = "force-dynamic";

const ALLOWED_PREFIX = "admin/";

async function forward(req: NextRequest, segments: string[]): Promise<NextResponse> {
  const path = segments.join("/");
  if (!path.startsWith(ALLOWED_PREFIX)) {
    return NextResponse.json({ error: "forbidden path" }, { status: 403 });
  }

  const base = serverApiBase().replace(/\/$/, "");
  const url = `${base}/${path}${req.nextUrl.search}`;

  const headers: Record<string, string> = {
    "Content-Type": req.headers.get("content-type") ?? "application/json",
  };
  const token = serverAdminToken();
  if (token) headers["X-Admin-Token"] = token;

  const init: RequestInit = { method: req.method, headers, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }

  try {
    const upstream = await fetch(url, init);
    const body = await upstream.text();
    return new NextResponse(body, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (err) {
    return NextResponse.json(
      { error: `upstream unreachable: ${String(err)}` },
      { status: 502 },
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}

export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
