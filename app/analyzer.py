import base64
import json
import logging
import os
import re
import tempfile

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.content_sufficiency import assess_capture_quality

load_dotenv()

# Brand Identity expert framework (v2) — loaded per request for brand roles
_default_framework_path = os.path.join(
    os.path.dirname(__file__), "brand_identity_expert_framework_v2.txt"
)

logger = logging.getLogger(__name__)


def _is_brand_role(candidate_role: str | None) -> bool:
    return bool(candidate_role and "brand" in candidate_role.lower())


# Optional in-memory override for the Brand Identity rubric text.
# If set, get_brand_framework() uses this instead of reading from disk.
# (Reserved for a future feedback-driven rubric rewrite path.)
BRAND_IDENTITY_EXPERT_FRAMEWORK: str = ""

def get_brand_framework() -> str:
    """Load expert framework from disk (fresh each call). Override path via BRAND_FRAMEWORK_PATH."""
    cached = globals().get("BRAND_IDENTITY_EXPERT_FRAMEWORK") or ""
    if isinstance(cached, str) and cached.strip():
        return cached.strip()
    path = os.getenv("BRAND_FRAMEWORK_PATH", "").strip() or _default_framework_path
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return content
            logger.warning("brand_framework_empty", extra={"path": path})
    except FileNotFoundError:
        logger.warning("brand_framework_missing", extra={"path": path})
    except OSError as e:
        logger.warning(
            "brand_framework_read_failed",
            extra={"path": path, "error": str(e)},
        )
    return ""


def _framework_preamble(framework: str) -> str:
    return (
        "=== PRIMARY: EXPERT BRAND IDENTITY EVALUATION FRAMEWORK ===\n"
        "This framework is authoritative for Brand Designer scoring on this request. "
        "If anything later in the prompt conflicts with it, follow the framework.\n\n"
        f"{framework}\n\n"
        "=== END EXPERT FRAMEWORK ===\n\n"
    )


