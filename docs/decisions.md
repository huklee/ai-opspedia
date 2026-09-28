# 기술 결정 기록 (ADR log)

> - 형식: 배경 → 결정 → 결과
> - 표시가 없으면 상태는 **채택**
> - 결정 변경 시 기존 기록은 수정 없이 두고 대체 ADR을 새로 추가

| ADR | 결정 (한 줄 요약) | 상태 |
|---|---|---|
| [001](#adr-001) | 작은 컴포넌트는 직접 구현 · 라이브러리는 사용, 플랫폼은 미사용 | 채택 |
| [002](#adr-002) | 웹 서버와 배치 워커를 한 Python 코드베이스, 한 배포 단위로 | 채택 |
| [003](#adr-003) | ~~git의 Markdown이 기준 원본~~ | 대체됨 (ADR-020) |
| [004](#adr-004) | 벡터는 float32 BLOB + numpy 전수 코사인 · 벡터 DB 없음 | 채택 |
| [005](#adr-005) | 하이브리드 검색 = FTS5(nori 토큰 + 바이그램 안전망 + 식별자) ⊕ 벡터, RRF 결합 · LLM 리랭크 없음 | 채택 (ADR-019로 갱신) |
| [006](#adr-006) | 교체 가능한 임베더 · 기본 로컬 KURE-v1, 대안 bge-m3 · (외부 API 승인 시) Voyage · 끄기 · 평가로 확정 | 채택 (ADR-019로 갱신) |
| [007](#adr-007) | ~~합성은 Claude(`claude-opus-5`)~~ | 대체됨 (ADR-018) |
| [008](#adr-008) | 사실 정보는 파서, 서술은 LLM 담당 · 모든 문서/엣지에 출처 기록 | 채택 |
| [009](#adr-009) | FastAPI + 서버 렌더링 HTML + vanilla JS · Markdown 렌더링은 서버에서 | 채택 |
| [010](#adr-010) | 자체 미니 스케줄러 + CLI + 폴링 · 파이프라인 워커는 파일 락으로 하나만 | 채택 |
| [011](#adr-011) | tailnet 전용 HTTPS · 신원은 Tailscale(`whois`), 역할은 설정 파일, API 토큰 | 채택 (Q12) |
| [012](#adr-012) | 그래프는 `edges` 테이블 + 재귀 CTE · 그래프 DB 없음 | 채택 |
| [013](#adr-013) | **의미 기반** 콘텐츠 해시로 증분 파이프라인 실행 여부 판단 | 채택 |
| [014](#adr-014) | 사람의 큐레이션: 리뷰 상태, 펜스 영역, 섹션별 정정, stale 후 재생성/유지 | 채택 |
| [015](#adr-015) | 에이전트 인터페이스 = 한 번에 끝나는 `/api/context` 포함 REST/OpenAPI · 정식 MCP 서버는 나중에 | 채택 |
| [016](#adr-016) | 검색 인덱스는 관찰·문서화·점검만, v1에 쓰기 없음 · 고정 페이지 단위는 인덱스 패밀리 | 채택 |
| [017](#adr-017) | 레지스트리로 확장: 문서 타입마다 `TypeSpec` 모듈 하나, 커넥터 종류마다 항목 하나 | 채택 |
| [018](#adr-018) | LLM 상한은 GPT-OSS-120B(사내 서빙, 추후 변경 가능) · 합성은 LLM 없이 자동화 가능한 부분 우선 | 채택 |
| [019](#adr-019) | 한국어 검색 품질 최우선: nori 형태소 분석(`_analyze`) + 사용자 사전 + 바이그램 안전망, 한국어 평가 기준치 | 채택 (Q15) |
| [020](#adr-020) | 배포·구동 시 git 사용 안 함 · Markdown 전문과 모든 버전을 SQLite에 저장, SQLite가 유일한 기준 원본 | 채택 |

---

### ADR-001
**작은 컴포넌트는 직접 구현. 라이브러리는 사용, 플랫폼은 미사용**
- *배경:* 요구사항
  - "오픈소스 대신 Python 서버와 직접 만든 프레임워크"
  - 빠른 전달
  - 유연성
- *결정:*
  - 플랫폼 도입 없음: 자체 인덱스용 Elasticsearch/OpenSearch, Qdrant/Milvus, Neo4j, 자체 작업용 Airflow, Wiki.js/Outline/Backstage/DataHub
  - 며칠을 아끼는 곳에는 작고 교체 가능한 라이브러리
    - `fastapi`/`uvicorn`, `jinja2`, `markdown-it-py`(+플러그인), `pygments`, `sqlglot`, `httpx`(LLM 호출 포함, 별도 LLM SDK 없음), `numpy`, `pypdfium2`, `pyyaml`
    - 선택: `sentence-transformers`(로컬 임베더) / `python-mecab-ko`(nori 폴백) / `voyageai`
  - 라이브러리마다 자체 모듈로 감싸 언제든 교체 가능
- *결과:*
  - Option C 대비 초기 구축 약 1주 단축
  - 분석기, 스케줄러, 트리, 링커, 하이브리드 랭커는 직접 소유
  - *해석 리스크:* "오픈소스 대신"이 서드파티 라이브러리 전면 금지인 경우
    - 얇은 모듈 경계 덕에 FastAPI는 표준 라이브러리 `http.server`+`wsgiref`로, markdown-it은 자체 렌더러로 교체 가능
    - 추가 비용 약 +3–4일

### ADR-002
**Python 코드베이스와 배포 단위는 하나**
- 웹(`opspedia serve`)과 배치(`opspedia run …`)가 모델, 저장소, 설정을 공유
- 프로세스 분리
  - 스케줄러는 서버 프로세스 안, 파이프라인 실행은 서브프로세스
  - 크래시 격리, 웹 요청과 GIL 경합 없음
- 서버 프로세스는 정확히 하나
  - `uvicorn --workers 1`, 운영에서 `--reload` 금지
  - 이유: 워커가 여럿이면 스케줄러와 벡터 행렬도 여럿이 되는 문제
- *결과:* venv, systemd/launchd 유닛, 로그 스트림 모두 하나

### ADR-003
> **대체됨 → [ADR-020](#adr-020)** (git 저장소 사용 불가). 아래는 결정 이력으로만 보존

**git의 Markdown이 기준 원본(source of truth), SQLite는 다시 만들 수 있는 인덱스**
- *배경:* 버전 관리, diff, 사람의 리뷰, 손쉬운 내보내기, 빠른 DB 스키마 변경 필요
- *결정:*
  - 합성 단계가 `content/**/*.md`를 쓰고 커밋(파이프라인 실행당 커밋 하나, 메시지 = run id + 요약)
  - `opspedia rebuild`: `content/`와 `embeddings.db`로 `opspedia.db`(문서, FTS, 청크, 벡터 캐시, 엔티티, 엣지) 재구성
  - 콘텐츠 저장소 보관 항목
    - 상태, 리뷰, 출처 해시, 생성기/프롬프트/모델 버전, 추론된 엣지, `_meta/redirects.yaml`
    - 재구성해도 링크 유지, 전체 재합성 불필요
  - 운영용 테이블(토큰, 신원 캐시, 작업, 실행, 리뷰 이벤트, 피드백, 검색 로그, 스냅샷, 감사 로그, 이후 `batch_items`)은 SQLite에만 두고 **백업 대상**([03-storage](components/03-storage.md) §1)
- *결과:*
  - 스키마 변경 비용이 낮음(drop 후 rebuild)
  - 이력은 `git log`
  - 저장소 인터페이스 덕에 Option B 여지도 유지

### ADR-004
**벡터는 SQLite BLOB에 저장, 검색은 numpy**
- 규모
  - 호스트 측정 50k×1024 기준 5.3 ms
  - 코퍼스 추정 5–20k 청크
- `data/vectors.npz`(ids, doc_idx, matrix, generation)에서 정규화된 float32 행렬 하나로 로드
- 교체 방식
  - 콘텐츠 작업마다 원자적 교체
  - 이 규모에선 mmap 불필요([03-storage](components/03-storage.md) §5)
- 청크 500k 초과 또는 p95 20 ms 미만 유지 불가 시 재검토(sqlite-vec ANN 또는 pgvector)

### ADR-005
**하이브리드 검색**
- 결합 방식
  - 분석된 텍스트(nori 형태소 토큰 + 한글 바이그램 안전망 + 식별자 조각·전체)의 FTS5 BM25와 코사인 top-k를 **RRF (k=60)** 로 결합
  - 분석기 상세는 [ADR-019](#adr-019)
- 필터(type, system, team, tags, status, env)는 랭킹 전 SQL에서 적용
- 엔티티 ID·제목 정확 일치는 1위로 승격
- LLM 리랭크 없음([ADR-018](#adr-018))
  - 순위는 RRF + 규칙 기반 가중치만
- 컬럼 가중치
  - nori > 식별자 > 바이그램, 한국어 평가 세트(`opspedia eval search`)로 결정
  - kiwipiepy 계획은 nori로 대체

### ADR-006
**교체 가능한 임베더**(`Embedder.embed(texts) -> float32[n, d]`)
- `local` (**기본값**, [ADR-019](#adr-019)): KURE-v1(bge-m3 한국어 파인튜닝, MTEB-ko-retrieval 0.762) ▸ bge-m3
  - sentence-transformers로 CPU 실행(~2 GB, 배치 느림)
  - 사내 GPU 서빙이 있으면 그쪽 우선
- `voyage`: API, 다국어
  - 외부 API 반출 승인 시에만 선택지
- `off`: BM25만 사용
  - 나머지 기능은 그대로 동작
- `(model, chunk_hash)` 캐시로 제공자를 바꿔도 재임베딩은 한 번
- M2에서 한국어 평가 세트(80–100개 질의)로 최종 확정

### ADR-007
> **대체됨 → [ADR-018](#adr-018).** 아래는 결정 이력으로만 보존

**합성은 Claude.** 모델은 `claude-opus-5`(현재 가이드 기준 기본값)
- **v1은 작은 스레드 풀(동시 4–8개) 동기 호출 + 예산 게이트**
  - 제대로 동작하는 가장 단순한 파이프라인(3차 리뷰)
- **Message Batches**(−50 %)는 처리량이 커지면 추가(로드맵 "later", 설계는 [02-synthesis](components/02-synthesis.md) §3.2에 보존)
- 호출 유형별 모델(예: 요약엔 Sonnet급)은 소유자 결정 사항이라 설정으로 노출
- 모든 JSON(엔티티, 카테고리, frontmatter 필드)은 **structured outputs**(`messages.parse` / `output_config.format`)
- 고정 시스템 프롬프트, 스키마, 분류 체계에 프롬프트 캐싱
- 실행 전 비용 추정은 `count_tokens`, 대화형 호출엔 서버 측 거절 대체 처리(refusal fallback)
- 프롬프트는 버전 파일로 관리(`@version`이 붙은 `synthesis/prompts/*.md`)
- 외부 LLM 불가 시 `llm=off`
  - 이때도 결정적(deterministic) 페이지, BM25, 그래프(M1 범위)는 정상 동작

### ADR-008
**사실 정보는 파서, 서술은 LLM 담당**
- 구조화 필드와 표는 모두 결정적 렌더러 담당
- LLM 담당 범위
  - 펜스로 나눈 서술 섹션(`summary`, `purpose`, `how it fails`, `runbook draft`)만 채움
  - 근거는 주어진 출처 발췌로 한정
- 인용 규칙
  - 섹션마다 `sources[n]` 인용 필수
  - 인용이 없거나 출처로 확인 안 되는 주장이 있으면 검증 실패, 그 섹션은 비움

### ADR-009
**웹 스택**
- 구성
  - FastAPI(API + OpenAPI 문서) + Jinja2 템플릿 + vanilla JS 모듈(트리, ⌘K, 패널)
  - SPA 빌드 체인 없음
- Markdown → HTML은 서버에서 markdown-it-py(+ 표, 앵커, 각주, 체크리스트, front-matter)와 Pygments(줄 번호)로 변환
- KaTeX, Mermaid
  - 브라우저 실행
  - tailnet 전용이라 에셋은 저장소에 포함
- 편집
  - PRD의 BlockNote/TipTap/MDX 편집기는 보류
  - v1은 읽기 전용, 편집은 섹션 정정과 간단한 리뷰 동작으로(버전은 SQLite `document_versions`, [ADR-020](#adr-020))

### ADR-010
**자체 미니 스케줄러**
- Cron 표현식(`m h dom mon dow` 자체 파서), 주기 트리거, 폴링, 선택적 tailnet 내부 webhook은 모두 `jobs` 테이블에 **넣기만** 함
- 실행은 워커 하나가 우선순위 순으로 처리(SQLite는 쓰기 주체가 하나)
- 워커는 하나
  - OS 파일 락(`data/worker.lock`에 `fcntl.flock`), 크래시 시 자동 해제
  - CLI `--direct`도 같은 락 사용
  - 2차 리뷰의 heartbeat 리스 테이블 대체(단일 호스트라 더 단순)
- 작업 가져오기는 문장 하나: `UPDATE jobs SET state='running', pid=? WHERE id=(SELECT id … LIMIT 1) RETURNING *`
- 시작 시 복구
  - pid가 죽은 `running` 작업은 실패 처리 후 재시도
  - 놓친 cron 슬롯은 **한 번만** 실행
- 타임아웃 시 워커가 서브프로세스 종료
- LLM 호출
  - 실행 안에서 동시 요청 풀로 처리(배치 API 없음, [ADR-018](#adr-018))
  - 처리량 게이트 초과 실행은 관리자 승인 후 진행
- CLI `opspedia run …`
  - 기본적으로 서버 큐에 추가
  - `--direct`는 락을 잡을 수 있을 때만(예: 서버 중단) 프로세스 안에서 바로 실행
- Webhook은 P2, 기본은 폴링

### ADR-011
**접근 제어**
- HTTPS는 tailnet 전용
  - TLS 관리자는 ai-research-note 것을 재사용(tailnet HTTPS가 켜져 있으면 Tailscale 인증서, 아니면 자체 서명)
- 서버는 **Tailscale 인터페이스 주소에만 바인딩**(상태 점검용 loopback 추가)
- 신원(기본값, Q12): 호출자의 Tailscale 로그인
  - `tailscale whois <remote ip>`로 확인 후 캐시
  - 로컬 비밀번호, 로그인 페이지, 요청 제한 불필요
- 역할: `viewer`는 tailnet 전원, `editor` / `admin`은 설정 파일 허용 목록
- 대안: 노드 공유 사용자나 tailnet 밖 사용자가 있는 경우(Q12 = no)
  - 로컬 계정(scrypt 해시, 세션)
  - ai-research-note와 같은 방식
- 에이전트·콜백용 API 토큰(해시 저장, 범위 `read` / `read+feedback`)
  - 쓰기 동작은 감사 로그
- Webhook은 tailnet 안 발신자만 수신(P2)
  - 외부 출처는 폴링

### ADR-012
**그래프는 테이블로 처리**
- `edges(src, rel, dst, confidence, source, doc_id)`, 양 끝 모두 인덱스
- 영향 분석("테이블 X에서 3홉 이내 다운스트림은?")
  - 재귀 CTE
  - 결과는 목록 + 작은 Mermaid 그래프

### ADR-013
**의미 기반 해시**
- 커넥터마다 정규화된 뷰와 해시를 출력
  - 파싱한 DAG 그래프/파라미터의 정렬 JSON
  - DDL AST
  - 수시로 바뀌는 통계를 뺀 매핑 JSON
- 합성은 이 해시가 바뀔 때만 실행
- 수시로 바뀌는 지표(문서 수, 마지막 실행)
  - 본문이 아닌 **스냅샷**(시계열 테이블)에 저장
  - 따라서 새 문서 버전 발생 없음

### ADR-014
**사람의 큐레이션 (3차 리뷰에서 단순화)**
- 리뷰 상태: `generated → reviewed → verified`, `stale`, `archived`
  - 생성 텍스트는 `<!-- gen:start … -->` 펜스 안
- 섹션별 정정: 편집자가 "정정"을 누르고 올바른 내용 입력, `<!-- human:start section -->` 블록으로 저장
  - 생성된 섹션을 **대체**, 재생성 후에도 유지
  - 이후 생성 때 권위 있는 출처 `S0`로 전달
- reviewed/verified 페이지의 출처 변경 시 사실 표 갱신, 페이지는 사실 diff와 함께 `stale`
  - 편집자가 재생성(동기) 또는 유지 선택
  - 별도 리비전 테이블 없음
  - 이력과 diff는 `document_versions`([ADR-020](#adr-020))
- `human_override: true`는 페이지 전체 고정(정정 기능 이후 거의 불필요)
- 팀별 리뷰 큐(`status:generated team:X`)
  - 원하면 주간 요약

### ADR-015
**에이전트 인터페이스**
- OpenAPI 계약이 있는 고정 REST 엔드포인트
  - 토큰 인증
  - 응답에 출처 인용
- 제공 범위
  - 검색, 페이지, 트리 컨텍스트, 엔티티, 그래프
  - **한 번에 끝나는 `GET /api/context`**(엔티티 + 요약 + 실시간 상태 + 다운스트림 + 알려진 장애 + 런북)
  - 알림 핸들러와 AIOps 에이전트용 설계
- 고정 URL `/e/<entity_id>`는 Airflow `doc_md` / `on_failure_callback`과 알림 템플릿에 삽입
- 같은 엔드포인트를 감싼 정식 MCP 서버는 P2
  - 3차 리뷰에서 자체 "MCP 스타일" JSON-RPC는 제외

### ADR-016
**검색 인덱스: 관찰·문서화·점검만, 운영은 안 함 (v1)**
- *배경:*
  - "검색 인덱스도 관리 대상이다"
  - 롤오버·리인덱스마다 새 실제 인덱스 생성(`products_v3 → v4`)
    - 실제 이름을 키로 삼으면 페이지가 계속 바뀌고 이력 단절
  - 운영자에겐 상태, 최신성, 변동, 변경 이력, 런북 필요
  - 운영 클러스터 쓰기 권한이 있는 위키는 위험이 큼
- *결정:*
  1. 고정 엔티티/페이지 단위는 **인덱스 패밀리**(alias 또는 패턴)
     - 실제 인덱스는 그 안의 버전 행
     - 템플릿과 수명 주기 정책은 독립 페이지
  2. 커넥터가 읽기 전용으로 수집: 클러스터 상태, 인덱스(상태 포함), 매핑, 설정, alias, 통계, 템플릿, ILM/ISM 정책과 explain 결과
  3. 상태 점검
     - red/yellow
     - 빌더 DAG 마지막 성공보다 오래된 alias
     - 패밀리 최신성 기준을 넘긴 빌드 경과 시간
     - 문서 수 감소, 고아 인덱스, 매핑 변경(diff 포함)
  4. 패밀리마다 롤오버 / 리인덱스 / alias 전환 / 롤백 런북
- *결과:*
  - 쓰기 자격 증명 없이 "오늘 인덱스가 빌드·전환됐고 정상인가, 아니면 뭘 해야 하나"에 답변 가능
  - *나중에 (P2):* 운영자 요청 시에만 보호 장치가 있는 동작 추가(예: 별도 자격 증명 + 2인 승인 alias 전환)

### ADR-017
**레지스트리로 확장**
- *배경:*
  - 레지스트리 없이는 문서 타입 하나 추가에 지식 모델, 렌더러, 템플릿, 분류 체계, 분류기 규칙, 카탈로그 뷰, 패싯을 모두 수정해야 함
  - 이름을 키로 쓰는 커넥터 설정으론 Airflow도 하나만 연결 가능했음
- *결정:*
  - `TypeSpec` 레지스트리: 문서 타입마다 `opspedia/types/` 아래 모듈 하나
    - 선언 항목: `id` / 트리 규칙, frontmatter·엔티티 속성 pydantic 모델, 섹션 목록이 있는 Jinja 템플릿, 엣지 관계(레이블, 방향), 카탈로그 컬럼과 패싯, 선택적 LLM 서술 명세
    - 렌더러, 검증기, 카탈로그, 검색 패싯이 이 레지스트리를 참조
  - 커넥터 레지스트리: `CONNECTORS = {kind: class}`
    - `sources:`가 `{kind, name, …}` **목록**이라 Airflow·클러스터 여러 개도 설정만으로 연결
- *결과:*
  - 새 페이지 타입 ≈ 모듈 1개 + 템플릿 1개
  - 새 출처 ≈ 커넥터 클래스 1개
  - 새 클러스터 ≈ 설정만 추가

### ADR-018
**LLM 상한은 GPT-OSS-120B. 합성은 LLM 없이 자동화 가능한 부분 우선** (ADR-007 대체)
- *배경:*
  - 현재 사용 가능한 LLM 최대치가 GPT-OSS-120B (추후 변경 여지 있음)
  - Claude 전용 기능(Message Batches, 프롬프트 캐싱, `messages.parse`, refusal fallback, `count_tokens`)을 전제한 ADR-007 설계 무효
- *결정:*
  - 모델: **GPT-OSS-120B**
    - 사내 서빙 가정, OpenAI 호환 Chat Completions API
    - endpoint·모델명은 설정값
  - `LLMClient` 인터페이스 뒤에 숨김
    - 이후 모델 교체는 설정 변경만
    - 호출은 `httpx` 직접 사용(별도 SDK 없음)
  - 실행: 동시 요청 풀(4–8), 입력 해시 기반 결과 캐시, 1실행당 **처리량 게이트**(LLM 호출 수·예상 소요 시간 상한)
  - 구조화 출력
    - 서빙이 지원하면 JSON schema 강제(`response_format` / guided decoding)
    - 아니면 pydantic 검증 + 1회 재시도
  - **LLM 없는 자동화 우선**
    - LLM은 규칙·파서·템플릿이 못 하는 서술과 잔여분만 담당

    | 작업 | LLM 없이 (기본) | LLM (보조) |
    |---|---|---|
    | 요약(`summary`) | frontmatter 사실 정보 + 템플릿 문장으로 1–2문장 생성 | 서술 보강 |
    | 엔티티 추출 | 레지스트리 + Aho–Corasick + 정규식 | 미해결 멘션만 |
    | 분류(트리 경로) | 타입 + 시스템 규칙 | 규칙 실패한 매뉴얼·노트만 |
    | 장애 문서 표준화 | Jira 필드 → 템플릿 섹션 매핑 · 코멘트 시각순 타임라인 | 근본 원인·요약 서술만 |
    | 매뉴얼 표준화 | 제목·목차 구조 보존 변환(Markdown/Confluence 변환기) | 요약·태그 보강 |
    | 청킹·인덱싱·링크·그래프·상태 점검 | 전부 결정적 처리 | 사용 안 함 |
    | 리랭크 | RRF만 | 사용 안 함 |
    | 모순 lint | — | 나중에(P2), 선택 |
- *결과:*
  - 토큰 과금 없음(사내 서빙)
    - 제약은 GPU 처리량 → 비용 표는 토큰 수·처리 시간 기준
  - `llm=off`여도 R1·M1b 전체와 M2의 결정적 부분 동작
  - M2의 LLM 서술은 GPT-OSS-120B 한국어 서술 품질을 골든 세트로 평가한 뒤 활성화
  - 데이터 외부 반출 문제는 LLM 쪽에서 해소(사내 서빙 전제)
    - Q5는 사내 엔드포인트·처리량 확인으로 변경

### ADR-019
**한국어 검색 품질 최우선: nori 형태소 분석** (ADR-005·ADR-006 갱신)
- *배경:*
  - 한국어 검색 품질은 최우선 요구사항
  - nori(Elasticsearch/OpenSearch `analysis-nori` 플러그인, mecab-ko-dic 사전) 사용 가능
  - 자체 바이그램 분석기의 한계
    - 재현율은 확보
    - 형태소 단위 정밀도·순위 품질에 한계(띄어쓰기 없는 복합어 등, [research.md](research.md) §2)
- *결정:*
  - **토크나이저 = nori**, ES/OpenSearch **`_analyze` API**로 호출
    - 인덱스 생성·쓰기 없음
    - 인라인 정의: `nori_tokenizer`(`decompound_mode: mixed`) + `nori_part_of_speech`(조사·어미 제거) + `nori_readingform` + `lowercase`
    - **사용자 사전**(`user_dictionary_rules`): 엔티티 레지스트리의 식별자(DAG·테이블·인덱스·alias 이름)와 운영 용어집 자동 반영 → 식별자 분리 방지
    - 동의어(예: 추천↔리코, 피처↔feature): 설정의 운영 용어 동의어로 **쿼리 확장**(직접 구현)
  - 인덱싱
    - nori 토큰은 FTS5 전용 컬럼(`ko`)
    - 바이그램 컬럼은 **재현율 안전망**으로 유지, 식별자 컬럼 유지
    - 가중치 nori > 식별자 > 바이그램(구체 값은 평가로 결정)
  - 쿼리
    - 같은 `_analyze`로 분석(결과 캐시, 목표 p95 +20 ms 이내)
    - nori 토큰 AND → 결과 부족 시 바이그램 OR 완화
  - 캐시
    - `analysis_cache(text_hash, analyzer_version) → tokens` 별도 파일(백업 대상)
    - 재인덱싱 시 재호출 최소화
  - 장애 대비
    - `_analyze` 불가 시 **python-mecab-ko**(nori와 같은 mecab-ko-dic) 로컬 폴백
    - 그것도 없으면 바이그램만
  - 에스컬레이션: 평가 목표 미달 시
    - 키워드 검색만 **전용 ES/OpenSearch 인덱스(nori + BM25)** 로 이전(Repository 인터페이스 뒤라 교체 가능)
    - 이 경우 대상 클러스터와 분리된 쓰기 가능 전용 인덱스 필요
  - 의미 검색: 임베딩 기본값을 한국어 기준으로 변경 → 로컬 KURE-v1 ▸ bge-m3 ▸ (승인 시) Voyage ([ADR-006](#adr-006))
- *평가 기준 (한국어 전용 평가 세트):*
  - 전체 질의 세트 **80–100개**, 그중 한국어 **60개 이상**
  - 유형
    - 조사 변형(장애가/장애를), 띄어쓰기 변형(장애 대응/장애대응)
    - 복합어(추천배치파이프라인), 한영 혼용(alias 전환)
    - 약어·동의어(피처/feature), 식별자(feature_store_daily), 자연어 질문
  - 목표
    - 한국어 recall@5 **≥ 0.85**, MRR@10 **≥ 0.7**, 무결과율 **< 5 %**
    - 키워드 기준치는 R1 완료 기준에 포함
  - 비교군: 바이그램 단독 / nori / nori + 바이그램 / (+ 벡터)
- *결과:*
  - nori 분석은 **R1(M1a) P0**
  - 옵션 A도 옵션 C와 같은 형태소 분석 품질 확보 → 옵션 재평가([options.md](options.md))
  - 새 질문 **Q15**: `analysis-nori` 설치 클러스터와 `_analyze` 호출 권한(읽기 전용 역할) 확인

### ADR-020
**런타임에서 git 사용 안 함. Markdown 전문과 모든 버전을 SQLite에 저장** (ADR-003 대체)
- *배경:*
  - 실제 배포·구동 환경에서 git 사용 불가
    - 개발과 설계·구현 문서 관리에는 git 사용 가능
  - 버전 이력·diff·사람의 수정 보존·손쉬운 스키마 변경은 여전히 필요
- *결정:*
  - **SQLite가 유일한 기준 원본(source of truth)**
  - `documents`: 문서별 현재 Markdown 전문(frontmatter + 본문) 텍스트로 저장
  - `document_versions`: 변경 시마다 전체 Markdown 추가(append-only)
    - 컬럼: `(doc_id, version, markdown, content_hash, run_id|user, change_note, created_at)`
    - 내용 해시가 같으면 새 버전 미생성(중복 제거)
    - 사람 수정본은 영구 보관, 생성 버전은 문서당 최근 50개 보관(설정값)
  - 이력·diff
    - `/api/pages/{id}/history`
    - 버전 간 diff는 Python `difflib`로 생성해 UI에 표시
  - 파생 데이터(FTS, 청크, 벡터, 엔티티, 엣지)는 `documents`에서 재구성 가능(`opspedia rebuild`)
  - 쓰기
    - 파이프라인 워커(파일 락)와 웹 요청 모두 짧은 DB 트랜잭션으로 직접 기록
    - `content-sync` 작업, git 단일 작성자 규칙, 작업 트리 검사, `last_indexed_commit` 모두 삭제
  - 페이지 이동: 문서·청크·엔티티·엣지·`redirects` 갱신을 **트랜잭션 하나**로 처리
  - 백업
    - 매일 `Connection.backup()`(7일 + 주간 4개)
    - 선택 기능 `opspedia export --markdown <dir>`(공유용 Markdown 파일 덤프, 버전 관리 아님)
  - DAG 코드 수집도 git 미사용: 파일시스템 디렉터리 스캔(mtime·해시) 또는 Airflow REST `dagSources`
  - 배포도 git 미사용
    - 개발 머신에서 빌드한 아티팩트(wheel/tarball)를 호스트에 복사해 설치
    - 호스트에 git 불필요
- *결과:*
  - 구조 단순화: 저장 경로 하나, 원자적 쓰기, 복구 = DB 백업 복원
  - DB 파일이 커짐(버전 누적)
    - 보관 개수 설정과 `VACUUM`으로 관리
    - 예상 수백 MB 수준
  - 외부 diff 도구 대신 자체 이력·diff 화면 필요(P1)
