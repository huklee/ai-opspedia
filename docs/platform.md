# 플랫폼 (공통 영역)

## 1. 한눈에 보기
- 구성: `uv`로 관리하는 Python 3.13 프로젝트 하나, tailnet 안 호스트 한 대의 프로세스 하나(`opspedia serve`), SQLite 파일 하나, 콘텐츠 git 저장소 하나
- 설정·인증·보안·배포·백업·비용·관측성·테스트 모두 **사용자 20–50명, 운영자 1명** 기준

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
    storage/             # repository.py (protocol), sqlite_repo.py, migrations/*.sql, analyzer.py, vectors.py, content_git.py
    catalog/             # tree.py, registry.py, linker.py (Aho–Corasick), graph.py, scorecard.py
    api/                 # app.py (FastAPI), routes_*.py, search.py, auth.py, agent.py, hooks.py
    frontend/            # templates/, static/ (css, js, 저장소에 포함한 katex/mermaid)
    types/               # TypeSpec 레지스트리: 문서 타입마다 모듈 하나 (ADR-017)
    common/              # models (pydantic), hashing.py, redact.py, markdown.py (markdown-it + pygments), logging.py
  content/               # 별도 git 저장소 (여기에 체크아웃), 생성된 Markdown + 사람이 쓴 Markdown
  data/                  # opspedia.db, embeddings.db, vectors.npz, worker.lock, raw/, backups/, evals/   (gitignore 대상)
  tests/                 # 단위, fixture (녹화한 HTTP), golden 페이지, e2e (Playwright)
  docs/
