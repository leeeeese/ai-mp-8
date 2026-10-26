/**
 * 화면 설정값. 루프 한도·KPI 반영 방식은 TBD 라 백엔드 schemas.json#/constants 와 맞춰 바꾼다.
 */
export const LIMITS = {
  maxToolCalls: 20, // MAX_TOOL_CALLS
  maxExploreLoop: 3, // MAX_EXPLORE_LOOP (Explorer ⇄ Retriever 왕복)
  maxReplan: 2, // MAX_REPLAN
};

/** VITE_USE_MOCK=false 이면 실제 API·SSE 를 호출한다 (기본은 MSW mock). */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';
