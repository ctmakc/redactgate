-- Build North (Oct 2026): map identity-provider organizations (Clerk) to tenants.
-- One Clerk organization = one firm = one RedactGate org (+ its first team).
ALTER TABLE org ADD COLUMN IF NOT EXISTS external_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_external_id ON org(external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_team_route_time ON audit_event(team_id, route, created_at DESC);
