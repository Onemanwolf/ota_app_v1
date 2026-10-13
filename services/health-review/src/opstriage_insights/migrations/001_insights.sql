CREATE SCHEMA IF NOT EXISTS insights;

-- One row per daily review or root-cause analysis (OTA-APP-002 section 10).
CREATE TABLE IF NOT EXISTS insights.reviews (
    id               uuid PRIMARY KEY,
    kind             text NOT NULL CHECK (kind IN ('review', 'rca')),
    cluster          text NOT NULL,
    subject          jsonb,                         -- RCA scope: display_id or entities + window
    status           text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    error            text,
    dropped_uncited  integer NOT NULL DEFAULT 0,
    guard_withheld   integer NOT NULL DEFAULT 0,
    started_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz,
    expires_at       timestamptz NOT NULL           -- report expiry; set to started_at + evidence max age until completion
);
CREATE INDEX IF NOT EXISTS reviews_cluster_started ON insights.reviews (cluster, started_at DESC);
CREATE INDEX IF NOT EXISTS reviews_expires ON insights.reviews (expires_at);

-- Redacted collected data. Deleted at min(collected_at + 28h, completed_at + 4h) (section 4).
CREATE TABLE IF NOT EXISTS insights.evidence (
    id            bigserial PRIMARY KEY,
    review_id     uuid NOT NULL REFERENCES insights.reviews (id) ON DELETE CASCADE,
    source        text NOT NULL CHECK (source IN ('logs', 'metrics', 'problems')),
    collected_at  timestamptz NOT NULL,
    expires_at    timestamptz NOT NULL,
    bundle        jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_expires ON insights.evidence (expires_at);

-- Digest, validated findings, recommendations, sparkline copies and Markdown. Deleted at completed_at + 48h.
CREATE TABLE IF NOT EXISTS insights.reports (
    review_id     uuid PRIMARY KEY REFERENCES insights.reviews (id) ON DELETE CASCADE,
    completed_at  timestamptz NOT NULL,
    expires_at    timestamptz NOT NULL,
    report        jsonb NOT NULL,
    markdown      text NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_expires ON insights.reports (expires_at);

-- Counts only, never content; kept 30 days.
CREATE TABLE IF NOT EXISTS insights.purge_log (
    id                bigserial PRIMARY KEY,
    ran_at            timestamptz NOT NULL DEFAULT now(),
    reviews_deleted   integer NOT NULL,
    evidence_deleted  integer NOT NULL,
    reports_deleted   integer NOT NULL
);
