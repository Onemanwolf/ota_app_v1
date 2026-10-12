CREATE SCHEMA IF NOT EXISTS poller;

CREATE TABLE IF NOT EXISTS poller.cursors (
    source          text PRIMARY KEY,
    high_water_mark timestamptz NOT NULL,
    last_sweep_at   timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
