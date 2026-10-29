/** mock 응답 내용. 목업 시나리오 정답셋(eval/ground_truth)을 바탕으로 만든 예시 결과다. */
import type { AgentAnswer, ChecklistItem, Report } from '@/api/types';

export const SC001_ANSWER: AgentAnswer = {
  complexity: 'complex',
  plannerVerdict: '복잡 — 화면 입력·저장과 T world 연계가 함께 걸림 (항목 2개: 저장 데이터 추가 · 연계 항목 추가)',
  process: [
    'Planner: 항목 1 저장 데이터 추가 → 목표 Table (정방향 후 역방향, hop 10) / 항목 2 연계 항목 추가 → 목표 Entry (역방향, hop 5)',
    'Explorer: 시작 노드 7개 (캠페인 등록 화면 · 광고여부 컬럼 · T world 배치 등), 경로 82개, tool 9회',
    'Retriever: 근거 74건 (소스 70 · 인터페이스 정의서 4), 재탐색 요청 없음',
    'Simulator: 요청안(신규 컬럼·화면 항목) vs 대안(기존 광고여부 변환) 비교',
    'Verifier: 영역 4개 근거 확인 — 통과',
  ],
  summary: [
    '신규 컬럼·화면 항목 없이 기존 광고여부(TCP_MSG_SCRT.ADVT_YN)를 T world 노출파일에 실어 보내는 대안으로 처리 가능합니다.',
    '화면·DB·MMS 는 변경이 필요 없습니다. 화면 "광고성 메시지" 체크로 이미 저장하고, MMS 는 배치에서 반전해 비마케팅여부로 이미 보냅니다.',
    '수정 위치: DBIO DCP_MSGSCRT_S02, tbtw1010.pc write_data_rec(), IF-TOS-TWD-021 (레코드 500 → 501 byte).',
  ],
  areas: [
    {
      area: 'screen',
      impact: '변경 없음 — 광고성 메시지 체크박스로 광고여부를 이미 입력·저장',
      path: ['SCR:TOSCP0110M', 'CTL:CmpnController.saveCmpn', 'SVC:CmpnService.saveMsgScrt', 'SQL:MsgScrtMapper.insertMsgScrt', 'COL:TCP_MSG_SCRT.ADVT_YN'],
      requested: { feasibility: 'feasible', difficulty: 'Medium' },
      alternative: { feasibility: 'feasible', difficulty: 'Minor' },
      confidence: 'confirmed',
    },
    {
      area: 'db',
      impact: '변경 없음 — MMS 는 make_snd_tgt 에서 ADVT_YN 을 반전해 NON_MKTG_YN 으로 적재',
      path: ['BAT:TBCH1010', 'SQL:tbch1010.make_snd_tgt#2', 'TBL:TCH_MMS_SND_TGT', 'COL:TCH_MMS_SND_TGT.NON_MKTG_YN'],
      requested: { feasibility: 'feasible', difficulty: 'Medium' },
      alternative: { feasibility: 'feasible', difficulty: 'Minor' },
      confidence: 'confirmed',
    },
    {
      area: 'batch',
      impact: '수정 — DBIO 에 ADVT_YN 조회 추가, write_data_rec() 비마케팅여부 1 byte 매핑',
      path: ['BAT:TBTW1010', 'DBIO:DCP_MSGSCRT_S02', 'TBL:TCP_MSG_SCRT', 'COL:TCP_MSG_SCRT.ADVT_YN'],
      requested: { feasibility: 'feasible', difficulty: 'Medium' },
      alternative: { feasibility: 'feasible', difficulty: 'Medium' },
      confidence: 'confirmed',
    },
    {
      area: 'interface',
      impact: '수정 — IF-TOS-TWD-021 Data 항목 추가, 레코드 500 → 501 byte (수신측 반영 필요)',
      path: ['BAT:TBTW1010'],
      requested: { feasibility: 'review', difficulty: 'Medium' },
      alternative: { feasibility: 'review', difficulty: 'Medium' },
      confidence: 'inferred',
    },
  ],
  openItemCount: 7,
};

