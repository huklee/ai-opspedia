# 방식 비교 분석

## 1. 한눈에 보기

- 비교 기준 제약: **Python 서버, 오픈소스 플랫폼 도입 대신 직접 다루는 컴포넌트, 사용자 20–50명, 빠른 구현, 규모보다 유연성**
- 결론: **Option A(SQLite 기반 경량 Python 모놀리스)가 큰 차이로 1위(4.85 / 5)**
- 한국어 검색 품질은 최우선 요구사항([ADR-019](decisions.md#adr-019)). nori `_analyze`로 A도 C와 같은 형태소 분석 품질
- Option B는 적은 비용으로 옮길 수 있는 업그레이드 경로로 유지

| | **A. 경량 모놀리스** ✅ | B. Postgres 중심 | C. PRD 그대로의 폴리글랏 |
|---|---|---|---|
| 운영할 프로세스 | **1** (Python) | 2 (Python + PostgreSQL) | 5–6 (Python + Postgres + OpenSearch + Qdrant + Neo4j [+ Airflow]) |
| 문서 저장소 | SQLite 테이블(Markdown 전문 + 전체 버전, 유일한 기준 원본) | PostgreSQL 테이블 | PostgreSQL / MongoDB |
| 키워드 검색 | SQLite FTS5 + nori 토큰(`_analyze`) + 바이그램 안전망 + 식별자 분석기 | tsvector (+ 한국어용 `pg_bigm`/mecab 확장) | OpenSearch BM25 + `nori` 한국어 분석기 |
| 벡터 검색 | float32 BLOB + numpy 전수 탐색 | pgvector (HNSW) | Qdrant / Milvus |
| 그래프 | `edges` 테이블 + 재귀 CTE | 동일 (또는 Apache AGE) | Neo4j |
| 자체 작업 스케줄링 | 프로세스 내 스케줄러 + CLI | 동일 | Airflow |
| 프런트엔드 | 서버 렌더링 HTML + vanilla JS | 동일 | React/MDX SPA (BlockNote/TipTap) |

## 2. 점수 (1 = 나쁨 … 5 = 매우 좋음)

| 기준 (가중치) | A | B | C | 비고 |
|---|---|---|---|---|
| 구현 속도 (25 %) | **5** | 4 | 2 | A: 인프라 없음, 스키마 파일 하나. C: 클라이언트 4개, 스키마 4개, 동기화 작업 |
| 유연성 / 적응성 (20 %) | **5** | 4 | 3 | A: 스키마 변경 = SQL 수정 + `documents`에서 재구축. C: 저장소 전체 리인덱스 |
| "직접 다루고, 플랫폼 도입 없음" 적합도 (15 %) | **5** | 4 | 1 | C는 플랫폼 4개 도입 |
| 운영 부담 (15 %) | **5** | 3 | 1 | 백업: A = `Connection.backup()`으로 파일 1개 복사. C = 백업 전략 4가지 |
| 한국어 검색 품질 (20 %) | **5** | 4 | **5** | A: nori `_analyze` + 바이그램 안전망. B: 같은 사전으로 분석 가능하나 tsvector 한계. C: nori 네이티브 |
| 확장 여유 (5 %) | 2 | 4 | **5** | 사용자 20–50명 / 청크 ≤100k 규모에선 무의미 |
| **가중 합계** | **4.85** | 3.85 | 2.65 | |

### 한국어 검색 품질 재평가 ([ADR-019](decisions.md#adr-019))
- 가중치 10 % → **20 %** 상향(최우선 요구사항). 대신 구현 속도 30 → 25 %, 유연성 25 → 20 %
- nori를 ES/OpenSearch `_analyze` API로 호출하면 A도 C와 같은 형태소 분석 품질(같은 mecab-ko-dic 사전, 사용자 사전 포함)
- 남는 차이(ES synonym 필터, 하이라이트 등)는 직접 구현(동의어 = 쿼리 확장) 또는 에스컬레이션 경로로 해결
- 에스컬레이션: 한국어 평가 목표(recall@5 ≥ 0.85, MRR@10 ≥ 0.7, 무결과율 < 5 %) 미달 시 **키워드 검색만 전용 ES/OpenSearch 인덱스(nori + BM25)로 이전**. `Repository` 인터페이스 뒤라 교체 가능, 나머지는 A 그대로

### 리뷰 3회 후 재검증
- 인덱스 감독, 복구 규칙, 정정, 컨텍스트 API 추가로 견적이 ≈ 25–35일로 증가(nori 반영 후)
- 같은 요구 사항이 **B와 C**에도 적용. 커넥터, 렌더러, 큐레이션, API가 그대로 필요하고 저장소만 더 많음. 늘어난 분량은 방식과 무관해 순위 변동 없음
- 3차 리뷰의 단순화로 A의 속도 유지
  - 동기 LLM 호출(동시 요청 풀 4–8)
  - 리스 대신 파일 락
  - 별도 계정 대신 Tailscale 신원
  - offset 맵 제외. 버전 이력은 `document_versions` 하나([ADR-020](decisions.md#adr-020))
  - webhook과 MCP는 뒤로 미룸

## 3. 방식별 상세

### A. SQLite 기반 경량 Python 모놀리스 ✅
- **형태**
  - FastAPI 앱 하나가 위키와 API 서빙
  - 배치 작업은 CLI 명령(`opspedia run <pipeline>`). 프로세스 내 스케줄러나 webhook이 실행하며 코드 공유
  - 저장소는 SQLite 파일 하나(WAL). Markdown 전문(`documents`)과 전체 버전(`document_versions`)까지 저장, git 없음([ADR-020](decisions.md#adr-020))
- **맞는 이유**
  - 청크 5–20k 규모에선 FTS5 BM25, numpy 행렬 스캔 모두 한 자릿수 ms 응답([research.md](research.md) §2)
  - SQLite로 동시 읽기 수십 개 + 배치 쓰기 하나는 무리 없이 처리
- **리스크와 대응**
  - *단일 writer* → DB 락을 잡는 파이프라인 워커 하나. 웹 쪽 쓰기는 소량(리뷰 상태, 계정)
  - *한국어 BM25 품질* → nori `_analyze` 토큰 컬럼(R1 P0) + 바이그램 안전망, 한국어 평가 세트로 측정. 목표 미달 시 키워드 검색만 전용 ES/OpenSearch 인덱스로 이전
  - *SQLite 한계 도달* → 모든 접근이 `storage.Repository` 경유. B 전환 = 모듈 교체 + `documents`·`document_versions` 이전 후 재구축

### B. Postgres 중심
- **형태:** 앱은 동일. PostgreSQL에 문서, `tsvector` FTS, `pgvector`, 경로용 `ltree`
- **장점:** 동시 쓰기, HNSW, 성숙한 백업 체계, BI 접근 용이
- **단점**
  - 운영·보안을 챙길 DB 서버 추가
  - 한국어 FTS엔 여전히 확장(`pg_bigm`/`textsearch_ko`) 필요. nori `_analyze` 토큰을 넣을 수는 있으나 tsvector 랭킹 한계
  - 스키마 반복 속도 저하
- **고를 때:** 여러 팀이 동시에 쓰거나 코퍼스가 청크 ~1M개를 넘을 때

### C. PRD 그대로의 폴리글랏
- **형태:** PostgreSQL/MongoDB + OpenSearch (+ nori) + Qdrant/Milvus + Neo4j. 합성 단계에서 여러 저장소로 나눠 쓰고, 프런트엔드는 React/MDX, 자체 파이프라인은 Airflow
- **장점:** 계층별 최고 수준 도구, 운영팀에 익숙함(이미 ES/Airflow 운영 중), 규모 제한 없음
- **단점**
  - 일관성을 맞출 저장소 4개, 클라이언트 4개
  - 구축·온콜 부담 ~4배, 며칠이 아니라 몇 주 단위 작업
  - "오픈소스 플랫폼 대신 직접 다룬다"는 원칙과 정면으로 충돌
- **고를 때:** 이게 전사 플랫폼이 될 때

## 4. Option A 안의 세부 결정

| 질문 | 검토한 선택지 | 결정 | ADR |
|---|---|---|---|
| 기준 원본 (source of truth) | DB 행 / git의 Markdown 파일 / 둘 다 | **SQLite = 유일한 원본**(`documents` + append-only `document_versions`). git 사용 불가로 ADR-003 대체 | [ADR-020](decisions.md#adr-020) |
| 웹 계층 | stdlib `http.server` / Starlette / **FastAPI** / Django | **FastAPI**(타입 지원, 에이전트용 OpenAPI, 가벼움)를 자체 얇은 모듈 뒤에 배치 | [ADR-009](decisions.md#adr-009) |
| 프런트엔드 | React SPA (BlockNote/TipTap/MDX) / **서버 렌더링 + vanilla JS** | 서버 렌더링(빌드 단계 없음). 트리, ⌘K, 패널에 점진적 JS | [ADR-009](decisions.md#adr-009) |
| Markdown 렌더링 | 클라이언트 측 (marked) / **서버 측 (markdown-it-py + Pygments)** / MkDocs | 서버 측: UI, API, 내보내기가 렌더러 하나 공유. KaTeX/Mermaid는 브라우저에서 | [ADR-009](decisions.md#adr-009) |
| 한국어 키워드 분석 | 자체 바이그램 / kiwipiepy / **nori `_analyze` + 사용자 사전 + 바이그램 안전망** / 전용 ES 인덱스 | nori(R1 P0). 목표 미달 시 전용 ES/OpenSearch 인덱스로 에스컬레이션 | [ADR-019](decisions.md#adr-019) |
| 임베딩 | 없음 / **교체 가능: 로컬 KURE-v1 ▸ bge-m3 ▸ (외부 API 승인 시) Voyage ▸ 끔** | 기본 KURE-v1, 한국어 평가 세트로 확정 | [ADR-006](decisions.md#adr-006) |
| 리랭크 | 없음 / cross-encoder / **RRF만** | RRF만. LLM 리랭크 없음 | [ADR-005](decisions.md#adr-005) |
| LLM | 외부 API / **사내 GPT-OSS-120B(`LLMClient` 뒤, 설정으로 교체)** / 끔 | GPT-OSS-120B, LLM 없는 자동화 우선 | [ADR-018](decisions.md#adr-018) |
| 스케줄링 | cron + CLI / APScheduler / Airflow / **자체 미니 스케줄러 + CLI + webhook** | 자체 구현(~150 LOC) + jobs 테이블 | [ADR-010](decisions.md#adr-010) |
