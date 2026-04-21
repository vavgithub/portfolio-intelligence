import base64
import json
import os
import re
import tempfile

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Brand Identity expert framework (v2) — injected into prompt for Brand Designer role
_framework_path = os.path.join(os.path.dirname(__file__), "brand_identity_expert_framework_v2.txt")
try:
    with open(_framework_path, encoding="utf-8") as f:
        BRAND_IDENTITY_EXPERT_FRAMEWORK = f.read()
except Exception:
    BRAND_IDENTITY_EXPERT_FRAMEWORK = ""

# Single source of truth for report JSON (gemini-2.0-flash deprecated; use 2.5-flash)
AI_MODEL_NAME = "gemini-2.5-flash"

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
    """All roles use Gemini; Brand Designer gets expert framework injected into prompt."""
    return AI_MODEL_NAME

def get_max_workers():
    """Concurrency for Gemini (sequential recommended for free tier)."""
    return 3


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

def analyze_portfolio_visuals(screenshot_paths, project_title, case_study_text="", design_specs=None, candidate_role=None):
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
        "If any answer is NO or UNCLEAR — score is capped at 2. No exceptions.\n\n"
        "2. DECORATIVE TEXT READING:\n"
        "When you encounter stylized, hand-lettered, or decorative typography in the work,\n"
        "treat it as readable content — not just a visual element.\n"
        "It likely contains the brand name, tagline, or campaign message.\n"
        "Reading it correctly is essential to understanding the strategic intent of the work.\n\n"
        "PORTFOLIO EVALUATION RULE: Evaluate the candidate holistically across all projects provided. "
        "A strong portfolio requires depth in 1-2 projects, not every project. If the candidate demonstrates "
        "research, problem framing, or process in at least one project, do not penalise other projects for lacking this. "
        "A polished final mockup without wireframes can still score 3 if the visual execution is strong.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ROLE-SPECIFIC EVALUATION CRITERIA\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
        "template animations, no original motion design.\n\n"
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
        "no print or editorial work, no typographic intention.\n\n"
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
        "- Include at least 1 concrete strength and 1 concrete gap tied to what is visible.\n"
        "- Include a brief role-fit note for the applied role.\n"
        "- Keep comments actionable: say what is missing to reach the next score band.\n\n"
        "Return ONLY strict JSON with these fields:\n"
        "{\n"
        '  "design_category": "Brand Identity / UI/UX / Motion / Graphic / Illustration",\n'
        '  "quality_indicators": ["specific strength 1", "specific strength 2"],\n'
        '  "weaknesses": ["specific weakness 1", "specific weakness 2"],\n'
        '  "role_fit_note": "1 sentence on fit for the applied role",\n'
        '  "confidence": "high/medium/low",\n'
        '  "logic_alignment": 1-10,\n'
        '  "responsiveness_score": 1-10,\n'
        '  "visual_consistency": 1-10,\n'
        '  "seniority": "junior/mid/senior/lead",\n'
        '  "score": 1-5,\n'
        '  "reasoning": "2-3 sentences citing specific evidence from what you see",\n'
        '  "next_level_delta": "1 sentence on what must improve to reach next score band"\n'
        "}"
    )
    if candidate_role and "brand" in candidate_role.lower() and BRAND_IDENTITY_EXPERT_FRAMEWORK:
        prompt = BRAND_IDENTITY_EXPERT_FRAMEWORK.strip() + "\n\n" + prompt

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

    print(f"🤖 Using model: {model_name}")
    try:
        response = genai_client.models.generate_content(
            model=AI_MODEL_NAME,
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
        conf = str(out.get("confidence", "")).strip().lower()
        out["confidence"] = conf if conf in {"high", "medium", "low"} else "medium"
        out["model"] = model_name
        return out
    except Exception as e:
        print(f"  ⚠️ Model failed: {e}")
        return {"error": str(e), "score": 0, "model": model_name}

if __name__ == "__main__":
    pass
