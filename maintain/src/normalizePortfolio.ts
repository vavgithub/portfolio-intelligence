import type { PortfolioCard } from "./types";

/** Match Maintain rows to pipeline output (report_*.json → export_ai_lookup_from_reports.py). */
export type AiLookup = Record<string, { aiScore: number; aiReasoning: string }>;

export function normalizePortfolioUrl(url: string): string {
  const s = url.trim();
  if (!s) return "";
  try {
    const u = new URL(s);
    u.hash = "";
    let out = u.toString();
    if (out.endsWith("/")) out = out.slice(0, -1);
    return out.toLowerCase();
  } catch {
    return s.replace(/\/$/, "").toLowerCase();
  }
}

type RawRow = Record<string, unknown>;

function num(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/**
 * Maps Geode-style JSON (`score`, `comment`) + optional camel/snake AI fields,
 * then overlays `ai_lookup.json` by normalized portfolio URL.
 */
export function normalizePortfolioRow(raw: RawRow, lookup: AiLookup): PortfolioCard {
  const portfolio = str(raw.portfolio);
  const key = normalizePortfolioUrl(portfolio);
  const fromLookup = key ? lookup[key] : undefined;

  const originalScore = num(raw.originalScore) ?? num(raw.score) ?? 0;

  const aiFromRow =
    num(raw.aiScore) ??
    num(raw.ai_score) ??
    (fromLookup != null ? fromLookup.aiScore : null);

  const aiReasoning =
    str(raw.aiReasoning) ||
    str(raw.ai_reasoning) ||
    str(raw.summary_reasoning) ||
    (fromLookup != null ? fromLookup.aiReasoning : "");

  return {
    name: str(raw.name) || "—",
    portfolio,
    jobProfile: str(raw.jobProfile) || "—",
    reviewer: str(raw.reviewer),
    originalScore,
    aiScore: aiFromRow,
    aiReasoning,
  };
}
