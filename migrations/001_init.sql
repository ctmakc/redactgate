-- RedactGate canonical schema (also the grant/compliance artifact).
-- Idempotent: safe to run on every container start.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Tenancy & access ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS org (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    default_policy_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_key (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    key_hash        TEXT NOT NULL UNIQUE,
    label           TEXT,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Redaction policy ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jurisdiction_pack (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    entity_types    TEXT[] NOT NULL,
    version         TEXT NOT NULL DEFAULT '1.0.0',
    definition      JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS policy (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES org(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    pack_ids        UUID[] NOT NULL DEFAULT '{}',
    mode            TEXT NOT NULL DEFAULT 'tokenize'
                    CHECK (mode IN ('tokenize','mask','hard_block')),
    blocked_types   TEXT[] NOT NULL DEFAULT '{}',
    allowed_providers TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Reversible token vault (the moat) ──────────────────────────────
CREATE TABLE IF NOT EXISTS redaction_session (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES team(id),
    policy_id       UUID NOT NULL REFERENCES policy(id),
    document_hash   TEXT,
    wrapped_dek     BYTEA NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours'
);

CREATE TABLE IF NOT EXISTS token_map (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES redaction_session(id) ON DELETE CASCADE,
    placeholder     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    value_ciphertext BYTEA NOT NULL,
    value_fingerprint TEXT NOT NULL,
    occurrences     INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, value_fingerprint),
    UNIQUE (session_id, placeholder)
);
CREATE INDEX IF NOT EXISTS idx_tokenmap_session ON token_map(session_id);

-- ── Audit (append-only, hash-chained) ──────────────────────────────
CREATE TABLE IF NOT EXISTS audit_event (
    id              BIGSERIAL PRIMARY KEY,
    team_id         UUID NOT NULL REFERENCES team(id),
    api_key_id      UUID REFERENCES api_key(id),
    session_id      UUID REFERENCES redaction_session(id),
    route           TEXT NOT NULL,
    provider        TEXT NOT NULL,
    entity_counts   JSONB NOT NULL,
    blocked         BOOLEAN NOT NULL DEFAULT false,
    prompt_tokens   INT,
    completion_tokens INT,
    latency_ms      INT,
    prev_hash       TEXT NOT NULL,
    event_hash      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_team_time ON audit_event(team_id, created_at DESC);

-- ── Fidelity benchmark ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS eval_run (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pack_id         UUID REFERENCES jurisdiction_pack(id),
    provider        TEXT NOT NULL,
    golden_set      TEXT NOT NULL,
    recall          NUMERIC(5,4),
    precision       NUMERIC(5,4),
    answer_fidelity NUMERIC(5,4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only guard: forbid UPDATE/DELETE on the audit log at the DB level.
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_event;
CREATE TRIGGER trg_audit_immutable
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
