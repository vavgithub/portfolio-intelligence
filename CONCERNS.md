# Portfolio Intelligence — Known Concerns & Status

Fresh-eyes audit of issues that affect scoring consistency and accuracy.

---

## 1. Project title detection (Behance) — **Addressed**

**Problem:** Every project was showing as "Visual Project"; AI had no project context.

**Status:** Fixed in `browser_capture.py`:
- Try multiple Behance selectors: `aria-label`, `[class*='ProjectCoverNeue-title']`, `[class*='ProjectCover-title']`, `h2`, `[class*='title']`.
- Fallback: parse title from Behance URL slug (e.g. `.../gallery/123/Event-Booking-App` → "Event Booking App").
- AI prompt already receives `project_title`; it now gets the real name when discovery works.

---

## 2. Behance lazy loading — **Addressed**

**Problem:** Playwright scrolls programmatically; Behance loads images on viewport entry. Screenshots could show grey placeholders.

**Status:** In `snapshot_project`, for Behance URLs we now:
- Do a **warm-up scroll**: scroll to bottom, then back to top, with short pauses, before taking any screenshots.
- This triggers lazy load so images are in DOM when we run the capped screenshot loop (plus existing image-wait + networkidle per position).

---

## 3. Behance login wall — **Not addressed (limitation)**

**Problem:** Some projects require login; unauthenticated runs only see preview content. Same portfolio, logged in vs not = different content.

**Status:** No login/session support. Pipeline runs unauthenticated. Options for later:
- Skip or flag URLs that show a login/sign-in wall (e.g. detect "Sign in" in body).
- Optional: support authenticated context (cookies/session) for internal reviewers.

---

## 4. Wrong aggregation — **Addressed**

**Problem:** Best-2-of-N inflated portfolios (e.g. 3+2+2 → 2.5 → Shortlist) when the strict mean (2.33) should stay Pass.

**Status:** Changed in `scoring.py`:
- **Strict mean:** `average_quality_score` is the arithmetic mean of all valid project scores (rounded to two decimals). No dropping lows or averaging only the top two.

---

## 5. AI has no reference point (RAG) — **Not addressed (future work)**

**Problem:** AI scores in a vacuum; humans calibrate against many portfolios. Showing 2–3 examples of score-4 work would anchor the model.

**Status:** No RAG or few-shot examples in the prompt yet. Potential approach:
- Curate 2–3 reference portfolios (or summary + screenshots) with known human scores (e.g. 4).
- Inject short “example of score-4 work” descriptions or thumbnails into the prompt before the current project.
- Requires a small reference dataset and prompt/pipeline changes.

---

## 6. Google Drive folders (~133 candidates) — **Option A (skip)**

**Problem:** Drive requires login in headless mode; the pipeline was snapshotting the login page and AI was scoring it as design work (score 1). We must not score Google sign-in UI.

**Status (Option A — implemented):**
- **Detect Drive URLs:** `drive.google.com/drive/folders/` or `drive.google.com/file/` → platform `google_drive`.
- **Public folders:** If the folder is shared publicly, we scrape the folder page for links to `/file/d/FILE_ID` and build view URLs (`/file/d/ID/view`). We snapshot each file’s preview page and send those screenshots to Gemini.
- **Login wall:** If the folder requires sign-in, the initial page will show “Sign in”; we skip the file list and treat the folder URL as a single “project” (one snapshot of the login/empty state). No Google API or credentials are used.
- **Worth checking:** For the 133 Drive candidates, determine how many are public vs private; private links need Google API credentials or manual export to be useful.

---

## Summary

| Concern              | Status    | Notes                                      |
|----------------------|-----------|--------------------------------------------|
| Project title        | Addressed | Better selectors + URL slug fallback       |
| Behance lazy load    | Addressed | Warm-up scroll before capture              |
| Login wall           | Open      | Documented; no auth support                 |
| Aggregation          | Addressed | Strict mean of valid project scores         |
| RAG / score examples | Open      | Documented; needs reference set + prompt   |
| Google Drive         | Skip (A)  | No screenshot/score; route to human review; API option later |
