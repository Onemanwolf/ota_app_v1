# ADR 0006: Kev as the local decision model: confidence semantics, thresholds, connectivity

- Status: accepted for local use (2026-10-12, Module 3). Thresholds are PROVISIONAL until a person reviews the
  synthetic eval labels (open question 7).
- Evidence: `evals/reports/kev-4b-2026-10-12.md` (and `.json`), recorded responses in `fixtures/decision/kev/`.

## 1. What Kev's `confidence` means
Kev's server (`kev/api.py`, read on the Spark) computes, for a choice question with K options:

    confidence = (max(p) - 1/K) / (1 - 1/K)        # chance-normalised top probability

It is 0 at chance and 1 at certainty, monotone in the top probability, but it depends on K: with the six
actions, confidence 0.90 needs a top probability of about 0.92. For score questions it is
`1 - E|level - mode| / (L - 1)`. The adapter keeps the spec's mapping (`action_confidence = answers.action.confidence`,
falling back to the chosen option's probability for providers that do not send it). The contract tests pin the
formula against every recorded response. The eval reports both measures.

## 2. Two adapter fixes found against the real Kev
- `latency_ms` arrives as a float (for example 219.7); `TriageDecision.latency_ms` is an int, so every response
  would have failed validation and silently fallen back to rules. The parser rounds it.
- Connect timeout is 3 s (read timeout stays `TRIAGE__DECISION__TIMEOUT_SECONDS`), so an unreachable model falls
  back to rules in seconds, not after three 10 s connect timeouts.

## 3. Eval findings (Kev-4B, `kev-latest`, temperature 2.41, synthetic set of 120 cases)
| Finding | Value |
|---|---|
| Accuracy / macro F1 | 0.625 / 0.536 |
| Confident errors (confidence >= 0.9) | 0. Kev is never confident on this set: its maximum confidence is 0.42 |
| Restart decisions | 27 predicted, 20 correct (precision 0.74, recall 0.83); the 7 wrong ones were labelled escalate (crash loops with a config cause, database-side faults) |
| `auto_remediable` | NOT discriminative here: higher on the 7 wrong restarts (0.72-0.76) than on most right ones (0.44-0.75) |
| Option-order sensitivity | 33-38% of answers change when the action options are shuffled |
| Weak classes | open_ticket recall 0.23 and suppress recall 0.13 (both mostly answered notify_only) |

Threshold sweep for `restart_container` (precision target 0.98, `evals/acceptance.yaml`): only confidence >= 0.25
reaches it, on 2 cases (coverage 0.08). That is not evidence, so it is NOT adopted.

## 4. Decision
- `config/policy.example.yaml` keeps the spec's 0.90 placeholder. With Kev that means a restart is never planned:
  every restart proposal escalates to a person. This is the safe production stance until the targets are met on
  reviewed, real data.
- `config/policy.local-kev.yaml` (used by `compose.kev.yaml`) is DEMO-ONLY: `min_action_confidence` 0.10
  (measured precision 0.76, coverage 0.79) and widened noise and severity bounds, so the remediation path can be
  exercised with Kev locally. It is acceptable only because every restart still needs an approval
  (`approval_required` autonomy and ceiling) and the remediation server defaults to dry run.
- `TRIAGE__DECISION__SHUFFLE_OPTIONS` stays false in runtime (deterministic answers for the same state); the order
  sensitivity is tracked by the eval.

## 5. Connectivity from Docker Desktop on macOS
Containers reach the internet and `host.docker.internal`, but nothing on the LAN (not the Spark, not even the Mac's
own LAN address), while Terminal can. This matches macOS's Local Network privacy permission being off for Docker
Desktop. Two ways to fix it:
1. Permanent: System Settings > Privacy & Security > Local Network > enable Docker (Docker Desktop restart), then set
   `KEV_BASE_URL=http://192.168.5.147:8003` in `.env`.
2. Used now: an SSH tunnel on the Mac, which containers reach through `host.docker.internal`:
   `ssh -N -L 127.0.0.1:18003:127.0.0.1:8003 -i ~/.ssh/spark_hermes_ed25519 timo@spark-337a.local` with
   `KEV_BASE_URL=http://host.docker.internal:18003`.
