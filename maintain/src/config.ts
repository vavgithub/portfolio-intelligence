const slot = import.meta.env.VITE_REVIEWER_SLOT ?? "";
/** Isolates drafts per batch (1 / 2 / 3 / legacy). */
export const STORAGE_KEY = `maintain-review-state-v2${slot ? `-${slot}` : ""}`;

/** Body sent to Google Apps Script `doPost` (JSON stringified, posted as text/plain to avoid CORS preflight). */
export interface ReviewPayload {
  timestamp: string;
  candidateName: string;
  portfolioUrl: string;
  jobProfile: string;
  originalReviewer: string;
  originalScore: number;
  aiScore: number | null;
  aiReasoning: string;
  khushiScore: number | null;
  khushiComment: string;
  agreement: boolean;
}

/**
 * Apps Script web apps block normal browser CORS. Use no-cors + text/plain so no OPTIONS preflight;
 * the row still appends; the response is opaque so we treat success optimistically.
 */
export async function postToSheet(
  data: ReviewPayload
): Promise<{ status: "ok" | "local" }> {
  const url = (import.meta.env.VITE_SHEET_ENDPOINT ?? "").trim();

  if (!url || !url.startsWith("http")) {
    console.warn("No sheet endpoint set");
    return { status: "local" };
  }

  await fetch(url, {
    method: "POST",
    mode: "no-cors",
    headers: { "Content-Type": "text/plain" },
    body: JSON.stringify(data),
  });

  return { status: "ok" };
}
