export interface PortfolioCard {
  name: string;
  portfolio: string;
  jobProfile: string;
  reviewer: string;
  originalScore: number;
  /** From JSON or ai_lookup; null if no pipeline run matched this URL. */
  aiScore: number | null;
  aiReasoning: string;
}

export interface ReviewRecord {
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
  action: "confirm" | "override";
}
