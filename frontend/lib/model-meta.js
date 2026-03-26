export const MODEL_ORDER = ["A", "B", "C", "D"];

export const MODEL_META = {
  A: {
    id: "A",
    name: "Range Reversion",
    subtitle: "Overheat pullback",
    description: "Contrarian entries targeting mean reversion after short-term overextension.",
  },
  B: {
    id: "B",
    name: "Reclaim",
    subtitle: "Support reclaim",
    description: "Momentum-following entries after support recovery and retest confirmation.",
  },
  C: {
    id: "C",
    name: "Breakout",
    subtitle: "Volatility expansion",
    description: "Trend-following model for volatility expansion and directional continuation.",
  },
  D: {
    id: "D",
    name: "Reset Bounce",
    subtitle: "Crash rebound",
    description: "Technical rebound model after panic-style drops and oversold extremes.",
  },
};

export function getModelMeta(modelId) {
  const key = String(modelId || "").trim().toUpperCase();
  return (
    MODEL_META[key] || {
      id: key || "-",
      name: `Model ${key || "-"}`,
      subtitle: "Unknown model",
      description: "Model metadata is not registered for this id.",
    }
  );
}
