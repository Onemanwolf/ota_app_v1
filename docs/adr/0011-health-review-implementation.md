# ADR 0011: Health review (OTA-APP-002) as built: decisions and deviations

- Status: accepted (2026-10-13), Module 5 (steps 5.1–5.8) complete.
- Related: OTA-APP-002 spec, ADR 0010 (model-agnostic model access).

## Context

OTA-APP-002 specified a scheduled and on-demand Kafka health review, with root-cause analysis and a web view, under strict retention:
- reports are kept 48 h;
- collected evidence is kept until min(collection + 28 h, completion + 4 h).

Building and evaluating it against fixtures and the real models on the DGX Spark led to the decisions below. Some refine the spec; a few deviate from it.

## Decisions

1. **Shared packages instead of copies.**
   - The Grail Query API client moved to `packages/dynatrace` (`opstriage_dynatrace`); the poller uses it too.
   - LLM access goes through `packages/llm` (ADR 0010).
   - The service's context is `opstriage_insights`, with the same import-linter contracts as the other contexts.

2. **Health endpoints follow the other services:** `/healthz` and `/readyz`, not the spec's single `/health`. `/readyz` reports the source and the number of clusters.

3. **Injection guard.**
   - The guard is the DeBERTa model on the Spark, `POST /check_batch`.
   - **Fail closed:** if the guard is unavailable, every log template is withheld and only counts reach the model.
   - **Screen separately:** the template and each example are screened as separate texts. The concatenated text pushed a plain producer warning to INJECTION 0.87; the parts alone score SAFE 0.998.
   - **Score floor:** a flag counts only at a score of at least `INSIGHTS__GUARD__MIN_SCORE` (0.95). Real injections score about 1.0.

4. **Digest signal rules**, all chosen on fixtures so that healthy clusters list no trends:
   - a trend needs a 30-day drift of at least 25% **and** a slope t statistic of at least 4, because noisy daily peaks otherwise read as trends;
   - a "departure from baseline" needs 3 consecutive points;
   - partial days are excluded from daily aggregates.

5. **Root-cause analysis.**
   - The window runs from 2 h before the problem started until it ended, capped at 22 h.
   - Departures are measured against a **local 3 h baseline**, so a night-time surge is not hidden by the daily cycle.
   - Log templates count as "new" when first seen inside the window; logs are collected from 24 h before it.
   - The model ranks hypotheses (`rank`), and the RCA prompt requires 1–3 of them.
   - Without that requirement, Qwen filed two correct causes as `current_issue`, scoring 8 of 10. With it, the score was 10 of 10 in two runs.

6. **Retention.**
   - The purge runs every `INSIGHTS__RETENTION__PURGE_INTERVAL` (15 min).
   - It deletes expired evidence and reports.
   - Expired reviews become **tombstones** for `INSIGHTS__RETENTION__TOMBSTONE` (30 days). A tombstone keeps only the id, cluster, kind, status and times; subject and error are cleared. This lets expired links answer **410 Gone** instead of 404. The spec asked for 410 but did not say how to keep it after deletion.
   - The purge log holds counts only, for 30 days.
   - RCA retention counts from collection time, because the analysis window can lie in the past.

7. **Scheduling is opt-in per cluster.**
   - Daily reviews run only for `INSIGHTS__SCHEDULE__CLUSTERS`, at `DAILY_AT` in `TIMEZONE`, at most once a day, with no catch-up run at start-up.
   - Otherwise the 26 fixture clusters would load the shared Qwen every morning.

8. **Web view.**
   - Pages are server-rendered with no JavaScript, a strict CSP, `no-store`, and Jinja2 autoescape with no `|safe` (enforced by a test).
   - **Local sign-in** uses a service token and sets an HMAC-signed `HttpOnly`, `SameSite=Strict` cookie, `Secure` except on localhost, lasting 8 h. Every form carries a CSRF token.
   - **Behind an authenticating proxy**, the identity header is trusted only from `INSIGHTS__WEB__TRUSTED_PROXY_ADDRESSES`.
   - The **Markdown download stays off**, because a downloaded copy outlives the 48 h rule.

9. **Local-only developer endpoints:**
   - `POST /v1/dev/digest/{cluster}` shows the digest the analyst sees;
   - `POST /v1/dev/expire/{review_id}` expires one review and runs the purge, for retention tests.

   Both need a token and exist only when `APP_ENV=local`.

10. **Triage cards link to RCA.** When `TRIAGE__INSIGHTS_BASE_URL` is set, cards for open problems link to `/ui/rca?display_id=…`. The local stack sets it.

## Evaluation (fixtures; labels and planted causes are generated, so these are upper bounds)

| Measure | Result | Target |
|---|---|---|
| Planted issues found at the right risk (12 planted, 4 healthy clusters) | Qwen 0.94; rules only 0.25 | ≥ 0.80 |
| False alarms in healthy reports | 0 | ≤ 1 per report |
| Safety violations (restart suggestions, injected text reaching the model) | 0 | 0 |
| Root cause in the top-2 hypotheses (10 RCA clusters) | 10/10, all at rank 1, in two runs | ≥ 8/10 |
| Postman folder 10 (live stack) | 23/23 assertions | green |

## Consequences and open items

- **Unconfirmed Grail details.** These are INFERRED until checked on a real tenant (spec open question 1):
  - the metric keys for disk and GC;
  - dimension names;
  - the `process.technology` filter;
  - the JSON encoding of `timeseries` intervals.
- **Fixture-based numbers.** Real incidents and human-reviewed labels are needed before trusting them.
- **Guard threshold.** The 0.95 floor was chosen from one false positive. It should be checked on real log volume.
- **Shared environments** need a real `INSIGHTS__WEB__SESSION_SECRET` (the app refuses the dev value outside local), and should put sign-in behind the organisation's identity provider.
