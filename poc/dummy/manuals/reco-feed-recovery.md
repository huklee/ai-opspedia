# 추천 피드 미갱신 대응 런북

> 문서 ID RB-RECO-001 · 담당 추천 플랫폼팀 · 최종 검토 2026-09-26 · 대상 시스템 Reco

reco-feed alias 가 오늘 날짜 인덱스로 전환되지 않아 사용자에게 **전날 추천 결과**가 노출되는 상황의 대응 절차.

## 1. 심각도 판단

| 조건 | 심각도 | 대응 |
|---|---|---|
| 07:00 까지 reco_feed_publish 미완료, alias 대상 인덱스 경과 < 30시간 | SEV3 | 업무 시간 내 복구 |
| alias 대상 인덱스 경과 ≥ 30시간 또는 추천 CTR 전일 대비 -10 % 이상 | SEV2 | 즉시 대응 · #reco-ops 공지 |
| reco-feed 인덱스 red, 추천 API 5xx 증가 | SEV1 | 검색 인프라팀 호출 · 장애 채널 개설 |

## 2. 증상 · 알림

- Airflow 알림: `reco_feed_publish` **upstream_failed** 또는 `ranking_score_daily` **failed**
- opspedia 상태 점검: `reco-feed` 패밀리 🔴 "alias 대상 경과 N시간 > 기준 26시간"
- 지표: 추천 피드 신선도 > 26시간, 추천 CTR 하락

## 3. 첫 5분

1. `/e/dag:reco_feed_publish` 에서 최근 실행 상태와 업스트림 확인
2. 실패한 업스트림 DAG 확인 (대부분 `ranking_score_daily`)
3. #reco-ops 에 인지 공지 (아래 템플릿)
4. 사용자 영향 확인: 추천 피드 신선도 · CTR 지표 페이지

## 4. 진단

```bash
# 최근 실행 상태
airflow dags list-runs -d ranking_score_daily --state failed -o table | head
airflow tasks states-for-dag-run ranking_score_daily <run_id>

# 실패 태스크 로그 (OOM · 파티션 크기 확인)
airflow tasks logs ranking_score_daily score_candidates <run_id> | grep -iE "oom|killed|memory"
```

```sql
-- 후보 파티션 크기 이상 여부 (평소 약 1.2억 행)
SELECT hr, count(*) FROM reco.candidates WHERE hr >= date_format(now() - interval 1 day, 'yyyyMMddHH') GROUP BY hr ORDER BY hr;
-- 오늘 랭킹 결과 존재 여부
SELECT count(*) FROM reco.ranked_items WHERE dt = current_date;
```

## 5. 원인별 조치

| 원인 | 판별 | 조치 |
|---|---|---|
| 후보생성 중복 실행으로 파티션 비대 | reco.candidates 같은 시간대 파티션 2개 이상 · 행 수 2–3배 | 중복 파티션 삭제 → ranking_score_daily 재실행 ([랭킹 배치 OOM 대응](ranking-oom.md)) |
| 임베딩 DAG 지연 | user_embedding_daily 미완료 | 센서 대기 · 임베딩 DAG 먼저 복구 |
| 모델 레지스트리 누락 | reco.model_registry 최신 버전 없음 | ranking_train_weekly 의 register_model 만 재실행 |
| 인덱스 빌드 실패 | build_reco_feed_index 실패 | 클러스터 상태 확인 ([검색 클러스터 yellow 대응](cluster-yellow.html)) |

## 6. 복구

1. 원인 해결 후 실패 DAG 재실행
   ```bash
   airflow tasks clear ranking_score_daily -s <ds> -e <ds> --only-failed --yes
   ```
2. reco_feed_publish 재실행 (upstream_failed 태스크 포함)
   ```bash
   airflow tasks clear reco_feed_publish -s <ds> -e <ds> --yes
   ```
3. `swap_reco_feed_alias` 성공 확인

## 7. 검증

- `GET _alias/reco-feed` → `reco-feed-<오늘 날짜>`
- 새 인덱스 문서 수가 전일 대비 ±5 % 이내
- 30분 뒤 추천 CTR 회복 추이 확인

## 8. 롤백

- 새 인덱스 품질 이상(문서 수 급감, 빈 추천) 시 alias 를 전날 인덱스로 되돌림
  ```
  POST _aliases
  {"actions": [{"remove": {"index": "reco-feed-<오늘>", "alias": "reco-feed"}},
               {"add": {"index": "reco-feed-<어제>", "alias": "reco-feed"}}]}
  ```

## 9. 에스컬레이션

- 30분 내 원인 미확인: 추천 플랫폼팀 리드
- 클러스터 문제: 검색 인프라팀 온콜 (@search-oncall)

## 10. 공지 템플릿

```text
[SEV2][추천] 추천 피드 미갱신 — 전날 추천 결과 노출 중
- 인지: 07:10 / 원인: ranking_score_daily 실패 (조사 중)
- 영향: 추천 피드 신선도 27시간, CTR -8 %
- 다음 업데이트: 30분 후
```

## 11. 사후 조치

- 장애 티켓(INC) 작성 · 근본 원인 · 재발 방지 항목 등록
- 반복 원인은 DAG 안전장치(파티션 크기 검사 등)로 자동화
