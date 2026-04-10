def _numeric_score(res):
    """Extract a single numeric score from a result (handles int, list, str)."""
    s = res.get("score", 0)
    if isinstance(s, list):
        return int(s[0]) if s else 0
    if isinstance(s, str):
        import re
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) if m else 0
    return int(s) if s else 0


def aggregate_scores(project_results):
    """
    Aggregates individual project analysis into a candidate-level scorecard.
    """
    if not project_results:
        return {
            "specialization_split": {},
            "average_quality_score": 0,
            "seniority_estimate": "Unknown",
            "top_standout_projects": [],
            "hire_recommendation": "Pass",
            "summary_reasoning": "No projects found or analysis failed."
        }

    categories = {}
    total_score = 0
    seniority_counts = {"junior": 0, "mid": 0, "senior": 0}
    standout_projects = []
    weakness_counts = {}
    strength_counts = {}
    next_level_notes = []

    # Successful results only for scoring
    valid_results = [r for r in project_results if "error" not in r and _numeric_score(r) > 0]
    total_valid = len(valid_results)
    
    for res in project_results:
        # Category/Specialization (still track for all if possible)
        cat = res.get("design_category") or res.get("Design category") or "Unknown"
        if isinstance(cat, list):
            cat = ", ".join(str(c) for c in cat) if cat else "Unknown"
        categories[cat] = categories.get(cat, 0) + 1
        
        # Scoring
        score = _numeric_score(res)
        
        # Seniority
        sen = (res.get("seniority") or res.get("Seniority signal") or "").lower()
        for level in seniority_counts:
            if level in sen:
                seniority_counts[level] += 1
        
        # Standout projects
        reasoning = res.get("reasoning") or res.get("Overall project score reasoning") or ""
        for w in (res.get("weaknesses") or []):
            if isinstance(w, str) and w.strip():
                key = w.strip()
                weakness_counts[key] = weakness_counts.get(key, 0) + 1
        for q in (res.get("quality_indicators") or []):
            if isinstance(q, str) and q.strip():
                key = q.strip()
                strength_counts[key] = strength_counts.get(key, 0) + 1
        delta = (res.get("next_level_delta") or "").strip()
        if delta:
            next_level_notes.append(delta)
        standout_projects.append({
            "title": res.get("project_title", "Untitled"),
            "score": score,
            "category": cat,
            "reasoning": reasoning,
            "role_fit_note": (res.get("role_fit_note") or "").strip(),
            "confidence": (res.get("confidence") or "medium"),
            "next_level_delta": delta,
            "strengths": [x for x in (res.get("quality_indicators") or []) if isinstance(x, str)],
            "weaknesses": [x for x in (res.get("weaknesses") or []) if isinstance(x, str)],
            "error": res.get("error")
        })

    # Sort and pick top 3
    standout_projects.sort(key=lambda x: x['score'], reverse=True)
    top_standouts = standout_projects[:3]

    # Specialization percentage (based on total discovered)
    total_discovered = len(project_results)
    specialization = {cat: round((count / total_discovered) * 100, 2) for cat, count in categories.items()}
    
    # Final Seniority Estimate
    seniority_estimate = max(seniority_counts, key=seniority_counts.get).capitalize()
    
    # Portfolio score: strict mean of all valid project scores (no best-2 uplift).
    # e.g. 3 + 2 + 2 → 7/3 ≈ 2.33 → Pass (< Shortlist threshold).
    if total_valid > 0:
        scores = [_numeric_score(r) for r in valid_results]
        consistency_score = round(sum(scores) / len(scores), 2)
    else:
        consistency_score = 0
    
    # Recommendation (1–5 scale: Hire >=4, Shortlist >=3, else Pass)
    recommendation = "Pass"
    if consistency_score >= 4:
        recommendation = "Hire"
    elif consistency_score >= 3:
        recommendation = "Shortlist"
        
    top_strengths = [k for k, _ in sorted(strength_counts.items(), key=lambda x: x[1], reverse=True)[:3]]
    top_gaps = [k for k, _ in sorted(weakness_counts.items(), key=lambda x: x[1], reverse=True)[:3]]
    next_step = next_level_notes[0] if next_level_notes else "Increase strategic depth and consistency across core projects."
    primary_focus = max(categories, key=categories.get) if categories else "N/A"
    summary_parts = [
        f"Role-fit summary: {seniority_estimate} level with primary focus on {primary_focus}.",
        f"Strengths: {'; '.join(top_strengths) if top_strengths else 'No clear repeated strengths captured.'}",
        f"Gaps: {'; '.join(top_gaps) if top_gaps else 'No repeated critical gaps captured.'}",
        f"To reach next level: {next_step}",
    ]
    return {
        "specialization_split": specialization,
        "average_quality_score": consistency_score,
        "seniority_estimate": seniority_estimate,
        "top_standout_projects": top_standouts,
        "hire_recommendation": recommendation,
        "summary_reasoning": " ".join(summary_parts) + f" (Based on {total_valid}/{total_discovered} analyzed projects)"
    }
