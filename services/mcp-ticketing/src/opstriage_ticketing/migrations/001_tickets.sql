CREATE SCHEMA IF NOT EXISTS ticketing;

CREATE SEQUENCE IF NOT EXISTS ticketing.ticket_seq;

CREATE TABLE IF NOT EXISTS ticketing.tickets (
    ticket_id       text PRIMARY KEY DEFAULT ('TCK-' || lpad(nextval('ticketing.ticket_seq')::text, 6, '0')),
    idempotency_key text NOT NULL UNIQUE,
    problem_id      text NOT NULL,
    title           text NOT NULL,
    description     text NOT NULL,
    priority        text NOT NULL CHECK (priority IN ('P1','P2','P3','P4')),
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
    resolution      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz
);
-- One open ticket per problem (spec 11).
CREATE UNIQUE INDEX IF NOT EXISTS tickets_one_open_per_problem
    ON ticketing.tickets (problem_id) WHERE status = 'open';
