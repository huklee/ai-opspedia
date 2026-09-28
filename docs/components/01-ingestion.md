# 1. 수집 엔진과 커넥터 (`opspedia/ingestion/`)

## 1. 한눈에 보기

- 모든 소스의 원본을 **변경 불가능하고 콘텐츠 해시가 붙은 스냅샷**(`RawItem`)으로 저장
- 합성 단계에는 *무엇이 바뀌었는지*만 전달. 의미 해석은 합성 담당
- 대상 시스템과 직접 통신하는 유일한 컴포넌트. 접근은 항상 **읽기 전용**

```
 sources ─▶ Connector.fetch() ─▶ normalize() ─▶ RawItem{uri, kind, body, meta, semantic_hash} ─▶ raw store (data/raw/…)
                                                                   │
 scheduler (cron | interval | webhook | CLI) ─▶ run ─▶ diff vs last run ─▶ ChangeSet{added, changed, removed} ─▶ synthesis
```

## 2. 커넥터 (v1 범위)

| 커넥터 | 소스 | 읽는 내용 | 내보내는 `kind` | 우선순위 |
|---|---|---|---|---|
| `dag_repo` | DAG 디렉터리 파일시스템 스캔(mtime·해시 비교, 5분 폴링) 또는 Airflow REST `dagSources` | `*.py` → Python `ast`: dag_id, 오퍼레이터와 인자, SQL 문자열 / `.sql` 템플릿, 인덱스 이름, `Dataset`/`Asset` outlets, 줄 번호 붙은 코드 발췌 | `dag_code` | **P0** |
| `airflow_rest` | Airflow REST(Airflow 3: JWT + `/api/v2`, Airflow 2: basic/session 인증 + `/api/v1`) | dags(`timezone` 포함), DAG 상세, `downstream_task_ids` 포함 tasks(모든 DAG의 태스크 그래프), import 오류, 최근 dagRuns와 실패 태스크 인스턴스(최근 N일) | `dag_live`, `dag_runs`(스냅샷) | **P0** |
| `ddl` | DDL 파일 디렉터리 스캔 또는 웨어하우스 `information_schema` | `sqlglot.parse_one(…, dialect)` → 테이블, 컬럼, 타입, 코멘트, 파티션 | `table` | **P0** |
| `search_index` | 설정된 클러스터별 Elasticsearch 또는 OpenSearch REST | `_cluster/health`, `_cat/indices?format=json&bytes=b`(health 포함), `{idx}/_mapping`, `_settings`, `_alias`, `_stats`, `_index_template`(+레거시 `_template`), 수명 주기: ES `_ilm/policy` + `_ilm/explain` ⟂ OpenSearch `_plugins/_ism/policies` + `_plugins/_ism/explain`. 인덱스는 설정된 alias/패턴 기준으로 패밀리로 묶음. nori `_analyze` 제공자 역할 겸함(아래 참고) | `index_family`, `index`, `alias`, `index_template`, `lifecycle_policy`, `index_stats` / `cluster_health`(스냅샷) | **P0** |
| `files` | Markdown / 텍스트 / PDF 폴더 | MD는 그대로. PDF는 pypdfium2(텍스트 + 페이지 번호). 표는 저품질로 표시, 표 많은 매뉴얼용 pdfplumber 옵션 | `manual` | **P1** |
| `confluence` | Confluence Cloud REST v2(`/wiki/api/v2/pages?space-id=…&body-format=storage`) 또는 Data Center v1(`/rest/api/content?spaceKey=…&expand=body.storage,version`). 설정 `edition`으로 구분 | storage XHTML → Markdown 자체 변환기: 제목, 목록, 표, code/`noformat`, panel/info/warning → admonition, `ri:page` 링크 → 위키 링크. **모르는 매크로는 플레이스홀더 + 원본 페이지 링크** | `manual` | **P1** |
| `incidents` | 포스트모템 폴더 / ITSM: Jira Data Center REST v2(`/rest/api/2/search`, 위키 마크업 코멘트) 또는 Cloud v3(ADF JSON 코멘트 → 텍스트). 설정 `edition`으로 구분 | 티켓(필드, 코멘트, 타임라인), 포스트모템 문서 | `incident` | **P1** |
| `alerts`(모니터링 로그) | REST로 가져오는 Airflow 실패(실패 taskInstances + `/dags/{id}/dagRuns/{run}/taskInstances/{task}/logs/{try}` 로그 끝부분), 항상 가능. 사용 중이면 Alertmanager `GET /api/v2/alerts`(+ 내보낸 이력)도 읽음. *모니터링 스택은 Q7* | 알림/실패 이벤트를 에피소드로 묶어 장애나 DAG 페이지에 첨부 | `alert_episode`(스냅샷 + 장애 증거) | **P1** |
| `config` | `opspedia.yaml` | 시스템, 팀, 소유자, 분류 체계 재정의 | `org` | **P0** |

