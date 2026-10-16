import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Every console route requires a signed-in Clerk user, except the auth pages and the
 * setup notice. Role checks (admin / compliance officer / member) happen server-side in
 * each page and server action via `lib/session.ts`.
 *
 * Without Clerk keys the console still builds and serves `/setup`, which explains how to
 * configure auth; nothing else is reachable.
 */
const isPublic = createRouteMatcher(["/sign-in(.*)", "/sign-up(.*)", "/setup"]);

const configured = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY,
);

const withClerk = clerkMiddleware(async (auth, req) => {
  if (!isPublic(req)) await auth.protect();
});

export default function middleware(req: NextRequest, ev: Parameters<typeof withClerk>[1]) {
  if (!configured) {
    const { pathname } = req.nextUrl;
    if (pathname === "/setup" || pathname.startsWith("/_next") || /\.[a-z0-9]+$/i.test(pathname)) {
      return NextResponse.next();
    }
    return NextResponse.redirect(new URL("/setup", req.url));
  }
  return withClerk(req, ev);
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
