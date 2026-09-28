# 3. 다층 저장소 엔진 (`opspedia/storage/`)

## 1. 한눈에 보기

- PRD의 네 계층(문서 저장소, 검색 엔진, 벡터 DB, 그래프 DB)을 **SQLite 파일 하나 안의 계층**으로 구현
- 기준 원본(source of truth)은 Markdown `content/` git 저장소([ADR-003](../decisions.md#adr-003))

| PRD 계층 | 구현 | 복구 방법 |
|---|---|---|
| Document Store (RDBMS/DocDB) | `documents` 테이블 + `content/` git 저장소(이력 = git log) | ✅ `content/`에서 재구축 |
| Search Engine (ES/OpenSearch) | **분석을 거친** 텍스트 위의 contentless FTS5 테이블 `doc_fts` / `chunk_fts` | ✅ 재구축 |
| Vector DB (pgvector/Qdrant) | `chunks.vector` + 메모리 내 numpy 행렬. 벡터는 `data/embeddings.db`(모델 + 청크 해시 단위 캐시)에서 로드 | ✅ `embeddings.db`를 백업해 뒀으면 재구축, 아니면 다시 임베딩(API 비용 발생) |
| Graph DB (Neo4j, 선택) | `entities`, `edges` 테이블 + 재귀 CTE | ✅ 파싱한 엣지는 다시 도출. **추론 엣지와 사람이 추가한 엣지는 frontmatter**(`edges:`)에 있어 유실 없음 |

상태별 저장 위치 (2차 리뷰 정정)

| 상태 | 저장 위치 | 재구축 시 동작 |
|---|---|---|
| 페이지 본문, 사실 정보, `status`, `review`, 출처 해시, generator/prompt/model 버전, 추론 엣지 | `content/`의 frontmatter + 본문 | git에서 재구축. `raw_index`(출처별 마지막 semantic hash)는 `sources[].hash`에서 다시 도출하므로 전체 재합성이 일어나지 **않음** |
| 리다이렉트 | `content/_meta/redirects.yaml` | 재구축 |
| 리뷰 이벤트, 피드백, 검색 로그, 토큰, Tailscale 신원 캐시, 작업, 실행, 스냅샷, 감사 로그(추후 `batch_items` 추가) | `opspedia.db`에만 | **백업 대상**, 재구축 불가 |
| 임베딩 캐시 | `data/embeddings.db`(별도 SQLite 파일) | **백업 대상**. 잃어버리면 한 번 다시 임베딩 |
| 원본 스냅샷 | `data/raw/` | 선택(90일 보관). 다시 가져올 수 있음 |

- 모든 접근은 `storage.Repository`(Python protocol) 경유. Postgres 구현(Option B)으로 교체 가능하게 하기 위함

## 2. 디스크 파일 구성
```
content/                  # git 저장소(별도 remote), Markdown + frontmatter, _meta/redirects.yaml. 콘텐츠 작업당 커밋 하나
data/opspedia.db          # SQLite (WAL): 인덱스 + 운영 테이블, 예상 크기 ~100–500 MB
data/embeddings.db        # 임베딩 캐시 (model, content_hash) → vector
data/vectors.npz          # {ids, doc_idx, matrix, generation} — 원자적으로 교체 (write tmp → os.replace)
data/raw/<source>/…       # 원본 스냅샷 (0700), 90일 보관
data/backups/             # opspedia.db + embeddings.db 야간 사본 (일간 7개 + 주간 4개)
```

## 3. 스키마 (v1, 요약)
```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);            -- last_indexed_commit, schema_version, vectors_generation
CREATE TABLE documents (
  n INTEGER PRIMARY KEY,             -- FTS용 rowid (contentless 테이블은 정수 키가 필요)
  id TEXT UNIQUE NOT NULL,           -- 트리 경로 (NFC, 대소문자 무시 기준 유일: `id_fold` 참고)
  id_fold TEXT UNIQUE NOT NULL,      -- casefold(id) — APFS 대소문자 충돌 방지
  title TEXT NOT NULL, type TEXT NOT NULL, entity TEXT, system TEXT, team TEXT, env TEXT,
  tags TEXT,                         -- JSON 배열
  status TEXT NOT NULL,              -- generated|reviewed|verified|stale|archived
  summary TEXT, body TEXT NOT NULL, frontmatter TEXT NOT NULL,  -- JSON
  parent TEXT, depth INT, sort_key TEXT,
  content_hash TEXT, updated_at INT, commit_sha TEXT
);
CREATE INDEX documents_parent ON documents(parent);
CREATE TABLE search_log (at INT, user TEXT, q TEXT, mode TEXT, results INT, clicked_doc TEXT, rank INT);  -- 지표용
CREATE TABLE redirects (old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL, at INT);   -- _meta/redirects.yaml 미러
CREATE TABLE chunks (n INTEGER PRIMARY KEY, id TEXT UNIQUE, doc_id TEXT, heading_path TEXT, ord INT, text TEXT, context TEXT,
  char_start INT, char_end INT, tokens INT, content_hash TEXT, embed_model TEXT);

CREATE VIRTUAL TABLE doc_fts   USING fts5(title, summary, body, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 2 tokenchars '_'");     -- rowid = documents.n
CREATE VIRTUAL TABLE chunk_fts USING fts5(context, text, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 2 tokenchars '_'");     -- rowid = chunks.n

CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT, system TEXT, doc_id TEXT, attrs TEXT /*JSON*/);
CREATE TABLE aliases  (alias TEXT, entity_id TEXT, PRIMARY KEY(alias, entity_id));  -- 이름/식별자 → 엔티티
CREATE TABLE edges (src TEXT, rel TEXT, dst TEXT, confidence TEXT, source TEXT, doc_id TEXT,
  PRIMARY KEY(src, rel, dst, source));   -- 주장하는 출처마다 한 행. UI에서 병합 (가장 강한 confidence 우선)
CREATE INDEX edges_dst ON edges(dst, rel);
CREATE TABLE snapshots (entity TEXT, metric TEXT, value TEXT, at INT);   -- 자주 바뀌는 사실 정보의 시계열
-- 워커는 하나: data/worker.lock에 fcntl.flock (ADR-010), 리스 테이블 없음
-- 운영용: tokens, identity_cache, jobs, runs, raw_index(uri, semantic_hash, run_id), audit, feedback (추후 batch_items 추가)
```

- `contentless_delete=1`을 켠 contentless FTS5(SQLite ≥ 3.43, 호스트는 3.51)라 인덱스가 작고 rowid로 삭제 가능
- `snippet()`은 분석된 바이그램 텍스트를 돌려주므로 미사용
- 스니펫은 상위 결과만 Python에서 생성. 쿼리 시점에 그 ~10개 청크에 분석기를 다시 돌려 일치 구간 탐색(오프셋 맵은 저장하지 않음, 3차 리뷰)

## 4. 분석기와 쿼리 빌더 ([ADR-005](../decisions.md#adr-005))

- 텍스트는 넣기 전에 **Python에서** 분석, 쿼리도 같은 함수 사용
- FTS 토크나이저는 `tokenchars '_'`를 지정한 `unicode61`. 식별자 전체 토큰이 쪼개지지 않고 유지됨

1. 한글 연속 구간 → 순서대로 겹치는 **바이그램**(1음절 구간은 그대로). 기본 `trigram`은 2음절 단어 누락(측정으로 확인)
2. 식별자(`[A-Za-z0-9_.-]+`) → 전체 소문자화, `.`와 `-`를 `_`로 치환(`es-prod.products_v3` → `es_prod_products_v3`). 여기에 `_ . -`와 camelCase로 쪼갠 조각도 추가(`feature_store_daily` → `feature_store_daily feature store daily`)
3. 라틴 문자 단어 → 소문자. 숫자 유지, 불용어 제거 안 함(운영 텍스트는 짧고 정확하므로)
4. 선택(M2 평가): kiwipiepy 형태소 컬럼. 가중치는 평가 결과로 결정

**쿼리 빌더** (사용자 입력을 MATCH에 그대로 넘기지 않음)
- 패싯 먼저: `type: system: team: tag: status: env: path:`와 날짜 연산자를 뽑아 SQL `WHERE` 절로 이동. FTS5가 `word:`를 컬럼 필터로 해석하지 않게 하기 위함
- 토큰: 나머지를 같은 방식으로 분석, 모든 토큰을 큰따옴표로 감쌈(안의 `"`는 두 번 겹쳐 씀). 따라서 `AND/OR/NOT/NEAR`, `-`, `*`, `^`, `:`는 항상 문자 그대로 취급
- 한글: 연속 구간 하나 = 연속 바이그램으로 된 구(phrase) 하나(`"장애" "애대" "대응"` → `"장애 애대 대응"`). 어순 강제 목적
- 조사: *쿼리*의 한글 구간에서는 흔한 조사(은/는/이/가/을/를/에/에서/의/로/으로/와/과/도/만)를 먼저 제거. 예: `장애가` → `장애`
- 1음절 구간: 제목/alias에 대한 `LIKE` 매칭으로 대체
- 결합: 그룹끼리 AND. 결과가 5개 미만이면 그룹 간 OR로 재검색하고, **동시에** 각 한글 구를 바이그램 OR로 완화. 이렇게 찾은 결과는 순위 하향
  - SQLite 3.51에서 *확인*: 붙여 쓴 복합어(`대응장애`)는 이 fallback 전까지 띄어 쓴 텍스트(`장애 대응`)와 매칭되지 않음
  - 이런 사례는 검색 평가 세트에 포함
- 순위: `bm25(doc_fts, 5.0, 3.0, 1.0)`(title, summary, body), `bm25(chunk_fts, 2.0, 1.0)`(context, text). 가중치는 인덱싱된 컬럼 순서에 대응, 점수가 낮을수록 상위
- 엔티티 정확 일치: 원본 쿼리를 `aliases`와도 대조해 일치하는 엔티티를 1위로 부스트

## 5. 벡터 ([ADR-004](../decisions.md#adr-004))
- 생성: 콘텐츠 작업이 끝날 때마다 워커가 `vectors.npz` = `{ids, doc_idx, matrix (float32, L2-normalized), generation}`을 원자적으로 기록하고 `meta.vectors_generation` 증가
- 다시 읽기
  - 서버는 30초마다(그리고 작업이 `done`이 될 때) `meta.vectors_generation`을 확인해 새 파일 로드
  - 행 수가 `COUNT(chunks)`와 같은지 검증한 뒤 `(ids, doc_idx, matrix)` 튜플 참조 하나를 교체. 처리 중인 쿼리는 이전 튜플 계속 사용
- 쿼리
  1. 순위를 매기기 *전에* 패싯 필터로 **불리언 행 마스크**(`np.isin(doc_idx, allowed_docs)`) 생성. 선택도가 높은 패싯에서 결과가 바닥나지 않게 하기 위함
  2. `scores = matrix @ q`를 계산하고 마스킹된 행은 −inf로 둔 뒤 `argpartition`으로 top-k(k = 50) 추출
- 메모리: 약 n × d × 4 bytes(20k × 1024 → 80 MB). 이 크기면 mmap 불필요
- 모델 변경: 새 행렬 생성, 전환 완료 전까지 기존 행렬로 서비스

## 6. 동시성, 내구성, 복구
- 프로세스 구성: **서버 하나**(`uvicorn --workers 1`, 운영에서는 `--reload` 없음)와 **파이프라인 워커 하나**(서버 스케줄러가 띄우는 서브프로세스). 둘은 `data/worker.lock`의 `fcntl.flock`으로 직렬화([ADR-010](../decisions.md#adr-010))
- SQLite
  - WAL, `synchronous=NORMAL`, `busy_timeout=5000`
  - 모든 쓰기 트랜잭션은 `BEGIN IMMEDIATE`로 시작. WAL에서는 읽기로 시작해 쓰기로 승격한 트랜잭션이 busy 재시도 없이 바로 `SQLITE_BUSY`를 받기 때문
  - 워커는 ~200개 문서 단위 배치로 커밋(FTS/벡터 재구축 포함). 웹의 짧은 쓰기가 굶지 않게 하기 위함
  - `sessions.last_seen`은 최대 5분에 한 번만 기록
- git 쓰기 주체는 하나
  - `content/`에는 워커만 기록
  - 웹 동작(review/verify, 정정, keep/regenerate, override)은 DB에 바로 기록해 UI에 즉시 반영, 우선순위 높은 `content-sync` 작업을 큐에 추가. 이 작업이 ~1분 안에 Markdown으로 내보내고 커밋
- 콘텐츠 작업 프로토콜
  1. `git status --porcelain`이 깨끗해야 함. 아니면 로그를 남기고 격리 브랜치로 `git stash`
  2. 파일 쓰기 + 커밋
  3. DB upsert 후 `meta.last_indexed_commit = HEAD` 설정
- 시작 시 복구
  1. pid가 사라진 `running` 작업은 실패 처리
  2. 1단계와 같은 방식으로 worktree 정리
  3. `git diff --name-only last_indexed_commit..HEAD` 리인덱스
- 페이지 이동: `git mv` + `_meta/redirects.yaml` 갱신 + 커밋을 **먼저** 처리. 그다음 DB 트랜잭션 하나로 `redirects` 행 추가, `documents` 갱신, `chunks`·`entities.doc_id`·`edges.doc_id` 키 변경, 그 문서의 FTS 행 재생성
- 백업
  - 매일 밤 `opspedia.db`와 `embeddings.db`를 `Connection.backup()`(Python sqlite3)으로 백업, `content/`는 `git push`
  - 복원: DB 파일을 다시 복사해 넣거나, 새로 clone한 저장소와 `embeddings.db`로 `rebuild`

## 7. 작업 목록 (일정의 기준 원본: [roadmap.md](../roadmap.md), Pri = 마일스톤 내 우선순위)
| 마일스톤 | Pri | 작업 |
|---|---|---|
| M0 | P0 | 스키마 + 마이그레이션(번호 붙인 SQL 파일), `Repository` protocol + SQLite 구현, 콘텐츠 저장소 writer(git), `rebuild` |
| M1a | P0 | 분석기 + 쿼리 빌더 + contentless 문서 FTS, 스냅샷, 워커 flock, 콘텐츠 작업 프로토콜 + 복구, search_log |
| M2 | P0 | 청크 + 청크 FTS, `embeddings.db`, `vectors.npz` 생성 + 다시 읽기, 마스크 적용 numpy 검색 |
| M3 | P1 | 페이지 이동 + 리다이렉트(`_meta/redirects.yaml`). id는 규칙으로 도출하므로 M3 전에는 이동이 드묾 |
| M4 | P1 | 백업 + 복원 드릴 |
| later | P2 | Postgres `Repository` 스파이크([options.md](../options.md)의 전환 조건 충족 시에만) |
