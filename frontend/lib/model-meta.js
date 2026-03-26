export const MODEL_ORDER = ["A", "B", "C", "D"];

export const MODEL_META = {
  A: {
    id: "A",
    name: "과열 되돌림",
    subtitle: "과매수 반락 포착",
    description: "단기 과열 이후 평균회귀를 노리는 역추세 진입 모델입니다.",
  },
  B: {
    id: "B",
    name: "지지 재탈환",
    subtitle: "지지 회복 추종",
    description: "지지 회복과 재확인을 거친 뒤 모멘텀을 추종해 진입하는 모델입니다.",
  },
  C: {
    id: "C",
    name: "브레이크아웃",
    subtitle: "변동성 확장 추세",
    description: "변동성 확장과 방향성 지속 구간을 추종하는 추세형 모델입니다.",
  },
  D: {
    id: "D",
    name: "리셋 반등",
    subtitle: "급락 반발 매매",
    description: "패닉성 급락과 과매도 극단 구간 이후 기술적 반등을 노리는 모델입니다.",
  },
};

export function getModelMeta(modelId) {
  const key = String(modelId || "").trim().toUpperCase();
  return (
    MODEL_META[key] || {
      id: key || "-",
      name: `모델 ${key || "-"}`,
      subtitle: "미등록 모델",
      description: "해당 ID에 대한 모델 메타데이터가 등록되지 않았습니다.",
    }
  );
}
