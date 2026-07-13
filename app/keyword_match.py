"""Shared keyword matching with word boundaries (avoids 'ui' in 'aquila')."""

from __future__ import annotations

import re


def _normalize(text: str) -> str:
    """Lowercase; treat /, -, _ as word separators (ui/ux, brand-identity)."""
    return re.sub(r"[/_\-]+", " ", str(text).lower())


def _plural_forms(word: str) -> list[str]:
    """Singular + simple English plural so 'logo' matches 'logos', 'study'→'studies'."""
    w = word.lower()
    forms = [w]
    if len(w) <= 1:
        return forms
    if w.endswith("y") and w[-2] not in "aeiou":
        forms.append(w[:-1] + "ies")
    elif w.endswith(("s", "x", "z")) or w.endswith(("ch", "sh")):
        forms.append(w + "es")
    else:
        forms.append(w + "s")
        if not w.endswith("e"):
            forms.append(w + "es")
    # de-dupe, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for f in forms:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def keyword_matches(text: str, keyword: str) -> bool:
    """
    True if keyword appears in text as a whole word/phrase (case-insensitive).

    Multi-word keywords (e.g. "visual identity", "case study") match with flexible
    whitespace between words. Hyphen/underscore/slash count as word edges.
    The last token also matches a simple plural (logo→logos, study→studies).
    """
    if not text or not keyword:
        return False
    kw = str(keyword).strip().lower()
    if not kw:
        return False
    parts = [p for p in re.split(r"\s+", kw) if p]
    if not parts:
        return False

    norm = _normalize(text)
    body_parts = [re.escape(p) for p in parts[:-1]]
    last_alts = "|".join(re.escape(f) for f in _plural_forms(parts[-1]))
    if body_parts:
        pattern = r"\b" + r"\s+".join(body_parts) + r"\s+(?:" + last_alts + r")\b"
    else:
        pattern = r"\b(?:" + last_alts + r")\b"
    return re.search(pattern, norm) is not None


def any_keyword_matches(text: str, keywords: list[str] | tuple[str, ...]) -> bool:
    return any(keyword_matches(text, k) for k in keywords)


def keyword_hit_score(text: str, keywords: list[str] | tuple[str, ...], weight: int = 2) -> int:
    return sum(weight for k in keywords if keyword_matches(text, k))