def _role_rubric_block(candidate_role: str | None, *, expert_framework_loaded: bool) -> str:
    """Role criteria appended to the prompt. Brand uses expert file when available."""
    if _is_brand_role(candidate_role) and expert_framework_loaded:
        return (
            "BRAND IDENTITY DESIGNER:\n"
            "Apply the expert framework above for all brand scoring (1–5), red flags, "
            "and hire/shortlist/pass judgment. Do not use a generic checklist.\n\n"
        )

    blocks = []
    if not _is_brand_role(candidate_role):
        blocks.append(
            "UI/UX DESIGNER:\n"
            "Score 1 if: Only final mockups shown, no process, no problem statement, "
            "looks like a Figma tutorial or UI kit copy, no user flows or research evidence.\n"
            "Score 2 if: Some screens shown but no clear problem-solution arc, "
            "generic UI patterns, no evidence of user thinking.\n"
            "Score 3 if: Basic case study structure present, problem and solution shown "
            "but surface level, competent but unremarkable execution.\n"
            "Score 4 if: Clear problem → research → ideation → solution arc, "
            "real user pain points addressed, interaction logic evident, "
            "strong visual execution with consistent design system.\n"
            "Score 5 if: Everything in score 4 plus exceptional depth of thinking, "
            "measurable outcomes shown, work that could be in a top agency portfolio.\n"
            "RED FLAGS: No wireframes or process, no user research evidence, "
            "copy-pasted UI kit components, only showing Figma auto-layouts.\n\n"
        )
    if _is_brand_role(candidate_role):
        blocks.append(
            "BRAND IDENTITY DESIGNER:\n"
            "IMPORTANT DISTINCTION: Label design, social media posts, and generic packaging are NOT brand identity work. "
            "True brand identity shows: a logo with concept rationale, color and typography system, how the brand works across touchpoints, "
            "and strategic thinking behind visual choices. If the work shown is ONLY labels, social media posts, or isolated graphic design "
            "with no brand system — score 1 or 2 regardless of execution quality.\n"
            "Score 1 if: Single logo with no brand system, generic typeface, "
            "no color rationale, looks like a logo generator output.\n"
            "Score 2 if: Logo with basic color palette but no full system, "
            "no brand voice or application shown, generic execution.\n"
            "Score 3 if: Logo + color + typography shown, some brand applications "
            "like business card or social media, functional but not distinctive.\n"
            "Score 4 if: Full brand identity system — logo variations, typography system, "
            "color palette with rationale, multiple real applications shown, "
            "distinctive and ownable visual language.\n"
            "Score 5 if: Everything in score 4 plus brand strategy evident, "
            "brand voice and tone guidelines, exceptional originality, "
            "work that could win a design award.\n"
            "RED FLAGS: Only one logo presented, no brand system, "
            "generic sans-serif with no rationale, template mockups only.\n\n"
        )
    if not _is_brand_role(candidate_role):
        blocks.extend(
            [
                "MOTION DESIGNER:\n"
                "Score 1 if: Static screenshots only, no motion evidence, "
                "basic After Effects templates, no timing or easing craft shown.\n"
                "Score 2 if: Some animation shown but generic transitions, "
                "no motion principles evident, looks like preset animations.\n"
                "Score 3 if: Competent motion work, clear timing and easing, "
                "functional but not distinctive style.\n"
                "Score 4 if: Strong motion craft — custom easing, purposeful animation, "
                "clear understanding of motion principles, distinctive style.\n"
                "Score 5 if: Exceptional motion work — could be broadcast quality, "
                "deep craft, original visual language in motion.\n"
                "RED FLAGS: Only static images in a motion portfolio, "
                "template animations, no original motion design.\n\n",
                "GRAPHIC DESIGNER:\n"
                "Score 1 if: Generic layouts, no typographic understanding, "
                "clip art or stock heavy, no clear design intent.\n"
                "Score 2 if: Basic design competency, some layout skill but "
                "inconsistent execution, limited range.\n"
                "Score 3 if: Solid technical execution, good typography, "
                "range across formats shown, competent professional work.\n"
                "Score 4 if: Strong typographic voice, distinctive visual style, "
                "excellent range across print and digital, confident design decisions.\n"
                "Score 5 if: Exceptional craft and originality, award-worthy work, "
                "strong personal visual language.\n"
                "RED FLAGS: Only social media posts, heavy Canva use, "
                "no print or editorial work, no typographic intention.\n\n",
            ]
        )
    return "".join(blocks)

# Single source of truth for report JSON (gemini-2.0-flash deprecated; use 2.5-flash)
AI_MODEL_NAME = None  # resolved lazily from config/pipeline.json / AI_MODEL_NAME env


def _model_name() -> str:
    global AI_MODEL_NAME
    if not AI_MODEL_NAME:
        from app.settings import get_settings

        AI_MODEL_NAME = get_settings().get("model_name", "gemini-2.5-flash")
    return AI_MODEL_NAME


GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "").strip()
GCP_REGION = os.getenv("GCP_REGION", "us-central1").strip()

_creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
if _creds_json:
    _creds_dict = json.loads(_creds_json)
    _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(_creds_dict, _tmp)
    _tmp.flush()
    _tmp.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _tmp.name

genai_client = genai.Client(
    vertexai=True,
    project=GCP_PROJECT_ID,
    location=GCP_REGION,
)
print(f"✅ Vertex AI client initialized for project {GCP_PROJECT_ID}")


def get_model_for_role(candidate_role: str) -> str:
    """All roles use Gemini; Brand Designer gets expert framework injected into the prompt."""
    return _model_name()

def get_max_workers():
    """Concurrency for Gemini (sequential recommended for free tier)."""
    return max(1, int(os.getenv("GEMINI_MAX_WORKERS", "3")))


def _parse_json_from_response(text: str) -> dict | None:
    """Robust JSON extraction: strip fences, try parse, then first { to last }, then fix common issues."""
    if not text or not text.strip():
        return None
    text = text.strip()

    # 1. Strip markdown fences
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # 2. Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Extract first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            text = text[start : end + 1]

    # 4. Fix common issues: trailing commas before } or ], smart quotes
    cleaned = re.sub(r",\s*}", "}", text)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    return None


