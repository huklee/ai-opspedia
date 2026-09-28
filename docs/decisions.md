# 기술 결정 기록 (ADR log)

> 형식: 배경 → 결정 → 결과. 표시가 없으면 상태는 **채택**. 결정 변경 시 기존 기록은 수정 없이 두고 대체 ADR을 새로 추가

| ADR | 결정 (한 줄 요약) | 상태 |
|---|---|---|
| [001](#adr-001) | 작은 컴포넌트는 직접 구현. 라이브러리는 사용, 플랫폼은 미사용 | 채택 |
| [002](#adr-002) | 웹 서버와 배치 워커를 한 Python 코드베이스, 한 배포 단위로 | 채택 |
| [003](#adr-003) | git의 Markdown이 기준 원본, SQLite 파일 하나는 재생성 가능한 인덱스 | 채택 |
| [004](#adr-004) | 벡터는 float32 BLOB + numpy 전수 코사인. 벡터 DB 없음 | 채택 |
| [005](#adr-005) | 하이브리드 검색 = FTS5(자체 한국어/식별자 분석기) ⊕ 벡터, RRF 결합. 리랭크는 선택 | 채택 |
| [006](#adr-006) | 교체 가능한 임베더. 기본 Voyage API, 대안은 로컬 bge-m3 또는 끄기. 평가로 선택 | 제안 (Q5 필요) |
| [007](#adr-007) | 합성은 Claude(`claude-opus-5`). **동기 호출 먼저**, Batches는 나중에. structured outputs, 캐싱 | 제안 (Q5 필요) |
| [008](#adr-008) | 사실 정보는 파서, 서술은 LLM 담당. 모든 문서/엣지에 출처 기록 | 채택 |
| [009](#adr-009) | FastAPI + 서버 렌더링 HTML + vanilla JS. Markdown 렌더링은 서버에서 | 채택 |
| [010](#adr-010) | 자체 미니 스케줄러 + CLI + 폴링. 파이프라인 워커는 파일 락으로 하나만 | 채택 |
| [011](#adr-011) | tailnet 전용 HTTPS. 신원은 Tailscale(`whois`), 역할은 설정 파일, API 토큰 | 채택 (Q12) |
| [012](#adr-012) | 그래프는 `edges` 테이블 + 재귀 CTE. 그래프 DB 없음 | 채택 |
| [013](#adr-013) | **의미 기반** 콘텐츠 해시로 증분 파이프라인 실행 여부 판단 | 채택 |
| [014](#adr-014) | 사람의 큐레이션: 리뷰 상태, 펜스 영역, 섹션별 정정, stale 후 재생성/유지 | 채택 |
| [015](#adr-015) | 에이전트 인터페이스 = 한 번에 끝나는 `/api/context` 포함 REST/OpenAPI. 정식 MCP 서버는 나중에 | 채택 |
| [016](#adr-016) | 검색 인덱스는 관찰·문서화·점검만, v1에 쓰기 없음. 고정 페이지 단위는 인덱스 패밀리 | 채택 |
| [017](#adr-017) | 레지스트리로 확장: 문서 타입마다 `TypeSpec` 모듈 하나, 커넥터 종류마다 항목 하나 | 채택 |

---

### ADR-001
**작은 컴포넌트는 직접 구현. 라이브러리는 사용, 플랫폼은 미사용**
- *배경:* 요구사항은 "오픈소스 대신 Python 서버와 직접 만든 프레임워크", 빠른 전달, 유연성
- *결정:*
  - 플랫폼 도입 없음: 자체 인덱스용 Elasticsearch/OpenSearch, Qdrant/Milvus, Neo4j, 자체 작업용 Airflow, Wiki.js/Outline/Backstage/DataHub
  - 며칠을 아끼는 곳에는 작고 교체 가능한 라이브러리: `fastapi`/`uvicorn`, `jinja2`, `markdown-it-py`(+플러그인), `pygments`, `sqlglot`, `httpx`, `numpy`, `anthropic`, `pypdfium2`, `pyyaml`, 선택 `kiwipiepy`/`voyageai`/`sentence-transformers`(로컬 임베더 전용)
  - 라이브러리마다 자체 모듈로 감싸 언제든 교체 가능
- *결과:*
  - Option C 대비 초기 구축 약 1주 단축. 분석기, 스케줄러, 트리, 링커, 하이브리드 랭커는 직접 소유
  - *해석 리스크:* "오픈소스 대신"이 서드파티 라이브러리 전면 금지라면, 얇은 모듈 경계 덕에 FastAPI는 표준 라이브러리 `http.server`+`wsgiref`로, markdown-it은 자체 렌더러로 교체 가능. 추가 비용 약 +3–4일

### ADR-002
**Python 코드베이스와 배포 단위는 하나**
- 웹(`opspedia serve`)과 배치(`opspedia run …`)가 모델, 저장소, 설정을 공유
- 스케줄러는 서버 프로세스 안, 파이프라인 실행은 서브프로세스. 크래시 격리, 웹 요청과 GIL 경합 없음
- 서버 프로세스는 정확히 하나: `uvicorn --workers 1`, 운영에서 `--reload` 금지. 워커가 여럿이면 스케줄러와 벡터 행렬도 여럿이 되는 문제
- *결과:* venv, systemd/launchd 유닛, 로그 스트림 모두 하나

### ADR-003
**git의 Markdown이 기준 원본(source of truth), SQLite는 다시 만들 수 있는 인덱스**
- *배경:* 버전 관리, diff, 사람의 리뷰, 손쉬운 내보내기, 빠른 DB 스키마 변경 필요
- *결정:*
  - 합성 단계가 `content/**/*.md`를 쓰고 커밋(파이프라인 실행당 커밋 하나, 메시지 = run id + 요약)
  - `opspedia rebuild`: `content/`와 `embeddings.db`로 `opspedia.db`(문서, FTS, 청크, 벡터 캐시, 엔티티, 엣지) 재구성
  - 콘텐츠 저장소에 상태, 리뷰, 출처 해시, 생성기/프롬프트/모델 버전, 추론된 엣지, `_meta/redirects.yaml`도 보관. 재구성해도 링크 유지, 전체 재합성 불필요
  - 운영용 테이블(토큰, 신원 캐시, 작업, 실행, 리뷰 이벤트, 피드백, 검색 로그, 스냅샷, 감사 로그, 이후 `batch_items`)은 SQLite에만 두고 **백업 대상**([03-storage](components/03-storage.md) §1)
- *결과:* 스키마 변경 비용이 낮음(drop 후 rebuild). 이력은 `git log`. 저장소 인터페이스 덕에 Option B 여지도 유지

### ADR-004
**벡터는 SQLite BLOB에 저장, 검색은 numpy**
- 호스트 측정 50k×1024 기준 5.3 ms, 코퍼스 추정 5–20k 청크
- `data/vectors.npz`(ids, doc_idx, matrix, generation)에서 정규화된 float32 행렬 하나로 로드
- 콘텐츠 작업마다 원자적 교체. 이 규모에선 mmap 불필요([03-storage](components/03-storage.md) §5)
- 청크 500k 초과 또는 p95 20 ms 미만 유지 불가 시 재검토(sqlite-vec ANN 또는 pgvector)

### ADR-005
**하이브리드 검색**
- 분석된 텍스트(한글 바이그램 + 식별자 조각 + 식별자 전체)의 FTS5 BM25와 코사인 top-k를 **RRF (k=60)** 로 결합
- 필터(type, system, team, tags, status, env)는 랭킹 전 SQL에서 적용
- 엔티티 ID·제목 정확 일치는 1위로 승격
- `rerank=true`면 Claude가 상위 20개 재정렬(에이전트 API)
- P1: kiwipiepy 형태소 컬럼 추가, 분석기 가중치는 평가 세트(`opspedia eval search`)로 결정

### ADR-006
**교체 가능한 임베더**(`Embedder.embed(texts) -> float32[n, d]`)
- `voyage`: API, 다국어. 외부 API 허용 시 기본값
- `local`: sentence-transformers로 CPU에서 bge-m3 / KURE-v1. ~2 GB, 배치가 느림
- `off`: BM25만 사용. 나머지 기능은 그대로 동작
- `(model, chunk_hash)` 캐시로 제공자를 바꿔도 재임베딩은 한 번
- M2에서 30–50개 쿼리 평가로 최종 선택. 데이터 외부 반출 질문이 풀릴 때까지 *상태는 제안*

### ADR-007
**합성은 Claude.** 모델은 `claude-opus-5`(현재 가이드 기준 기본값)
- **v1은 작은 스레드 풀(동시 4–8개) 동기 호출 + 예산 게이트.** 제대로 동작하는 가장 단순한 파이프라인(3차 리뷰)
- **Message Batches**(−50 %)는 처리량이 커지면 추가(로드맵 "later", 설계는 [02-synthesis](components/02-synthesis.md) §3.2에 보존)
- 호출 유형별 모델(예: 요약엔 Sonnet급)은 소유자 결정 사항이라 설정으로 노출
- 모든 JSON(엔티티, 카테고리, frontmatter 필드)은 **structured outputs**(`messages.parse` / `output_config.format`)
- 고정 시스템 프롬프트, 스키마, 분류 체계에 프롬프트 캐싱
- 실행 전 비용 추정은 `count_tokens`, 대화형 호출엔 서버 측 거절 대체 처리(refusal fallback)
- 프롬프트는 버전 파일로 관리(`@version`이 붙은 `synthesis/prompts/*.md`)
- 외부 LLM 불가 시 `llm=off`. 이때도 결정적(deterministic) 페이지, BM25, 그래프(M1 범위)는 정상 동작

### ADR-008
**사실 정보는 파서, 서술은 LLM 담당**
- 구조화 필드와 표는 모두 결정적 렌더러 담당
- LLM은 펜스로 나눈 서술 섹션(`summary`, `purpose`, `how it fails`, `runbook draft`)만 채움. 근거는 주어진 출처 발췌로 한정
- 섹션마다 `sources[n]` 인용 필수. 인용이 없거나 출처로 확인 안 되는 주장이 있으면 검증 실패, 그 섹션은 비움

### ADR-009
**웹 스택**
- FastAPI(API + OpenAPI 문서) + Jinja2 템플릿 + vanilla JS 모듈(트리, ⌘K, 패널). SPA 빌드 체인 없음
- Markdown → HTML은 서버에서 markdown-it-py(+ 표, 앵커, 각주, 체크리스트, front-matter)와 Pygments(줄 번호)로 변환
- KaTeX, Mermaid는 브라우저 실행. tailnet 전용이라 에셋은 저장소에 포함
- PRD의 BlockNote/TipTap/MDX 편집기는 보류. v1은 읽기 전용, 편집은 Markdown(git)이나 간단한 리뷰 동작으로

### ADR-010
**자체 미니 스케줄러**
- Cron 표현식(`m h dom mon dow` 자체 파서), 주기 트리거, 폴링, 선택적 tailnet 내부 webhook은 모두 `jobs` 테이블에 **넣기만** 함
- 실행은 워커 하나가 우선순위 순으로 처리(SQLite는 쓰기 주체가 하나)
- 워커는 하나: OS 파일 락(`data/worker.lock`에 `fcntl.flock`), 크래시 시 자동 해제. CLI `--direct`도 같은 락 사용. 2차 리뷰의 heartbeat 리스 테이블 대체(단일 호스트라 더 단순)
- 작업 가져오기는 문장 하나: `UPDATE jobs SET state='running', pid=? WHERE id=(SELECT id … LIMIT 1) RETURNING *`
- 시작 시 pid가 죽은 `running` 작업은 실패 처리 후 재시도, 놓친 cron 슬롯은 **한 번만** 실행
- 타임아웃 시 워커가 서브프로세스 종료
- 오래 걸리는 LLM 배치(나중에 Batches를 켜면)는 `synth-submit` / `synth-collect`로 분리해 대기 중 락 미보유([02-synthesis](components/02-synthesis.md) §3.2)
- CLI `opspedia run …`은 기본적으로 서버 큐에 추가. `--direct`는 락을 잡을 수 있을 때만(예: 서버 중단) 프로세스 안에서 바로 실행
- Webhook은 P2, 기본은 폴링

### ADR-011
**접근 제어**
- HTTPS는 tailnet 전용. TLS 관리자는 ai-research-note 것을 재사용(tailnet HTTPS가 켜져 있으면 Tailscale 인증서, 아니면 자체 서명)
- 서버는 **Tailscale 인터페이스 주소에만 바인딩**(상태 점검용 loopback 추가)
- 신원(기본값, Q12): 호출자의 Tailscale 로그인. `tailscale whois <remote ip>`로 확인 후 캐시. 로컬 비밀번호, 로그인 페이지, 요청 제한 불필요
- 역할: `viewer`는 tailnet 전원, `editor` / `admin`은 설정 파일 허용 목록
- 대안: 노드 공유 사용자나 tailnet 밖 사용자가 있으면(Q12 = no) 로컬 계정(scrypt 해시, 세션). ai-research-note와 같은 방식
- 에이전트·콜백용 API 토큰(해시 저장, 범위 `read` / `read+feedback`). 쓰기 동작은 감사 로그
- Webhook은 tailnet 안 발신자만 수신(P2). 외부 출처는 폴링

### ADR-012
**그래프는 테이블로 처리**
- `edges(src, rel, dst, confidence, source, doc_id)`, 양 끝 모두 인덱스
- 영향 분석("테이블 X에서 3홉 이내 다운스트림은?")은 재귀 CTE. 결과는 목록 + 작은 Mermaid 그래프

### ADR-013
**의미 기반 해시**
- 커넥터마다 정규화된 뷰와 해시를 출력: 파싱한 DAG 그래프/파라미터의 정렬 JSON, DDL AST, 수시로 바뀌는 통계를 뺀 매핑 JSON
- 합성은 이 해시가 바뀔 때만 실행
- 수시로 바뀌는 지표(문서 수, 마지막 실행)는 본문이 아닌 **스냅샷**(시계열 테이블)에 저장. 따라서 커밋 발생 없음

### ADR-014
**사람의 큐레이션 (3차 리뷰에서 단순화)**
- 리뷰 상태: `generated → reviewed → verified`, `stale`, `archived`. 생성 텍스트는 `<!-- gen:start … -->` 펜스 안
- 섹션별 정정: 편집자가 "정정"을 누르고 올바른 내용 입력, `<!-- human:start section -->` 블록으로 저장
  - 생성된 섹션을 **대체**, 재생성 후에도 유지
  - 이후 생성 때 권위 있는 출처 `S0`로 전달
- reviewed/verified 페이지의 출처 변경 시 사실 표 갱신, 페이지는 사실 diff와 함께 `stale`
  - 편집자가 재생성(동기) 또는 유지 선택
  - 리비전 테이블 없음. 이력과 diff는 git
- `human_override: true`는 페이지 전체 고정(정정 기능 이후 거의 불필요)
- 팀별 리뷰 큐(`status:generated team:X`), 원하면 주간 요약

### ADR-015
**에이전트 인터페이스**
- OpenAPI 계약이 있는 고정 REST 엔드포인트. 토큰 인증, 응답에 출처 인용
- 검색, 페이지, 트리 컨텍스트, 엔티티, 그래프, 그리고 **한 번에 끝나는 `GET /api/context`**(엔티티 + 요약 + 실시간 상태 + 다운스트림 + 알려진 장애 + 런북). 알림 핸들러와 AIOps 에이전트용 설계
- 고정 URL `/e/<entity_id>`는 Airflow `doc_md` / `on_failure_callback`과 알림 템플릿에 삽입
- 같은 엔드포인트를 감싼 정식 MCP 서버는 P2(3차 리뷰에서 자체 "MCP 스타일" JSON-RPC는 제외)

### ADR-016
**검색 인덱스: 관찰·문서화·점검만, 운영은 안 함 (v1)**
- *배경:*
  - "검색 인덱스도 관리 대상이다"
  - 롤오버·리인덱스마다 새 실제 인덱스 생성(`products_v3 → v4`). 실제 이름을 키로 삼으면 페이지가 계속 바뀌고 이력 단절
  - 운영자에겐 상태, 최신성, 변동, 변경 이력, 런북 필요
  - 운영 클러스터 쓰기 권한이 있는 위키는 위험이 큼
- *결정:*
  1. 고정 엔티티/페이지 단위는 **인덱스 패밀리**(alias 또는 패턴). 실제 인덱스는 그 안의 버전 행, 템플릿과 수명 주기 정책은 독립 페이지
  2. 커넥터가 읽기 전용으로 수집: 클러스터 상태, 인덱스(상태 포함), 매핑, 설정, alias, 통계, 템플릿, ILM/ISM 정책과 explain 결과
  3. 상태 점검: red/yellow, 빌더 DAG 마지막 성공보다 오래된 alias, 패밀리 최신성 기준을 넘긴 빌드 경과 시간, 문서 수 감소, 고아 인덱스, 매핑 변경(diff 포함)
  4. 패밀리마다 롤오버 / 리인덱스 / alias 전환 / 롤백 런북
- *결과:*
  - 쓰기 자격 증명 없이 "오늘 인덱스가 빌드·전환됐고 정상인가, 아니면 뭘 해야 하나"에 답변 가능
  - *나중에 (P2):* 운영자 요청 시에만 보호 장치가 있는 동작 추가(예: 별도 자격 증명 + 2인 승인 alias 전환)

### ADR-017
**레지스트리로 확장**
- *배경:* 레지스트리 없이는 문서 타입 하나 추가에 지식 모델, 렌더러, 템플릿, 분류 체계, 분류기 규칙, 카탈로그 뷰, 패싯을 모두 수정해야 함. 이름을 키로 쓰는 커넥터 설정으론 Airflow도 하나만 연결 가능했음
- *결정:*
  - `TypeSpec` 레지스트리: 문서 타입마다 `opspedia/types/` 아래 모듈 하나. `id` / 트리 규칙, frontmatter·엔티티 속성 pydantic 모델, 섹션 목록이 있는 Jinja 템플릿, 엣지 관계(레이블, 방향), 카탈로그 컬럼과 패싯, 선택적 LLM 서술 명세를 선언
  - 렌더러, 검증기, 카탈로그, 검색 패싯이 이 레지스트리를 참조
  - 커넥터 레지스트리: `CONNECTORS = {kind: class}`. `sources:`가 `{kind, name, …}` **목록**이라 Airflow·클러스터 여러 개도 설정만으로 연결
- *결과:* 새 페이지 타입 ≈ 모듈 1개 + 템플릿 1개, 새 출처 ≈ 커넥터 클래스 1개, 새 클러스터 ≈ 설정만 추가
