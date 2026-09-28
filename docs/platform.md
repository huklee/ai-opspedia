# 플랫폼 (공통 영역)

## 1. 한눈에 보기
- 구성
  - `uv`로 관리하는 Python 3.13 프로젝트 하나
  - tailnet 안 호스트 한 대의 프로세스 하나(`opspedia serve`)
  - SQLite 파일 하나(Markdown 전문·버전 포함, 유일한 기준 원본, [ADR-020](decisions.md#adr-020))
- 설정·인증·보안·배포·백업·처리량·관측성·테스트 모두 **사용자 20–50명, 운영자 1명** 기준

## 2. 코드 구조
```
ai-opspedia/
  pyproject.toml  uv.lock  opspedia.yaml.example
  opspedia/
    __main__.py          # CLI: serve | run <pipeline> | rebuild | lint | eval | user | token | backup
    config.py            # YAML + env: 참조, pydantic 검증
    ingestion/           # connectors/, scheduler.py, raw_store.py, changeset.py
    synthesis/           # renderers/, prompts/, llm.py, standardizer.py, extractor.py, categorizer.py,
                         # summarizer.py, chunker.py, embedder.py, pipeline.py, lint.py
    storage/             # repository.py (protocol), sqlite_repo.py, migrations/*.sql, analyzer.py, vectors.py, versions.py
    catalog/             # tree.py, registry.py, linker.py (Aho–Corasick), graph.py, scorecard.py
    api/                 # app.py (FastAPI), routes_*.py, search.py, auth.py, agent.py, hooks.py
    frontend/            # templates/, static/ (css, js, 저장소에 포함한 katex/mermaid)
    types/               # TypeSpec 레지스트리: 문서 타입마다 모듈 하나 (ADR-017)
    common/              # models (pydantic), hashing.py, redact.py, markdown.py (markdown-it + pygments), logging.py
  data/                  # opspedia.db, embeddings.db, vectors.npz, worker.lock, raw/, backups/, evals/   (gitignore 대상, 배포 아티팩트에 미포함)
  tests/                 # 단위, fixture (녹화한 HTTP), golden 페이지, e2e (Playwright)
  docs/
```
- 의존성(ADR-001)
  - 필수: `fastapi uvicorn jinja2 pydantic pyyaml httpx markdown-it-py mdit-py-plugins pygments sqlglot numpy pypdfium2`
  - 선택: `sentence-transformers`(로컬 임베더), `python-mecab-ko`(nori 폴백), `voyageai`
- LLM(GPT-OSS-120B, OpenAI 호환 API)
  - `httpx`로 직접 호출
  - 별도 LLM SDK 없음([ADR-018](decisions.md#adr-018))

## 3. 설정
- `opspedia.yaml`: 출처, 스케줄, 분류 체계, 시스템/팀, LLM(endpoint·모델명)·임베더·분석기(nori `_analyze`, 동의어) 설정, 처리량 게이트
- 비밀 값은 환경 변수로만 전달(`env:NAME` 참조만 허용)
- `opspedia config check`: 설정 검증 + 출처별 읽기 전용 연결 확인

## 4. 보안과 개인정보
| 항목 | 통제 방법 |
|---|---|
| 네트워크 노출 | HTTPS, tailnet 전용(밖 IP → 403) · TLS는 Tailscale 인증서 또는 자체 서명(ai-research-note 방식) |
| 인증 | **Tailscale 신원**(상대 IP에 `tailscale whois`, 결과 캐시), 관리할 비밀번호 없음 · Q12 = no면 로컬 계정(scrypt, 세션, 요청 제한)으로 대체([ADR-011](decisions.md#adr-011)) · 쓰기 요청엔 CSRF 헤더 |
| 인가 | viewer = tailnet 전원, editor/admin은 설정 허용 목록 · API 토큰(해시 저장, 범위 `read`, `read+feedback`), 토큰별 요청 제한 |
| 출처 자격 증명 | 읽기 전용 서비스 계정, 비밀 값은 환경 변수에서 로드, 로그·콘텐츠·DB 저장 금지 |
| **LLM/임베딩/분석기로 데이터 전송** | 외부 반출 최소화 · 사내 전송에도 마스킹 유지 · 출처별 `llm: off` 스위치 |
| 출처를 통한 프롬프트 인젝션 | 출처는 구분 블록 안 데이터로만 취급, 시스템 프롬프트로 출처 내 지시 수행 금지 · 출력 스키마 검증 |
| 렌더링 안전성 | `MarkdownIt("commonmark", {"html": False})` · 링크 스킴 제한 · Mermaid `securityLevel: "strict"`, KaTeX `trust: false` · 엄격한 CSP 헤더 |
| 콘텐츠 안전성 | DB 저장 전 **모든** 페이지 비밀 값 스캔 · 스캐너가 저장 차단 · 모든 쓰기 동작은 감사 로그 |
| 프로세스 모델 | uvicorn 프로세스 하나(`--workers 1`, 운영에선 `--reload` 없음), Tailscale IP에 바인딩 · 스케줄러 + 워커 서브프로세스 하나를 `fcntl.flock`으로 보호([ADR-002](decisions.md#adr-002), [ADR-010](decisions.md#adr-010)) |

- **보충:**
  - LLM/임베딩/분석기로 데이터 전송
    - LLM은 사내 서빙 GPT-OSS-120B라 외부 반출 없음(엔드포인트·인증은 Q5)
    - 임베딩은 기본 로컬 KURE-v1, 외부 API(Voyage)는 소유자 승인 시에만
    - nori `_analyze`는 사내 ES/OpenSearch 클러스터로만(Q15)
    - 사내 전송 마스킹 대상: 토큰, 키, DSN, 이메일, IP, 설정 가능한 정규식
    - 전송 가능한 출처 종류 허용 목록
  - 렌더링 안전성
    - commonmark 프리셋은 raw HTML을 기본 허용
    - 링크 스킴은 http/https/mailto/상대 경로만
    - 링커는 HTML 문자열이 아닌 markdown-it 토큰 대상
  - 콘텐츠 안전성
    - 스캔 범위: 결정적 페이지 포함, DAG `default_args`, params, 연결 문자열
    - 정규식 마스킹은 최선의 노력이라 누락이 반드시 생김
    - 미스캔 내용은 `opspedia export` 대상에서 제외

## 5. 배포와 운영
- **호스트:**
  - tailnet 안 Mac mini(launchd plist) 또는 작은 Linux VM(systemd 유닛)
  - 실행 명령은 둘 다 `uv run opspedia serve`
- **git 사용 범위:**
  - 소스 코드 개발·설계 문서는 개발 머신의 git
  - 운영 호스트는 git 불필요(설치·호출 없음, [ADR-020](decisions.md#adr-020))
- **업그레이드:** 호스트에서 `git pull` 없이 아티팩트로 배포
  1. 개발 머신에서 빌드: `uv build`(wheel) 또는 tarball
  2. 호스트로 복사: `scp` / `rsync`
  3. 아티팩트에서 설치: `uv pip install <wheel>` 또는 tarball 전개 후 `uv sync`
  4. `opspedia migrate` 후 서비스 재시작(launchd/systemd)
  - 마이그레이션은 번호 붙은 SQL 파일, 전진 전용
- **백업:**
  - 매일 밤 `Connection.backup()`(일간 7개 + 주간 4개)
  - `documents`·`document_versions` 포함이라 DB 백업이 곧 콘텐츠 백업
  - `embeddings.db`·`analysis_cache`도 함께
  - 선택: `opspedia export --markdown <dir>`(공유용 Markdown 덤프, 버전 관리 아님)
- **복구 드릴:**
  - 분기마다, git 미사용
  - 기준: 빈 호스트에 아티팩트 설치 + DB 백업 복원 후 `opspedia rebuild`(`documents`에서 FTS·청크·벡터·엔티티·엣지 재구성) 성공
- **이 도구 자체의 런북**
  - 위키 `Systems/ai-opspedia/` 아래에 보관
  - 도그푸딩으로 검증

## 6. 관측성
- 구조화 JSON 로그
  - 요청: 요청 id, 사용자, 라우트, 지연 시간
  - 실행: run id, 출처, 항목 수, LLM 호출 수·토큰·소요 시간
- `/admin/runs`: 실행별 타임라인과 오류
- `/healthz`: DB, 콘텐츠 저장소, 출처별 마지막 성공 실행, 임베더 연결 여부 확인
- `/metrics`(Prometheus 텍스트 형식): 요청 지연 시간, 파이프라인 카운트
- 일일 요약(실패, lint, LLM 처리량) Slack 전송은 선택

## 7. 처리량 모델 (LLM)
- GPT-OSS-120B 사내 서빙
  - 토큰 과금 없음
  - 제약은 GPU 처리량([ADR-018](decisions.md#adr-018))

| 항목 | 추정 |
|---|---|
| 최초 전체 빌드(≈ 2 000개 항목) | LLM 호출 상한 ≈ 4 000–8 000회(항목당 최대 2–4회) · 실제는 서술 보강·규칙 실패분만 · 계산은 [research.md](research.md) §5 |
| 소요 시간 | LLM 호출 수 ÷ (동시성 4–8 × 호출당 처리 속도) · GPU 처리량은 첫 실행에서 측정 |
| 일일 증분(2–5 % 변경) | 전체 빌드 호출의 2–5 % 수준 |
| 검색·리랭크 | LLM 호출 없음(RRF만) |
| 임베딩(로컬 KURE-v1) | CPU 배치(느림) 또는 사내 GPU 서빙 · 바뀐 청크만 재임베딩 |

- **보충:**
  - 최초 전체 빌드: 요약·추출·분류·표준화 대부분이 결정적 처리라 실제 LLM 호출은 서술 보강·규칙 실패분만

- 통제 수단
  - 실행별 처리량 게이트(LLM 호출 수·예상 소요 시간 상한)
  - 입력 해시 기반 결과 캐시
  - 멱등성 기반 건너뛰기
  - `llm=off`
- 모델 교체는 `LLMClient` 설정(endpoint·모델명) 변경만

## 8. 확장 방식 ([ADR-017](decisions.md#adr-017))
| 추가할 것 | 할 일 |
|---|---|
| 페이지 타입 | `opspedia/types/`에 모듈 하나(TypeSpec: id 규칙, pydantic 모델, 템플릿, 엣지, 카탈로그 컬럼, 패싯) + Jinja 템플릿 하나 |
| 출처 종류 | `CONNECTORS`에 등록한 `Connector` 클래스 하나 + 설정 스키마 |
| Airflow / 클러스터 / Confluence 스페이스 추가 | 설정만 추가(`sources:` 목록 항목) |
| 엔티티/엣지 타입 | 그 타입을 만드는 TypeSpec에 선언 · 그래프, 링커, 패싯에 자동 반영 |

## 9. 성공 지표 (매월 검토)
| 지표 | 1개월 후 목표 |
|---|---|
| 주간 활성 사용자 | 팀의 ≥ 60 % |
| 검색 결과 0건 비율(실사용 `search_log`) | < 10 % |
| 한국어 평가 세트 recall@5 | ≥ 0.85 ([ADR-019](decisions.md#adr-019), R1 완료 기준) |
| 한국어 평가 세트 MRR@10 | ≥ 0.7 |
| 한국어 평가 세트 무결과율 | < 5 % |
| 리뷰 완료된 SLA 핵심 페이지 | ≥ 80 % |
| **"새벽 3시 드릴"**: 시나리오 5개(DAG 실패, 오래된 인덱스, 컬럼 이름 변경, 온보딩, 에이전트 컨텍스트) | 각각 ≤ 2번 클릭 / < 30 s 안에 답변 |
| opspedia 링크를 인용한 포스트모템 | ≥ 50 % |

## 10. 테스트 전략
| 수준 | 대상 |
|---|---|
| 단위 | 분석기(nori `_analyze` 응답 fixture, python-mecab-ko 폴백, 바이그램, 동의어 쿼리 확장), 해싱, cron 파서, 렌더러, 병합 규칙, 링커, RRF, 그래프 CTE, 버전 저장(해시 중복 제거, 보관 개수), `difflib` diff |
| Fixture/통합 | Airflow/ES/OpenSearch(`_analyze` 포함)/Confluence/Jira 녹화 HTTP, fixture DAG 디렉터리(정적 + 동적), golden Markdown(바이트 단위 일치), 페이지 이동 트랜잭션, DB 백업 복원 후 `opspedia rebuild` |
| LLM 계약 | 녹화한 GPT-OSS-120B 출력 스키마 검증, `llm=off` 전체 동작 확인, 미리 준비한 응답으로 도는 오프라인 모드, 환경 변수로 켜는 소규모 실제 호출 스모크 테스트 |
| 검색 평가 | 질의 80–100개(한국어 60개 이상: 조사·띄어쓰기 변형, 복합어, 한영 혼용, 약어·동의어, 식별자, 자연어 질문)로 `opspedia eval search` 실행(recall@5, MRR@10, 무결과율) · 비교군: 바이그램 단독 / nori / nori + 바이그램 / (+ 벡터) |
| E2E | tailnet 너머 Playwright로 로그인, 트리, ⌘K, 페이지 앵커, 리뷰 흐름, 관리자 실행 화면, 모바일 레이아웃 확인 |
