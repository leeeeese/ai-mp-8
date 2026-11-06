/**
 * 화면 설정값. 루프 한도·KPI 반영 방식은 TBD 라 백엔드 schemas.json#/constants 와 맞춰 바꾼다.
 */
export const LIMITS = {
  maxToolCalls: 20, // MAX_TOOL_CALLS
  maxExploreLoop: 3, // MAX_EXPLORE_LOOP (Explorer ⇄ Retriever 왕복)
  maxReplan: 2, // MAX_REPLAN
};

/** KPI 기준값 (백엔드 schemas.json#/constants KPI_MIN_RECALL · KPI_MIN_PRECISION, 잠정). Critical 미탐률은 권장 0 */
export const KPI_MIN = { recall: 0.8, precision: 0.5 };

/** VITE_USE_MOCK=false 이면 실제 API·SSE 를 호출한다 (기본은 MSW mock). */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';