_GUARD_ANSWERS = frozenset({"yes", "no", "unclear"})


def _normalize_guard_answer(value) -> str:
    if value is None:
        return "unclear"
    text = str(value).strip().lower()
    if text in _GUARD_ANSWERS:
        return text
    if text in {"y", "true"}:
        return "yes"
    if text in {"n", "false"}:
        return "no"
    return "unclear"


def _numeric_project_score(value) -> float:
    if isinstance(value, list):
        return float(value[0]) if value else 0.0
    if isinstance(value, str):
        m = re.search(r"(\d+(?:\.\d+)?)", value)
        return float(m.group(1)) if m else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def filter_low_score_strengths(out: dict) -> dict:
    """
    Score-driven enforcement (no keyword lists): when final score is at or below
    empty_strengths_max_score, clear quality_indicators entirely.
    """
    from app.settings import get_settings

    max_score = float(get_settings().get("empty_strengths_max_score", 2))
    score = _numeric_project_score(out.get("score"))
    if score <= max_score:
        out["quality_indicators"] = []
    elif not isinstance(out.get("quality_indicators"), list):
        out["quality_indicators"] = []
    return out


def apply_visual_polish_guard_cap(out: dict, candidate_role: str | None) -> dict:
    """
    Enforce VISUAL POLISH GUARD in Python after model JSON is parsed.

    Disable independently (does not affect hub filter, relevance, craft_quality, gaps):
      VISUAL_POLISH_GUARD_CAP=0

    - All three guards = no -> cap at 1
    - All three guards = unclear -> no cap (uncertainty is not failure)
    - Mix of no and unclear -> cap at 2
    - Single no with the other two yes -> cap at 2
    - Two or more no (remaining yes) -> cap at 2
    - All three yes -> no cap
    - Unclear mixed with yes (no no) -> no cap
    """
    enabled = os.getenv("VISUAL_POLISH_GUARD_CAP", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    if not enabled:
        return out
    if not _is_brand_role(candidate_role):
        return out

    q1 = _normalize_guard_answer(out.get("guard_q1_positioning"))
    q2 = _normalize_guard_answer(out.get("guard_q2_typography"))
    q3 = _normalize_guard_answer(out.get("guard_q3_distinctive"))
    out["guard_q1_positioning"] = q1
    out["guard_q2_typography"] = q2
    out["guard_q3_distinctive"] = q3

    answers = (q1, q2, q3)
    if all(a == "no" for a in answers):
        cap = 1
    elif all(a == "unclear" for a in answers):
        return out
    elif any(a == "no" for a in answers) and any(a == "unclear" for a in answers):
        cap = 2
    elif answers.count("no") == 1 and answers.count("yes") == 2:
        cap = 2
    elif answers.count("no") >= 2:
        cap = 2
    else:
        return out

    raw_score = _numeric_project_score(out.get("score"))
    if raw_score > cap:
        out["score_before_guard_cap"] = raw_score
        out["score"] = cap
        out["guard_cap_applied"] = cap
    else:
        out.pop("score_before_guard_cap", None)
        out.pop("guard_cap_applied", None)
    return out


def _response_json_schema(candidate_role: str | None) -> str:
    from app.settings import get_settings

    settings = get_settings()
    empty_max = int(float(settings.get("empty_strengths_max_score", 2)))
    shortlist_min = int(float(settings.get("shortlist_min_score", 4)))
    shortlist_msg = settings.get(
        "shortlist_ready_message", "no significant gaps — ready to shortlist"
    )
    guard_block = ""
    if _is_brand_role(candidate_role):
        guard_block = (
            '  "guard_q1_positioning": "yes/no/unclear — is there specific, non-generic brand positioning driving decisions?",\n'
            '  "guard_q2_typography": "yes/no/unclear — is typography justified beyond clean or elegant?",\n'
            '  "guard_q3_distinctive": "yes/no/unclear — is the visual language distinctive and ownable?",\n'
        )
    return (
        "Return ONLY strict JSON with these fields (answer guard questions before score):\n"
        "{\n"
        '  "design_category": "Brand Identity / UI/UX / Motion / Graphic / Illustration",\n'
        f'  "quality_indicators": ["specific strength 1"] or [] if score <= {empty_max} and none substantively found,\n'
        '  "weaknesses": ["specific weakness 1"] or [] if none substantively found,\n'
        '  "role_fit_note": "1 sentence on fit for the applied role",\n'
        '  "confidence": "high/medium/low",\n'
        '  "logic_alignment": 1-10,\n'
        '  "responsiveness_score": 1-10,\n'
        '  "visual_consistency": 1-10,\n'
        '  "seniority": "junior/mid/senior/lead",\n'
        '  "craft_quality": 1-5 — technical execution/skill independent of brand-category fit,\n'
        f"{guard_block}"
        '  "score": 1-5 — role-fit quality score (unchanged criteria; craft_quality is separate),\n'
        '  "reasoning": "2-3 sentences citing specific evidence from what you see",\n'
        f'  "next_level_delta": "1 sentence on what must improve to reach next band, OR exactly '
        f'"{shortlist_msg}" when score is {shortlist_min}-5 and no substantive gaps remain"\n'
        "}"
    )


def encode_image(image_path):
    if not os.path.exists(image_path):
        return None
    try:
        from PIL import Image
        import io
        
        with Image.open(image_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            if img.width > 1280:
                scale = 1280 / img.width
                img = img.resize((1280, int(img.height * scale)), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=75, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"  ⚠️ Compression failed for {image_path}: {e}")
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_portfolio_visuals(
    screenshot_paths,
    project_title,
    case_study_text="",
    design_specs=None,
    candidate_role=None,
    *,
    used_fallback_selector: bool = False,
    page_url: str | None = None,
    behance_wall_marker: str | None = None,
):
    """
    Analyzes project screenshots and case study text using Gemini. For Brand Designer role, expert framework is injected into the prompt.
    candidate_role: role the candidate applied for (e.g. UI UX, Brand Designer, Motion Designer) — evaluate against this role's criteria.
    """
    model_name = get_model_for_role(candidate_role)
    if not screenshot_paths:
        return {"error": "No screenshots provided", "model": model_name}
    if not genai_client:
        return {"error": "Vertex AI client not initialized.", "score": 0, "model": model_name}

    specs_context = ""
    if design_specs:
        specs_context = f"\nTECHNICAL DESIGN SPECS:\n- Fonts: {', '.join(design_specs.get('fonts', []))}\n- Colors: {', '.join(design_specs.get('colors', []))}\n- Stack: {', '.join(design_specs.get('tech_stack', []))}\n"

    role_context = f"CANDIDATE APPLIED FOR: {candidate_role}\n\n" if candidate_role else ""
    role_instruction = f"Evaluate this portfolio specifically for the {candidate_role} role requirements.\n\n" if candidate_role else "Evaluate this portfolio for design quality.\n\n"

    framework = get_brand_framework() if _is_brand_role(candidate_role) else ""
    role_rubrics = _role_rubric_block(
        candidate_role, expert_framework_loaded=bool(framework)
    )

    from app.settings import get_settings

    settings = get_settings()
    empty_max = int(float(settings.get("empty_strengths_max_score", 2)))
    shortlist_min = int(float(settings.get("shortlist_min_score", 4)))
    shortlist_msg = settings.get(
        "shortlist_ready_message", "no significant gaps — ready to shortlist"
    )

    prompt = (
        f"{role_context}"
        f"{role_instruction}"
        f"You are a Senior Design Director at a top creative agency with 15 years of hiring experience. "
        f"You recognize both weak and strong work. You are evaluating a portfolio project titled '{project_title}'.\n\n"
        "CONTEXT (Designer's Written Process):\n"
        f"{case_study_text[:3000]}\n"
        f"{specs_context}\n"
        "YOUR GOAL: Evaluate the DESIGN WORK only — not the portfolio hosting site.\n"
        "IGNORE Behance/Framer/Webflow templates, navigation, and standard portfolio UI.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "SCORING SCALE — READ THIS BEFORE SCORING\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Score 1 = Poor. Unacceptable. Do not hire.\n"
        "Score 2 = Below standard. Significant gaps.\n"
        "Score 3 = Competent professional work. Shows genuine design effort even without full case studies. Meets baseline.\n"
        "Score 4 = Strong work. Clear design thinking, good execution. Would shortlist. Use 4 when the work clearly meets the role's 'Score 4 if' criteria below.\n"
        "Score 5 = Exceptional for the role. Use 5 when the work clearly meets the role's 'Score 5 if' criteria — standout depth, craft, or originality.\n\n"
        "CALIBRATION — USE THE FULL SCALE:\n"
        "Do not avoid 4 or 5. When the work clearly meets the criteria for 4 (strong, would shortlist), score 4. When it clearly meets the criteria for 5 (exceptional for the role), score 5. "
        "Do not default to 1-2; reserve those for genuinely poor or incomplete work. "
        "Do not cluster all good work at 3; if the work is strong and would shortlist, score 4. If it is exceptional for the role, score 5. "
        "If borderline between 3 and 4, prefer 4 when there is clear design thinking and polished execution. If borderline between 4 and 5, prefer 5 when the work is clearly standout for the role.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "CRITICAL SCORING RULES\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. VISUAL POLISH GUARD — HARD RULE:\n"
        "Polished mockups, consistent color application, and clean presentation\n"
        "are NOT scoring criteria. They are table stakes.\n"
        "These alone CANNOT push a score above 2.\n\n"
        "Score 3+ is BLOCKED unless you can explicitly answer YES to all three:\n"
        "- Is there a specific, non-generic brand positioning driving every decision?\n"
        "- Is the typography choice justified beyond \"clean\" or \"elegant\"?\n"
        "- Is the visual language distinctive and ownable — not interchangeable with 10 other brands?\n\n"
        "If any answer is NO or UNCLEAR — score is capped at 2. No exceptions.\n"
        "You MUST record these three answers in guard_q1_positioning, guard_q2_typography, and guard_q3_distinctive "
        "in the JSON (before the score field) before assigning score.\n\n"
        "2. DECORATIVE TEXT READING:\n"
        "When you encounter stylized, hand-lettered, or decorative typography in the work,\n"
        "treat it as readable content — not just a visual element.\n"
        "It likely contains the brand name, tagline, or campaign message.\n"
        "Reading it correctly is essential to understanding the strategic intent of the work.\n\n"
        "PORTFOLIO EVALUATION RULE: Evaluate the candidate holistically across all projects provided. "
        "A strong portfolio requires depth in 1-2 projects, not every project. If the candidate demonstrates "
        "research, problem framing, or process in at least one project, do not penalise other projects for lacking this. "
        "A polished final mockup without wireframes can still score 3 if the visual execution is strong.\n\n"
        "CRAFT QUALITY (separate field — does NOT change score):\n"
        "Rate craft_quality (1-5) on technical execution, polish, and design skill alone — "
        "independent of whether the work is brand identity, UI/UX, illustration, or another category. "
        "A strong generalist with excellent craft but limited branding volume should still score high on craft_quality.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ROLE-SPECIFIC EVALUATION CRITERIA\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{role_rubrics}"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "EVALUATION INSTRUCTIONS\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "- MOBILE AUDIT: Check if design is truly responsive on small screens.\n"
        "- VISUAL CONSISTENCY: Do fonts and colors form a coherent system?\n"
        "- Be specific in reasoning — cite what you actually see.\n"
        "- Note weaknesses at the portfolio level, not per project. Only flag missing wireframes or process if absent across ALL projects shown, not just this one.\n"
        "- Be honest and specific. When the work clearly meets the criteria for 4 or 5, give that score; do not under-score strong or exceptional work.\n\n"
        "PROJECT-SPECIFIC COMMENT QUALITY RULES:\n"
        "- For this project, provide role-specific evidence, not generic text.\n"
        f"- quality_indicators (strengths): when score is {empty_max + 1}+, include at least 1 concrete strength "
        "tied to what is visible. When score is "
        f"1-{empty_max} and no substantive design strengths are found, "
        "use an empty array [] — do NOT invent generic positives "
        "(e.g. 'clean layout', 'legible typography') just to fill the field.\n"
        f"- weaknesses: list only substantive gaps visible in the work. Use an empty array [] when score is {shortlist_min}-5 and no real weaknesses are found — do NOT invent gaps.\n"
        f"- next_level_delta: when score is {shortlist_min}-5 and no substantive gaps remain, use exactly: "
        f"\"{shortlist_msg}\". Otherwise give one actionable improvement.\n"
        "- Include a brief role-fit note for the applied role.\n\n"
        f"{_response_json_schema(candidate_role)}"
    )
    if framework:
        prompt = _framework_preamble(framework) + prompt
        logger.info(
            "brand_framework_injected",
            extra={
                "framework_chars": len(framework),
                "prompt_chars": len(prompt),
            },
        )

    target_screenshots = screenshot_paths[:6]
    parts = [types.Part.from_text(text=prompt)]
    for path in target_screenshots:
        b64 = encode_image(path)
        if b64:
            parts.append(
                types.Part.from_bytes(
                    data=base64.b64decode(b64),
                    mime_type="image/jpeg",
                )
            )

    sufficient, sufficiency_reasons = assess_capture_quality(
        screenshot_paths,
        case_study_text,
        page=None,
        used_fallback_selector=used_fallback_selector,
        page_url=page_url,
        behance_wall_marker=behance_wall_marker,
    )
    if not sufficient:
        return {
            "insufficient_content": True,
            "reasons": sufficiency_reasons,
            "model": model_name,
        }

    print(f"🤖 Using model: {model_name}")
    try:
        response = genai_client.models.generate_content(
            model=_model_name(),
            contents=parts,
            config=types.GenerateContentConfig(temperature=0),
        )
        text = response.text
        if not text:
            return {"error": "Empty response from model", "score": 0, "model": model_name}
        out = _parse_json_from_response(text)
        if out is None:
            return {"error": "Could not parse model response as JSON", "score": 0, "model": model_name}
        # Normalize common fields so downstream summary can stay structured.
        if not isinstance(out.get("quality_indicators"), list):
            out["quality_indicators"] = []
        if not isinstance(out.get("weaknesses"), list):
            out["weaknesses"] = []
        if not isinstance(out.get("role_fit_note"), str):
            out["role_fit_note"] = ""
        if not isinstance(out.get("next_level_delta"), str):
            out["next_level_delta"] = ""
        from app.settings import get_settings

        settings = get_settings()
        shortlist_msg = str(settings.get("shortlist_ready_message", "")).strip().lower()
        shortlist_min = float(settings.get("shortlist_min_score", 4))
        try:
            score_val = float(out.get("score", 0))
            if score_val < shortlist_min and (out.get("next_level_delta") or "").strip().lower() == shortlist_msg:
                out["next_level_delta"] = (
                    "Address the substantive gaps above to reach the next score band."
                )
        except (TypeError, ValueError):
            pass
        craft = out.get("craft_quality")
        try:
            out["craft_quality"] = max(1, min(5, int(float(craft)))) if craft is not None else None
        except (TypeError, ValueError):
            out["craft_quality"] = None
        conf = str(out.get("confidence", "")).strip().lower()
        out["confidence"] = conf if conf in {"high", "medium", "low"} else "low"
        out["model"] = model_name
        out = apply_visual_polish_guard_cap(out, candidate_role)
        out = filter_low_score_strengths(out)
        return out
    except Exception as e:
        print(f"  ⚠️ Model failed: {e}")
        return {"error": str(e), "score": 0, "model": model_name}

if __name__ == "__main__":
    pass
