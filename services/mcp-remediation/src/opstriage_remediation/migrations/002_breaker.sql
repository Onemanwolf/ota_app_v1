-- Circuit-breaker resets (spec 10.3): an operator resets with `opstriage-remediation reset-breaker`.
-- Failures before the latest reset no longer count towards the breaker.
CREATE TABLE IF NOT EXISTS remediation.breaker_resets (
    id        bigserial PRIMARY KEY,
    reset_by  text NOT NULL,
    reason    text,
    reset_at  timestamptz NOT NULL DEFAULT now()
);
