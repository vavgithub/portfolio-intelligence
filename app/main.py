import json
import os
import sys
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from app.browser_capture import PortfolioBrowser
from app.analyzer import analyze_portfolio_visuals, get_max_workers, AI_MODEL_NAME, genai_client
from app.scoring import aggregate_scores
from dotenv import load_dotenv

load_dotenv()

def analyze_single_project(project, design_specs=None, candidate_role=None):
    """Worker for parallel analysis."""
    print(f"  👁️ Analyzing (AI): {project['title']}")
    analysis = analyze_portfolio_visuals(
        project['screenshots'], 
        project['title'], 
        project.get('case_study_text', ""),
        design_specs=design_specs,
        candidate_role=candidate_role
    )
    analysis['project_title'] = project['title']
    if "error" in analysis:
        print(f"  ❌ AI Error for {project['title']}: {analysis['error']}")
    else:
        print(f"  ✅ AI Score for {project['title']}: {analysis.get('score', 'N/A')}")
    return analysis

def run_portfolio_intelligence_pipeline(url, candidate_role=None):
    print(f"🌟 Starting Portfolio Intelligence Pipeline for: {url}")
    
    # Generate unique run ID (suffix)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    
    pb = PortfolioBrowser()
    
    # Stage 1-3: Browser Pipeline (run_id, candidate_role, genai_client for Brand Designer AI project selection)
    metadata, projects, run_id = pb.full_pipeline_scan(
        url, run_id=timestamp, candidate_role=candidate_role, genai_client=genai_client
    )
    
    if metadata.get("skipped"):
        print("\n" + "✨" * 5 + " CANDIDATE INSIGHT " + "✨" * 5)
        print(f"👤 Name: {metadata.get('name', 'N/A')}")
        print(f"⏭️ Status: Cannot evaluate — {metadata.get('skip_reason', 'skipped')}")
        print(f"⭐ Quality Score: — (not scored)")
        print(f"🎯 Recommendation: Route to human review")
        print("✨" * 21 + "\n")
        return {
            "model_used": None,
            "candidate_identity": metadata,
            "visual_analysis_results": [],
            "final_scorecard": {
                "average_quality_score": None,
                "seniority_estimate": "Unknown",
                "hire_recommendation": "Route to human review",
                "summary_reasoning": metadata.get("skip_reason", "Skipped — route to human review."),
            },
            "run_id": run_id,
            "status": "skipped",
            "skip_reason": metadata.get("skip_reason", ""),
        }
    
    # Stage 4: Parallel Vision AI Analysis
    max_workers = get_max_workers()
    print(f"🤖 Processing projects through AI (Parallel, {max_workers} workers)...")
    
    # Use partial to pass global design specs and candidate role to all project workers
    worker_fn = partial(analyze_single_project, design_specs=metadata.get("design_specs"), candidate_role=candidate_role)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        project_analyses = list(executor.map(worker_fn, projects))
        
    # Stage 5: Scoring & Output
    print("📊 Aggregating and finalizing report...")
    final_scorecard = aggregate_scores(project_analyses)
    
    # Live Terminal Summary
    print("\n" + "✨" * 5 + " CANDIDATE INSIGHT " + "✨" * 5)
    print(f"👤 Name: {metadata.get('name', 'N/A')}")
    print(f"🎓 Seniority: {final_scorecard.get('seniority_estimate', 'N/A')}")
    print(f"⭐ Quality Score: {final_scorecard.get('average_quality_score', 0)}/5")
    print(f"🎯 Recommendation: {final_scorecard.get('hire_recommendation', 'N/A')}")
    print(f"📝 Summary: {final_scorecard.get('summary_reasoning', 'N/A')}")
    print("✨" * 21 + "\n")
    
    return {
        "model_used": AI_MODEL_NAME,
        "candidate_identity": metadata,
        "visual_analysis_results": project_analyses,
        "final_scorecard": final_scorecard,
        "run_id": run_id
    }

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://www.behance.net/live"
    candidate_role = (sys.argv[2].strip() or None) if len(sys.argv) > 2 else None
    try:
        final_report = run_portfolio_intelligence_pipeline(target, candidate_role=candidate_role)
        output_file = f"report_{final_report['run_id']}.json"
        with open(output_file, "w") as f:
            json.dump(final_report, f, indent=4)
        print(f"✅ Analysis Complete. Full report saved to {output_file}")
    except Exception as e:
        print(f"❌ Pipeline Failed: {e}")