```
- 의존성(ADR-001): `fastapi uvicorn jinja2 pydantic pyyaml httpx markdown-it-py mdit-py-plugins pygments sqlglot
  numpy anthropic pypdfium2` (+ 선택: `voyageai`, `kiwipiepy`, `sentence-transformers`)

## 3. 설정
- `opspedia.yaml`: 출처, 스케줄, 분류 체계, 시스템/팀, LLM·임베더 설정, 예산
- 비밀 값은 환경 변수로만 전달(`env:NAME` 참조만 허용)
- `opspedia config check`: 설정 검증 + 출처별 읽기 전용 연결 확인

## 4. 보안과 개인정보
| 항목 | 통제 방법 |
|---|---|
| 네트워크 노출 | HTTPS, tailnet 전용(밖 IP → 403). TLS는 Tailscale 인증서 또는 자체 서명(ai-research-note 방식) |
| 인증 | **Tailscale 신원**(상대 IP에 `tailscale whois`, 결과 캐시), 관리할 비밀번호 없음. Q12 = no면 로컬 계정(scrypt, 세션, 요청 제한)으로 대체([ADR-011](decisions.md#adr-011)). 쓰기 요청엔 CSRF 헤더 |
| 인가 | viewer = tailnet 전원, editor/admin은 설정 허용 목록. API 토큰(해시 저장, 범위 `read`, `read+feedback`), 토큰별 요청 제한 |
| 출처 자격 증명 | 읽기 전용 서비스 계정, 비밀 값은 환경 변수에서 로드, 로그·콘텐츠·DB 저장 금지 |
| **LLM/임베딩 API로 데이터 반출** | 전송 전 마스킹(토큰, 키, DSN, 이메일, IP, 설정 가능한 정규식). 전송 가능한 출처 종류는 허용 목록. 출처별 `llm: off` 스위치. **소유자 승인 필요(Q5)** |
| 출처를 통한 프롬프트 인젝션 | 출처는 구분 블록 안 데이터로만 취급, 시스템 프롬프트로 출처 내 지시 수행 금지. 출력 스키마 검증 |
| 렌더링 안전성 | `MarkdownIt("commonmark", {"html": False})`(commonmark 프리셋은 raw HTML을 기본 허용). 링크 스킴은 http/https/mailto/상대 경로만. Mermaid `securityLevel: "strict"`, KaTeX `trust: false`. 링커는 HTML 문자열이 아닌 markdown-it 토큰 대상. 엄격한 CSP 헤더 |
| 콘텐츠 안전성 | 커밋 전 결정적 페이지 포함 **모든** 페이지 비밀 값 스캔(DAG `default_args`, params, 연결 문자열). 정규식 마스킹은 최선의 노력이라 누락이 반드시 생김. 스캐너가 커밋 차단, 미스캔 내용은 원격 push 금지. 모든 쓰기 동작은 감사 로그 |
| 프로세스 모델 | uvicorn 프로세스 하나(`--workers 1`, 운영에선 `--reload` 없음), Tailscale IP에 바인딩. 스케줄러 + 워커 서브프로세스 하나를 `fcntl.flock`으로 보호([ADR-002](decisions.md#adr-002), [ADR-010](decisions.md#adr-010)) |

## 5. 배포와 운영
- **호스트:** tailnet 안 Mac mini(launchd plist) 또는 작은 Linux VM(systemd 유닛). 실행 명령은 둘 다 `uv run opspedia serve`
- **업그레이드:** `git pull && uv sync && opspedia migrate && restart`. 마이그레이션은 번호 붙은 SQL 파일, 전진 전용
- **백업:** 매일 밤 DB `.backup`(일간 7개 + 주간 4개), `content/`는 원격 저장소로 `git push`
- **복구 드릴:** 분기마다. 새로 clone한 저장소에서 `opspedia rebuild` 성공이 기준
- **이 도구 자체의 런북**은 위키 `Systems/ai-opspedia/` 아래에 보관(도그푸딩으로 검증)

## 6. 관측성
- 구조화 JSON 로그: 요청 id, 사용자, 라우트, 지연 시간 / run id, 출처, 항목 수, LLM 토큰, $
- `/admin/runs`: 실행별 타임라인과 오류
- `/healthz`: DB, 콘텐츠 저장소, 출처별 마지막 성공 실행, 임베더 연결 여부 확인
- `/metrics`(Prometheus 텍스트 형식): 요청 지연 시간, 파이프라인 카운트
- 일일 요약(실패, lint, 비용) Slack 전송은 선택

## 7. 비용 모델 (LLM)
| 항목 | 추정 |
|---|---|
| 최초 전체 빌드(≈ 2 000개 항목 × 2–4회 호출), `claude-opus-5` **동기** 호출(v1) | ≈ **$250–500** (계산은 [research.md](research.md) §5) |
| 같은 작업을 Batches로(나중에) | ≈ $120–250 |
| 일일 증분(2–5 % 변경), 동기 호출 | ≈ **$8–20 / day** (Batches: $4–10) |
| 에이전트 리랭크 호출(선택) | 호출당 ≈ $0.01–0.03 |
| 임베딩(API) | 소액 (바뀐 청크만 재임베딩) |

- 통제 수단: 실행별 예산 게이트, 월간 예산 알림, 프롬프트 캐싱, 멱등성 기반 건너뛰기
- 비용이 문제면 Batches(−50 %) 또는 호출 유형별로 더 싼 모델
- 기본값(`claude-opus-5`) 외 모델은 소유자 결정. 설정에서 호출 유형마다 한 줄 수정

## 8. 확장 방식 ([ADR-017](decisions.md#adr-017))
| 추가할 것 | 할 일 |
|---|---|
| 페이지 타입 | `opspedia/types/`에 모듈 하나(TypeSpec: id 규칙, pydantic 모델, 템플릿, 엣지, 카탈로그 컬럼, 패싯) + Jinja 템플릿 하나 |
| 출처 종류 | `CONNECTORS`에 등록한 `Connector` 클래스 하나 + 설정 스키마 |
| Airflow / 클러스터 / Confluence 스페이스 추가 | 설정만 추가(`sources:` 목록 항목) |
| 엔티티/엣지 타입 | 그 타입을 만드는 TypeSpec에 선언. 그래프, 링커, 패싯에 자동 반영 |

## 9. 성공 지표 (매월 검토)
| 지표 | 1개월 후 목표 |
|---|---|
| 주간 활성 사용자 | 팀의 ≥ 60 % |
| 검색 결과 0건 비율 | < 10 % |
| 리뷰 완료된 SLA 핵심 페이지 | ≥ 80 % |
| **"새벽 3시 드릴"**: 시나리오 5개(DAG 실패, 오래된 인덱스, 컬럼 이름 변경, 온보딩, 에이전트 컨텍스트) | 각각 ≤ 2번 클릭 / < 30 s 안에 답변 |
| opspedia 링크를 인용한 포스트모템 | ≥ 50 % |

## 10. 테스트 전략
| 수준 | 대상 |
|---|---|
| 단위 | 분석기, 해싱, cron 파서, 렌더러, 병합 규칙, 링커, RRF, 그래프 CTE |
| Fixture/통합 | Airflow/ES/OpenSearch/Confluence/Jira 녹화 HTTP, fixture DAG 저장소(정적 + 동적), golden Markdown(바이트 단위 일치) |
| LLM 계약 | 녹화한 LLM 출력 스키마 검증, 미리 준비한 응답으로 도는 오프라인 모드, 환경 변수로 켜는 소규모 실제 호출 스모크 테스트 |
| 검색 평가 | 운영자 쿼리 세트로 `opspedia eval search` 실행(recall@5, MRR@10) |
| E2E | tailnet 너머 Playwright로 로그인, 트리, ⌘K, 페이지 앵커, 리뷰 흐름, 관리자 실행 화면, 모바일 레이아웃 확인 |
