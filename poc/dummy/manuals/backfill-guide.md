# 피처스토어 백필 표준 절차 (SOP)

> 문서 ID SOP-RECO-003 · 담당 추천 플랫폼팀 · 최종 검토 2026-09-15

feature_store_daily 결과(reco.user_features, reco.item_features)를 과거 날짜로 재처리(백필)하는 표준 절차. 백필은 **하위 DAG 연쇄 재처리**가 핵심.

## 1. 백필이 필요한 경우

- 원천 로그(logs.user_events, logs.search_clicks) 지연·누락 후 복구
- 피처 정의 변경(새 컬럼) 후 과거 기간 재계산
- 조인 키 중복 등 데이터 품질 문제

## 2. 영향 범위

| 순서 | DAG | 재처리 이유 |
|---|---|---|
| 1 | feature_store_daily | 대상 |
| 2 | user_embedding_daily · item_embedding_daily | 피처 입력 |
| 3 | ranking_score_daily | 최신 날짜만 재실행 |
| 4 | reco_feed_publish | 최신 날짜만 재실행 |

## 3. 사전 확인

- [ ] 원천 파티션 존재: `SHOW PARTITIONS logs.user_events` 대상 기간 전부
- [ ] 클러스터 여유: 야간(22:00–05:00) 또는 주말 실행
- [ ] #reco-ops 사전 공지

## 4. 실행

```bash
# 1) 피처 백필 (최대 동시 2개)
airflow dags backfill feature_store_daily -s 2026-09-01 -e 2026-09-07 --reset-dagruns -y
# 2) 임베딩 백필
airflow dags backfill user_embedding_daily -s 2026-09-01 -e 2026-09-07 -y
airflow dags backfill item_embedding_daily -s 2026-09-01 -e 2026-09-07 -y
# 3) 최신 날짜 랭킹 · 피드 재실행
airflow tasks clear ranking_score_daily -s <오늘> -e <오늘> --yes
```

## 5. 검증

```sql
SELECT dt, count(*), avg(clicks_7d) FROM reco.user_features WHERE dt BETWEEN '2026-09-01' AND '2026-09-07' GROUP BY dt ORDER BY dt;
```
- 날짜별 행 수 편차 ±5 % 이내, avg(clicks_7d) 급변 없음

## 6. 주의

- `--reset-dagruns` 는 기존 성공 실행도 덮어씀 → 대상 기간 재확인
- 백필 중 정기 실행과 겹치면 센서 대기로 지연 → 07:00 이전 종료 계획
