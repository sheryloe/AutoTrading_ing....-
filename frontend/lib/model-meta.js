export const MODEL_ORDER = ["A", "B", "C", "D"];

export const MODEL_META = {
  A: {
    id: "A",
    name: "레인지 리버전",
    subtitle: "과열 구간 되돌림",
    description: "과열 구간에서 레인지 하단 재진입을 노리는 모델입니다.",
  },
  B: {
    id: "B",
    name: "리클레임",
    subtitle: "지지 회복 재안착",
    description: "지지 회복 후 재안착 구간에서 진입/손절/목표가를 계산합니다.",
  },
  C: {
    id: "C",
    name: "압축 돌파",
    subtitle: "수축 후 확장",
    description: "변동성 수축 후 확장 구간의 돌파 진입 계획을 세웁니다.",
  },
  D: {
    id: "D",
    name: "리셋 바운스",
    subtitle: "급락 후 반등",
    description: "급락 후 안정화 구간의 되돌림 진입을 계산합니다.",
  },
};

export function getModelMeta(modelId) {
  const key = String(modelId || "").trim().toUpperCase();
  return MODEL_META[key] || {
    id: key || "-",
    name: `모델 ${key || "-"}`,
    subtitle: "미정의 모델",
    description: "정의되지 않은 모델입니다.",
  };
}