-- Build North (Oct 2026): atomic playground quota per team.
-- The console playground reserves a run with one conditional UPDATE ... RETURNING on the
-- team row before calling the provider, so concurrent runs cannot all pass a stale count.
-- Fixed 24 h window that starts at the first run after the previous window expired.
ALTER TABLE team ADD COLUMN IF NOT EXISTS playground_window_start TIMESTAMPTZ;
ALTER TABLE team ADD COLUMN IF NOT EXISTS playground_used INTEGER NOT NULL DEFAULT 0;
