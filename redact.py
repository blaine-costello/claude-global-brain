"""Secret redaction + privacy handling for the local Claude brain.

Every value written to the brain passes through `clean()` first:
  1. `<private>...</private>` spans are dropped entirely (model-controlled opt-out).
  2. Known secret shapes (API keys, tokens, AWS keys, private-key blocks,
     `key = value` secrets) are replaced with ‹redacted›.

Conservative by design: we'd rather miss an exotic secret than mangle ordinary
prose. The daemon never makes network calls, and the DB is local + user-owned,
but machine-wide capture spans sensitive repos, so this is a hard gate.
"""
from __future__ import annotations

import re

REDACTED = "‹redacted›"  # ‹redacted›

# Ordered list of (compiled regex, replacement). Specific patterns first.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM private-key blocks (any type).
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.DOTALL), REDACTED),
    # Anthropic / OpenAI style keys.
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"), REDACTED),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), REDACTED),
    # AWS access key id + a value following an aws secret label.
    (re.compile(r"AKIA[0-9A-Z]{16}"), REDACTED),
    (re.compile(r"(?i)(aws_secret_access_key\s*[=:]\s*)[A-Za-z0-9/+=]{30,}"), r"\1" + REDACTED),
    # GitHub tokens.
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), REDACTED),
    (re.compile(r"github_pat_[A-Za-z0-9_]{30,}"), REDACTED),
    # Slack tokens.
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), REDACTED),
    # Google API key.
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), REDACTED),
    # JWTs.
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), REDACTED),
    # Bearer tokens.
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{20,}"), r"\1" + REDACTED),
    # Generic `secret/token/password/api_key = value`.
    (re.compile(r"(?i)\b(api[_\-]?key|secret|token|password|passwd|pwd|access[_\-]?key|private[_\-]?key)"
                r"\b(\s*[=:]\s*)([\"']?)([^\s\"',;}]{8,})(\3)"),
     lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(5)}"),
]

_PRIVATE = re.compile(r"<private>.*?</private>", re.DOTALL | re.IGNORECASE)


def strip_private(text: str) -> str:
    """Remove any <private>...</private> spans (and a stray unclosed opener)."""
    text = _PRIVATE.sub("", text)
    # If an opener has no closer, drop from the opener to end of string.
    idx = text.lower().find("<private>")
    if idx != -1:
        text = text[:idx]
    return text


def redact(text: str) -> str:
    """Replace known secret shapes with the redaction marker."""
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def clean(text: str | None) -> str:
    """Full pipeline: strip private spans, then redact secrets. Safe on None."""
    if not text:
        return ""
    return redact(strip_private(text))


def clean_obj(obj):
    """Recursively clean strings inside a JSON-able structure (dict/list/str)."""
    if isinstance(obj, str):
        return clean(obj)
    if isinstance(obj, dict):
        return {k: clean_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_obj(v) for v in obj]
    return obj


if __name__ == "__main__":  # quick self-test
    import sys
    print(clean(sys.stdin.read()))