export const SC006_ANSWER: AgentAnswer = {
  complexity: 'simple',
  plannerVerdict: '단순 — 발송 실패 사유 저장 위치·조회 방법 질의 (소스 수정 없음)',
  process: [
    'Planner: 구조·데이터 위치 질의 → 목표 Column (정방향)',
    'Explorer: 시작 노드 4개 (MMS 발송대상 · 타사 발송대상 테이블), 경로 13개, tool 4회',
    'Retriever: 근거 17건',
  ],
  summary: [
    '자사는 TCH_MMS_SND_TGT 에서 SND_STAT_CD = 90 건의 FAIL_CD 를 공통코드 CD_CAMP_ERR_01 과 조인해 코드별 건수를 봅니다.',
    '타사는 TCH_EXT_SND_TGT 에서 SND_STAT_CD = F2(수신 실패) 건의 EXT_RSLT_CD·EXT_RSLT_MSG 별 건수를 봅니다.',
    '고객 목록이 필요하면 데이터 추출 IT 요청서를, 타사 사유는 중개사 고객지원으로 문의합니다. 코드 의미는 추정입니다.',
  ],
  areas: [],
  openItemCount: 4,
  evidence: [
    { claim: '자사 실패 사유 = FAIL_CD', evidence: 'TCH_MMS_SND_TGT.FAIL_CD 코멘트', location: 'db/ddl/TCH_MMS_SND_TGT.sql L31-32', confidence: 'confirmed' },
    { claim: '타사 실패 = SND_STAT_CD F2', evidence: 'TCH_EXT_SND_TGT 코멘트, 운영 가이드', location: 'db/ddl/TCH_EXT_SND_TGT.sql L18-21', confidence: 'confirmed' },
    { claim: '자사 코드 그룹 CD_CAMP_ERR_01', evidence: 'CD_CAMP_ERR_02 는 RCS 용 (같은 E301 이 다른 의미)', location: 'snapshot TCP_CMM_CD', confidence: 'confirmed' },
    { claim: '반영 시점 익일 06:00', evidence: '배치 정의와 운영 가이드 표현이 다름', location: 'docs/tos/ops/send_result_ops_guide.md', confidence: 'inferred' },
  ],
  checkQueries: [
    {
      title: '자사 (MMS) 실패 사유별 건수',
      sql: `SELECT T.FAIL_CD, C.CD_NM, COUNT(*) AS CNT
  FROM TCH_MMS_SND_TGT T
  LEFT JOIN TCP_CMM_CD C ON C.GRP_CD = 'CD_CAMP_ERR_01' AND C.CD = T.FAIL_CD
 WHERE T.CMPN_ID = 'C260900301' AND T.SND_STAT_CD = '90'
 GROUP BY T.FAIL_CD, C.CD_NM
 ORDER BY CNT DESC;`,
    },
    {
      title: '타사 (중개사) 실패 사유별 건수',
      sql: `SELECT EXT_RSLT_CD, EXT_RSLT_MSG, COUNT(*) AS CNT
  FROM TCH_EXT_SND_TGT
 WHERE CMPN_ID = 'C260900302' AND SND_STAT_CD = 'F2'
 GROUP BY EXT_RSLT_CD, EXT_RSLT_MSG
 ORDER BY CNT DESC;`,
    },
  ],
  changeDetected: false,
};

/** 단순 응답의 상세 분석 권장 사유 예 (백엔드 Composer 가 탐색 신호로 만드는 문장, 노드 ID 포함) */
export const SC006_DETAIL_REASONS = [
  '공통 모듈 CTL:CmmCdController.selectCdList 이 경로에 있어 다른 화면·처리 영향 확인 필요',
  '적재 범위 밖 연계(BAT:TBCH1020 결과 반영 프로그램) 확인 필요',
];

