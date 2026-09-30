# ai-opspedia PoC

dummy 데이터로 동작하는 오픈소스 기반 PoC. 설계 · 결과는 [../docs/mvp-poc.md](../docs/mvp-poc.md), 결정은 [ADR-021](../docs/decisions.md#adr-021).

```
원자료(dummy/) → 수집 → 합성(ast · sqlglot · 이름 매칭 · 템플릿 · LLM) → SQLite → OpenSearch nori 색인
                                                                     ↘ 위키 · 검색 · 관리자 화면 · API · MCP
```

## 목차

- [실행 모드](#실행-모드)
- [설치](#설치)
- [실행](#실행)
- [LLM 설정](#llm-설정-gpt-oss-120b)
- [화면과 API](#화면과-api)
- [운영 기능](#운영-기능)
- [프로젝트 구조](#프로젝트-구조)
- [테스트 · 평가](#테스트--평가)
- [문제 해결](#문제-해결)

## 실행 모드

| 모드 | 설정 파일 | 필요 | 검색 | LLM |
|---|---|---|---|---|
| **lite** | `config.lite.yaml` | Python 만 | SQLite FTS5 바이그램 | mock (흐름 시연) |
| **full** | `config.yaml` | + Java 21 · OpenSearch 3.8 · nori | OpenSearch nori (+ FTS5 비교군) | GPT-OSS-120B, 없으면 로컬 Ollama 대역 |

## 설치

지원: macOS (Apple Silicon · Intel), Linux (x86_64 · arm64). 모든 런타임은 `poc/.runtime/` 에 설치되며 시스템 설정을 바꾸지 않습니다.

```sh
cd poc
scripts/setup.sh --lite               # lite: uv · Python 3.12 · 의존성
scripts/setup.sh                      # full: + Temurin JDK 21 · OpenSearch 3.8(min) · analysis-nori
scripts/setup.sh --with-ollama        # full + 로컬 LLM(Ollama) · qwen2.5:3b
```

`setup.sh` 가 하는 일:

1. `uv` 가 없으면 설치 → `uv sync --python 3.12` (FastAPI · sqlglot · opensearch-py · markitdown · pyahocorasick · networkx · markdown-it-py · openai · mcp …)
2. (full) 호스트 CPU 에 맞는 Temurin JDK 21 — Apple Silicon 은 Rosetta 셸이어도 arm64
3. (full) OpenSearch min 배포본 + `analysis-nori` 플러그인 — 순수 Java 라 macOS 에서도 리눅스 tarball 사용, 보안 플러그인이 없으므로 **127.0.0.1 전용**
4. (`--with-ollama`) macOS 는 공식 릴리스 바이너리, Linux 는 공식 설치 스크립트 → `qwen2.5:3b`

수동 설치: Python 3.12 + `uv sync`, Java 21, [OpenSearch](https://opensearch.org/downloads.html) + `bin/opensearch-plugin install analysis-nori`, (선택) [Ollama](https://ollama.com/download).

## 실행

```sh
# full 모드
scripts/opensearch.sh start           # 127.0.0.1:9200 · 단일 노드 · 힙 768 MB
scripts/ollama.sh start               # (선택) 127.0.0.1:11434
uv run opspedia-poc seed-history      # DB 초기화 후 지난 1주 25회 실행 재현 (버전 · diff · 합성 내역)
uv run opspedia-poc serve --http      # http://127.0.0.1:8443

# lite 모드
uv run opspedia-poc -c config.lite.yaml seed-history
uv run opspedia-poc -c config.lite.yaml serve --http
```

| 명령 | 설명 |
|---|---|
| `run [--job full-sync\|status-refresh\|metrics-hourly] [--all-backends]` | 파이프라인 1회 |
| `seed-history [--keep]` | `dummy/history.yaml` 순서로 과거 실행 재현 |
| `serve [--http] [--host] [--port]` | 위키 · API 서버 |
| `eval` · `eval-ask` | 한국어 키워드 검색 평가 · 자연어 질의 평가 |
| `mcp` | MCP 도구 서버 (stdio) · 도구 `search` · `context` · `impact` |
| `context <entity>` | 엔티티 컨텍스트 JSON |

HTTPS(tailnet): `tailscale.crt` · `tailscale.key` 가 있는 디렉터리를 `OPSPEDIA_CERTS` 로 지정하고 `--http` 없이 `serve`. 서버는 loopback · Tailscale 대역(100.64.0.0/10) 외 접속을 거부합니다.

## LLM 설정 (GPT-OSS-120B)

`config.yaml` 의 `llm` (합성) · `ask` (자연어 검색) 는 같은 구조입니다.

```yaml
llm:
  provider: openai                 # off | mock | ollama | openai(OpenAI 호환)
  model: gpt-oss-120b
  base_url_env: GPT_OSS_BASE_URL   # 사내 엔드포인트 URL
  api_key_env: GPT_OSS_API_KEY
  fallback: {provider: ollama, model: "qwen2.5:3b", base_url: "http://127.0.0.1:11434/v1"}
```

- `GPT_OSS_BASE_URL` 이 있으면 GPT-OSS-120B, 없거나 장애면 `fallback` 모델 — 생성 이력에 **요청 모델 / 실제 응답 모델 / 사유**를 기록
- **Ollama Cloud** 로 GPT-OSS-120B 를 쓰려면 `scripts/ollama.sh signin` (ollama.com 계정 연결, API 키 불필요) 후 `fallback.model` 을 `gpt-oss:120b-cloud` 로. 요청 데이터가 ollama.com 으로 나가므로 **실데이터에는 사용 금지** (ADR-018)
- LLM 결과는 (모델, 프롬프트, 스키마) 해시로 `data/llm_cache.db` 에 캐시 → 같은 입력은 재호출 없음

## 화면과 API

| 경로 | 용도 |
|---|---|
| `/` | 홈: 최근 본 페이지 · 확인 필요 항목 · 최근 업데이트 · 중요 지표 |
| `/search?q=&tab=` | 검색 결과 (탭 · 지식 패널 · AI 답변) · 상단 검색창 자동완성 · `⌘K` |
| `/e/<entity>` | 엔티티 페이지 (예: `/e/dag:ranking_score_daily`) |
| `/e/<entity>/history` | 생성 이력: 버전 diff · 단계별 소요 · 원본 데이터 · 엔티티 추출 추적 · (장애) 합성 과정 · 되돌리기 |
| `/metrics` | 서비스 지표 (카테고리 · SLO · 추세) |
| `/admin` · `/admin/runs/<id>` | 잡 · 실행 이력 · 단계 · 합성 내역 · 차단된 원자료 |
| `/admin/upload` | 문서 업로드 (txt · html · Confluence XML · md) |
| `/api/search` · `/api/ask` · `/api/context/<entity>` · `/api/graph/<entity>` | 검색 · 자연어 질의 · 장애 대응 컨텍스트 · 영향 범위 |
| `/docs` | OpenAPI |

쓰기 API(`POST` · `DELETE`)는 `X-Requested-With: fetch` 헤더가 필요합니다. `OPSPEDIA_API_TOKEN` 을 설정하면 `/api/search` 외 API 에 `Authorization: Bearer` 가 필요합니다.

## 운영 기능

- **잡**: `full-sync`(전체 합성) · `status-refresh`(Airflow · 검색 엔진 상태만) · `metrics-hourly`(지표) — 원자료 해시가 같으면 새 버전 없음
- **커넥터 장애**: 해당 원천만 마지막 정상 스냅샷으로 대체, 실행은 `partial`
- **문서 업로드**: 미리보기(DB 쓰기 없음) → 반영(파이프라인 실행) → 삭제(문서 · 버전 · 엣지 · 원자료 흔적 제거)
- **되돌리기(undo)**: 엔티티의 최신 버전을 직전 내용으로 복원(v1 은 삭제), 원인 원자료를 차단하면 다음 수집부터 직전 원자료로 대체
- **주기 실행**: cron 예시 `5 * * * * cd /path/to/poc && uv run opspedia-poc run --job metrics-hourly`

## 프로젝트 구조

| 경로 | 역할 |
|---|---|
| `dummy/` | DAG 코드 · DDL · Airflow/검색 엔진 스냅샷 · Jira · 장애 원자료 · 매뉴얼 · 지표 · 업로드 샘플 · 이력 시나리오 · 평가 세트 |
| `opspedia_poc/interfaces.py` | 교체 경계 (Protocol) |
| `opspedia_poc/connectors/` | 원천별 커넥터 (DAG `ast` 파서 포함) |
| `opspedia_poc/synthesis/` | 엔티티 · 엣지 · 근거 수집, 템플릿 렌더링, 지표 판정, 장애 6단계 파서 |
| `opspedia_poc/adapters/` | 오픈소스 어댑터: sqlglot · OpenSearch · SQLite FTS5 · networkx · pyahocorasick · markitdown · 정적 변환기 · LLM(OpenAI 호환 · 라우팅 · 캐시 · mock) |
| `opspedia_poc/pipeline.py` · `history.py` | 잡 실행 · 단계 기록 · 원자료 차단 · 과거 이력 재현 |
| `opspedia_poc/storage.py` | SQLite 저장소 (문서 · 버전 · 엣지 · 실행 · 원자료 · 합성 내역) |
| `opspedia_poc/retrieval.py` · `ask.py` | 검색 결합(RRF) · 자연어 질의 |
| `opspedia_poc/api.py` · `viewer.py` · `web/` | API · 화면 (Jinja2 · markdown-it-py · Mermaid · Chart.js) |
| `scripts/` | `setup.sh` · `opensearch.sh` · `ollama.sh` · `gen_metrics.py` |

## 테스트 · 평가

```sh
uv run pytest -q              # 40개 · OpenSearch · LLM 없이 실행
uv run opspedia-poc eval      # 한국어 검색 34문항: OpenSearch nori vs FTS5+nori vs 바이그램
uv run opspedia-poc eval-ask  # 자연어 12문항: 키워드 vs +규칙 엔티티 vs +LLM 재작성
```

## 문제 해결

| 증상 | 조치 |
|---|---|
| `OpenSearch 없음` | `scripts/setup.sh` (full) 실행 |
| `TLS 인증서 없음` | `serve --http` 또는 `OPSPEDIA_CERTS` 지정 |
| 검색 결과가 비어 있음 | `seed-history` 또는 `run --all-backends` 로 색인 생성 |
| AI 답변 "실패" | `scripts/ollama.sh start` 또는 `GPT_OSS_BASE_URL` 확인 · lite 모드는 규칙 답변만 |
| 포트 충돌 | `serve --port 8010` |
