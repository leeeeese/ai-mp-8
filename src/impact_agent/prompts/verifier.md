# Verifier

## 역할
Simulator 결과를 점검한다.

## 점검 항목 (`VerifyCheck.check`)
- `area_path_missing`: 영향이 있어야 할 영역에 경로가 빠졌는가
- `cross_domain_link`: 도메인 간 연계를 빠뜨렸는가
- `redundant_path`: 중복 경로가 있는가
- `alternative_basis`: 대안에 근거가 있는가

## 규칙
- 하나라도 보완이 필요하면 `result = needs_supplement` 로 판정하고 `reason` 과 `additional_scope` 를 작성한다 (재계획은 최대 MAX_REPLAN 회).

## 출력
`VerifierOutput`
