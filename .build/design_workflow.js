export const meta = {
  name: 'redactgate-design-directions',
  description: 'Explore 3 distinct visual identities for the RedactGate admin console, then judge-panel score them',
  phases: [
    { title: 'Explore', detail: '3 independent design directions grounded in the firewall/vault subject' },
    { title: 'Judge', detail: '3 judges score distinctiveness / subject-fit / implementability' },
  ],
}

const ROOT = '/data/projects/redactgate'

const SUBJECT = `THE PRODUCT — RedactGate is a self-hosted PII/financial-redaction FIREWALL that sits between staff and cloud LLMs. It detects sensitive entities (SIN, IBAN, credit card, email, names…), reversibly tokenizes them into placeholders like [[SIN_7f3a]] in an encrypted vault, forwards only the sanitized text to the LLM, then re-inflates the real values into the answer. Its promises: "raw values never leave the perimeter", "reversible", "every request audited (type counts only)". The admin console (Next.js) has 4 screens: Dashboard (redaction volume, entity-type breakdown, per-provider routing, latest benchmark, median latency), Audit (searchable append-only hash-chained log — counts only), Policies (tokenize/mask/hard-block rules), Benchmark (detection recall/precision + answer-fidelity scorecard). The buyer is a compliance officer at a regulated firm (tax/legal/corporate services). The feeling to earn: precise, trustworthy, instrument-grade, calm under audit — NOT a generic SaaS dashboard.`

const ANTI_DEFAULT = `AVOID the three AI-design clichés entirely: (1) warm cream #F4F1EA + high-contrast serif + terracotta; (2) near-black + a single acid-green/vermilion accent; (3) broadsheet/newspaper hairline columns with zero radius. Also avoid the current generic look (slate-50 background, indigo #6366f1 accent, rounded-xl white cards, uppercase tracked micro-labels) — that is exactly the templated default we are replacing. Ground every choice in the subject's own world (redaction, vaults, tokens, cryptography, audit trails, signal/telemetry, the physical security of a sealed appliance). Take ONE real, justifiable aesthetic risk and spend your boldness on a single signature element; keep everything else quiet and disciplined.`

const DIRECTION_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    name: { type: 'string', description: 'the design language name' },
    thesis: { type: 'string', description: 'one paragraph: the core idea and why it fits THIS product' },
    palette: { type: 'string', description: '4-6 NAMED hex values for light mode AND the dark-mode set; say what each role is (bg, surface, ink, accent, signal, danger…)' },
    typography: { type: 'string', description: 'real font choices (Google Fonts preferred) for display / body / mono roles, with the type scale, weights, and any distinctive treatment' },
    tokens: { type: 'string', description: 'radius, border/hairline treatment, shadow/elevation philosophy, spacing rhythm, and motion (page-load + micro-interactions) — concrete values' },
    components: { type: 'string', description: 'how the key components look & behave: nav/shell, stat tile, the entity-type breakdown viz, the audit table, policy cards, badges, and the placeholder/token chip (e.g. [[SIN_7f3a]])' },
    signature: { type: 'string', description: 'the ONE memorable element this console is remembered by, embodying redaction/vault/audit' },
    hero: { type: 'string', description: 'the dashboard "hero" thesis — the most characteristic first thing the operator sees (not necessarily a big-number card)' },
    wireframe: { type: 'string', description: 'ASCII wireframe of the Dashboard (and optionally the Audit table) showing layout/hierarchy' },
    risk: { type: 'string', description: 'the one aesthetic risk taken and the justification' },
    anti_default_note: { type: 'string', description: 'explicitly why this is NOT any of the clichés or the current generic look' },
  },
  required: ['name', 'thesis', 'palette', 'typography', 'tokens', 'components', 'signature', 'hero', 'wireframe', 'risk', 'anti_default_note'],
}

const JUDGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    scores: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          name: { type: 'string' },
          distinctiveness: { type: 'number' },
          subject_fit: { type: 'number' },
          polish_coherence: { type: 'number' },
          implementability: { type: 'number' },
          total: { type: 'number' },
          note: { type: 'string' },
        },
        required: ['name', 'distinctiveness', 'subject_fit', 'polish_coherence', 'implementability', 'total', 'note'],
      },
    },
    winner: { type: 'string' },
    grafts: { type: 'string', description: 'best ideas from the runners-up worth grafting onto the winner' },
    rationale: { type: 'string' },
  },
  required: ['scores', 'winner', 'grafts', 'rationale'],
}

