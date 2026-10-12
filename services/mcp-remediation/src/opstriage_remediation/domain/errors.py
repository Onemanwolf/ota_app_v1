from __future__ import annotations


class PolicyViolation(Exception):
    """A request that can never succeed as sent (for example an unknown target)."""
