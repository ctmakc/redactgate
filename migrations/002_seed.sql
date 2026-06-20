-- Deterministic default tenant so the proxy works out-of-the-box in dev / air-gapped
-- single-tenant mode (require_api_key=false). Fixed UUIDs are referenced by app.auth.

INSERT INTO org (id, name) VALUES
    ('00000000-0000-0000-0000-0000000000aa', 'Default Org')
ON CONFLICT (id) DO NOTHING;

INSERT INTO policy (id, org_id, name, mode, blocked_types, allowed_providers) VALUES
    ('00000000-0000-0000-0000-0000000000cc',
     '00000000-0000-0000-0000-0000000000aa',
     'Default Tokenize Policy', 'tokenize', '{}', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO team (id, org_id, name, default_policy_id) VALUES
    ('00000000-0000-0000-0000-0000000000bb',
     '00000000-0000-0000-0000-0000000000aa',
     'Default Team', '00000000-0000-0000-0000-0000000000cc')
ON CONFLICT (id) DO NOTHING;
