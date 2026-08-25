"""Read/merge/write the project ``.env`` while preserving comments and order.

The setup webapp is the only writer of ``.env`` at runtime, so it must be
careful not to clobber the hand-written comments and layout that document each
variable. :func:`merge_env` rewrites only the values of keys it is given,
appending any brand-new keys at the end, and leaves everything else untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

# A KEY=VALUE assignment line (KEY may be preceded by leading whitespace). We do
# not attempt full POSIX-shell parsing -- .env here is simple KEY=VALUE lines.
_ASSIGN_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>.*)$")


def parse_env(text: str) -> dict[str, str]:
    """Parse ``.env`` text into a dict of KEY -> VALUE (commented lines ignored)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ASSIGN_RE.match(line)
        if m:
            out[m.group("key")] = _unquote(m.group("val").strip())
    return out


def read_env(path: Path) -> dict[str, str]:
    """Return the active (uncommented) KEY -> VALUE pairs from ``path``."""
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def merge_env(path: Path, updates: dict[str, str]) -> None:
    """Merge ``updates`` into the ``.env`` at ``path``, preserving comments/order.

    Existing *active* assignments are updated in place. A key that only appears
    commented-out (``#KEY=...``) is uncommented and set. Keys not present at all
    are appended in a trailing block. Keys mapped to ``None``-like are skipped.
    """
    updates = {k: v for k, v in updates.items() if v is not None}
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)

    for i, line in enumerate(lines):
        m = _ASSIGN_RE.match(line)
        if m and m.group("key") in remaining:
            key = m.group("key")
            lines[i] = f"{m.group('indent')}{key}={_quote(remaining.pop(key))}"
            continue
        # Uncomment a commented assignment (e.g. "#LSM_MODEL_BASE_URL=...").
        stripped = line.lstrip()
        if stripped.startswith("#"):
            cm = _ASSIGN_RE.match(stripped.lstrip("#").lstrip())
            if cm and cm.group("key") in remaining:
                key = cm.group("key")
                lines[i] = f"{key}={_quote(remaining.pop(key))}"

    if remaining:
        header = "# --- Added by the setup webapp ---"
        if header not in lines:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(header)
        for key, val in remaining.items():
            lines.append(f"{key}={_quote(val)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quote(value: str) -> str:
    """Quote a value only if it contains characters that need it."""
    if value == "" or re.search(r"[\s#\"']", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _unquote(value: str) -> str:
    """Strip surrounding quotes and unescape a ``.env`` value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value
