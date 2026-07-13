"""Shared capture-quality gate used by browser_capture and analyzer."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from app.settings import get_settings

logger = logging.getLogger(__name__)


def _cs() -> dict:
    return get_settings().get("content_sufficiency") or {}


def min_case_study_chars() -> int:
    return int(_cs().get("min_case_study_chars", 200))


def min_loaded_images() -> int:
    return int(_cs().get("min_loaded_images", 2))


def min_natural_width() -> int:
    return int(_cs().get("min_natural_width", 200))


def __getattr__(name: str):
    """Back-compat for scripts importing MIN_* / _BEHANCE_WALL_PHRASES."""
    if name == "MIN_CASE_STUDY_CHARS":
        return min_case_study_chars()
    if name == "MIN_LOADED_IMAGES":
        return min_loaded_images()
    if name == "MIN_NATURAL_WIDTH":
        return min_natural_width()
    if name == "_BEHANCE_WALL_PHRASES":
        return _wall_phrases()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_PRIMARY_CONTENT_SELECTOR = (
    "main, article, .project-canvas, #project-content, #main-content"
)

_IMAGE_SELECTORS: dict[str, str] = {
    "behance": (
        'img[src*="behance.net/projects"], img[src*="project_modules"], '
        "img.project-image, .project-canvas img, [class*='ProjectModule'] img"
    ),
    "dribbble": "img[src*='dribbble'], .shot img, img[data-src]",
    "framer": "main img, article img, [data-framer-component-type] img",
    "figma": "img, canvas",
    "google_docs": "img",
    "personal": "main img, article img, section img, img",
}

_DEFAULT_IMAGE_SELECTORS = "main img, article img, section img, img"


def _wall_phrases() -> tuple[str, ...]:
    phrases = (get_settings().get("behance") or {}).get("wall_phrases") or []
    return tuple(phrases)


_BEHANCE_WALL_JS = """(strictPhrases) => {
    const path = (location.pathname || '').toLowerCase();
    if (path.includes('/login') || path.includes('/signup') || path.includes('/join')) {
        return { walled: true, marker: 'redirect_path:' + path };
    }
    const bodyLower = (document.body?.innerText || '').toLowerCase();
    for (const ph of strictPhrases) {
        if (bodyLower.includes(ph)) {
            return { walled: true, marker: 'phrase:' + ph };
        }
    }
    const dialogs = document.querySelectorAll(
        '[role="dialog"], [class*="Modal"], [class*="SignIn"], [class*="SignUp"]'
    );
    for (const dialog of dialogs) {
        const style = window.getComputedStyle(dialog);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        const rect = dialog.getBoundingClientRect();
        if (rect.width < 80 || rect.height < 80) continue;
        const t = (dialog.innerText || '').toLowerCase();
        for (const ph of strictPhrases) {
            if (t.includes(ph)) {
                return { walled: true, marker: 'dialog:' + ph };
            }
        }
        if (
            t.includes('sign in') && t.includes('sign up') &&
            (t.includes('view project') || t.includes('adult content'))
        ) {
            return { walled: true, marker: 'dialog:behance_view_gate' };
        }
    }
    if (path.includes('/gallery/')) {
        const modules = document.querySelectorAll(
            '[class*="project-module"], [class*="ProjectModule"]'
        ).length;
        if (modules === 0) {
            return { walled: true, marker: 'gallery:no_project_modules' };
        }
    }
    return { walled: false, marker: null };
}"""


def platform_from_url(url: str | None) -> str:
    if not url:
        return "personal"
    netloc = urlparse(url.lower()).netloc
    if "behance.net" in netloc:
        return "behance"
    if "dribbble.com" in netloc:
        return "dribbble"
    if "framer.ai" in netloc or "framer.com" in netloc or "framer.website" in netloc:
        return "framer"
    if "figma.com" in netloc or "embed.figma.com" in netloc:
        return "figma"
    if "docs.google.com" in netloc:
        return "google_docs"
    return "personal"


def _platform_from_page(page: Any) -> str:
    try:
        return platform_from_url(page.url)
    except Exception:
        return "personal"


def detect_behance_wall(page: Any) -> tuple[bool, str | None]:
    """Return (is_walled, marker) from live Behance DOM. Behance URLs only."""
    if _platform_from_page(page) != "behance":
        return False, None
    try:
        result = page.evaluate(_BEHANCE_WALL_JS, list(_wall_phrases()))
        if isinstance(result, dict) and result.get("walled"):
            return True, str(result.get("marker") or "behance_wall")
    except Exception as e:
        logger.warning("behance_wall_check_failed", extra={"error": str(e)})
    return False, None


def _count_loaded_images(page: Any) -> tuple[int, list[dict[str, Any]]]:
    """Count DOM images with naturalWidth above threshold; return samples for diagnostics."""
    platform = _platform_from_page(page)
    selector = _IMAGE_SELECTORS.get(platform, _DEFAULT_IMAGE_SELECTORS)
    min_w = min_natural_width()
    try:
        result = page.evaluate(
            """({ selector, minWidth }) => {
                const seen = new Set();
                const samples = [];
                let count = 0;
                for (const img of document.querySelectorAll(selector)) {
                    if (!(img instanceof HTMLImageElement)) continue;
                    if (img.naturalWidth <= minWidth) continue;
                    const key = img.currentSrc || img.src || '';
                    if (!key || seen.has(key)) continue;
                    seen.add(key);
                    count += 1;
                    if (samples.length < 8) {
                        samples.push({
                            w: img.naturalWidth,
                            h: img.naturalHeight,
                            src: key.slice(0, 100),
                        });
                    }
                }
                return { count, samples };
            }""",
            {"selector": selector, "minWidth": min_w},
        )
        if isinstance(result, dict):
            return int(result.get("count", 0)), list(result.get("samples") or [])
    except Exception as e:
        logger.warning("content_sufficiency_dom_check_failed", extra={"error": str(e)})
    return 0, []


def assess_capture_quality(
    screenshots: list[str],
    case_study_text: str,
    page: Any | None = None,
    *,
    used_fallback_selector: bool = False,
    page_url: str | None = None,
    behance_wall_marker: str | None = None,
) -> tuple[bool, list[str]]:
    """
    Returns (is_sufficient, reasons). Called twice:
    1. Inside snapshot_project (page still open) — DOM-based checks
    2. In analyzer.py before generate_content — final defensive gate (page=None)
    """
    reasons: list[str] = []
    platform = _platform_from_page(page) if page is not None else platform_from_url(page_url)
    min_imgs = min_loaded_images()
    min_chars = min_case_study_chars()

    wall_marker = behance_wall_marker
    if page is not None and platform == "behance":
        walled, detected = detect_behance_wall(page)
        if walled:
            wall_marker = detected

    if wall_marker:
        reasons.append(f"behance login/paywall detected ({wall_marker})")

    if platform == "behance" and used_fallback_selector:
        reasons.append(
            "behance page missing primary content container "
            f"({_PRIMARY_CONTENT_SELECTOR}) — used body fallback text"
        )

    if page is not None:
        loaded, _samples = _count_loaded_images(page)
        if loaded < min_imgs:
            reasons.append(
                f"fewer than {min_imgs} substantively-sized project images in DOM "
                f"(found {loaded})"
            )

    if len((case_study_text or "").strip()) < min_chars:
        reasons.append(f"case study text under {min_chars} chars")

    if len(screenshots) == 0:
        reasons.append("zero screenshots captured")

    return (len(reasons) == 0, reasons)


def diagnose_capture_quality(
    screenshots: list[str],
    case_study_text: str,
    page: Any | None = None,
    *,
    used_fallback_selector: bool = False,
    page_url: str | None = None,
) -> dict[str, Any]:
    """Verbose diagnostics for scripts / live tests."""
    platform = _platform_from_page(page) if page is not None else platform_from_url(page_url)
    wall_marker: str | None = None
    image_count = 0
    image_samples: list[dict[str, Any]] = []

    if page is not None:
        walled, wall_marker = detect_behance_wall(page)
        if walled and not wall_marker:
            wall_marker = "behance_wall"
        image_count, image_samples = _count_loaded_images(page)

    sufficient, reasons = assess_capture_quality(
        screenshots,
        case_study_text,
        page=page,
        used_fallback_selector=used_fallback_selector,
        page_url=page_url,
        behance_wall_marker=wall_marker,
    )
    return {
        "platform": platform,
        "used_fallback_selector": used_fallback_selector,
        "behance_wall_marker": wall_marker,
        "case_study_chars": len((case_study_text or "").strip()),
        "screenshot_count": len(screenshots),
        "loaded_image_count": image_count,
        "loaded_image_samples": image_samples,
        "sufficient": sufficient,
        "reasons": reasons,
    }


def assess_capture_quality_legacy(
    screenshots: list[str],
    case_study_text: str,
    page: Any | None = None,
) -> tuple[bool, list[str]]:
    """Pre-strengthening gate (image count + text length + zero screenshots only)."""
    reasons: list[str] = []
    min_imgs = min_loaded_images()
    min_chars = min_case_study_chars()
    min_w = min_natural_width()
    if page is not None:
        loaded, _ = _count_loaded_images(page)
        try:
            loaded = page.evaluate(
                """(minWidth) => {
                    return [...document.querySelectorAll(
                        'img[src*="behance"], img.project-image'
                    )].filter(img => img.naturalWidth > minWidth).length;
                }""",
                min_w,
            )
        except Exception:
            loaded = 0
        if loaded < min_imgs:
            reasons.append(
                f"fewer than {min_imgs} substantively-sized images in DOM "
                f"(found {loaded})"
            )
    if len((case_study_text or "").strip()) < min_chars:
        reasons.append(f"case study text under {min_chars} chars")
    if len(screenshots) == 0:
        reasons.append("zero screenshots captured")
    return (len(reasons) == 0, reasons)
