import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import portfoliosLegacy from "./data/portfolios.json";
import aiLookupLegacy from "./data/ai_lookup.json";
import portfoliosReviewer1 from "./data/portfolios_reviewer1.json";
import aiLookupReviewer1 from "./data/ai_lookup_reviewer1.json";
import portfoliosReviewer2 from "./data/portfolios_reviewer2.json";
import aiLookupReviewer2 from "./data/ai_lookup_reviewer2.json";
import portfoliosUnassigned from "./data/portfolios_unassigned.json";
import aiLookupUnassigned from "./data/ai_lookup_unassigned.json";

/** VITE_REVIEWER_SLOT=1 Rudra, =2 Kshitija, =3 unassigned batch (size from JSON); unset uses portfolios.json + ai_lookup.json (legacy). */
const slot = import.meta.env.VITE_REVIEWER_SLOT;
const portfoliosRaw =
  slot === "3"
    ? portfoliosUnassigned
    : slot === "2"
      ? portfoliosReviewer2
      : slot === "1"
        ? portfoliosReviewer1
        : portfoliosLegacy;
const aiLookup =
  slot === "3"
    ? aiLookupUnassigned
    : slot === "2"
      ? aiLookupReviewer2
      : slot === "1"
        ? aiLookupReviewer1
        : aiLookupLegacy;
import { postToSheet, STORAGE_KEY, type ReviewPayload } from "./config";
import { normalizePortfolioRow, type AiLookup } from "./normalizePortfolio";
import type { ReviewRecord } from "./types";
import { ScoreRing } from "./components/ScoreRing";

declare global {
  interface Window {
    confetti?: (opts?: Record<string, unknown>) => void;
  }
}

type DraftAnswer = {
  score: number | null;
  comment: string;
};

type PersistedDraft = {
  currentIndex: number;
  answers: DraftAnswer[];
  submitted: boolean;
};

const INCLUDED_REVIEWERS = new Set(["Rudra Sindwani", "Kshitija Chavan"]);
const WEEKLY_LIMIT = 10;
/** Minimum trimmed characters so reviewers justify each score (not optional). */
const MIN_COMMENT_LENGTH = 40;

function payloadFromRecord(r: ReviewRecord): ReviewPayload {
  return {
    timestamp: r.timestamp,
    candidateName: r.candidateName,
    portfolioUrl: r.portfolioUrl,
    jobProfile: r.jobProfile,
    originalReviewer: r.originalReviewer,
    originalScore: r.originalScore,
    aiScore: r.aiScore,
    aiReasoning: r.aiReasoning,
    khushiScore: r.khushiScore,
    khushiComment: r.khushiComment,
    agreement: r.agreement,
  };
}

function loadDraft(total: number): PersistedDraft {
  const emptyAnswers = Array.from({ length: total }, () => ({ score: null, comment: "" }));
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { currentIndex: 0, answers: emptyAnswers, submitted: false };
    const parsed = JSON.parse(raw) as Partial<PersistedDraft>;
    const savedAnswers = Array.isArray(parsed.answers) ? parsed.answers : [];
    const answers = emptyAnswers.map((_, i) => {
      const s = savedAnswers[i];
      return {
        score: typeof s?.score === "number" ? s.score : null,
        comment: typeof s?.comment === "string" ? s.comment : "",
      };
    });
    return {
      currentIndex:
        typeof parsed.currentIndex === "number"
          ? Math.min(Math.max(0, parsed.currentIndex), Math.max(0, total - 1))
          : 0,
      answers,
      submitted: Boolean(parsed.submitted),
    };
  } catch {
    return { currentIndex: 0, answers: emptyAnswers, submitted: false };
  }
}

function saveDraft(draft: PersistedDraft) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
}

function scoreRingHue(score: number | null): string {
  if (score == null || Number.isNaN(score)) return "hsl(240, 5%, 45%)";
  const t = Math.min(5, Math.max(1, score));
  const hue = 0 + (150 * (t - 1)) / 4;
  const sat = 70 + (t - 1) * 2;
  const light = t < 2.5 ? 58 : 52;
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}

