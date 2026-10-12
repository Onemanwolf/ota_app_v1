from __future__ import annotations

ACTION_OPTIONS: dict[str, str] = {
    "notify_only": "Inform on-call. No automated change. Default for most problems.",
    "restart_container": "A single container is unhealthy or down and a restart is the standard, low-risk fix.",
    "open_ticket": "Needs follow-up work but no immediate action, for example a slow degradation.",
    "escalate_human": "High impact, unclear cause, or several components affected. A person must look now.",
    "suppress": "Known noise, test or maintenance activity. Nothing to do.",
    "cannot_tell": "The information is insufficient to choose.",
}

SEVERITY_LEVELS: list[str] = ["noise", "warning", "degraded", "outage"]  # ordered, lowest first

QUESTIONS: dict[str, dict[str, object]] = {
    "action": {
        "type": "choice",
        "instructions": (
            "You triage an infrastructure problem detected by Dynatrace. Choose the single most appropriate next "
            "action. Prefer automated restart only when the evidence shows one container is down or crash-looping "
            "and nothing suggests a dependency, data or capacity problem."
        ),
        "criteria": ACTION_OPTIONS,
    },
    "severity": {
        "type": "score",
        "instructions": "Rate the user-facing severity of this problem right now.",
        "criteria": SEVERITY_LEVELS,
    },
    "auto_remediable": {
        "type": "noul",
        "instructions": "Would restarting the root-cause container most likely resolve this problem?",
        "criteria": {
            "true": "A restart is likely to fix it.",
            "false": "A restart is unlikely to fix it or is risky.",
        },
    },
    "is_noise": {
        "type": "noul",
        "instructions": "Is this problem expected noise (test, maintenance, known flapping) rather than a real issue?",
    },
}