const DIRECTOR_BASE = `You are a design lead at a studio known for visual identities that could not be mistaken for anyone else's. Produce a COMPLETE, opinionated visual direction for the RedactGate admin console — a design SPEC (not code). Be specific and buildable: real hex values, real font names, concrete radii/spacing/motion. Read the actual UI to ground in real content: read ${ROOT}/web/app/page.tsx, ${ROOT}/web/app/audit/page.tsx, ${ROOT}/web/app/policies/page.tsx, ${ROOT}/web/app/benchmark/page.tsx, ${ROOT}/web/lib/api.ts and ${ROOT}/web/app/globals.css (the current look you are replacing).

${SUBJECT}

${ANTI_DEFAULT}

Return the structured direction. Make deliberate, defensible choices specific to a privacy-firewall/vault, with one signature element and disciplined restraint everywhere else.`

const DIRECTORS = [
  {
    key: 'redaction-material',
    prompt: `${DIRECTOR_BASE}

YOUR STARTING LENS (push it as far as it credibly goes): "REDACTION AS THE MATERIAL." The product's defining act is turning a real value into a reversible token. Make the redaction gesture and the token (e.g. [[SIN_7f3a]]) into the identity itself — typographically and structurally — without it becoming a gimmick. Think about reversibility, the moment of substitution, the token as a first-class object, the boundary between "raw" and "safe". Derive the palette and type from this idea.`,
  },
  {
    key: 'telemetry-instrument',
    prompt: `${DIRECTOR_BASE}

YOUR STARTING LENS (push it as far as it credibly goes): "PRECISION INSTRUMENT / LIVE TELEMETRY." RedactGate is a monitored security appliance on the wire — like a well-made oscilloscope, network analyzer, or HSM control panel. Signal, status, throughput, thresholds, a sense of something being measured in real time. Tight engineered grid, exact data typography, status semantics that mean something. Make the dashboard feel like an instrument reading the live flow of redactions — calm, dense, legible.`,
  },
  {
    key: 'audit-trust',
    prompt: `${DIRECTOR_BASE}

YOUR STARTING LENS (push it as far as it credibly goes): "THE TRUST ARTIFACT / SEALED LEDGER." The real buyer is the compliance officer who must hand an auditor a defensible record. The hash-chained, append-only, zero-raw-value audit log is what they actually buy. Make the console feel like an authoritative, tamper-evident instrument of record — chain-of-custody, seals, verifiable lineage — WITHOUT becoming the newspaper/broadsheet cliché. Earn gravitas and warmth through material, type and a credible "verified" signature, not hairline-column density.`,
  },
]

function judgePrompt(brief, lens) {
  return `You are judging 3 candidate visual identities for the RedactGate admin console (a self-hosted PII-redaction firewall; buyer = compliance officer; feeling = precise, trustworthy, instrument-grade). ${ANTI_DEFAULT}

Score each direction 1–10 on: distinctiveness (could only be THIS product, not a template), subject_fit (earns trust for a security/privacy firewall, serves the 4 real screens), polish_coherence (a complete, consistent system that will look elegant when built), implementability (buildable well in Next.js + Tailwind in a focused pass). total = sum.

JUDGING LENS to weight most heavily: ${lens}

Pick a single winner and name the best ideas from the runners-up worth grafting onto it. Be decisive and specific.

CANDIDATES:
${brief}`
}

// ── Orchestration ──
phase('Explore')
log('Generating 3 independent design directions grounded in the firewall/vault subject…')
const dirs = (await parallel(
  DIRECTORS.map((d) => () => agent(d.prompt, { label: `direction:${d.key}`, phase: 'Explore', schema: DIRECTION_SCHEMA })),
)).filter(Boolean)
log(`Directions: ${dirs.map((d) => d.name).join(' · ')}`)

const brief = dirs
  .map((d, i) => `### Candidate ${i + 1}: ${d.name}\nThesis: ${d.thesis}\nPalette: ${d.palette}\nType: ${d.typography}\nTokens: ${d.tokens}\nComponents: ${d.components}\nSignature: ${d.signature}\nHero: ${d.hero}\nRisk: ${d.risk}\nWhy not default: ${d.anti_default_note}`)
  .join('\n\n')

phase('Judge')
const LENSES = [
  'DISTINCTIVENESS & anti-cliché — ruthlessly penalize anything that reads as a templated SaaS dashboard or one of the three AI clichés.',
  'SUBJECT-FIT & TRUST — which identity best earns a compliance officer\'s trust and best serves the redaction/audit/vault content of the 4 screens.',
  'IMPLEMENTABILITY & POLISH — which will actually look elegant and coherent when built in Next.js + Tailwind in one focused pass, with the fewest ways to go wrong.',
]
const judgements = (await parallel(
  LENSES.map((lens, i) => () => agent(judgePrompt(brief, lens), { label: `judge:${i + 1}`, phase: 'Judge', schema: JUDGE_SCHEMA })),
)).filter(Boolean)

// Tally winners.
const tally = {}
for (const j of judgements) tally[j.winner] = (tally[j.winner] || 0) + 1
log(`Judge winners: ${Object.entries(tally).map(([k, v]) => `${k}×${v}`).join('  ')}`)

return { directions: dirs, judgements, winner_tally: tally }
