CREATE SCHEMA IF NOT EXISTS intake;

CREATE TABLE IF NOT EXISTS intake.alert_inbox (
    alert_id      uuid PRIMARY KEY,
    dedup_key     text NOT NULL UNIQUE,              -- problem_id:status
    display_key   text,                              -- display_id:status (secondary dedup)
    source        text NOT NULL,
    problem_id    text NOT NULL,
    display_id    text,
    status        text NOT NULL CHECK (status IN ('ACTIVE','CLOSED')),
    event         jsonb NOT NULL,                    -- canonical ProblemEvent
    raw_payload   jsonb,                             -- original body, for audit (size-capped)
    received_at   timestamptz NOT NULL DEFAULT now(),
    state         text NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending','processing','awaiting_approval','done','failed','superseded')),
    attempts      int NOT NULL DEFAULT 0,
    locked_until  timestamptz,
    last_error    text
);
CREATE UNIQUE INDEX IF NOT EXISTS alert_inbox_display_key_uq
    ON intake.alert_inbox (display_key) WHERE display_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS alert_inbox_claim_idx ON intake.alert_inbox (state, received_at);
