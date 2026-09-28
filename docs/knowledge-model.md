# 지식 모델

> - 모든 컴포넌트의 공통 계약
> - 여기 없는 필드에 의존 금지

## 1. 한눈에 보기

- **문서**: ai-opspedia의 기본 단위
  - YAML frontmatter가 붙은 Markdown 전문
  - SQLite `documents`(현재 전문) + `document_versions`(append-only 전체 버전)에 저장
  - 별도 파일 저장소 없음([ADR-020](decisions.md#adr-020))
- **엔티티**: 문서가 설명하는 대상
  - 대상 시스템에 실재하는 DAG, 테이블, 인덱스 등
- **엣지**: 엔티티 간 관계(DAG → 테이블 *writes*, 인덱스 → DAG *built_by*, 장애 → DAG *affected* 등)
- **청크**: 검색용 문서 분할 단위

```
 entity (dag:reco.feature_store_daily) ──described_by──▶ document (Systems/Reco/DAGs/feature_store_daily.md)
     │                                                         │  frontmatter + body + provenance
     ├──writes──▶ entity (table:dw.user_features)              └──▶ chunks (heading-bounded, ~500 tokens)
     └──builds──▶ entity (index:search-prod.products_v3) ──version_of──▶ entity (index_family:search-prod.products)
                        ◀──affected── entity (incident:ops.inc-2291)      ◀──reads── entity (service:reco.ranking-api)
```

## 2. 식별자

| 종류 | 정식 ID 형식 | 예시 |
|---|---|---|
| 엔티티 | `<type>:<namespace>.<name>` (소문자, 불변) | `dag:reco.feature_store_daily`, `table:dw.user_features`, `index:search-prod.products_v3`, `index_family:search-prod.products`, `alias:search-prod.products` |
| 문서 | 트리 경로 | `Systems/Reco/DAGs/feature_store_daily` |
| 청크 | `<doc_id>#<heading-slug>[~n]` | `Systems/Reco/DAGs/feature_store_daily#backfill~2` |
| 출처 | URI 형태 | `file://reco-dags/dags/feature_store.py#L12-L88`(내용 해시는 `sources[].hash`), `airflow://prod/dagSources/<file_token>`, `airflow://prod/dags/feature_store_daily@2026-09-28T02:00Z`, `es://prod/products_v3/_mapping@…`, `jira://OPS-2291` |

- ID에 시크릿·자격 증명이 든 호스트명은 절대 금지
- **네임스페이스 규칙**
  - `dag`, `pipeline`, `model`, `service`: 소유 시스템 키(`reco`)
  - `table`: 데이터베이스/데이터셋(`dw`)
  - `index`, `index_family`, `alias`, `index_template`, `lifecycle_policy`: 설정의 클러스터 키(`search-prod`, 환경 정보 포함)
  - `incident`, `runbook`: 출처 프로젝트(`ops`)
  - `team`, `system`: 네임스페이스 없음
  - DAG·테이블의 환경은 ID가 아닌 frontmatter `env:`에 기록
- **트리 경로 규칙**
  - 트리 세그먼트: 설정의 표시 이름(`systems.reco.title: Reco`, `clusters.search-prod.title: search-prod`)
  - 리프: 원래 식별자(`feature_store_daily`) 사용
  - 문서 `id` = 트리 경로
    - 분류기 출력도 이 `id`
  - 문서 이동(id 변경) 시 같은 트랜잭션에서 처리
    - `redirects` 행(이전 → 새 경로) 기록
    - `documents`·`document_versions`·청크·`entities.doc_id`·`edges.doc_id` 키 변경([03-storage](components/03-storage.md) §6)

## 3. 문서 타입

| `type` | 생성 원천 | 생성 방식 | 트리 경로 예시 |
|---|---|---|---|
| `dag` | DAG 코드(AST) + Airflow REST 스냅샷 | 결정적(deterministic) + LLM 요약 | `Systems/<system>/DAGs/<dag_id>` |
| `task` | DAG 페이지 안의 섹션 · 엔티티로만 존재 | 결정적 | — |
| `table` | DDL / information_schema | 결정적 + LLM 요약 | `Systems/<system>/Schemas/<db>/<table>` |
| `index_family` | alias나 인덱스 패턴으로 묶은 실제 인덱스(`products_v*` → `products`) · 롤오버·리인덱스 후에도 남는 **고정 페이지** | 결정적 + LLM 요약 | `Search/<cluster>/Indices/<family>` |
| `index` | 실제 인덱스 하나(`products_v3`)의 매핑, 설정, 통계, 수명 주기 상태 · 별도 페이지 없이 패밀리 페이지의 버전 섹션/행 | 결정적 | (패밀리 페이지의 섹션) |
| `alias` | `_alias` / `_cat/aliases` | 결정적 | `Search/<cluster>/Aliases/<alias>` |
| `index_template` | `_index_template` (+ 레거시 `_template`) | 결정적 | `Search/<cluster>/Templates/<name>` |
| `lifecycle_policy` | ES `_ilm/policy` / OpenSearch `_plugins/_ism/policies` | 결정적 + LLM 요약 | `Search/<cluster>/Policies/<name>` |
| `service` | 설정(v1): 서빙 API, 테이블·인덱스 소비자 | 설정 + LLM | `Systems/<system>/Services/<name>` |
| `pipeline` | 설정의 DAG 목록 또는 DAG 태그(예: "candidate generation") → 결정적 DAG 순서 + 리니지 · LLM 개요(M2) | 결정적 + LLM | `Systems/<system>/Pipelines/<name>` |
| `start_here` | 시스템별 온보딩 페이지(사람이 쓴 `note`, 트리 맨 위 고정) | 사람 | `Systems/<system>/Start here` |
| `model` | ML 모델 레지스트리 / DAG 파라미터(선택) | 결정적 + LLM | `Systems/<system>/Models/<name>` |
| `incident` | 포스트모템, ITSM 티켓, 알림 로그 | LLM(출처 기반) | `Incidents/<yyyy>/<id>-<slug>` |
| `runbook` | 매뉴얼, 장애에서 뽑은 절차 | LLM(출처 기반) + 사람 | `Runbooks/<area>/<slug>` |
| `manual` | 기존 문서(MD/PDF/Confluence) | LLM 표준화기 | `Manuals/<space>/<slug>` |
| `system`, `team` | 설정 + LLM 집계 | 결정적 + LLM | `Systems/<system>`, `Teams/<team>` |
| `note` | 사람이 작성 | 사람 | 어디든 |

## 4. frontmatter 스키마 (v1)

```yaml
---
id: Systems/Reco/DAGs/feature_store_daily        # = 트리 경로 (필수)
title: feature_store_daily — daily user feature build   # 필수
type: dag                                        # §3 (필수)
entity: dag:reco.feature_store_daily             # 이 문서가 설명하는 주 엔티티 (note는 선택)
system: reco                                     # 소유 시스템 (트리 + 패싯)
team: reco-platform                              # 소유 팀 (패싯)
tags: [features, daily, sla-critical]
env: [prod]
summary: >-                                      # 1–3문장. 검색 결과·에이전트 컨텍스트에 사용
  Builds dw.user_features every day at 02:00 KST from event logs; feeds ranking and the products index.
entities:                                        # 언급된 엔티티 (자동 추출, 백링크 근거)
  - table:dw.user_features
  - index_family:search-prod.products
runbooks: [Runbooks/Reco/feature-store-backfill]   # 사람이 고른 링크 (추출 결과보다 우선)
links:                                           # 버튼으로 렌더링. 기본값은 시스템별 설정
  airflow: https://airflow.internal/dags/feature_store_daily/grid
oncall: reco-platform-oncall                     # 설정에서 가져옴 (팀 → 로테이션/채널). 페이지 헤더에 표시
status: generated                                # generated | reviewed | verified | stale | archived
review: { by: null, at: null }                   # 사람이 리뷰/검증하면 채움
sources:                                         # 출처 (생성 문서는 필수)
  - uri: file://reco-dags/dags/feature_store.py#L12-L88
    hash: sha256:4be1…
    fetched_at: 2026-09-28T01:10:00+09:00
generator: { name: dag-renderer, version: 1.2.0, llm: gpt-oss-120b, prompt: dag-summary@3 }
content_hash: sha256:77a0…                        # 본문(frontmatter 제외) 해시. 멱등 판단용
updated_at: 2026-09-28T01:12:00+09:00
human_override: false                            # true면 생성기가 이 문서를 다시 쓰지 않음
---
```

- **규칙**
  - 다시 쓸 때 모르는 키도 보존(상위 호환)
  - `status: reviewed`/`verified` 문서의 출처 해시 변경 시
    - 사실 정보 표는 바로 갱신
    - 서술은 *수정 제안*으로 전환
    - 리뷰어 승인 전까지 페이지는 `status: stale`([02-synthesis](components/02-synthesis.md) §4)
  - 순서·구조용 선택 키: `sort_key`(트리 순서), `aliases`(링커용 추가 이름)
    - 엔티티 이름과 `aliases`는 nori 사용자 사전(`user_dictionary_rules`)에도 반영
    - 목적: 식별자 분리 방지([ADR-019](decisions.md#adr-019))
  - 생성 영역: `<!-- gen:start name --> … <!-- gen:end -->`로 감쌈
    - 바깥은 사람이 자유롭게 덧붙이는 영역
  - 사람의 정정: `<!-- human:start name --> … <!-- human:end -->`로 감쌈
    - 같은 이름의 생성 섹션보다 우선
    - 재생성 후에도 유지
    - LLM에는 권위 있는 출처 `S0`로 전달([ADR-014](decisions.md#adr-014))
  - 모든 엔티티에 현재 페이지로 리다이렉트하는 **고정 URL** `/e/<entity_id>` 부여
    - 페이지 이동이나 인덱스 롤오버에도 불변
    - Airflow `doc_md`, `on_failure_callback` 메시지, 알림 템플릿에 넣어도 안전

### 본문 템플릿 (섹션 순서 고정, diff 안정)

| 타입 | 섹션 |
|---|---|
| `dag` | 요약 · 스케줄 & SLA · 태스크(표) · 입력 / 출력 · 다운스트림 영향 · 백필 & 멱등성 · 알려진 장애 · 런북 · 출처 |
| `table` | 요약 · 컬럼(표) · 파티셔닝 & 최신성 SLA · 생산자 · 소비자(DAG, 서비스, 인덱스) · 알려진 장애 · 출처 |
| `index_family` | 요약 · 현재 상태(alias → 쓰기/읽기 인덱스, health, 문서 수, 크기, 수명 주기 단계) · **버전**(실제 인덱스 표: 생성일, 문서 수, 크기, health, 매핑 해시) · 매핑(현재 버전, 이전 버전과의 diff) · 수명 주기 정책 · 빌더 DAG & 스케줄 · 소비자(서비스) · 런북: 롤오버 / 리인덱스 / alias 전환 / 롤백 · 알려진 장애 · 출처 |
| `alias` | 가리키는 대상(인덱스 + is_write_index) · 전환 이력(스냅샷 기반) · 소비자 · 출처 |
| `incident` | 요약 · 영향 · 타임라인 · 탐지 · 근본 원인 · 조치 · 후속 작업 · 영향받은 엔티티 · 출처 |
| `runbook` | 트리거 / 증상 · 사전 점검 · 절차 · 검증 · 롤백 · 담당자 · 관련 문서 |

## 5. 엔티티와 엣지 타입

| 엔티티 타입 | 주요 속성 (`entities.attrs` JSON) |
|---|---|
| `dag` | schedule, owner, tags, retries, sla, catchup, start_date, tasks[], datasets[], `dynamic`(bool: AST로 태스크 목록을 못 알아낸 경우) |
| `task` | dag, operator, upstream[], downstream[], pool, sla, callable/sql 참조, `columns_read[]` / `columns_written[]`(sqlglot 추출, `SELECT *`면 `unknown_columns: true`), `external_deps[]`(`ExternalTaskSensor` / `external_dag_id`에서 추출) |
| `table` | db, schema, columns[{name,type,nullable,comment}], 파티션 키, 추정 행 수 |
| `index_family` | cluster, pattern, alias, 현재 쓰기/읽기 인덱스, 빌더 DAG, 정책, 기대 최신성(현재 인덱스의 최대 허용 나이) |
| `index` | family, 생성일, 매핑 해시, shards/replicas, health, docs.count, store.size(스냅샷), 수명 주기 단계/상태 |
| `alias` | cluster, 대상 인덱스, is_write_index |
| `index_template` / `lifecycle_policy` | patterns, priority, phases/states, 롤오버 조건 |
| `service` | system, owners, 읽는 대상(테이블/인덱스/alias), 엔드포인트(설정) |
| `incident` | id, severity, 시작/해결 시각, 탐지 경로, 근본 원인 분류, 영향받은 엔티티 |
| `runbook` | 트리거/증상, 절차, 검증, 롤백, 담당자 |
| `system`, `team`, `model`, `pipeline` | name, owners, links |

| 엣지 (`src --rel--> dst`) | 도출 근거 |
|---|---|
| `dag --contains--> task` | DAG AST |
| `task --upstream_of--> task` | REST `tasks.downstream_task_ids` |
| `dag --waits_for--> dag/task` | AST의 `ExternalTaskSensor` / `external_dag_id` 인자(DAG 간 연쇄 실행 · 추천 시스템 파이프라인에 흔함) |
| `task/dag --reads--> table`, `--writes--> table` | 오퍼레이터 안 SQL(sqlglot), dataset/outlets, LLM 추출(`inferred` 표시) |
| `dag --builds--> index`, `alias --points_to--> index`, `index --version_of--> index_family` | ES alias API, 인덱스 패턴 설정, DAG 코드(인덱스 이름), LLM 추출 |
| `index_template/lifecycle_policy --applies_to--> index_family` | 템플릿 패턴, 정책 할당 |
| `service --reads--> table/index_family/alias` | 설정(v1), 매뉴얼에서 LLM 추출(inferred) |
| `incident --affected--> dag/table/index` | 장애 추출 |
| `runbook --fixes--> incident/dag/index` | 추출 + 사람 |
| `doc --mentions--> entity` | 엔티티 추출기(백링크 근거) |
| `entity --owned_by--> team`, `--part_of--> system/pipeline` | 설정 / frontmatter |

- 모든 엣지에 `confidence`와 `source`(출처 URI) 포함
  - `confidence`: `parsed`(코드/API에서 추출), `inferred`(LLM 추론), `human`
- 추론 엣지 전달 방식
  - UI: 추론 엣지를 점선으로 표시
  - 에이전트: confidence를 함께 전달

## 6. 청크

- 분할: Markdown 제목(H2, 다음은 H3) 단위로 먼저 자른 뒤 문단을 **~500 토큰(최대 800)** 으로 채움
  - 청크 사이 ~60 토큰 겹침
    - 제목이 바뀌면 겹침 초기화
  - 코드 블록과 표는 중간에서 자르지 않음
    - 너무 크면 그 자체로 청크 하나
- 컨텍스트 헤더: 청크마다 `title › section path · type · entity · system` 저장
  - 임베딩·BM25 색인 때 본문 앞에 붙임
  - *Contextual Retrieval* 기법의 경량판([research.md](research.md) §3)
- 청크 행 필드: `doc_id`, `heading_path`, `char_start`, `char_end`, `content_hash`, `embed_model`, `vector`([03-storage](components/03-storage.md) §3과 같은 이름)
