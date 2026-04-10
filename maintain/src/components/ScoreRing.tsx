import { motion } from "framer-motion";

function ringColor(score: number): string {
  const t = Math.min(5, Math.max(1, score));
  const hue = 0 + (150 * (t - 1)) / 4;
  const sat = 70 + (t - 1) * 2;
  const light = t < 2.5 ? 58 : 52;
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}

type Props = { score: number | null; size?: number };

export function ScoreRing({ score, size = 120 }: Props) {
  const r = (size - 12) / 2;
  const c = 2 * Math.PI * r;

  if (score == null || Number.isNaN(score)) {
    return (
      <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90 transform">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke="#27272a"
            strokeWidth={4}
            strokeDasharray="6 8"
          />
        </svg>
        <span
          className="font-mono absolute text-2xl font-semibold tabular-nums tracking-tight text-zinc-500"
          style={{ fontSize: size * 0.22 }}
        >
          —
        </span>
      </div>
    );
  }

  const pct = Math.min(5, Math.max(0, score)) / 5;
  const offset = c * (1 - pct);
  const stroke = ringColor(score);

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90 transform">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#27272a"
          strokeWidth={4}
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={stroke}
          strokeWidth={4}
          strokeLinecap="butt"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: offset }}
          transition={{ type: "spring", stiffness: 80, damping: 20 }}
        />
      </svg>
      <span
        className="font-mono absolute text-4xl font-semibold tabular-nums tracking-tight text-zinc-100"
        style={{ fontSize: size * 0.28 }}
      >
        {score}
      </span>
    </div>
  );
}
