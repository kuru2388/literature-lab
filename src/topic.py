"""Join the idea with optional task, domain, and constraint lines."""

from __future__ import annotations


def fold_topic(idea: str, task: str = "", domain: str = "", constraint: str = "") -> str:
    """Keep the free-text idea, then append the three precise parts if present."""
    parts = [(idea or "").strip()]
    lines = []
    if (task or "").strip():
        lines.append(f"Task: {task.strip()}")
    if (domain or "").strip():
        lines.append(f"Domain: {domain.strip()}")
    if (constraint or "").strip():
        lines.append(f"Constraint: {constraint.strip()}")
    if lines:
        parts.append("\n".join(lines))
    return "\n\n".join(part for part in parts if part).strip()
