CREATE SCHEMA IF NOT EXISTS notification;

CREATE TABLE IF NOT EXISTS notification.deliveries (
    idempotency_key text PRIMARY KEY,
    kind            text NOT NULL,
    severity        text NOT NULL,
    title           text NOT NULL,
    outcomes        jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
