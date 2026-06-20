-- Harden the append-only audit log. The row-level UPDATE/DELETE trigger (001) does NOT
-- fire on TRUNCATE, so the whole tamper-evident log could be wiped in one statement.
-- Add a statement-level TRUNCATE guard and revoke the destructive grants from the app role.

CREATE OR REPLACE FUNCTION audit_event_no_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only (TRUNCATE blocked)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_no_truncate ON audit_event;
CREATE TRIGGER trg_audit_no_truncate
    BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION audit_event_no_truncate();

-- Defense in depth: the application role should never need to mutate or drop audit rows.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM PUBLIC;
DO $$
BEGIN
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM %I', current_user);
EXCEPTION WHEN OTHERS THEN
    NULL;  -- best effort; the triggers are the hard guarantee
END $$;