모든 커넥터의 공통 인터페이스:

```python
class Connector(Protocol):
    name: str
    def fetch(self, since: datetime | None) -> Iterable[RawItem]: ...   # 읽기 전용, 페이지네이션, 레이트 리밋 적용
    def normalize(self, item: RawItem) -> RawItem: ...                   # 안정적인 의미 뷰 + semantic_hash
```

`RawItem = {uri, kind, title, body (text/bytes), meta (dict), fetched_at, raw_hash, semantic_hash, env}`

**`search_index`의 nori `_analyze` 제공자 역할** ([ADR-019](../decisions.md#adr-019))

- 인덱싱·쿼리용 한국어 형태소 분석을 대상 클러스터의 `_analyze` API로 제공. 인덱스 생성·쓰기 없음
  - 인라인 분석기: `nori_tokenizer`(`decompound_mode: mixed`) + `nori_part_of_speech` + `nori_readingform` + `lowercase`
  - 텍스트 배치 단위 호출, 결과는 `analysis_cache(text_hash, analyzer_version)`에 캐시([03-storage](03-storage.md))
- 사용자 사전(`user_dictionary_rules`) 소스
  - 엔티티 레지스트리의 식별자(DAG·테이블·인덱스·alias 이름, [04-catalog](04-catalog.md) §3)
  - 설정의 운영 용어집
  - 레지스트리·용어집 변경 시 `analyzer_version` 갱신
- `_analyze` 불가 시 python-mecab-ko 로컬 폴백, 그것도 없으면 바이그램만

## 3. 정규화와 변경 감지 ([ADR-013](../decisions.md#adr-013))

| Kind | 해시 대상 의미 뷰 | 제외 항목(자주 바뀌는 값) |
|---|---|---|
| `dag_code` | {dag_id, schedule, default_args, tasks[id, operator, 주요 인자], edges, sql 해시}를 정렬한 JSON | 주석, 포매팅, 줄 번호 |
| `dag_live` | {schedule, owners, tags, paused, tasks, is_active} | 마지막 실행, 다음 실행 |
| `table` | sqlglot으로 정규화한 DDL(정규 SQL) | 행 수 |
| `index` | 매핑 + 설정(uuid/creation_date/version 제외) + ILM/ISM 정책 id | docs.count, store.size, 샤드 할당 |
| `manual` / `incident` | 공백 정규화 텍스트 + 주요 필드 | 조회수, 마지막 조회 시각 |

- 자주 바뀌는 값(인덱스 문서 수, 마지막 DAG 실행 상태, 실패 횟수 등)은 `snapshots(entity, metric, value, at)`에 기록
  - 페이지에 준실시간으로 표시하되 재생성 트리거는 아님
- 실행(run)마다 `uri`별 의미 해시를 마지막 성공 실행과 비교해 `ChangeSet` 생성
- 사라진 항목은 `archived` 페이지로 전환(조용한 삭제 없음)

## 4. DAG 파싱: 그래프는 REST, 코드는 AST

- **태스크 그래프(모든 DAG)**: REST `tasks` + `downstream_task_ids`
  - TaskGroup(접두사 붙은 id), `expand()` 매핑 태스크, `a >> [b, c]`, `cross_downstream`, `chain()`, 팩토리·설정 생성 DAG까지 정확
  - Airflow의 DAG 구성 로직 재구현 불필요
- **AST(코드 지식)**: DAG 정의(`with DAG(...)`, `@dag`, Airflow 3 `airflow.sdk` import) 탐지, 태스크 id와 오퍼레이터 호출 위치 매핑. 추출 항목:
  - SQL(`sql=`, `SQLExecuteQueryOperator`, 템플릿 `.sql` 파일)
  - 인덱스 이름(설정된 패턴과 맞는 문자열 리터럴)
  - `schedule=[Asset(...)]` / `outlets`
  - DAG 간 의존성: `ExternalTaskSensor(external_dag_id=…, external_task_id=…)`와 파티션/테이블 센서 → `waits_for` 엣지(추천 시스템 파이프라인의 흔한 연결 방식)
  - 태스크별 `columns_read` / `columns_written`(sqlglot 추출, `SELECT *`면 `unknown_columns`)
  - 줄 번호 붙은 코드 발췌
  - 매핑 실패(루프, 외부 모듈 import 상수 등) 시 `dynamic: true` 표시, 찾은 부분만 반영
- **SQL 처리**
  - Jinja 플레이스홀더 먼저 치환(`{{ ds }}` → `'2000-01-01'`, `{{ params.x }}` → 파라미터 기본값 또는 `'__param__'`)
  - `sqlglot.parse(sql, dialect)`로 여러 문장이 든 파일 처리
  - 파싱 실패 문장은 버리지 않고 `parse_error` 플래그 + 원문 발췌로 보존
- **판단 기준**: *DAG·태스크 존재 여부*는 REST, *코드 동작*은 AST. 불일치는 lint가 보고

## 5. 스케줄러 ([ADR-010](../decisions.md#adr-010))

| 트리거 | 예시 | 비고 |
|---|---|---|
| cron | `0 1 * * *` 전체 구조 동기화, `*/30 * * * *` airflow_rest + 인덱스 통계 | 자체 5필드 cron 파서, KST |
| 폴링(기본) | DAG/DDL 디렉터리는 5분마다 스캔(mtime·해시 비교, [ADR-020](../decisions.md#adr-020)), Jira/Confluence는 30분마다 `updated >= last_run` | 소스가 tailnet 밖이어도 동작 |
| 웹훅(선택) | `POST /hooks/incident`. **tailnet 안 발신자만 가능**(tailnet 전용 호스트는 Jira Cloud 요청 수신 불가) | HMAC-SHA256 서명 헤더 또는 공유 토큰을 `hmac.compare_digest`로 검증. 타임스탬프 있으면 ±5분만 허용. delivery id로 중복 제거. 웹훅은 일반 수집 작업 큐잉만 담당 |
| 수동 | `opspedia run ingest --source search_index` / UI "resync"(관리자) | |

**요청 시 실시간 상태**

- DAG·인덱스 패밀리 페이지 조회 시 서버가 마지막 DAG 실행(Airflow REST)·alias/health 상태(ES)를 읽기 전용으로 조회
- 60초 캐시, 경과 시간 함께 표시("02:12 기준, 30초 전")
- 새벽 3시 실패도 다음 30분 스냅샷 없이 즉시 확인

**기본 스케줄**

- `0 1 * * *`: 전체 구조 동기화(DAG 디렉터리, DDL, 템플릿, 정책)
- `*/30 * * * *`: Airflow REST + 인덱스/클러스터 스냅샷(자주 바뀌는 값만. 의미 해시가 그대로면 페이지 재작성 없음)

작업 큐: `jobs(id, pipeline, params, trigger, priority, state, attempts, pid, started, finished, batch_id, log)`

- 실행: 단일 워커가 우선순위 순, 같은 우선순위는 FIFO
  - 웹 쓰기(리뷰·정정)는 작업 큐를 거치지 않고 DB 트랜잭션으로 즉시 기록([ADR-020](../decisions.md#adr-020))
- 실패
  - 실행별 타임아웃 초과 시 서브프로세스 종료
  - 일시적 HTTP 오류는 백오프 두고 3번 재시도
  - 커넥터 하나의 실패가 다른 커넥터를 막지 않음. 부분 ChangeSet 유지, 오류는 실행 기록에 남겨 `/admin/runs`에 표시
- 놓친 cron 슬롯(호스트 절전·재시작)은 시작 시 한 번만 실행. 밀린 슬롯 백필 없음
- 워커 락(`flock`), 작업 선점, 복구 규칙: [ADR-010](../decisions.md#adr-010)

## 6. 설정

- 소스는 커넥터 레지스트리로 해석되는 `{kind, name, …}` **목록**([ADR-017](../decisions.md#adr-017))
- Airflow 인스턴스 2개, 클러스터 3개여도 설정만 변경
- 아래 예시는 가독성용 맵 축약형

```yaml
sources:
  dag_repo:      { mode: dir, path: /srv/dags/reco-dags, poll: 5m, system_map: { "dags/reco/*": reco } }   # 또는 mode: dag_sources (Airflow REST dagSources)
  airflow_rest:  { base_url: https://airflow.internal, api: v2, auth: { user: env:AIRFLOW_USER, password: env:AIRFLOW_PASS }, envs: [prod] }
                 # v2 → POST {base_url}/auth/token으로 JWT를 받고 만료 전에 갱신; 경로는 /api/v2/...; id는 URL 인코딩;
                 # 페이지네이션은 limit ≤ 100 + offset; 매핑 태스크 로그는 map_index가 필요
  ddl:           { dialect: bigquery, paths: [/srv/schemas/**/*.sql] }   # 또는 info_schema: {dsn: env:DW_DSN}
  search_index:  { clusters: { search-prod: { engine: elasticsearch, url: https://es.internal:9200, auth: env:ES_API_KEY } },
                   families: { products: { pattern: "products_v*", alias: products, builder_dag: reco.products_index_build, max_age: 26h } } }
  confluence:    { edition: datacenter, url: https://wiki.internal, spaces: [RECO, SEARCH], auth: env:CONFLUENCE_TOKEN }
  incidents:     { jira: { edition: datacenter, url: …, jql: "project = OPS AND labels in (reco, search)" }, folders: [/srv/postmortems] }
```

- 시크릿은 `env:` 참조로만 지정
- 자격 증명은 반드시 읽기 전용 서비스 계정

## 7. 실패 처리와 안전장치

- 읽기 전용: `POST /auth/token`(Airflow 3 JWT) 외에는 **GET만** 사용(nori `_analyze`도 본문 포함 GET). ES `_search`, 쓰기/관리 엔드포인트는 사용 안 함
- ES/OpenSearch 최소 권한
  - 서비스 역할에 `monitor`(클러스터 health/통계), `view_index_metadata`(매핑, 설정, alias, ILM explain), `read_ilm` 필요
  - nori `_analyze`(인덱스 없는 호출): 기본 가정은 `monitor` + `view_index_metadata`로 충분. 인덱스 없는 `_analyze`에 클러스터 권한이 따로 필요한지 확인 필요(Q15)
  - Elasticsearch `GET _index_template`은 `manage_index_templates` 필요. 이 권한은 *쓰기*까지 허용하므로 템플릿은 **선택 사항**
  - 권한 있을 때만 읽고, 없으면 "보이지 않음" 표시. 패밀리는 설정된 패턴 기준
- API 선택: 기계용 데이터는 `_cluster/health?level=indices`와 `_stats`로 조회
  - `_cat/*`는 공식 문서상 "애플리케이션용이 아니다"라서 폴백 전용
  - 데이터 스트림이 있으면 `_data_stream`도 조회
- 소스별 레이트 리밋·페이지 크기 상한, 요청 타임아웃 30초. 연속 5번 실패한 소스는 서킷 브레이커로 차단
- 원본 스냅샷의 민감 텍스트 대비
  - `data/raw/`(0700)에 저장
  - LLM(사내 GPT-OSS-120B 엔드포인트) 전송 **전** 마스킹(토큰, 이메일, IP를 설정된 정규식으로)([platform.md](../platform.md) §4)

## 8. 작업 목록 (일정의 기준 원본(source of truth)은 [roadmap.md](../roadmap.md), Pri = 마일스톤 안에서의 우선순위)
| 마일스톤 | Pri | 작업 |
|---|---|---|
| M1a | P0 | `Connector` 프로토콜, `RawItem`, 원본 저장소, 실행/ChangeSet 로직, 의미 해싱 |
| M1a | P0 | `dag_repo`(AST), `airflow_rest`, `ddl`, 엔진 하나 대상 `search_index`(패밀리, 매핑, alias, 통계, 클러스터 health), `config`(시스템, 팀, 온콜, 링크, 파이프라인, 서비스) |
| M1b | P1 | `search_index`: 템플릿, 수명 주기 정책. 두 번째 엔진 분기는 Q2에서 필요할 때만 |
| M1a | P0 | jobs 테이블, 워커, cron 파서, CLI `opspedia run ingest` |
| M1a | P0 | 스냅샷(인덱스 통계, 클러스터 health, 실행 결과) + 요청 시 실시간 상태(60초 캐시), DAG/DDL 디렉터리 폴링, AST `ExternalTaskSensor` + 컬럼 추출 |
| M2 | P0 | `files`(MD/PDF), `confluence`, `incidents`(Jira + 폴더) |
| M2 | P1 | `alerts`(Airflow 실패, Q7 = yes면 Alertmanager) |
| 이후 | P2 | tailnet 전용 웹훅, `info_schema` 실시간 웨어하우스 리더, Slack 스레드 내보내기, 알림 규칙 리더 |

## 9. 테스트

- 픽스처 디렉터리(정적 DAG, 동적 DAG)
- Airflow/ES/OpenSearch/Confluence/Jira 녹화 HTTP 픽스처
- 정규화 뷰 골든 JSON
- 속성 테스트: DAG 파일 재포매팅 후에도 `semantic_hash` 불변 확인
