# 랭킹 배치 OOM 대응 런북

> 문서 ID RB-RECO-002 · 담당 추천 플랫폼팀 · 최종 검토 2026-09-20 · 관련 장애 INC-2291

ranking_score_daily 의 score_candidates 태스크가 executor 메모리 초과(OOM)로 실패할 때의 절차.

## 1. 증상

- 태스크 로그: `Container killed by YARN for exceeding memory limits` 또는 `java.lang.OutOfMemoryError`
- 재시도 2회 모두 실패 → reco_feed_publish upstream_failed

## 2. 원인 후보 (빈도순)

1. **reco.candidates 파티션 비대** — candidate_gen_hourly 중복 실행 (INC-2291)
2. 활성 유저 급증(프로모션) → 후보 행 수 증가
3. reco.user_features 조인 키 중복

## 3. 진단

```sql
-- 시간대별 후보 파티션 행 수
SELECT hr, count(*) AS rows FROM reco.candidates
WHERE hr BETWEEN '<어제 05시>' AND '<오늘 05시>' GROUP BY hr ORDER BY rows DESC;

-- 조인 키 중복
SELECT user_id, count(*) FROM reco.user_features WHERE dt = '<ds>' GROUP BY user_id HAVING count(*) > 1 LIMIT 10;
```

```bash
# 후보생성 중복 실행 여부
airflow dags list-runs -d candidate_gen_hourly -s <어제> -o table
```

## 4. 조치

| 원인 | 조치 | 예상 소요 |
|---|---|---|
| 중복 파티션 | 중복 hr 파티션 삭제 후 score_candidates 재실행 | 30–40분 |
| 활성 유저 급증 | `executor_memory` 12g → 16g 임시 상향 후 재실행 (conf 전달) | 45분 |
| 조인 키 중복 | feature_store_daily 해당 dt 재처리 ([피처스토어 백필 가이드](backfill-guide.md)) | 1–2시간 |

```bash
# 중복 파티션 삭제
hive -e "ALTER TABLE reco.candidates DROP PARTITION (hr='<중복 hr>');"
# 메모리 상향 재실행
airflow dags trigger ranking_score_daily -e <ds> -c '{"executor_memory": "16g"}'
```

## 5. 검증

- score_candidates 성공, reco.ranked_items 오늘 파티션 행 수 = 활성 유저 수 ± 3 %
- 이어서 [추천 피드 미갱신 대응 런북](reco-feed-recovery.md) 6–7단계

## 6. 재발 방지

- candidate_gen_hourly `max_active_runs=1` 확인
- score_candidates 앞에 파티션 크기 검사 태스크 추가 (평소 대비 2배 초과 시 실패)
