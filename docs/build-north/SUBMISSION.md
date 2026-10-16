# RedactGate — Build North submission

**Tagline:** Use Claude on client files without sending Claude your clients.

- Live console: https://redactgate.mmix.biz (sign up, name a firm, open Playground)
- Code: https://github.com/ctmakc/redactgate (AGPL-3.0), Build Week branch `build-north`
- Landing: https://redactgate.pages.dev
- Team: Maksym Stepanenko (solo, Ottawa)

## Inspiration

Accounting firms, law offices and clinics in Canada already paste client notes into chat
assistants, because the answers are good and the alternative is an afternoon of drafting.
Those notes carry SINs, health card numbers, bank accounts and home addresses. Firms either
ban the tools (and staff use them anyway on their phones) or accept the leak. A partner at a
small firm needs a third option they can switch on in a day and defend to a privacy
regulator: the model gets the reasoning task, the firm keeps the identifiers.

## What it does

RedactGate is a self-hosted gateway that sits between staff and any cloud model. It speaks
the OpenAI API, so existing tools point at it with one URL change.

1. **Detect.** Jurisdiction regex packs (Canada: SIN, health cards, bank transit and account,
   postal codes, street addresses, CRA business numbers; plus US, EU, Ukraine) and Presidio
   NER for names and places.
2. **Seal.** Each value is replaced by a reversible placeholder such as `[[SIN_c49750]]`,
   encrypted with AES-256-GCM in a local vault. The same value gets the same placeholder
   within a request, so the model can still reason about "the client" consistently.
3. **Ask Claude.** Only placeholders cross the network. The default model is
   `claude-opus-5-5`.
4. **Restore.** Placeholders in Claude's answer are swapped back on the way in, including
   mid-stream.
5. **Audit.** Every request writes a hash-chained audit event with entity type counts, tokens
   and latency. No values, no prompt text.

The console (new this week) is where a firm uses and governs it:

- **Sign-in with Clerk.** A firm is a Clerk organization. Roles: **admin** (playground,
  policies, audit), **compliance officer** (reads the audit trail, re-verifies the hash
  chain, reads policies), **member** (playground).
- **Playground.** Paste a note (three synthetic samples included) and see each hop side by
  side: your note with detected values tagged, the exact payload Claude received, the answer
  with restored values marked, and the audit row that was written.
- **Policies.** Tokenize, mask or hard-block; refuse specific entity types outright; restrict
  which providers may receive traffic.
- **Audit.** The firm's ledger, with the hash chain recomputed on every page load.

## How we built it

- Gateway: Python 3.12, FastAPI, async SQLAlchemy on Postgres 16, Presidio + spaCy, httpx
  adapters for Anthropic, OpenAI, Gemini, Azure, Bedrock, DigitalOcean and Ollama.
- Console: Next.js 15 App Router with server components and server actions, Tailwind,
  Clerk (`@clerk/nextjs` 7): middleware protection, `<SignIn/>`, `<SignUp/>`,
  `<OrganizationSwitcher/>`, the organization-creation session task, and organization roles
  read in server code (`auth()` → `orgId`, `orgRole`).
- Tenancy: the console's server resolves the active Clerk organization to a RedactGate
  tenant (`POST /admin/tenants/resolve`). The browser never sends a tenant id; every admin
  call is made server-side with the team id derived from the verified session.
- Live deployment: gateway and console run as systemd services on our own machine in
  Ottawa, published through a Cloudflare tunnel. Only the console is public; the gateway API
  listens on localhost.
- Tests: 389 pass (unit lane plus Postgres integration), including Anthropic adapter cases,
  the new Canadian patterns with negative cases (money, years, prose), tenant isolation,
  the playground cap and fallback rules.

## Challenges we ran into

- **The first live run leaked.** Sending the synthetic tax note through Claude for the first
  time, the SIN, OHIP number, bank account, address and names went out in clear text. Our
  test SIN failed the checksum, OHIP had no pattern, and the bank details had no cue
  patterns. Claude's own reply opened by warning us about it. We added cue-anchored
  patterns (a 9-digit run right after "SIN" is sealed even when the checksum fails), health
  cards, bank transit and account numbers, postal codes, street addresses, a
  case-insensitive DOB cue and policy/claim numbers, each with tests for things that must
  not match.
- **Over-redaction hurts answers.** With Presidio enabled, "this year", "three weeks" and
  "14 Oct 2026" were being sealed, and a tax answer that cannot see the year is useless. We
  stopped tokenizing generic dates; dates of birth are still sealed via their cue.
- **The NER pass was itself a leak.** The optional LLM entity pass sent raw text to the
  configured cloud provider. It now runs on a local model by default and refuses a cloud
  provider unless explicitly allowed.
- **Credits expire before judging.** Our Anthropic credits last seven days from grant, so we
  added a fallback chain: if Claude fails, the request goes to a local model on our machine,
  and only when the firm's policy allows that provider. The playground says when this
  happened.

## Accomplishments that we're proud of

- On the three synthetic notes in the playground (27 sensitive values), the June default
  configuration sealed 6; the Build North configuration seals 26. The one miss is an
  employer name.
- A compliance officer can sign in, see only counts, and re-verify the audit chain
  without being able to run prompts or change policy.
- The console had no login before this week. It now has real multi-tenant access control,
  and the unauthenticated admin proxy route is gone.

## What we learned

- Synthetic test data needs to be realistic in the ways that break detectors: test SINs that
  fail checksums, health card version codes, account numbers that only make sense next to
  the word "account".
- Showing users what the model received is the fastest way to earn trust, and the fastest
  way to find your own bugs.

## What's next

- Employer and organization names (an ORG recognizer tuned on Canadian business names).
- French-language cues for Quebec firms (NAS, RAMQ already partly covered).
- Per-role policies (members on hard-block for health data, admins on tokenize).
- A packaged install for a single office server, then a pilot with a small accounting firm.

## Built with

python, fastapi, postgresql, sqlalchemy, presidio, spacy, httpx, next.js, react, typescript,
tailwindcss, clerk, anthropic-claude, cloudflare-tunnel, docker

## Partner platforms used

- **Clerk**: all console authentication — sign-up, sign-in with email, Google or GitHub,
  bot protection on sign-up, the organization-creation step, the firm switcher, and
  organization roles including a custom `org:compliance` role. The roles decide what each
  page and server action allows, and the organization id decides which tenant's data is
  read.
- **Anthropic (Claude API)**: the cloud model behind the gateway (`claude-opus-5-5`), using
  the Build North credits.

## Prior work and AI-tool disclosure

- **Existed before Build Week (June 2026):** the gateway core — detection engine and regex
  packs, reversible token vault, stream re-inflation, hash-chained audit, policy engine,
  provider adapters, the first version of the admin console (no login), the landing page.
- **Built during Build Week (Oct 16–25, 2026):** Clerk sign-in, firms and roles; removal of the
  unauthenticated admin proxy; tenant-scoped admin API; the playground and explain view;
  Anthropic adapter update for current Claude models; the Canadian detection fixes listed
  above; local-only LLM-NER; policy-safe provider fallback; dark mode; the live deployment.
  The commit history on `build-north` shows each change.
- **AI tools:** the code and this write-up were produced with Claude Code (Anthropic),
  directed and reviewed by the author. Claude (via the API) is also the model under test in
  the product.
- All names, numbers and addresses in the demo and video are invented.
