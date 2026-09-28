# 3. 다층 저장소 엔진 (`opspedia/storage/`)

## 1. 한눈에 보기

- PRD의 네 계층(문서 저장소, 검색 엔진, 벡터 DB, 그래프 DB)을 **SQLite 파일 하나 안의 계층**으로 구현
- 기준 원본(source of truth)은 SQLite 하나. `documents`(현재 Markdown 전문) + `document_versions`(append-only 전체 버전)([ADR-020](../decisions.md#adr-020))
- 이력·diff도 DB 기준(`document_versions`, `/api/pages/{id}/history`)

| PRD 계층 | 구현 | 복구 방법 |
|---|---|---|
| Document Store (RDBMS/DocDB) | `documents`(현재 Markdown 전문) + `document_versions`(이력) 테이블 | 기준 원본. DB 백업 복원 |
| Search Engine (ES/OpenSearch) | **분석을 거친** 텍스트(nori 토큰 + 바이그램 + 식별자) 위의 contentless FTS5 테이블 `doc_fts` / `chunk_fts` | ✅ `documents`에서 재구축. 분석 결과는 `data/analysis_cache.db`에서 재사용 |
| Vector DB (pgvector/Qdrant) | `chunks.vector` + 메모리 내 numpy 행렬. 벡터는 `data/embeddings.db`(모델 + 청크 해시 단위 캐시)에서 로드 | ✅ `embeddings.db`를 백업해 뒀으면 재구축, 아니면 다시 임베딩(로컬 KURE-v1 기준 처리 시간 소요) |
| Graph DB (Neo4j, 선택) | `entities`, `edges` 테이블 + 재귀 CTE | ✅ 파싱한 엣지는 다시 도출. **추론 엣지와 사람이 추가한 엣지는 frontmatter**(`edges:`)에 있어 유실 없음 |

상태별 저장 위치 (2차 리뷰 정정, [ADR-020](../decisions.md#adr-020) 반영)

| 상태 | 저장 위치 | 재구축 시 동작 |
|---|---|---|
| 페이지 Markdown 전문: 본문, 사실 정보, `status`, `review`, 출처 해시, generator/prompt/model 버전, 추론 엣지 | `documents.markdown`(frontmatter + 본문) | 기준 원본, **백업 대상**. `raw_index`(출처별 마지막 semantic hash)는 `sources[].hash`에서 다시 도출하므로 전체 재합성 **없음** |
| 페이지 버전 이력 | `document_versions`(append-only) | 기준 원본, **백업 대상**, 재구축 불가 |
| 리다이렉트 | `redirects` 테이블 | **백업 대상**, 재구축 불가 |
| 파생 데이터: FTS, 청크, 엔티티, 파싱한 엣지, 벡터 행렬 | `opspedia.db`, `data/vectors.npz` | `opspedia rebuild`로 `documents`에서 재구성 |
| 리뷰 이벤트, 피드백, 검색 로그, 토큰, Tailscale 신원 캐시, 작업, 실행, 스냅샷, 감사 로그 | `opspedia.db`에만 | **백업 대상**, 재구축 불가 |
| 임베딩 캐시 | `data/embeddings.db`(별도 SQLite 파일) | **백업 대상**. 잃어버리면 한 번 다시 임베딩 |
| 분석 캐시 `analysis_cache` | `data/analysis_cache.db`(별도 SQLite 파일) | **백업 대상**. 다시 채울 수는 있지만 전체 텍스트 `_analyze` 재호출 비용이 큼 |
| 원본 스냅샷 | `data/raw/` | 선택(90일 보관). 다시 가져올 수 있음 |

- 모든 접근은 `storage.Repository`(Python protocol) 경유. Postgres 구현(Option B)이나 키워드 검색 전용 ES 인덱스(§4 에스컬레이션)로 교체 가능하게 하기 위함

## 2. 디스크 파일 구성
```
data/opspedia.db          # SQLite (WAL): 기준 원본(documents, document_versions, redirects) + 인덱스 + 운영 테이블, 예상 크기 ~100–500 MB (버전 누적 포함)
data/embeddings.db        # 임베딩 캐시 (model, content_hash) → vector
data/analysis_cache.db    # 분석 캐시 (text_hash, analyzer_version) → tokens
data/vectors.npz          # {ids, doc_idx, matrix, generation} — 원자적으로 교체 (write tmp → os.replace)
data/raw/<source>/…       # 원본 스냅샷 (0700), 90일 보관
data/backups/             # opspedia.db + embeddings.db + analysis_cache.db 야간 사본 (일간 7개 + 주간 4개)
```

## 3. 스키마 (v1, 요약)
```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);            -- schema_version, vectors_generation, analyzer_version
CREATE TABLE documents (
  n INTEGER PRIMARY KEY,             -- FTS용 rowid (contentless 테이블은 정수 키가 필요)
  id TEXT UNIQUE NOT NULL,           -- 트리 경로 (NFC, 대소문자 무시 기준 유일: `id_fold` 참고)
  id_fold TEXT UNIQUE NOT NULL,      -- casefold(id) — URL·ID 일관성용
  markdown TEXT NOT NULL,            -- 현재 Markdown 전문(frontmatter + 본문). 기준 원본
  version INT NOT NULL,              -- 현재 버전 = document_versions.version 최댓값
  title TEXT NOT NULL, type TEXT NOT NULL, entity TEXT, system TEXT, team TEXT, env TEXT,
  tags TEXT,                         -- JSON 배열
  status TEXT NOT NULL,              -- generated|reviewed|verified|stale|archived
  summary TEXT, body TEXT NOT NULL, frontmatter TEXT NOT NULL,  -- JSON. markdown에서 파싱한 값
  parent TEXT, depth INT, sort_key TEXT,
  content_hash TEXT, updated_at INT
);
CREATE INDEX documents_parent ON documents(parent);
CREATE TABLE document_versions (     -- append-only. 내용 해시가 같으면 새 버전 미생성
  doc_id TEXT NOT NULL, version INT NOT NULL, markdown TEXT NOT NULL, content_hash TEXT NOT NULL,
  run_id TEXT, user TEXT,            -- 생성 버전은 run_id, 사람 수정본은 user
  change_note TEXT, created_at INT NOT NULL,
  PRIMARY KEY(doc_id, version));     -- 사람 수정본 영구 보관, 생성 버전은 문서당 최근 50개(설정값)
CREATE TABLE search_log (at INT, user TEXT, q TEXT, mode TEXT, results INT, clicked_doc TEXT, rank INT);  -- 지표용
CREATE TABLE redirects (old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL, at INT);   -- 테이블이 기준 원본
CREATE TABLE chunks (n INTEGER PRIMARY KEY, id TEXT UNIQUE, doc_id TEXT, heading_path TEXT, ord INT, text TEXT, context TEXT,
  char_start INT, char_end INT, tokens INT, content_hash TEXT, embed_model TEXT);

-- title/summary/context: 짧은 필드라 nori + 식별자 + 바이그램 토큰을 한 컬럼에 모두 기록
-- 본문(body/text): 분석 방식별 컬럼 분리 → ko(nori 형태소), ident(식별자 전체 + 조각), bigram(한글 바이그램)
CREATE VIRTUAL TABLE doc_fts   USING fts5(title, summary, ko, ident, bigram, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 2 tokenchars '_'");     -- rowid = documents.n
CREATE VIRTUAL TABLE chunk_fts USING fts5(context, ko, ident, bigram, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 2 tokenchars '_'");     -- rowid = chunks.n

CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT, system TEXT, doc_id TEXT, attrs TEXT /*JSON*/);
CREATE TABLE aliases  (alias TEXT, entity_id TEXT, PRIMARY KEY(alias, entity_id));  -- 이름/식별자 → 엔티티. nori 사용자 사전의 원천
CREATE TABLE edges (src TEXT, rel TEXT, dst TEXT, confidence TEXT, source TEXT, doc_id TEXT,
  PRIMARY KEY(src, rel, dst, source));   -- 주장하는 출처마다 한 행. UI에서 병합 (가장 강한 confidence 우선)
CREATE INDEX edges_dst ON edges(dst, rel);
CREATE TABLE snapshots (entity TEXT, metric TEXT, value TEXT, at INT);   -- 자주 바뀌는 사실 정보의 시계열
-- 워커는 하나: data/worker.lock에 fcntl.flock (ADR-010), 리스 테이블 없음
-- 운영용: tokens, identity_cache, jobs, runs, raw_index(uri, semantic_hash, run_id), audit, feedback

-- data/analysis_cache.db (별도 파일, 백업 대상)
CREATE TABLE analysis_cache (
  text_hash TEXT NOT NULL,           -- 분석 대상 텍스트(문서 필드·청크·쿼리)의 해시
  analyzer_version TEXT NOT NULL,    -- hash(분석기 종류 nori|mecab + 인라인 분석기 정의 + 사용자 사전)
  tokens TEXT NOT NULL,              -- JSON 배열
  PRIMARY KEY(text_hash, analyzer_version));
```

- `contentless_delete=1`을 켠 contentless FTS5(SQLite ≥ 3.43, 호스트는 3.51)라 인덱스가 작고 rowid로 삭제 가능
- `snippet()`은 분석된 토큰 텍스트를 돌려주므로 미사용
- 스니펫은 상위 결과만 Python에서 생성. 쿼리 시점에 그 ~10개 청크를 다시 분석(대부분 `analysis_cache` 적중)해 일치 구간 탐색(오프셋 맵은 저장하지 않음, 3차 리뷰)
- 사용자 사전이 바뀌면 `analyzer_version`도 바뀜 → 캐시 미스분만 점진적으로 재분석·재인덱싱

## 4. 분석기와 쿼리 빌더 ([ADR-005](../decisions.md#adr-005), [ADR-019](../decisions.md#adr-019))

- 텍스트는 넣기 전에 **Python에서** 분석, 쿼리도 같은 함수 사용
- FTS 토크나이저는 `tokenchars '_'`를 지정한 `unicode61`. 이미 분석된 토큰을 공백 구분으로 넣으므로 식별자 전체 토큰도 쪼개지지 않고 유지

**분석 단계**
1. **nori 형태소 분석**(1순위) → `ko` 컬럼
   - ES/OpenSearch `_analyze` API로 호출(`search_index` 커넥터가 제공). 인덱스 생성·쓰기 없음, 읽기 전용 권한으로 충분
   - 인라인 정의: `nori_tokenizer`(`decompound_mode: mixed`) + `nori_part_of_speech`(조사·어미 제거) + `nori_readingform` + `lowercase`
   - `user_dictionary_rules`: 엔티티 레지스트리(`entities`, `aliases`)의 식별자(DAG·테이블·인덱스·alias 이름)와 운영 용어집에서 자동 생성 → 식별자 분리 방지
   - 결과는 `analysis_cache`에 저장. 재인덱싱 때는 캐시 미스분만 호출
2. 한글 연속 구간 → 순서대로 겹치는 **바이그램**(1음절 구간은 그대로) → `bigram` 컬럼. 재현율 안전망으로 유지
   - 기본 `trigram`은 2음절 단어 누락(측정으로 확인)
3. 식별자(`[A-Za-z0-9_.-]+`) → `ident` 컬럼
   - 전체 소문자화, `.`와 `-`를 `_`로 치환(`es-prod.products_v3` → `es_prod_products_v3`)
   - `_ . -`와 camelCase로 쪼갠 조각도 추가(`feature_store_daily` → `feature_store_daily feature store daily`)
4. 라틴 문자 단어 → 소문자. 숫자 유지, 불용어 제거 안 함(운영 텍스트는 짧고 정확하므로)

**장애 대비와 에스컬레이션**
- `_analyze` 불가 시: **python-mecab-ko**(nori와 같은 mecab-ko-dic 사전, 같은 사용자 사전 규칙) 로컬 폴백
- python-mecab-ko도 없으면 바이그램 + 식별자만. 폴백으로 분석한 행은 `analyzer_version`이 달라 복구 후 재분석 대상
- kiwipiepy는 대안으로만 검토(기존 P1 형태소 컬럼 계획은 nori로 대체)
- 에스컬레이션: 한국어 평가 목표([05-search-api](05-search-api.md) §7) 미달 시 키워드 검색만 **전용 ES/OpenSearch 인덱스(nori + BM25)** 로 이전
  - `Repository` 인터페이스 뒤라 교체 가능
  - 이 경우 대상 클러스터와 분리된 쓰기 가능 전용 인덱스 권한 필요

**쿼리 빌더** (사용자 입력을 MATCH에 그대로 넘기지 않음)
- 패싯 먼저: `type: system: team: tag: status: env: path:`와 날짜 연산자를 뽑아 SQL `WHERE` 절로 이동. FTS5가 `word:`를 컬럼 필터로 해석하지 않게 하기 위함
- 분석: 나머지를 문서와 같은 `_analyze`로 분석
  - 결과 캐시(메모리 LRU + `analysis_cache`). 쿼리 분석 추가 지연 목표 p95 +20 ms 이내
  - 모든 토큰을 큰따옴표로 감쌈(안의 `"`는 두 번 겹쳐 씀). 따라서 `AND/OR/NOT/NEAR`, `-`, `*`, `^`, `:`는 항상 문자 그대로 취급
- 조사·어미: nori의 `nori_part_of_speech` 필터가 제거. 예: `장애가` → `장애`
- 동의어: 설정의 운영 용어 동의어(예: 추천↔리코, 피처↔feature)로 **쿼리 확장**. 토큰마다 `("피처" OR "feature")` 그룹 생성(ES synonym 필터 대신 직접 구현)
- 1차 검색: nori 토큰 + 식별자 그룹끼리 AND
- 완화: 결과가 5개 미만이면 그룹 간 OR로 재검색하고, **동시에** 한글 구간을 바이그램 OR로 검색. 이렇게 찾은 결과는 순위 하향
  - 바이그램 경로: 연속 구간 하나 = 연속 바이그램으로 된 구(phrase) 하나(`"장애" "애대" "대응"` → `"장애 애대 대응"`)
  - 바이그램 경로에서만 흔한 조사(은/는/이/가/을/를/에/에서/의/로/으로/와/과/도/만) 수동 제거
  - 바이그램 경로의 1음절 구간: 제목/alias에 대한 `LIKE` 매칭으로 대체
  - SQLite 3.51에서 *확인*: 바이그램만 쓰면 붙여 쓴 복합어(`대응장애`)는 이 fallback 전까지 띄어 쓴 텍스트(`장애 대응`)와 매칭 안 됨. nori `decompound_mode: mixed`로 1차 검색에서 해결 기대
  - 이런 사례는 검색 평가 세트에 포함
- 순위: 가중치 순서 nori(`ko`) > 식별자(`ident`) > 바이그램(`bigram`). 구체 값은 한국어 평가 세트로 결정, 점수가 낮을수록 상위
  - 초기값: `bm25(doc_fts, 5.0, 3.0, 2.0, 1.5, 1.0)`(title, summary, ko, ident, bigram), `bm25(chunk_fts, 2.0, 1.5, 1.2, 1.0)`(context, ko, ident, bigram). 가중치는 인덱싱된 컬럼 순서에 대응
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

## 6. 동시성, 내구성, 복구 ([ADR-020](../decisions.md#adr-020))
- 프로세스 구성: **서버 하나**(`uvicorn --workers 1`, 운영에서는 `--reload` 없음)와 **파이프라인 워커 하나**(서버 스케줄러가 띄우는 서브프로세스). 파이프라인 실행은 `data/worker.lock`의 `fcntl.flock`으로 직렬화([ADR-010](../decisions.md#adr-010))
- SQLite
  - WAL, `synchronous=NORMAL`, `busy_timeout=5000`
  - 모든 쓰기 트랜잭션은 `BEGIN IMMEDIATE`로 시작. WAL에서는 읽기로 시작해 쓰기로 승격한 트랜잭션이 busy 재시도 없이 바로 `SQLITE_BUSY`를 받기 때문
  - 워커는 ~200개 문서 단위 트랜잭션으로 기록(FTS/벡터 재구축 포함). 웹의 짧은 쓰기가 굶지 않게 하기 위함
  - `sessions.last_seen`은 최대 5분에 한 번만 기록
- 쓰기 경로: 워커와 웹 요청 모두 짧은 DB 트랜잭션으로 직접 기록
  - 웹 동작(review/verify, 정정, keep/regenerate, override, 이동)은 즉시 DB에 반영, UI에 바로 표시
  - 별도 동기화 작업 없음
- 문서 쓰기 프로토콜 (문서마다 트랜잭션 하나)
  1. 새 Markdown의 `content_hash` 계산. 현재 값과 같으면 건너뜀(새 버전 미생성)
  2. `document_versions`에 새 버전 추가(`run_id` 또는 `user`, `change_note`) + `documents` 갱신
  3. 파생 행(FTS, 청크, 엔티티, 파싱한 엣지) 갱신
  4. 보관 정리: 생성 버전이 문서당 50개(설정값)를 넘으면 오래된 것부터 삭제. 사람 수정본은 삭제 대상 아님
- 페이지 이동: **트랜잭션 하나**로 `documents.id` 변경, `document_versions`·`chunks`·`entities.doc_id`·`edges.doc_id` 키 변경, `redirects` 행 추가, 그 문서의 FTS 행 재생성
- 시작 시 복구
  1. pid가 사라진 `running` 작업은 실패 처리
  2. 쓰기는 트랜잭션 단위로 원자적이라 별도 정리 불필요
  3. `vectors.npz` 행 수가 `COUNT(chunks)`와 다르면 재생성
- 백업
  - 매일 밤 `opspedia.db`, `embeddings.db`, `analysis_cache.db`를 `Connection.backup()`(Python sqlite3)으로 백업, 일간 7개 + 주간 4개 보관
  - 선택 기능: `opspedia export --markdown <dir>`(공유용 Markdown 파일 덤프, 버전 관리 아님)
- 복원: DB 백업 파일 복원. 파생 데이터가 의심스러우면 `opspedia rebuild`로 `documents`에서 재구성(`embeddings.db`·`analysis_cache.db`가 있으면 재임베딩·재분석 최소)
- 용량: 버전 누적으로 DB 파일 증가 → 보관 개수 설정과 `VACUUM`으로 관리

## 7. 작업 목록 (일정의 기준 원본: [roadmap.md](../roadmap.md), Pri = 마일스톤 내 우선순위)
| 마일스톤 | Pri | 작업 |
|---|---|---|
| M0 | P0 | 스키마 + 마이그레이션(번호 붙인 SQL 파일), `Repository` protocol + SQLite 구현, `documents`/`document_versions` 저장(버전 추가·중복 제거·보관 정리), `rebuild` |
| M1a | P0 | **nori 분석**(`_analyze` 인라인 분석기 + 레지스트리·용어집 기반 사용자 사전) + `analysis_cache.db` + python-mecab-ko/바이그램 폴백 |
| M1a | P0 | 바이그램·식별자 분석기 + 쿼리 빌더(동의어 쿼리 확장, 바이그램 완화) + contentless 문서 FTS(`ko`/`ident`/`bigram` 컬럼), 스냅샷, 워커 flock, 문서 쓰기 프로토콜 + 복구, search_log |
| M2 | P0 | 청크 + 청크 FTS, `embeddings.db`, `vectors.npz` 생성 + 다시 읽기, 마스크 적용 numpy 검색 |
| M3 | P1 | 페이지 이동(트랜잭션 하나) + `redirects`. id는 규칙으로 도출하므로 M3 전에는 이동이 드묾 |
| M4 | P1 | 백업 + 복원 드릴, `opspedia export --markdown` |
| later | P2 | Postgres `Repository` 스파이크([options.md](../options.md)의 전환 조건 충족 시에만) |