export const H1_ANSWER: AgentAnswer = {
  complexity: 'complex',
  plannerVerdict: '복잡 — 결합 조건 검증·결합그룹 생성·할인 배치가 함께 걸림',
  process: [
    'Planner: 항목 3개 — 검증 로직 변경(Logic) · 저장 데이터 추가(Table) · 배치 처리 변경(DataAccess)',
    'Explorer: 시작 노드 5개, 경로 41개, tool 8회',
    'Retriever: 근거 38건',
    'Simulator: 요청안 비교',
    'Verifier: 통과',
  ],
  summary: [
    '결합 가입 검증(validateCombMbr)에서 유선 1회선 필수 조건을 빼고 무선+무선 최소 2회선 검증을 추가하면 가능합니다.',
    '유선이 없으면 상품 B 로 결합그룹을 만들고, 할인 재산정 배치에 B 할인 규칙을 추가해야 합니다 (미반영 시 할인 0원).',
  ],
  areas: [
    { area: 'screen', impact: '수정 — 유선 필수 사전검증 제거, 상품명 A/B 표시', path: ['SCR:ZCMBSS0100010', 'TP:CombScrbSvc.joinComb'], requested: { feasibility: 'feasible', difficulty: 'Medium' }, alternative: null, confidence: 'confirmed' },
    { area: 'db', impact: '수정 — 결합그룹 상품 B, 상품 데이터 등록', path: ['TP:CombScrbSvc.joinComb', 'DBIO:CombSql.insertCombGrp', 'TBL:TB_COMB_GRP'], requested: { feasibility: 'feasible', difficulty: 'Medium' }, alternative: null, confidence: 'confirmed' },
    { area: 'batch', impact: '수정 — 상품 B 할인 규칙 추가', path: ['BAT:ZCMBBB0100010', 'MOD:CombDcBatch.calcDcAmt'], requested: { feasibility: 'feasible', difficulty: 'Major' }, alternative: null, confidence: 'inferred' },
  ],
  openItemCount: 6,
};

const SC001_CHECKLIST: ChecklistItem[] = [
  { id: 'b1', owner: 'business', question: '광고여부 N 을 비마케팅으로 보는 MMS 기준을 T world 에도 그대로 적용해도 되는가', status: 'unconfirmed' },
  { id: 'b2', owner: 'business', question: '기존 등록 캠페인에도 적용하는가, 적용 시점은 언제인가', status: 'unconfirmed' },
  { id: 'b3', owner: 'business', question: '화면에서 별도로 비마케팅 여부를 입력받아야 하는 다른 사유가 있는가', status: 'unconfirmed' },
  { id: 'd1', owner: 'developer', question: 'T world 레코드 500 → 501 byte 변경 시 수신측(TWORLD-CMPN) 반영 일정', status: 'unconfirmed' },
  { id: 'd2', owner: 'developer', question: 'Header/Trailer 공백 길이 조정 및 수신측 길이 검증 방식', status: 'unconfirmed' },
  { id: 'd3', owner: 'developer', question: 'DBIO DCP_MSGSCRT_S02 출력 구조체 헤더 재생성과 tbtw1010 재컴파일', status: 'unconfirmed' },
  { id: 'd4', owner: 'developer', question: '기존 주석 코드(cnv_non_mktg_yn) 재사용 여부와 변환 규칙의 MMS(DECODE) 일치', status: 'unconfirmed' },
];

export function reportFor(answer: AgentAnswer, version: number): Report {
  const v2 = version >= 2;
  const checklist = SC001_CHECKLIST.map((c) =>
    v2 && c.id === 'b1'
      ? { ...c, status: 'confirmed' as const, answer: 'MMS 와 같은 기준으로 적용 (마케팅팀 확인)', changed: true }
      : { ...c },
  );
  return {
    version,
    versions: [
      { version: 1, label: 'v1 최초 분석' },
      { version: 2, label: 'v2 현업 답변 반영' },
    ],
    feasibility: 'feasible',
    difficulty: 'Medium',
    scheduleUncertainty: v2 ? '중간' : '높음',
    areas: answer.areas.map((a) =>
      v2 && a.area === 'interface' ? { ...a, confidence: 'confirmed' as const, impact: a.impact + ' — 기준 확정', changed: true } : a,
    ),
    scheduleFactors: [
      'T world 수신측(TWORLD-CMPN) 레이아웃 반영 일정 (2020.09 400 → 500 변경 선례)',
      'DBIO 구조체 헤더 재생성과 배치 재컴파일·재배포',
      '기존 캠페인 적용 범위 확정 (ADVT_YN 기본값 N)',
    ],
    limits: ['T world 수신측(TWORLD-CMPN) 처리 소스는 적재 범위 밖', '공통 프레임워크(tmkcom.h, tmkdbio.h, EAI 송신 모듈)는 적재 범위 밖'],
    checklist,
  };
}
