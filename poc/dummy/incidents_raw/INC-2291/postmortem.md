# INC-2291 포스트모템 — 추천 피드 미갱신 (2026-08-14)

작성: 박추천 · 리뷰: 추천 플랫폼팀 · 상태: 최종

## 요약
새벽 랭킹 배치(ranking_score_daily)가 메모리 초과로 실패해서 약 2시간 45분 동안 사용자에게 전날 추천 결과가 노출됐다. 원인은 candidate_gen_hourly 가 같은 시간대에 두 번 실행되면서 후보 파티션이 3배로 커진 것.

## 영향
- 05:21 ~ 07:55 추천 피드가 전일 데이터로 서비스됨 (약 2시간 34분)
- 추천 CTR 이 당일 오전 약 9% 하락한 것으로 추정
- 검색 서비스 영향 없음

## 탐지
Airflow 실패 알림(05:24)으로 온콜이 인지. 사용자 영향 지표 기반 알림은 없었음 → 개선 필요.

## 근본 원인
스케줄러 재시작 후 catchup 동작으로 candidate_gen_hourly 04시 실행이 중복됐고, 중복 파티션 때문에 score_candidates 의 입력이 평소의 약 3배(3.4억 행)가 되어 executor 메모리(12GB)를 초과했다.

## 대응
1. 중복된 reco.candidates 파티션 삭제
2. ranking_score_daily 의 score_candidates 재실행 (약 45분)
3. reco_feed_publish 수동 트리거 후 alias 전환 확인

## 재발 방지
- [ ] candidate_gen_hourly max_active_runs=1 (담당: 박추천)
- [ ] score_candidates 앞에 입력 파티션 크기 검사 태스크 추가 (담당: 박추천)
- [x] 추천 피드 신선도 알림(RecoFeedStale) 신설 (담당: 김운영)
