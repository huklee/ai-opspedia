# 조사 및 검증

> 근거 태그: **[measured]** 대상 호스트 직접 측정 · **[strong]** 1차 문서/논문 · **[moderate]** 벤더 자료 또는
> 2차 출처 · **[inferred]** 자체 추론. 조사일: 2026-09-28

## 1. 요약: 조사 후 계획에서 달라진 점

| 발견 사항 | 계획 반영 |
|---|---|
| 기본 SQLite FTS5 `trigram`으로는 2음절 한국어 단어(`장애`, `배치`) 검색 불가 **[measured]** | FTS5 앞단에 한글 바이그램·식별자 분리용 자체 분석기 ([03-storage](components/03-storage.md) §4) |
| 전수 코사인 계산으로 충분(50k×1024에서 5.3 ms, 100k에서 ~9 ms) **[measured]** | 벡터 DB / ANN 인덱스 없이 float32 BLOB + numpy ([ADR-004](decisions.md#adr-004)) |
| DeepWiki식 생성은 중요 컴포넌트를 빠뜨리고 공식 문서로 오인되기 쉬움 **[moderate]** | **인벤토리 엔티티마다 페이지 하나** 생성, "AI 생성" 라벨과 출처 표시 ([02-synthesis](components/02-synthesis.md)) |
| Karpathy "LLM Wiki": raw/ → LLM 위키 → 스키마 구조, **ingest / query / lint** 연산, append-only 로그 **[strong]** | `raw` 스냅샷과 `content/` 분리, 야간 **lint** 작업, `log` 테이블, 답변 write-back (P2) |
| 해시 기반 stale 판정은 과잉 발동(raw 해시 변경 중 의미 있는 것은 ~34 %) **[moderate]** | raw 바이트 대신 **정규화한 시맨틱 뷰**(파싱한 DAG 그래프 / DDL AST / 매핑 JSON) 해시 |
| Airflow REST가 실제 상태의 기준, AST는 동적 DAG에서 실패 **[strong/moderate]** | DAG 커넥터 = REST 스냅샷 + 코드 세부용 AST. AST로 해석 불가하면 `dynamic: true` |
| OpenSearch는 **ISM**(`_plugins/_ism/explain`), Elasticsearch는 **ILM**(`_ilm/explain`) **[strong]** | 인덱스 커넥터에서 엔진별 분기 |
| Contextual Retrieval: 컨텍스트 임베딩 + BM25로 검색 실패 −49 %, 리랭크 추가 시 −67 % **[strong]** | 청크마다 결정적 컨텍스트 헤더(비용 없음), LLM 컨텍스트 문장은 선택 (P1) |
| RRF(k=60)는 점수 정규화 불필요, k∈[20,100]에서 안정적 **[strong]** | 결합 방식 = RRF, 리랭크는 선택 |
| Anthropic에는 **임베딩 엔드포인트 없음**. 한국어 선택지는 Voyage(API), bge-m3 / KURE-v1(로컬) **[moderate]** | 교체 가능한 embedder. 기본 Voyage API, fallback 로컬 bge-m3. 30–50개 쿼리 평가로 결정 ([ADR-006](decisions.md#adr-006)) |

## 2. 환경 조사 (대상 호스트: `huklee-01`, tailnet의 Mac mini)

| 항목 | 확인 결과 | 비고 |
|---|---|---|
| Python | 시스템 3.9.6, `/usr/local/bin/python3.13`에 **Python 3.13.7**, **uv 0.9.5** | `uv` 프로젝트로 3.13 사용 |
| SQLite | **3.51.0**, FTS5(unicode61, trigram) 포함 | JSON1, 윈도 함수, WAL 사용 가능 |
| 로컬 Docker / Postgres / Airflow / ES | 미설치 | 옵션 A(로컬 인프라 없음) 근거 강화. 대상 Airflow/ES는 원격 |
| 기존 프로젝트 | `ai-research-note`(Go, 계정·채널·SQLite가 있는 tailnet HTTPS 앱), `peekadoc` / docserv(MkDocs Material 기반 읽기 전용 Markdown 브라우저), `claude-slack-bridge` | 재사용 패턴: tailnet 전용 + TLS manager, 계정 모델, 터미널 UI 가이드, peekadoc의 트리·딥 링크 UX |

### 측정 결과 [measured]

**한국어 대응 FTS5 프로토타입**: 한글은 겹치는 바이그램, 식별자는 `_ . camelCase` 기준 분리 + 원형 유지, `unicode61`

| 쿼리 | 결과 | 의미 |
|---|---|---|
| `장애` (2음절) | ✅ 문서 1,3 | 기본 `trigram`은 **0**건 |
| `파이프라인` | ✅ | 바이그램 AND 결합 |
| `feature store` | ✅ `feature_store_daily` | 식별자 분리 |
| `alias 전환`, `임베딩 생성` | ✅ | 한영 혼합 |

**쿼리 빌더 점검**(2차 리뷰): `tokenchars '_'` 설정한 contentless FTS5 기준

| 케이스 | 결과 |
|---|---|
| `feature_store_daily` | 단일 토큰 유지(부분 토큰도 함께) |
| `es-prod.products_v3` | `es_prod_products_v3` 토큰으로 매칭 |
| `장애가` | → `장애` (조사 제거) |
| 따옴표로 감싼 `NOT OR` | 일반 단어로 처리 |
| rowid로 삭제 | 정상 동작 |
| `대응장애`(띄어쓰기 없음) vs `장애 대응` | 완화된 OR fallback 필요 |

**벡터 검색**: numpy float32, 정규화 후 matmul + argpartition

- 50 000 × 1024 → **5.3 ms, 205 MB**
- 리서치 에이전트 측정: 100k × 1024 ≈ 9 ms (410 MB), 20k × 1024 ≈ 2 ms
- 예상 코퍼스(청크 5–20k개) 기준 **< 3 ms** 예상

## 3. 선행 사례: 가져올 것과 가져오지 않을 것

| 시스템 | 가져올 것 | 가져오지 않는 이유 |
|---|---|---|
| Karpathy "LLM Wiki" (2026) — [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | raw/wiki/schema 계층, **lint**(모순, stale, 고아 페이지, 누락 링크), `index`/`log`, 좋은 답변 write-back | 제품이 아닌 패턴이라 그대로 적용 가능 |
| DeepWiki (Cognition) — [docs](https://docs.devin.ai/work-with-devin/deepwiki) | 소스 라인 역링크, 다이어그램 | 커버리지 편향. 이 계획은 엔티티 인벤토리 기준 생성 |
| Backstage catalog & TechDocs — [annotations](https://backstage.io/docs/features/software-catalog/well-known-annotations/) | YAML 정의 엔티티 종류 + owner/관계, **스코어카드**(완성도 점검) | 플랫폼(Node, 플러그인)이라 과함 |
| OpenMetadata / DataHub — [ES connector](https://docs.open-metadata.org/latest/connectors/search/elasticsearch/yaml), [Airflow lineage](https://docs.datahub.com/docs/lineage/airflow) | Pipeline→Task→Table→SearchIndex 엔티티 모델, sqlglot 기반 리니지 | 완전한 메타데이터 플랫폼이라 운영 자체가 별도 프로젝트 |
| incident.io — [AI](https://incident.io/ai-platform) | 고정 포스트모템 템플릿, "유사한 과거 장애" 블록 | SaaS |
| Anthropic Contextual Retrieval — [post](https://www.anthropic.com/engineering/contextual-retrieval) | 청크 컨텍스트 헤더, BM25 + 임베딩 + 리랭크 | — (기법) |
| peekadoc (자체) | 트리 + 딥 링크 + 읽기 UX | MkDocs 렌더러는 무거운 외부 의존성. 이 계획은 프로세스 내 렌더링 |

## 4. 기법

### 4.1 키워드 검색의 한국어·식별자 처리
- `unicode61`은 조사가 붙은 단어를 별개 토큰으로 취급(서울은 ≠ 서울), 검색 누락 발생 **[moderate]**
- `trigram`은 조사 문제를 풀지만 3글자 이상 필요 **[moderate]**. 우회책: LIKE fallback, 이중 인덱스, 바이그램 토크나이저(fts5-cjk)
- **kiwipiepy**(pip, 순수 wheel)로 형태소 사전 토큰화 시 한국어 BM25 벤치마크(AutoRAG)에서 정밀도 우위 **[moderate]**
- **결정**: v1은 자체 바이그램 분석기(의존성 없음, 측정 완료). P1에서 kiwipiepy 형태소 컬럼 추가 후 평가 세트 결과로 선택([ADR-005](decisions.md#adr-005)). 식별자는 형태소 분리 제외

### 4.2 하이브리드 검색
- RRF, k = 60 (Cormack et al. 2009) **[strong]**
- 리랭크: 크로스 인코더 `bge-reranker-v2-m3`(GPU <100 ms, CPU는 더 느림) vs LLM 리랭크(50건에 0.6–2 s) **[moderate]**
  - **결정**: UI는 RRF만. 에이전트 호출에는 상위 20건 Claude 리랭크 옵션(`rerank=true`), v1은 로컬 모델 미사용
- 청킹: 제목 단위 경계, ~500 토큰(최대 800), 약 60 토큰 오버랩(제목마다 리셋), 코드·표는 분할 안 함 **[moderate]**

### 4.3 임베딩 (한국어 근거)
- MTEB-ko-retrieval nDCG@10: KURE-v1 0.762, bge-m3 0.751, KoE5 0.734 **[moderate]**
- Voyage 다국어 모델도 한국어 성능 우수 주장(벤더 벤치마크) **[moderate]**
- 우리 텍스트는 한영 혼합에 식별자가 많아 BM25가 대부분 담당 **[inferred]**. 임베딩 효과는 주로 자연어 질문

### 4.4 소스 파싱
| 소스 | 방법 | 근거 |
|---|---|---|
| Airflow | REST `/api/v2`(Airflow 3, `POST /auth/token` JWT) 또는 `/api/v1`(Airflow 2, basic/세션 인증): `dags`, `dags/{id}/details`, `dags/{id}/tasks`, `dagRuns`, `importErrors`, `dagSources`(엔드포인트 경로는 실제 인스턴스에서 확인 필요) + SQL/인자는 Python `ast` | [API](https://airflow.apache.org/docs/apache-airflow/stable/security/api.html) [strong/moderate] |
| SQL DDL | `sqlglot.parse_one(ddl, dialect=…)`, 스키마를 알면 컬럼 리니지는 `sqlglot.lineage` | [sqlglot](https://sqlglot.com/sqlglot/lineage.html) [strong] |
| ES/OpenSearch | `_cat/indices?format=json&bytes=b`, `{idx}/_mapping`, `_alias`, `_settings`, `_stats`, `_ilm/explain` / `_plugins/_ism/explain` | [ILM explain](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-ilm-explain-lifecycle) [strong] |
| Confluence | `GET /wiki/api/v2/pages/{id}?body-format=storage` → XHTML(`ac:` 매크로) → Markdown(인스턴스에서 확인 필요) | [exporter ref](https://github.com/Spenhouet/confluence-markdown-exporter) [moderate] |
| PDF | 기본 pypdfium2(빠르고 라이선스 관대), 표가 많은 매뉴얼은 Docling(선택) | [benchmarks](https://arxiv.org/html/2410.09871v1) [moderate] |

### 4.5 추천 시스템 배치·검색 인덱스 운영 지식 [moderate/inferred]
운영자가 페이지에서 찾는 정보. [knowledge-model.md](knowledge-model.md) §4 섹션 템플릿의 바탕

- 테이블/피처별 **최신성 SLA**("파이프라인이 멈춤", "소스가 멈춤" 각각의 런북 포함)
- 생산자/소비자, **백필 절차와 멱등성 메모**
- 모델 버전 ↔ 학습 DAG ↔ 서빙 배치 ↔ 출력 테이블/인덱스 연결
- 인덱스 **alias → 현재 인덱스**, ILM/ISM 정책, **롤오버 / 리인덱스 / alias 전환 런북**
- `_stats` 스냅샷, 최근 장애

### 4.6 LLM 생성 문서의 함정과 대응
| 함정 | 이 계획의 대응 |
|---|---|
| 사실 환각 | 구조화 필드는 파서가 렌더링, LLM은 서술만. 섹션별 `sources`. 추론 엣지 표시 |
| 커버리지 편향 | 인벤토리 엔티티마다 페이지 생성, 페이지 없는 엔티티는 lint가 보고 |
| 최신성 저하 | 페이지별 소스 해시 → `stale` 상태. UI에 `updated_at` / `fetched_at` 표시 |
| 잦은 변경 / diff 노이즈 | 시맨틱 해싱, temperature와 무관한 결정적 템플릿, 섹션 단위 재생성, 생성 영역 펜스 구분 |
| 사람 수정 내용 유실 | `human_override`, 펜스 영역, verified 페이지는 덮어쓰지 않고 수정안 *제안* |

## 5. 계획에 쓴 Claude API 사실 (2026-09-28 확인)
| 사실 | 용도 |
|---|---|
| 기본 모델 **`claude-opus-5`**(MTok당 $5 / $25). Batches API **−50 %**, 배치당 요청 ≤100k개 또는 256 MB, 대부분 < 1 h 내 완료, 결과 29일 보관 | 전체 재빌드·야간 합성을 Batches로 처리 |
| 구조화 출력: `output_config.format` / `client.messages.parse()`(검증된 JSON) | 엔티티 추출, 분류, frontmatter 필드 |
| 프롬프트 캐싱: 접두어 일치, 캐시 읽기는 입력 가격의 ≈ 0.1×, breakpoint ≤ 4개 | 공통 시스템 프롬프트 + 스키마 + 분류 체계를 호출 간 캐싱 |
| 토큰 계산: `messages.count_tokens` | 재빌드 전 비용 추정 |
| Opus 5는 서버 측 거절 fallback(`fallbacks: "default"`, beta 헤더) 권장 | 대화형 호출에 적용(Batches에서는 불가) |
| 임베딩 API 없음 | 별도 embedder ([ADR-006](decisions.md#adr-006)) |

**비용 추정** (추론, 2차 리뷰에서 수정)

- 항목당 호출 1회 기준: 소스 항목 ≈ 2 000개 × 입력 ~6k + 출력 ~1.5k 토큰 ≈ 입력 12 M / 출력 3 M
  - `claude-opus-5` Batches 기준 호출 유형당 12 × $2.5 + 3 × $12.5 = $67.5
- 실제로는 항목당 **2–4회 호출**(서술/표준화, 요약, 추출, 분류) + thinking 토큰
- **Batches 사용 시: 전체 빌드 ≈ $120–250**, 일일 증분(≈ 2–5 % 변경) ≈ $4–10/일
- **v1은 동기 호출**(3차 리뷰에서 단순화)이라 약 2배: **전체 빌드 ≈ $250–500, $8–20/일**
- 예산은 Batches 내 캐시 이득 없음을 가정. 첫 실행 측정 후 기준 재설정
