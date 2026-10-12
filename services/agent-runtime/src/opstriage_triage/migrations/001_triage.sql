CREATE SCHEMA IF NOT EXISTS triage;

CREATE TABLE IF NOT EXISTS triage.triage_runs (
    run_id          text PRIMARY KEY,                -- equals intake alert_id and the LangGraph thread_id
    problem_id      text NOT NULL,
    display_id      text,
    event_status    text NOT NULL,
    decision        jsonb,
    decision_error  text,
    target          jsonb,
    plan            jsonb NOT NULL DEFAULT '[]',
    approval        jsonb,
    outcomes        jsonb NOT NULL DEFAULT '[]',
    finished_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS triage_runs_problem_idx ON triage.triage_runs (problem_id);

CREATE TABLE IF NOT EXISTS triage.approval_requests (
    approval_id   uuid PRIMARY KEY,
    run_id        text NOT NULL UNIQUE,
    problem_id    text NOT NULL,
    target_id     text NOT NULL,
    state         text NOT NULL DEFAULT 'requested'
                  CHECK (state IN ('requested','approved','rejected','expired','cancelled')),
    decided_by    text,
    reason        text,
    requested_at  timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz,
    resumed_at    timestamptz
);
CREATE INDEX IF NOT EXISTS approval_requests_pending_idx ON triage.approval_requests (state, requested_at);

-- Append-only: the service role gets no UPDATE or DELETE grants in shared environments.
CREATE TABLE IF NOT EXISTS triage.audit_log (
    id          bigserial PRIMARY KEY,
    run_id      text NOT NULL,
    kind        text NOT NULL,
    detail      jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_run_idx ON triage.audit_log (run_id, id);
