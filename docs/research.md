# 조사 및 검증

> - 근거 태그
>   - **[measured]** 대상 호스트 직접 측정
>   - **[strong]** 1차 문서/논문
>   - **[moderate]** 벤더 자료 또는 2차 출처
>   - **[inferred]** 자체 추론
> - 조사일: 2026-09-28

## 1. 요약: 조사 후 계획에서 달라진 점

| 발견 사항 | 계획 반영 |
|---|---|
| 기본 SQLite FTS5 `trigram`으로는 2음절 한국어 단어(`장애`, `배치`) 검색 불가 **[measured]** | 토크나이저 = nori(ES/OpenSearch `_analyze` + 사용자 사전) · 자체 한글 바이그램은 재현율 안전망 · 식별자 분리 유지 ([ADR-019](decisions.md#adr-019), [03-storage](components/03-storage.md) §4) |
| 전수 코사인 계산으로 충분(50k×1024에서 5.3 ms, 100k에서 ~9 ms) **[measured]** | 벡터 DB / ANN 인덱스 없이 float32 BLOB + numpy ([ADR-004](decisions.md#adr-004)) |
| DeepWiki식 생성은 중요 컴포넌트를 빠뜨리고 공식 문서로 오인되기 쉬움 **[moderate]** | **인벤토리 엔티티마다 페이지 하나** 생성 · "AI 생성" 라벨과 출처 표시 ([02-synthesis](components/02-synthesis.md)) |
| Karpathy "LLM Wiki": raw/ → LLM 위키 → 스키마 구조 · **ingest / query / lint** 연산 · append-only 로그 **[strong]** | `raw` 스냅샷과 SQLite `documents`(기준 원본) 분리 · 야간 **lint** 작업 · `log` 테이블 · 답변 write-back (P2) |
| 해시 기반 stale 판정은 과잉 발동(raw 해시 변경 중 의미 있는 것은 ~34 %) **[moderate]** | raw 바이트 대신 **정규화한 시맨틱 뷰**(파싱한 DAG 그래프 / DDL AST / 매핑 JSON) 해시 |
| Airflow REST가 실제 상태의 기준 · AST는 동적 DAG에서 실패 **[strong/moderate]** | DAG 커넥터 = REST 스냅샷 + 코드 세부용 AST · AST로 해석 불가하면 `dynamic: true` |
| OpenSearch는 **ISM**(`_plugins/_ism/explain`), Elasticsearch는 **ILM**(`_ilm/explain`) **[strong]** | 인덱스 커넥터에서 엔진별 분기 |
| Contextual Retrieval: 컨텍스트 임베딩 + BM25로 검색 실패 −49 % · 리랭크 추가 시 −67 % **[strong]** | 청크마다 결정적 컨텍스트 헤더(비용 없음) · LLM 컨텍스트 문장은 선택 (P1) |
| RRF(k=60)는 점수 정규화 불필요 · k∈[20,100]에서 안정적 **[strong]** | 결합 방식 = RRF만 · LLM 리랭크 없음 ([ADR-005](decisions.md#adr-005)) |
| 한국어 임베딩 선택지는 KURE-v1 / bge-m3(로컬), Voyage(API) · MTEB-ko-retrieval에서 KURE-v1 최상위(§4.3) **[moderate]** | 교체 가능한 임베더 · 기본 로컬 KURE-v1 ▸ bge-m3 ▸ (외부 API 승인 시) Voyage · 한국어 평가 세트(질의 80–100개)로 확정 ([ADR-006](decisions.md#adr-006), [ADR-019](decisions.md#adr-019)) |
| 사용 가능한 LLM 상한은 사내 서빙 GPT-OSS-120B(OpenAI 호환 API, 배치 API·토큰 과금 없음) (§5) | LLM 없는 자동화 우선 · LLM은 서술 보강·규칙 실패분만 · `LLMClient` 뒤에 숨겨 설정으로 교체 ([ADR-018](decisions.md#adr-018)) |
| 운영 호스트에서 git 사용 불가(개발·문서는 git 허용) | SQLite가 유일한 기준 원본 · `documents` + append-only `document_versions` ([ADR-020](decisions.md#adr-020)) |

## 2. 환경 조사 (대상 호스트: `huklee-01`, tailnet의 Mac mini)

| 항목 | 확인 결과 | 비고 |
|---|---|---|
| Python | 시스템 3.9.6 · `/usr/local/bin/python3.13`에 **Python 3.13.7** · **uv 0.9.5** | `uv` 프로젝트로 3.13 사용 |
| SQLite | **3.51.0**, FTS5(unicode61, trigram) 포함 | JSON1, 윈도 함수, WAL 사용 가능 |
| 로컬 Docker / Postgres / Airflow / ES | 미설치 | 옵션 A(로컬 인프라 없음) 근거 강화 · 대상 Airflow/ES는 원격 |
| 기존 프로젝트 | `ai-research-note`(Go, 계정·채널·SQLite가 있는 tailnet HTTPS 앱) · `peekadoc` / docserv(MkDocs Material 기반 읽기 전용 Markdown 브라우저) · `claude-slack-bridge` | 재사용 패턴: tailnet 전용 + TLS manager · 계정 모델 · 터미널 UI 가이드 · peekadoc의 트리·딥 링크 UX |

### 측정 결과 [measured]

- **한국어 대응 FTS5 프로토타입**
  - 한글은 겹치는 바이그램
  - 식별자는 `_ . camelCase` 기준 분리 + 원형 유지
  - `unicode61`

| 쿼리 | 결과 | 의미 |
|---|---|---|
| `장애` (2음절) | ✅ 문서 1,3 | 기본 `trigram`은 **0**건 |
| `파이프라인` | ✅ | 바이그램 AND 결합 |
| `feature store` | ✅ `feature_store_daily` | 식별자 분리 |
| `alias 전환`, `임베딩 생성` | ✅ | 한영 혼합 |

- **쿼리 빌더 점검**(2차 리뷰): `tokenchars '_'` 설정한 contentless FTS5 기준

| 케이스 | 결과 |
|---|---|
| `feature_store_daily` | 단일 토큰 유지(부분 토큰도 함께) |
| `es-prod.products_v3` | `es_prod_products_v3` 토큰으로 매칭 |
| `장애가` | → `장애` (조사 제거) |
| 따옴표로 감싼 `NOT OR` | 일반 단어로 처리 |
| rowid로 삭제 | 정상 동작 |
| `대응장애`(띄어쓰기 없음) vs `장애 대응` | 완화된 OR fallback 필요 |

- **벡터 검색**: numpy float32, 정규화 후 matmul + argpartition
  - 50 000 × 1024 → **5.3 ms, 205 MB**
  - 리서치 에이전트 측정: 100k × 1024 ≈ 9 ms (410 MB), 20k × 1024 ≈ 2 ms
  - 예상 코퍼스(청크 5–20k개) 기준 **< 3 ms** 예상

## 3. 선행 사례: 가져올 것과 가져오지 않을 것

| 시스템 | 가져올 것 | 가져오지 않는 이유 |
|---|---|---|
| Karpathy "LLM Wiki" (2026) — [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | raw/wiki/schema 계층 · **lint**(모순, stale, 고아 페이지, 누락 링크) · `index`/`log` · 좋은 답변 write-back | 제품이 아닌 패턴이라 그대로 적용 가능 |
| DeepWiki (Cognition) — [docs](https://docs.devin.ai/work-with-devin/deepwiki) | 소스 라인 역링크 · 다이어그램 | 커버리지 편향 · 이 계획은 엔티티 인벤토리 기준 생성 |
| Backstage catalog & TechDocs — [annotations](https://backstage.io/docs/features/software-catalog/well-known-annotations/) | YAML 정의 엔티티 종류 + owner/관계 · **스코어카드**(완성도 점검) | 플랫폼(Node, 플러그인)이라 과함 |
| OpenMetadata / DataHub — [ES connector](https://docs.open-metadata.org/latest/connectors/search/elasticsearch/yaml), [Airflow lineage](https://docs.datahub.com/docs/lineage/airflow) | Pipeline→Task→Table→SearchIndex 엔티티 모델 · sqlglot 기반 리니지 | 완전한 메타데이터 플랫폼이라 운영 자체가 별도 프로젝트 |
| incident.io — [AI](https://incident.io/ai-platform) | 고정 포스트모템 템플릿 · "유사한 과거 장애" 블록 | SaaS |
| Anthropic Contextual Retrieval — [post](https://www.anthropic.com/engineering/contextual-retrieval) | 청크 컨텍스트 헤더 · BM25 + 임베딩 + 리랭크 | — (기법) |
| peekadoc (자체) | 트리 + 딥 링크 + 읽기 UX | MkDocs 렌더러는 무거운 외부 의존성 · 이 계획은 프로세스 내 렌더링 |

## 4. 기법

### 4.1 키워드 검색의 한국어·식별자 처리
- `unicode61`은 조사가 붙은 단어를 별개 토큰으로 취급(서울은 ≠ 서울), 검색 누락 발생 **[moderate]**
- `trigram`은 조사 문제를 풀지만 3글자 이상 필요 **[moderate]**
  - 우회책: LIKE fallback, 이중 인덱스, 바이그램 토크나이저(fts5-cjk)
- **kiwipiepy**(pip, 순수 wheel)로 형태소 사전 토큰화 시 한국어 BM25 벤치마크(AutoRAG)에서 정밀도 우위 **[moderate]**
- **nori**(ES/OpenSearch `analysis-nori` 플러그인, mecab-ko-dic 사전)
  - `_analyze` API로 인덱스 생성 없이 토큰화 가능
  - 사내 클러스터에서 사용 가능(Q15에서 권한 확인)
- **결정**: 토크나이저 = nori, `_analyze` 호출([ADR-019](decisions.md#adr-019))
  - 구성
    - `nori_tokenizer`(`decompound_mode: mixed`) + `nori_part_of_speech` + `nori_readingform` + `lowercase`
    - 사용자 사전(`user_dictionary_rules`)에 엔티티 식별자·운영 용어집 반영
  - 바이그램의 역할
    - §2의 바이그램 측정 결과는 재현율 안전망 컬럼의 근거로 유지
    - 가중치 nori > 식별자 > 바이그램(평가로 결정)
  - 폴백
    - `_analyze` 불가 시 python-mecab-ko(같은 mecab-ko-dic) 로컬 폴백
    - 그것도 없으면 바이그램만
    - kiwipiepy는 대안으로만 검토
  - 식별자는 형태소 분리 제외(사용자 사전으로 보호)

### 4.2 하이브리드 검색
- RRF, k = 60 (Cormack et al. 2009) **[strong]**
- 리랭크: 크로스 인코더 `bge-reranker-v2-m3`(GPU <100 ms, CPU는 더 느림) vs LLM 리랭크(50건에 0.6–2 s) **[moderate]**
  - **결정**: UI·에이전트 모두 RRF만, LLM 리랭크 없음([ADR-018](decisions.md#adr-018))
  - 크로스 인코더도 v1 미사용
- 청킹 **[moderate]**
  - 제목 단위 경계
  - ~500 토큰(최대 800)
  - 약 60 토큰 오버랩(제목마다 리셋)
  - 코드·표는 분할 안 함

### 4.3 임베딩 (한국어 근거)
- MTEB-ko-retrieval nDCG@10: KURE-v1 0.762, bge-m3 0.751, KoE5 0.734 **[moderate]**
- Voyage 다국어 모델도 한국어 성능 우수 주장(벤더 벤치마크) **[moderate]**
- 우리 텍스트는 한영 혼합에 식별자가 많아 BM25가 대부분 담당 **[inferred]**
  - 임베딩 효과는 주로 자연어 질문
- **결정**: 기본값 로컬 KURE-v1 ▸ bge-m3 ▸ (외부 API 승인 시) Voyage
  - 사내 GPU 서빙이 있으면 그쪽 우선
  - 한국어 평가 세트로 최종 확정([ADR-006](decisions.md#adr-006))

### 4.4 소스 파싱
| 소스 | 방법 | 근거 |
|---|---|---|
| Airflow | REST `/api/v2`(Airflow 3, `POST /auth/token` JWT) 또는 `/api/v1`(Airflow 2, basic/세션 인증) · 대상: `dags`, `dags/{id}/details`, `dags/{id}/tasks`, `dagRuns`, `importErrors`, `dagSources` · 엔드포인트 경로는 실제 인스턴스에서 확인 필요 · SQL/인자는 Python `ast` | [API](https://airflow.apache.org/docs/apache-airflow/stable/security/api.html) [strong/moderate] |
| SQL DDL | `sqlglot.parse_one(ddl, dialect=…)` · 스키마를 알면 컬럼 리니지는 `sqlglot.lineage` | [sqlglot](https://sqlglot.com/sqlglot/lineage.html) [strong] |
| ES/OpenSearch | `_cat/indices?format=json&bytes=b`, `{idx}/_mapping`, `_alias`, `_settings`, `_stats`, `_ilm/explain` / `_plugins/_ism/explain` | [ILM explain](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-ilm-explain-lifecycle) [strong] |
| Confluence | `GET /wiki/api/v2/pages/{id}?body-format=storage` → XHTML(`ac:` 매크로) → Markdown · 인스턴스에서 확인 필요 | [exporter ref](https://github.com/Spenhouet/confluence-markdown-exporter) [moderate] |
| PDF | 기본 pypdfium2(빠르고 라이선스 관대) · 표가 많은 매뉴얼은 Docling(선택) | [benchmarks](https://arxiv.org/html/2410.09871v1) [moderate] |

### 4.5 추천 시스템 배치·검색 인덱스 운영 지식 [moderate/inferred]
- 운영자가 페이지에서 찾는 정보
  - [knowledge-model.md](knowledge-model.md) §4 섹션 템플릿의 바탕
- 테이블/피처별 **최신성 SLA**
  - "파이프라인이 멈춤", "소스가 멈춤" 각각의 런북 포함
- 생산자/소비자, **백필 절차와 멱등성 메모**
- 모델 버전 ↔ 학습 DAG ↔ 서빙 배치 ↔ 출력 테이블/인덱스 연결
- 인덱스 운영
  - **alias → 현재 인덱스**
  - ILM/ISM 정책
  - **롤오버 / 리인덱스 / alias 전환 런북**
- `_stats` 스냅샷, 최근 장애

### 4.6 LLM 생성 문서의 함정과 대응
| 함정 | 이 계획의 대응 |
|---|---|
| 사실 환각 | 구조화 필드는 파서가 렌더링, LLM은 서술만 · 섹션별 `sources` · 추론 엣지 표시 |
| 커버리지 편향 | 인벤토리 엔티티마다 페이지 생성 · 페이지 없는 엔티티는 lint가 보고 |
| 최신성 저하 | 페이지별 소스 해시 → `stale` 상태 · UI에 `updated_at` / `fetched_at` 표시 |
| 잦은 변경 / diff 노이즈 | 시맨틱 해싱 · temperature와 무관한 결정적 템플릿 · 섹션 단위 재생성 · 생성 영역 펜스 구분 |
| 사람 수정 내용 유실 | `human_override` · 펜스 영역 · verified 페이지는 덮어쓰지 않고 수정안 *제안* |

## 5. 계획에 쓴 LLM 사실 (2026-09-28 기준)
> - 이전 판의 Claude API 사실은 [ADR-007](decisions.md#adr-007)의 근거(이력)
> - [ADR-018](decisions.md#adr-018)로 대체되어 삭제

- GPT-OSS-120B 사내 서빙 전제 **[inferred]**
  - 엔드포인트·동시성은 Q5에서 확인

| 사실 | 용도 |
|---|---|
| 모델 **GPT-OSS-120B**, 사내 서빙 · OpenAI 호환 Chat Completions API · endpoint·모델명은 설정값 | `LLMClient` 뒤에서 `httpx`로 직접 호출(별도 SDK 없음) · 모델 교체는 설정 변경만 |
| 구조화 출력: 서빙이 지원하면 JSON schema 강제(`response_format` / guided decoding) | 엔티티 추출·분류의 규칙 실패분 · frontmatter 서술 필드 · 미지원 시 pydantic 검증 + 1회 재시도 |
| 배치 API 없음 | 동시 요청 풀(4–8) + 입력 해시 기반 결과 캐시 |
| 토큰 과금 없음 · 제약은 GPU 처리량 | 1실행당 처리량 게이트(LLM 호출 수·예상 소요 시간 상한) |
| 임베딩은 LLM과 별도 모델 | 별도 임베더 ([ADR-006](decisions.md#adr-006)) |

- **처리량 추정** (추론, ADR-018 기준)
  - 상한
    - 소스 항목 ≈ 2 000개 × 항목당 최대 2–4회 호출 ≈ 4 000–8 000회
    - 호출당 입력 ~6k + 출력 ~1.5k 토큰 → 입력 ≈ 24–48 M / 출력 ≈ 6–12 M 토큰
  - 실제
    - 요약·엔티티 추출·분류·장애/매뉴얼 표준화 대부분이 결정적 처리
    - LLM은 서술 보강과 규칙 실패분만이라 호출 수는 상한보다 크게 적을 전망(정량 값은 미정)
  - 소요 시간 ≈ LLM 호출 수 ÷ (동시성 4–8 × 호출당 처리 속도)
  - 일일 증분(≈ 2–5 % 변경)은 전체 빌드 호출의 2–5 % 수준
  - 비용
    - 달러 비용 없음
    - GPU 처리량은 첫 실행에서 측정 후 처리량 게이트 기준 설정
