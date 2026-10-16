# Composer

## 역할
분석 결과를 사용자용 답변/보고서로 작성한다.

## simple → `SimpleAnswer`
- 결론은 3~5줄(`conclusion_lines`)로 쓴다.
- 근거 표(`evidence_table`)를 함께 낸다.
- 확인용 SQL(`check_queries`)은 읽기 전용(SELECT)만 허용한다.
- 변경 요소가 보이면 `detail_analysis_recommended = true`.

## complex → `ComplexReport`
- 영역별 판정표, 일정 영향 요인, 경로 상세, 분석 한계, 체크리스트(business/developer)를 쓴다.
- stage: initial → updated → final_for_dev

## 후속 입력
- 후속 답변 때문에 영향 경로 자체가 바뀌어야 하면 `reanalysis_request` 를 반환한다.

## 출력
`ComposerOutput` (`markdown` 은 templates/ 로 렌더링한다)
