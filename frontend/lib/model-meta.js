export const MODEL_ORDER = ["A", "B", "C", "D"];

export const MODEL_META = {
  A: {
    id: "A",
    name: "레인지 리버전",
    subtitle: "과열 구간 되돌림",
    description: "레인지 하단 재진입을 노리는 계획형 모델입니다.",
  },
  B: {
    id: "B",
    name: "리클레임",
    subtitle: "지지 회복 재안착",
    description: "지지 회복 이후 재안착 구간에서 진입 계획을 계산합니다.",
  },
  C: {
    id: "C",
    name: "압축 돌파",
    subtitle: "수축 후 확장",
    description: "변동성 수축 이후의 돌파 확장을 추적하는 모델입니다.",
  },
  D: {
    id: "D",
    name: "리셋 바운스",
    subtitle: "급락 후 안정화 반등",
    description: "급락 이후 안정화 구간에서 되돌림 진입을 계산합니다.",
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
