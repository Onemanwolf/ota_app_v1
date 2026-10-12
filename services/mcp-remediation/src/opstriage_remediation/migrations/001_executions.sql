CREATE SCHEMA IF NOT EXISTS remediation;

CREATE TABLE IF NOT EXISTS remediation.executions (
    idempotency_key text PRIMARY KEY,
    target_id       text NOT NULL,
    problem_id      text NOT NULL,
    status          text NOT NULL,          -- RemediationStatus
    reason          text,
    executor        text,
    approved_by     text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    output_tail     text
);
CREATE INDEX IF NOT EXISTS executions_target_time_idx
    ON remediation.executions (target_id, started_at DESC);
-- At most one in-flight restart per target, enforced by the database (a second insert fails -> REJECTED busy).
CREATE UNIQUE INDEX IF NOT EXISTS executions_one_inflight_per_target
    ON remediation.executions (target_id) WHERE status = 'in_progress';

CREATE TABLE IF NOT EXISTS remediation.rejections (
    id              bigserial PRIMARY KEY,
    idempotency_key text NOT NULL,
    target_id       text NOT NULL,
    problem_id      text NOT NULL,
    reason          text NOT NULL,
    rejected_at     timestamptz NOT NULL DEFAULT now()
);