function commentMeetsBar(comment: string): boolean {
  return comment.trim().length >= MIN_COMMENT_LENGTH;
}

function incompleteRationaleSummary(
  answers: DraftAnswer[],
  cardsLen: number,
  minLen: number
): string {
  const parts: string[] = [];
  for (let i = 0; i < cardsLen; i += 1) {
    const a = answers[i];
    if (!a) {
      parts.push(`#${i + 1}: no data`);
      continue;
    }
    if (a.score === null) parts.push(`#${i + 1}: pick score 1–5`);
    else if (!commentMeetsBar(a.comment)) {
      const n = a.comment.trim().length;
      parts.push(`#${i + 1}: rationale too short (${n}/${minLen} chars)`);
    }
  }
  if (!parts.length) return "";
  const head = parts.slice(0, 4).join(" · ");
  return parts.length > 4 ? `${head} · …` : head;
}

type StructuredAiComment = {
  roleFit: string;
  strengths: string;
  gaps: string;
  nextLevel: string;
  raw: string;
};

function parseStructuredAiComment(text: string): StructuredAiComment | null {
  const raw = text.trim();
  if (!raw) return null;
  const roleFit = raw.match(/Role-fit summary:\s*([^]+?)(?=\sStrengths:|$)/i)?.[1]?.trim() ?? "";
  const strengths = raw.match(/Strengths:\s*([^]+?)(?=\sGaps:|$)/i)?.[1]?.trim() ?? "";
  const gaps = raw.match(/Gaps:\s*([^]+?)(?=\sTo reach next level:|$)/i)?.[1]?.trim() ?? "";
  const nextLevel = raw.match(/To reach next level:\s*([^]+?)(?=\s*\(Based on|\s*$)/i)?.[1]?.trim() ?? "";

  if (!roleFit && !strengths && !gaps && !nextLevel) return null;
  return { roleFit, strengths, gaps, nextLevel, raw };
}

export default function App() {
  const cards = useMemo(() => {
    const lookup = aiLookup as AiLookup;
    return (portfoliosRaw as Record<string, unknown>[])
      .map((row) => normalizePortfolioRow(row, lookup))
      .filter(
        (p) =>
          !p.reviewer.trim() ||
          INCLUDED_REVIEWERS.has(p.reviewer)
      )
      .slice(0, WEEKLY_LIMIT);
  }, []);

  const [answers, setAnswers] = useState<DraftAnswer[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  useEffect(() => {
    const draft = loadDraft(cards.length);
    setAnswers(draft.answers);
    setCurrentIndex(draft.currentIndex);
    setSubmitted(draft.submitted);
    setHydrated(true);
  }, [cards.length]);

  useEffect(() => {
    if (!hydrated) return;
    saveDraft({ currentIndex, answers, submitted });
  }, [answers, currentIndex, submitted, hydrated]);

  const total = cards.length;
  const active = total > 0 ? cards[currentIndex] : null;
  const activeAiComment = useMemo(
    () => (active?.aiReasoning ? parseStructuredAiComment(active.aiReasoning) : null),
    [active?.aiReasoning]
  );
  const complete = submitted;
  const completeCount = answers.filter((a) => a.score !== null && commentMeetsBar(a.comment)).length;
  const allAnswered = total > 0 && completeCount === total;
  const progressPct = total ? Math.min(100, (completeCount / total) * 100) : 0;

  const showToast = useCallback((msg: string, ok: boolean) => {
    setToast({ msg, ok });
    window.setTimeout(() => setToast(null), 3200);
  }, []);

  const fireConfetti = useCallback(() => {
    const c = window.confetti;
    if (typeof c === "function") {
      c({
        particleCount: 140,
        spread: 86,
        origin: { y: 0.65 },
        colors: ["#6ee7b7", "#34d399", "#a7f3d0"],
      });
    }
  }, []);

  const setScore = (idx: number, score: number) => {
    setAnswers((prev) => prev.map((a, i) => (i === idx ? { ...a, score } : a)));
  };

  const setComment = (idx: number, comment: string) => {
    setAnswers((prev) => prev.map((a, i) => (i === idx ? { ...a, comment } : a)));
  };

  const nextCard = useCallback(() => {
    setCurrentIndex((i) => Math.min(Math.max(0, total - 1), i + 1));
  }, [total]);

  const prevCard = useCallback(() => {
    setCurrentIndex((i) => Math.max(0, i - 1));
  }, []);

  const submitAll = useCallback(async () => {
    if (submitting) return;
    if (!allAnswered) {
      showToast(
        `Each portfolio needs a score (1–5) and a written rationale (at least ${MIN_COMMENT_LENGTH} characters).`,
        false
      );
      return;
    }

    setSubmitting(true);
    let failed = 0;
    let localMode = false;
    try {
      for (let i = 0; i < cards.length; i += 1) {
        const card = cards[i];
        const ans = answers[i];
        if (!card || !ans || ans.score === null) {
          failed += 1;
          continue;
        }
        const record: ReviewRecord = {
          timestamp: new Date().toISOString(),
          candidateName: card.name,
          portfolioUrl: card.portfolio,
          jobProfile: card.jobProfile,
          originalReviewer: card.reviewer,
          originalScore: card.originalScore,
          aiScore: card.aiScore,
          aiReasoning: card.aiReasoning,
          khushiScore: ans.score,
          khushiComment: ans.comment.trim(),
          agreement: card.aiScore != null && ans.score === card.aiScore,
          action: "override",
        };

        try {
          const result = await postToSheet(payloadFromRecord(record));
          if (result.status === "local") localMode = true;
        } catch {
          failed += 1;
        }
      }

      if (failed > 0) {
        showToast(`Retry — ${failed} response(s) not saved`, false);
      } else {
        showToast(localMode ? "Saved locally (set VITE_SHEET_ENDPOINT)" : "Saved ✓", true);
        setSubmitted(true);
        fireConfetti();
      }
    } finally {
      setSubmitting(false);
    }
  }, [submitting, allAnswered, cards, answers, showToast, fireConfetti]);

  const submitExistingAgain = useCallback(async () => {
    if (submitting || !allAnswered) {
      showToast("Complete all ratings first, then retry submit.", false);
      return;
    }
    setSubmitted(false);
    await submitAll();
  }, [submitting, allAnswered, submitAll, showToast]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (submitting || complete || !active) return;
      const t = e.target as HTMLElement;
      const typing = t.tagName === "TEXTAREA" || t.tagName === "INPUT";
      if (typing) return;

      if (e.key >= "1" && e.key <= "5") {
        e.preventDefault();
        setScore(currentIndex, Number(e.key));
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        nextCard();
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        prevCard();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        if (currentIndex === total - 1) {
          void submitAll();
        } else {
          nextCard();
        }
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submitting, complete, active, currentIndex, total, nextCard, prevCard, submitAll]);

  const summary = useMemo(() => {
    if (!answers.length) return null;
    const rated = answers.filter((a) => a.score !== null).length;
    const agreements = answers.reduce((acc, ans, i) => {
      if (ans.score === null || !cards[i] || cards[i].aiScore === null) return acc;
      return acc + (ans.score === cards[i].aiScore ? 1 : 0);
    }, 0);
    const compared = answers.reduce((acc, ans, i) => {
      if (ans.score === null || !cards[i] || cards[i].aiScore === null) return acc;
      return acc + 1;
    }, 0);
    const overrides = compared - agreements;
    const deltas: number[] = [];
    for (let i = 0; i < answers.length; i += 1) {
      const c = cards[i];
      if (answers[i].score != null && c && c.aiScore !== null) {
        deltas.push(Math.abs((answers[i].score as number) - c.aiScore));
      }
    }
    let mostRange = "—";
    if (deltas.length) {
      const buckets = [0, 0, 0, 0, 0];
      for (const d of deltas) {
        const idx = Math.min(4, Math.max(0, Math.floor(d)));
        buckets[idx] += 1;
      }
      const maxIdx = buckets.indexOf(Math.max(...buckets));
      mostRange = maxIdx === 0 ? "0 (exact)" : `±${maxIdx}`;
    }
    return { agreements, overrides, mostRange, total: rated };
  }, [answers, cards]);

  if (!hydrated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0a0a0a] text-zinc-500">
        Loading…
      </div>
    );
  }

  return (
    <div className="relative flex min-h-screen flex-col bg-[#0a0a0a]">
      <header className="flex shrink-0 items-center justify-between border-b border-zinc-800/80 px-6 py-4">
        <span className="text-sm font-semibold tracking-tight text-zinc-100">Pulse</span>
        {!complete && active && (
          <span className="font-mono text-xs tabular-nums text-zinc-500">
            {currentIndex + 1} / {total}
          </span>
        )}
        {complete && <span className="font-mono text-xs text-emerald-400/90">Complete</span>}
      </header>

      <div className="h-px w-full bg-zinc-800">
        <motion.div
          className="h-px bg-emerald-400/90"
          initial={false}
          animate={{ width: `${progressPct}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 24 }}
        />
      </div>

      <main className="flex min-h-0 flex-1 flex-col items-center justify-center px-4 py-8">
        <AnimatePresence mode="wait">
          {complete && summary ? (
            <motion.div
              key="done"
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              className="w-full max-w-lg space-y-8 text-center"
            >
              <h2 className="text-2xl font-semibold tracking-tight text-zinc-100">Submitted</h2>
              <div className="space-y-2 text-left text-sm text-zinc-400">
                <p>
                  <span className="text-zinc-500">Reviewed</span>{" "}
                  <span className="font-mono text-zinc-200">{summary.total}</span>
                </p>
                <p>
                  <span className="text-zinc-500">Agreements</span>{" "}
                  <span className="font-mono text-zinc-200">{summary.agreements}</span>
                </p>
                <p>
                  <span className="text-zinc-500">Overrides</span>{" "}
                  <span className="font-mono text-zinc-200">{summary.overrides}</span>
                </p>
                <p>
                  <span className="text-zinc-500">Most common correction</span>{" "}
                  <span className="font-mono text-zinc-200">{summary.mostRange}</span>
                </p>
              </div>
              <div className="flex flex-wrap items-center justify-center gap-3">
                <button
                  type="button"
                  disabled={submitting || !allAnswered}
                  onClick={() => void submitExistingAgain()}
                  className="min-w-[170px] border border-emerald-500/40 bg-emerald-500/10 px-5 py-3 text-sm font-medium text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-40"
                  style={{ borderRadius: 4 }}
                >
                  Submit Again
                </button>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => setSubmitted(false)}
                  className="min-w-[170px] border border-zinc-700 bg-zinc-900/50 px-5 py-3 text-sm font-medium text-zinc-200 hover:border-zinc-500 disabled:opacity-40"
                  style={{ borderRadius: 4 }}
                >
                  Re-open Responses
                </button>
              </div>
            </motion.div>
          ) : active ? (
            <motion.div
              key={active.portfolio + currentIndex}
              initial={{ opacity: 0, y: 48 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -40 }}
              transition={{ type: "spring", stiffness: 320, damping: 28 }}
              className="noise-card w-full max-w-2xl border border-zinc-800 bg-zinc-950/80 p-8 shadow-[0_0_0_1px_rgba(255,255,255,0.03)]"
              style={{ borderRadius: 4 }}
            >
              <div className="relative z-10 flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0 flex-1 space-y-4">
                  <h1 className="font-semibold tracking-tight text-zinc-50" style={{ fontSize: 24 }}>
                    {active.name}
                  </h1>
                  <span
                    className="inline-block border border-zinc-700/80 bg-zinc-900/50 px-2.5 py-1 text-xs font-medium text-zinc-400"
                    style={{ borderRadius: 4 }}
                  >
                    {active.jobProfile}
                  </span>
                  <a
                    href={active.portfolio}
                    target="_blank"
                    rel="noreferrer"
                    className="flex max-w-full items-center gap-2 border border-zinc-800 bg-black/40 px-3 py-2 font-mono text-xs text-emerald-200/90 hover:border-zinc-600"
                    style={{ borderRadius: 4 }}
                  >
                    <img
                      src="https://www.behance.net/favicon.ico"
                      alt=""
                      className="h-4 w-4 shrink-0 opacity-80"
                      width={16}
                      height={16}
                    />
                    <span className="truncate text-zinc-400">{active.portfolio}</span>
                  </a>
                </div>
                <div className="flex shrink-0 flex-col items-center gap-2">
                  <span className="text-[10px] font-medium uppercase tracking-widest text-zinc-600">
                    AI score
                  </span>
                  <ScoreRing score={active.aiScore} size={128} />
                  <span
                    className="font-mono text-[10px] tabular-nums"
                    style={{ color: scoreRingHue(active.aiScore) }}
                  >
                    ●
                  </span>
                  {active.aiScore == null && (
                    <p className="max-w-[160px] text-center text-[10px] leading-snug text-zinc-500">
                      Not in <span className="font-mono text-zinc-400">ai_lookup.json</span>—run the pipeline
                      on this URL, then regenerate the lookup.
                    </p>
                  )}
                </div>
              </div>

              <div className="relative z-10 mt-8 border-t border-zinc-800/90 pt-5">
                <h2 className="text-xs font-medium uppercase tracking-widest text-zinc-500">AI comment</h2>
                <div
                  className="mt-3 border border-zinc-800/90 bg-black/35 px-4 py-3 text-sm leading-relaxed text-zinc-300"
                  style={{ borderRadius: 4 }}
                >
                  {active.aiReasoning.trim() ? activeAiComment ? (
                    <div className="space-y-2">
                      <p>
                        <span className="text-zinc-500">Role fit:</span>{" "}
                        <span className="font-mono">{activeAiComment.roleFit || "—"}</span>
                      </p>
                      <p>
                        <span className="text-zinc-500">Strengths:</span>{" "}
                        <span className="font-mono">{activeAiComment.strengths || "—"}</span>
                      </p>
                      <p>
                        <span className="text-zinc-500">Gaps:</span>{" "}
                        <span className="font-mono">{activeAiComment.gaps || "—"}</span>
                      </p>
                      <p>
                        <span className="text-zinc-500">Next level:</span>{" "}
                        <span className="font-mono">{activeAiComment.nextLevel || "—"}</span>
                      </p>
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap font-mono">{active.aiReasoning.trim()}</p>
                  ) : (
                    <p className="text-zinc-500">
                      No AI comment yet for this URL (same reason as the score above—add a pipeline report,
                      regenerate the lookup, and refresh).
                    </p>
                  )}
                </div>
              </div>
            </motion.div>
          ) : (
            <p className="text-zinc-500">No portfolios in data.</p>
          )}
        </AnimatePresence>
      </main>

      {!complete && active && (
        <footer className="shrink-0 border-t border-zinc-800/90 bg-[#0a0a0a]/95 px-4 py-6 backdrop-blur">
          <div className="mx-auto flex max-w-2xl flex-col gap-4">
            <h3 className="text-center text-sm font-medium tracking-wide text-zinc-200">
              Your score and rationale
            </h3>
            <div className="flex flex-wrap items-center justify-center gap-2">
              {[1, 2, 3, 4, 5].map((n) => (
                <motion.button
                  key={n}
                  type="button"
                  disabled={submitting}
                  onClick={() => setScore(currentIndex, n)}
                  className={`font-mono tabular-nums transition ${
                    answers[currentIndex]?.score === n
                      ? "border border-emerald-400/80 bg-emerald-400/10 text-emerald-200"
                      : "border border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-600"
                  }`}
                  style={{ borderRadius: 4, width: 48, height: 40, fontSize: 20 }}
                  whileTap={{ scale: 0.96 }}
                  animate={answers[currentIndex]?.score === n ? { scale: [1, 1.06, 1] } : {}}
                >
                  {n}
                </motion.button>
              ))}
            </div>
            <p className="text-center text-[10px] text-zinc-600">
              <kbd className="font-mono">1–5</kbd> rate · <kbd className="font-mono">←/→</kbd>{" "}
              navigate · <kbd className="font-mono">Enter</kbd> next/submit
            </p>
            <div className="space-y-1.5">
              <label
                htmlFor="review-comment"
                className="block text-left text-xs font-medium text-zinc-400"
              >
                Why this score? <span className="text-emerald-400/90">(required)</span>
              </label>
              <textarea
                id="review-comment"
                required
                value={answers[currentIndex]?.comment ?? ""}
                onChange={(e) => setComment(currentIndex, e.target.value)}
                rows={4}
                className="w-full resize-y border border-zinc-800 bg-black/50 px-3 py-2 text-sm text-zinc-300 placeholder:text-zinc-600 focus:border-emerald-500/40 focus:outline-none"
                style={{ borderRadius: 4 }}
                placeholder="Write a short rationale: what you looked at, strengths or gaps, and why this number—not a single word."
              />
              <p className="text-left text-[10px] text-zinc-600">
                At least {MIN_COMMENT_LENGTH} characters.{" "}
                <span className="tabular-nums text-zinc-500">
                  {(answers[currentIndex]?.comment ?? "").trim().length}/{MIN_COMMENT_LENGTH}
                </span>
              </p>
            </div>

            <div className="flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                disabled={submitting || currentIndex === 0}
                onClick={prevCard}
                className="min-w-[120px] border border-zinc-700 bg-zinc-900/50 px-5 py-3 text-sm font-medium text-zinc-200 hover:border-zinc-500 disabled:opacity-40"
                style={{ borderRadius: 4 }}
              >
                Previous
              </button>
              <button
                type="button"
                disabled={submitting || currentIndex >= total - 1}
                onClick={nextCard}
                className="min-w-[120px] border border-zinc-700 bg-zinc-900/50 px-5 py-3 text-sm font-medium text-zinc-200 hover:border-zinc-500 disabled:opacity-40"
                style={{ borderRadius: 4 }}
              >
                Next
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={() => {
                  if (!allAnswered) {
                    const detail = incompleteRationaleSummary(answers, total, MIN_COMMENT_LENGTH);
                    showToast(
                      detail
                        ? `Finish every card first: ${detail}`
                        : `Each card needs a score and rationale (≥${MIN_COMMENT_LENGTH} characters).`,
                      false
                    );
                    return;
                  }
                  void submitAll();
                }}
                className={`min-w-[180px] border border-emerald-500/40 bg-emerald-500/10 px-5 py-3 text-sm font-medium text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-40 ${!allAnswered && !submitting ? "opacity-60" : ""}`}
                style={{ borderRadius: 4 }}
              >
                Submit All
              </button>
            </div>
            {!allAnswered && !submitting && (
              <p className="text-center text-[10px] leading-relaxed text-zinc-500">
                Submit runs when all {total} cards have a score and a rationale of at least {MIN_COMMENT_LENGTH}{" "}
                characters ({completeCount}/{total} ready). Tap Submit for a checklist of what is missing.
              </p>
            )}
          </div>
        </footer>
      )}

      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            className={`fixed bottom-6 left-1/2 z-50 -translate-x-1/2 border px-4 py-2 font-mono text-xs ${
              toast.ok
                ? "border-emerald-500/30 bg-zinc-950/95 text-emerald-200"
                : "border-red-500/30 bg-zinc-950/95 text-red-300"
            }`}
            style={{ borderRadius: 4 }}
          >
            {toast.msg}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

