# 상품 인덱스 alias 전환 · 롤백 런북

> 문서 ID RB-SRCH-001 · 담당 검색 인프라팀 · 최종 검토 2026-09-03 · 관련 장애 INC-2310

products alias 를 새 인덱스(products_vN)로 수동 전환하거나 이전 인덱스로 되돌리는 절차. **운영 클러스터 쓰기 작업**이라 2인 확인 필수.

## 1. 언제 쓰나

- product_index_alias_swap 이 안전장치(문서 수 20 % 초과 감소)로 실패한 뒤 원인을 해소한 경우
- 리인덱스 후 수동 검증이 필요한 경우
- 새 인덱스 품질 문제로 즉시 롤백해야 하는 경우

## 2. 사전 점검 체크리스트

| 항목 | 명령 | 기준 |
|---|---|---|
| 새 인덱스 health | `GET _cat/indices/products_v*?v&h=index,health,docs.count,creation.date.string` | green |
| 문서 수 | 위 결과 비교 | 현재 alias 대상 대비 -5 % ~ +10 % |
| 매핑 변경 | `GET products_vN/_mapping` diff | 추가 필드만 허용 · 타입 변경 시 검색팀 리뷰 |
| 원천 적재 | `SELECT count(*) FROM search.products WHERE in_stock` | 전일 대비 ±5 % |
| 레플리카 할당 | `GET _cluster/health/products_vN` | unassigned_shards = 0 |

## 3. 전환 (원자적)

```
POST _aliases
{"actions": [
  {"remove": {"index": "products_v41", "alias": "products"}},
  {"add":    {"index": "products_v42", "alias": "products"}}
]}
```

## 4. 검증 (전환 후 10분)

1. `GET _alias/products` → products_v42
2. 대표 검색어 스모크 테스트 20개 (`search-smoke` 스크립트) — 결과 0건 질의 없음
3. 지표: 검색 무결과율 · 검색 CTR · p95 지연 이상 없음

## 5. 롤백

- 무결과율 +1 %p 이상 또는 CTR -5 % 이상이면 즉시 롤백
```
POST _aliases
{"actions": [
  {"remove": {"index": "products_v42", "alias": "products"}},
  {"add":    {"index": "products_v41", "alias": "products"}}
]}
```
- 롤백 후 새 인덱스는 **삭제하지 말고** 원인 분석까지 보관

## 6. 문서 수 급감 시 원인 분기

| 판별 | 원인 | 조치 |
|---|---|---|
| search.products 행 수 감소 | 원천 적재 지연 | 적재 완료 대기 후 product_index_build 재실행 |
| 행 수 정상, in_stock 비율 급감 | 재고 동기화 오류 | 재고팀 확인 · 필터 임시 해제 여부 결정 |
| 빌드 로그 bulk reject | 클러스터 부하 | [검색 클러스터 yellow 대응](cluster-yellow.html) |

## 7. 커뮤니케이션

```text
[검색] products alias 전환 작업 — 14:00 시작 / 영향 없음 예상
- 대상: products_v41 → products_v42 / 작업자: A, 확인자: B
- 롤백 기준: 무결과율 +1%p, CTR -5%
```
