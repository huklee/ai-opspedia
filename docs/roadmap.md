# 로드맵

## 1. 한눈에 보기
- **새벽 3시에 필요한 답을 LLM 없이 먼저** 제공
- 첫 릴리스 **R1 "온콜 카탈로그"**(≈ 2주)의 대상 질문
  - "이 DAG가 실패했다. 뭘 하는 DAG고, 다운스트림은 뭐고, 온콜은 누구인가"
  - "오늘 인덱스가 빌드되고 alias 전환까지 끝났나, 정상인가"
- 답은 코드와 API만으로 생성
  - LLM 합성, 그래프 심화, 에이전트 기능은 그 이후 단계

| 릴리스 / 마일스톤 | 주제 | 결과 (완료 기준) | 규모 |
|---|---|---|---|
| **PoC** | **오픈소스 기반 가설 검증** | H1–H5 판정 · OpenSearch nori vs FTS5 비교 · 상세는 [mvp-poc.md](mvp-poc.md) ([ADR-021](decisions.md#adr-021)) | 5–7 d |
| **R1** = M0 + M1a | **온콜 카탈로그 (LLM 없음)** | 범위 안 모든 DAG, 테이블, 인덱스 패밀리, alias에 페이지 · **nori 기반 한국어 키워드 검색** · `/e/<entity>` 고정 URL · **드릴:** 시나리오 1–2에 클릭 ≤ 2번 / < 30 s로 답함 · 세부는 표 아래 보충 | **9–11 d** |
| M1b | 카탈로그 심화 | 설정 기반 파이프라인과 리니지 · 컬럼 단위 영향 분석(`column:`) · 템플릿·수명 주기 정책 페이지 · Q2 결과상 필요하면 두 번째 검색 엔진 · `/api/context` + 읽기 토큰 · 카탈로그 뷰, 지표 페이지 · **드릴:** 시나리오 3–5(API로 에이전트 컨텍스트 조회) 통과 | 3–5 d |
| M2 | LLM 합성과 하이브리드 검색 | 매뉴얼 + 장애 기록 표준화(규칙·템플릿 우선) · 엔티티 페이지에 인용 달린 요약·서술 · 파이프라인 개요 · 키워드 평가 먼저, 벡터 + RRF는 조건부 도입 · 세부는 표 아래 보충 | 7–10 d |
| M3 | 상호 링크와 그래프 | 자동 링크 + 백링크, 다단계 영향 범위 + Mermaid, DAG/테이블/인덱스 페이지에 알려진 장애, 엔티티/그래프 API, lint, 페이지 이동 + 리다이렉트 | 3–4 d |
| M4 | 에이전트와 큐레이션 | `/api/retrieve`, `/api/tree/context`, 리뷰 워크플로(reviewed/verified, stale → 재생성/유지), 팀별 리뷰 큐 + 다이제스트, 백업 + 복구 드릴, `/healthz`, `/metrics` | 3–5 d |

- **보충:** 온콜 카탈로그 결과 세부
  - 갱신 시각 붙은 실시간 상태, 파싱한 다운스트림 목록
  - 인덱스 상태 점검(alias/빌드/업스트림 최신성, 변동, red/yellow)
  - 온콜 + 링크, 트리, ⌘K
  - nori 기반 한국어 키워드 검색 목표: 한국어 recall@5 ≥ 0.85, MRR@10 ≥ 0.7, 무결과율 < 5 %, p95 < 150 ms
  - 섹션 정정 + 피드백
- **보충:** LLM 합성과 하이브리드 검색 결과 세부
  - 요약·서술은 GPT-OSS-120B, 한국어 서술 품질 평가 통과 후 활성화
  - 키워드 평가 먼저, 차이가 확인되면 벡터 + RRF 도입(recall@5 ≥ +10 pts)
  - 아니면 키워드만 유지하는 이유를 문서화
- **합계 ≈ 25–35 영업일** (AI 코딩 어시스턴트를 쓰는 개발자 1명 기준)
  - 첫 실용 릴리스는 ~11일 차
  - nori 분석·사용자 사전·캐시가 R1에 들어가며 R1 +1일
- 일정 변동이 가장 큰 부분은 Confluence 변환기와 평가 세트 수집(다른 사람의 시간에 의존)
- 기술 선택
  - LLM은 사내 GPT-OSS-120B([ADR-018](decisions.md#adr-018))
  - 한국어 검색은 nori([ADR-019](decisions.md#adr-019))
  - 저장은 git 없이 SQLite 하나([ADR-020](decisions.md#adr-020))

## 2. 마일스톤별 작업 목록 (Pri = 마일스톤 안 우선순위, 컴포넌트 문서도 같은 행을 따름)

### M0 — 뼈대 (R1의 일부)
| Pri | 작업 | 컴포넌트 |
|---|---|---|
| P0 | `uv` 프로젝트, 설정 로더 + `config check`, CLI 뼈대, 로깅, `TypeSpec` + 커넥터 레지스트리 | platform |
| P0 | SQLite 마이그레이션, `Repository` 프로토콜, `documents`/`document_versions` 저장(트랜잭션 쓰기), `rebuild` | 03 |
| P0 | FastAPI 앱, TLS 관리자, Tailscale IP 바인딩, **Tailscale 신원** + 역할 허용 목록, 기본 템플릿 | 05, 06 |
| P1 | CI: ruff, 테스트, 타입 체크 | platform |

### M1a — 온콜 카탈로그 핵심 (R1의 나머지)
| Pri | 작업 | 컴포넌트 |
|---|---|---|
| P0 | `Connector`/`RawItem`/ChangeSet, 시맨틱 해싱 · 작업 + 워커(`flock`) + cron 스케줄러, DAG·DDL 디렉터리 스캔 폴링(mtime·해시, 5분) | 01 |
| P0 | 커넥터: `dag_repo`(SQL, `ExternalTaskSensor`, 컬럼 단위 AST), `airflow_rest`, `ddl`, 엔진 하나 대상 `search_index`, `config`(시스템, 팀, 온콜, 링크, 서비스) | 01 |
| P0 | 스냅샷 + **요청 시 실시간 상태 조회**(60 s 캐시), 갱신 시각과 함께 표시 | 01, 06 |
| P0 | TypeSpec 기반 렌더러: dag, table, index_family, alias, system, team · 펜스, 결정성, 변경 시 `document_versions` 추가, **파싱한 엣지 + 다운스트림 목록** | 02, 04 |
| P0 | **인덱스 상태 점검** + 업스트림 최신성 + `/api/health/indices` | 04, 05 |
| P0 | **nori 분석**(`_analyze`, 인라인 `nori_tokenizer` + `nori_part_of_speech` + `nori_readingform` + `lowercase`) + **사용자 사전**(엔티티 레지스트리 식별자 + 운영 용어집 자동 반영) + `analysis_cache`, 바이그램 안전망 + 식별자 컬럼, 동의어 쿼리 확장, python-mecab-ko 폴백 | 01, 03, 04 |
| P0 | 키워드 검색(패싯·자동완성·`search_log`) + 한국어 키워드 평가(기준치는 R1 완료 기준) · 트리 API, 인벤토리, `/e/{entity}` | 03, 04, 05 |
| P0 | 프런트엔드: 트리, 리더(코드/줄 번호/수식/표/앵커/Mermaid), ⌘K, 엔티티 헤더(온콜, 링크, 실시간 상태), **섹션 정정**, **피드백** | 06 |

### M1b — 카탈로그 심화
| Pri | 작업 | 컴포넌트 |
|---|---|---|
| P0 | 읽기 토큰 + **`/api/context`**(엔티티, 요약, 실시간 상태, 다운스트림, 장애, 런북, 온콜) | 05 |
| P0 | 설정 기반 파이프라인(렌더링 + Mermaid 리니지) · 컬럼 단위 영향 분석(`column:`, *Columns used by*) | 02, 04 |
| P1 | 템플릿·수명 주기 정책 커넥터 + 렌더러 · 두 번째 엔진 분기(Q2 결과상 필요할 때만) | 01, 02 |
| P1 | 패싯 검색 페이지, 카탈로그 뷰, 관리자용 실행 기록·지표 페이지 | 05, 06 |

### M2 — LLM 합성과 하이브리드 검색
| Pri | 작업 | 컴포넌트 |
|---|---|---|
| P0 | `LLMClient`(GPT-OSS-120B, OpenAI 호환 Chat Completions, `httpx` 직접 호출, 동시 요청 풀 4–8, 입력 해시 결과 캐시, 처리량 게이트, JSON schema 강제 또는 pydantic 검증 + 1회 재시도) · 인용 검사 validator | 02 |
| P0 | LLM 없는 자동화: 템플릿 요약(frontmatter 사실 정보), Jira 필드 → 템플릿 섹션 매핑·코멘트 시각순 타임라인, 매뉴얼 구조 보존 변환 · GPT-OSS-120B 한국어 서술 품질 골든 세트 평가 후 LLM 서술 활성화 | 02 |
| P0 | 엔티티 페이지 요약기·서술, 파이프라인 개요 · 커넥터 `files`(MD/PDF), `confluence`(DC/Cloud), `incidents`(Jira DC/Cloud), 표준화기 | 01, 02 |
| P0 | 평가 하네스 + 운영자 쿼리 세트(질문 80–100개, 그중 한국어 60개 이상: 조사·띄어쓰기 변형, 복합어, 한영 혼용, 약어·동의어, 식별자, 자연어 질문) · 비교군: 바이그램 단독 / nori / nori + 바이그램 / (+ 벡터) | 05 |
| P0 | 청커 + 청크 FTS, 임베더(ADR-006, 기본 로컬 KURE-v1) + `embeddings.db`, 마스킹 적용 numpy 검색, RRF · 평가에서 차이가 확인되면 활성화 | 02, 03, 05 |
| P1 | 추출기(레지스트리 우선, LLM은 미해결 멘션만), 분류기(규칙 우선, LLM은 규칙 실패분만) · 사람의 정정을 `S0`로 입력, `alerts` 커넥터(Airflow 실패, Q7 답에 따라 Alertmanager) | 01, 02 |

### M3 — 상호 링크와 그래프
| Pri | 작업 | 컴포넌트 |
|---|---|---|
| P0 | 장애 `affected` 엣지, 링커(토큰 대상 Aho–Corasick) + 백링크 + `[[wiki links]]` | 02, 04 |
| P0 | 그래프 서비스(다단계) + 엔티티/그래프 API + 컨텍스트 패널(영향 범위, 알려진 장애) | 04, 05, 06 |
| P1 | lint(고아 페이지, 깨진 링크, 해석 안 된 언급, REST↔AST 불일치, stale), 설정 기반 서비스, 페이지 이동 + 리다이렉트 | 03, 04 |

### M4 — 에이전트와 큐레이션
| Pri | 작업 | 컴포넌트 |
|---|---|---|
| P0 | `/api/retrieve`, `/api/tree/context`, OpenAPI 다듬기 | 05 |
| P0 | 리뷰 워크플로(reviewed/verified, stale → 재생성/유지), 팀별 리뷰 큐 + 주간 다이제스트 | 02, 05, 06 |
| P1 | 백업(매일 `Connection.backup()`, 7일 + 주간 4개) + 복구 드릴(DB 백업 복원 후 `opspedia rebuild`), 버전 이력·diff 화면, `/healthz`, `/metrics` | platform |

### 이후 (P2, 요청 시에만)
- 실제 MCP 서버, tailnet 전용 webhook, `opspedia export --markdown`
- 완성도 스코어카드, 모순 lint, contextual-retrieval 컨텍스트 줄, 답변 write-back, 인라인 에디터
- 안전장치를 둔 인덱스 작업(ADR-016), `info_schema` 리더, Postgres repository

## 3. 담당자 확인 사항
- [overview.md의 확인이 필요한 질문](overview.md#확인이-필요한-질문) 참고
  - 질문마다 답이 없을 때의 기본값 있음
- R1을 막는 질문: Q1–Q3, Q10, Q12, Q15

## 4. 리스크
| 리스크 | 영향 | 대응 |
|---|---|---|
| GPT-OSS-120B 엔드포인트 미확보(Q5) | 서술 없음 | R1만으로도 가치 있음 · LLM 없는 자동화 우선, `llm=off`, 로컬 임베더(KURE-v1) |
| 동적 DAG라 AST 파싱 불가 | 코드 세부 정보 누락 | 모든 DAG의 태스크 그래프는 REST 기준 · `dynamic` 플래그 |
| 한국어 검색 품질 목표 미달 | 결과 누락, 순위 저하 | nori + 사용자 사전 + 바이그램 안전망, 한국어 평가 세트로 R1부터 측정 · 미달 시 키워드 검색만 전용 ES/OpenSearch 인덱스(nori + BM25)로 이전 |
| nori 가용성(`analysis-nori` 미설치, `_analyze` 권한 없음, Q15) | nori 토큰 없음 | python-mecab-ko 로컬 폴백(같은 mecab-ko-dic), 그것도 없으면 바이그램만 · `analysis_cache`로 재호출 최소화 |
| GPT-OSS-120B 한국어 서술 품질 | 어색하거나 부정확한 서술 | 골든 세트 평가 후 활성화, 인용 검사, 규칙·템플릿 요약을 기본값으로 유지, `LLMClient` 설정으로 모델 교체 |
| 생성 문서 불신 | 도입률 저조 | 출처, 실시간 상태 갱신 시각, "AI-generated" 라벨, 클릭 한 번 섹션 정정, 리뷰 큐 |
| LLM 처리량 부족(GPU) | 합성 실행 지연 | 멱등 실행, 입력 해시 결과 캐시, 처리량 게이트(실행당 호출 수·예상 소요 시간 상한), LLM 없는 자동화 우선 |
| R1 전 범위 확대 | 첫 가치 전달 지연 | R1 완료 기준 = 드릴 시나리오 1–2 · 나머지는 전부 뒤로 |
