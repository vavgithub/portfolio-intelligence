/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SHEET_ENDPOINT?: string;
  /** "1" = Rudra, "2" = Kshitija, "3" = unassigned batch; omit = legacy portfolios.json + ai_lookup.json */
  readonly VITE_REVIEWER_SLOT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
