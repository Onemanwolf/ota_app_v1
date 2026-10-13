"""Redact secrets and personal data from log text before it is stored or shown to a model.

OTA-APP-002 section 8.

Rules run in order; secret rules come first so that a token inside an e-mail-like string is caught as a token.
IPv4 addresses keep their /24 network (`10.42.3.x`): enough to tell clients apart in a review, not to
identify a host.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

Replacement = str | Callable[[re.Match[str]], str]


@dataclass(frozen=True, slots=True)
class RedactionRule:
    name: str
    pattern: re.Pattern[str]
    replacement: Replacement


def _rule(name: str, pattern: str, replacement: Replacement, flags: int = 0) -> RedactionRule:
    return RedactionRule(name, re.compile(pattern, flags), replacement)


DEFAULT_RULES: tuple[RedactionRule, ...] = (
    _rule(
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "<private-key>",
        re.DOTALL,
    ),
    _rule("jaas", r"(sasl\.jaas\.config\s*[=:]\s*)[^;\n]*;?", r"\1<redacted>", re.IGNORECASE),
    _rule("jwt", r"\beyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}", "<jwt>"),
    _rule("dynatrace_token", r"\bdt0[a-z]\d{2}\.[A-Z0-9]{20,}\.[A-Z0-9]{40,}\b", "<token>"),
    _rule("bearer", r"(\bbearer\s+)[\w\-.~+/]+=*", r"\1<token>", re.IGNORECASE),
    _rule(
        "secret_assignment",
        r"""(\b(?:password|passwd|pwd|secret|client[_.-]?secret|token|api[_.-]?key|access[_.-]?key|"""
        r"""ssl\.(?:key|keystore|truststore)\.password)\b\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;)]+)""",
        r"\1<redacted>",
        re.IGNORECASE,
    ),
    _rule("email", r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", "<email>"),
    _rule("ipv6", r"\b(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}\b", "<ipv6>", re.IGNORECASE),
    _rule("ipv4", r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.\d{1,3}\b", r"\1.\2.\3.x"),
    _rule("long_hex", r"\b[0-9a-f]{32,}\b", "<hex>", re.IGNORECASE),
    _rule("long_base64", r"\b(?=[A-Za-z0-9+/]*\d)(?=[A-Za-z0-9+/]*[A-Z])[A-Za-z0-9+/]{40,}={0,2}", "<b64>"),
)


@dataclass(frozen=True, slots=True)
class Redacted:
    text: str
    counts: dict[str, int]  # rule name -> replacements (for audit counts; never the matched text)


class Redactor:
    def __init__(self, rules: Sequence[RedactionRule] = DEFAULT_RULES) -> None:
        self._rules = tuple(rules)

    def redact(self, text: str) -> Redacted:
        counts: Counter[str] = Counter()
        for rule in self._rules:
            text, n = rule.pattern.subn(rule.replacement, text)
            if n:
                counts[rule.name] += n
        return Redacted(text, dict(counts))

    def __call__(self, text: str) -> str:
        return self.redact(text).text
