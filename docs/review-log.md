# 리뷰 기록

- 관점이 다른 리뷰어 3명이 사전 정보 없이 각자 리뷰
- 지적 사항은 전부 수정, 미반영 부분은 이유 명시

| 회차 | 관점 | 지적 사항 | 수정 | 미반영 |
|---|---|---|---|---|
| 1 | PRD·브리프 커버리지, 문서 간 일관성, 링크 | 17건 (high 2, med 8, low 7) | 17 | 0 |
| 2 | 기술적 실현 가능성과 정확성 | 20건 (high 8, med 9, low 3) | 20 | 0 |
| 3 | 운영자 가치, 단순성, 일정 리스크 | 17건 (high 7, med 8, low 2) | 17 (1건 일부 반영) | 0 |

---

## 1차 리뷰: 커버리지와 일관성

- 링크 점검: 상대 링크, ADR 앵커 모두 정상(이후에 만든 이 파일은 제외)

| # | 심각도 | 지적 사항 | 수정 내용 (위치) |
|---|---|---|---|
| 1 | HIGH | 검색 인덱스 관리가 조회 수준. 페이지 키가 구체 인덱스라 롤오버마다 바뀜. 클러스터 상태·정책·템플릿·최신성/변동 점검·본문 템플릿 없음 | **index_family**를 고정 페이지로, 구체 인덱스는 버전 행으로. `index_template`, `lifecycle_policy` 타입 추가. 커넥터가 `_cluster/health`, 인덱스 상태, 템플릿, ILM/ISM 정책 수집. 상태 점검(red/yellow, alias·빌더 간 최신성, 빌드 경과 시간, 문서 수 변동, 고아 인덱스, 매핑 diff). 롤오버/리인덱스/alias 전환 런북 포함 본문 템플릿. ADR-016 신설 (knowledge-model §3–5, 01 §2, 04 §5, 05 §2, decisions) |
| 2 | HIGH | 로드맵 순서가 의존 관계와 어긋남(그래프/엔티티 API는 M4인데 UI는 M3, M2 완료 기준에 P1 항목 필요) | 엔티티/그래프 API → M3 P0, chunker/embedder/하이브리드/평가 → M2 P0 (roadmap §2) |
| 3 | MED | 컴포넌트별 우선순위 표가 로드맵과 불일치 | 모든 컴포넌트 작업 표에 로드맵과 같은 **마일스톤** 열 추가, "Pri = 마일스톤 내 우선순위" |
| 4 | MED | 웹훅 일정 없음, "스케줄"이 M4에 잘못 들어감 | 웹훅 → M1 P1. 개요의 M4 이름을 "에이전트와 큐레이션"으로 |
| 5 | MED | 청크 파라미터가 문서마다 다름 | 단일 스펙: ~500 토큰, 최대 800, 약 60 토큰 오버랩, 제목마다 리셋 |
| 6 | MED | PRD 패싯 "category", "date" 누락, `env`도 없음 | `path=` (category), `updated_after/before`, `env=`. 패싯 카운트에 날짜 버킷 (05 §2) |
| 7 | MED | PRD의 "Service" 그래프 노드가 모델에 없음 | `service` 엔티티 타입, `service --reads--> table/index_family/alias` 엣지 (knowledge-model §3, §5) |
| 8 | MED | 미정의 필드(tree_path, sort_key, dynamic, redirects, offsets), 이름 불일치, 이동 시 동작 미정 | `id` = 트리 경로. `sort_key`, `aliases`, `dynamic` 정의. `redirects` 테이블. 청크 컬럼 통일(`char_start/char_end/offsets/embed_model`). 페이지 이동 트랜잭션 (03 §6) |
| 9 | MED | 엔티티 이름 규칙 제각각, 네임스페이스·대소문자 매핑 미정 | 표준 예시, 타입별 네임스페이스 규칙, 트리 표시 이름 매핑 (knowledge-model §2) |
| 10 | MED | "모니터링 로그"가 모호함 | `alerts` 커넥터: Airflow 실패 이력(항상) + 쓰는 경우 Alertmanager. 확인이 필요한 질문 Q7 |
| 11 | LOW | stale 규칙이 문서마다 다름(verified만 vs reviewed+verified) | reviewed **와** verified 모두 → 수정안 제안 + `stale` |
| 12 | LOW | 비용이 $68, $70으로 제각각 | 전 문서 ≈ $70, 계산은 research §5 |
| 13 | LOW | bcrypt, scrypt 혼재 | scrypt (stdlib) |
| 14 | LOW | 개요에 "3회 리뷰 완료"를 너무 일찍 표기 | 3차 리뷰 전까지 상태 draft |
| 15 | LOW | 읽기 전용 문장이 깨짐 (01 §7) | "GET만 사용, 단 POST /auth/token (Airflow 3 JWT)은 예외; ES `_search` 사용 안 함" |
| 16 | LOW | 의존성 목록이 문서마다 다름 | ADR-001 선택 의존성에 `sentence-transformers` 추가 |
| 17 | LOW | `edges` PK로는 두 번째 출처 정보 유실 | PK `(src, rel, dst, source)`, UI에서 행 병합 |

## 2차 리뷰: 기술적 실현 가능성과 정확성

- 새 FTS 주장을 SQLite 3.51에서 재검증: tokenchars `_`, contentless delete, 조사 제거, 따옴표 처리, 띄어쓰기 없는 복합어. 결과는 [research.md](research.md) §2

| # | 심각도 | 지적 사항 | 수정 내용 (위치) |
|---|---|---|---|
| 1 | HIGH | 배치 `custom_id` `doc_id:call`이 `^[a-zA-Z0-9_-]{1,64}$` 위반 | `sha1(doc_id|call|prompt_version)[:32]` + `batch_items` 매핑 테이블 (02 §3) |
| 2 | HIGH | 최대 24시간짜리 배치 동안 writer 락 점유 | `synth-submit` / `synth-collect`로 분리. errored/expired는 최대 3회 재제출. 검증 재시도는 동기, 횟수 제한 (02 §3, ADR-010) |
| 3 | HIGH | 단일 프로세스 보장, 스케줄러 리스 미정(멀티 워커 중복 실행, 락 고착, CLI 경합) | `--workers 1`, reload 없음. `leases(name, owner_pid, host, expires_at)` 하트비트. `UPDATE … RETURNING`으로 원자적 선점. 죽은 pid 복구. 놓친 cron 슬롯은 한 번만. CLI는 기본적으로 큐 적재만 (ADR-002, ADR-010) |
| 4 | HIGH | git writer가 둘, 크래시 복구 미정, 이동 "트랜잭션"이 git까지 걸침 | git writer는 워커 하나. 웹 동작은 DB 기록 후 `content-sync` 큐 적재. worktree clean 점검. `meta.last_indexed_commit` + 시작 시 diff 리인덱스. 이동은 git 먼저, DB 나중 (03 §6) |
| 5 | HIGH | "git에서 재구성 가능" 표가 틀림 | 상태 표 재작성. status/review/hashes/versions/추론 엣지는 frontmatter에. `_meta/redirects.yaml`. `embeddings.db`는 별도 백업, revisions/feedback 등도 백업 (03 §1, ADR-003) |
| 6 | HIGH | tailnet 전용 호스트에 웹훅이 닿지 않음. GitLab 토큰은 HMAC 아님, 재전송 방어 없음 | 기본은 폴링(git fetch 5분, 이슈 트래커 30분). 웹훅은 tailnet 내부 발신자만. compare_digest, 타임스탬프 허용 구간, delivery-id 중복 제거 (01 §5, ADR-011) |
| 7 | HIGH | Confluence v2는 Cloud 전용. Jira DC/Cloud 포맷 차이, 변환기 작업량 큼 | `edition` 설정, DC는 v1 경로, ADF 처리, 모르는 매크로는 플레이스홀더 + 링크, 변환기 작업을 M2에 반영 (01 §2) |
| 8 | MED | `unicode61`이 `_`에서 분리. contentless/external 미정, id가 TEXT, `snippet()`이 바이그램 반환 | `tokenchars '_'` + `.`/`-` → `_`. contentless + `contentless_delete=1`, INTEGER rowid. 하이라이트는 오프셋 맵으로 Python에서 (03 §3–4, 05 §3) |
| 9 | MED | 쿼리 빌더 스펙 없음(조사, 구문 정밀도, 이스케이프, 패싯 접두어, bm25 인자 순서) | 전체 스펙: 패싯 우선, 따옴표 처리, 바이그램 구문, 조사 제거, 1음절 fallback, 위치 기반 `bm25` 가중치 (03 §4) |
| 10 | MED | SQLite 경합 처리 세부 누락 | `BEGIN IMMEDIATE`, 200문서 단위 커밋 배치, `last_seen` 갱신 스로틀링, `Connection.backup()`, 원자적 `vectors.npz` 교체 (03 §6) |
| 11 | MED | 벡터 패싯 필터를 top-k 뒤에 적용, 리로드 조율 없음 | argpartition 전 행 마스크. generation·행 수를 확인하는 `.npz`. `meta.vectors_generation` 폴링. mmap 미사용 (03 §5) |
| 12 | MED | ES 권한(`_index_template`은 쓰기 권한 필요), `_cat`은 애플리케이션용 아님, 데이터 스트림, Airflow 인코딩/페이지네이션/JWT/map_index/base URL | 최소 권한 목록, 템플릿은 선택, `_cluster/health?level=indices` + `_stats`, `_data_stream`. Airflow 설정 `base_url`과 주의 사항 (01 §6–7) |
| 13 | MED | 정적 파싱 누락(Jinja SQL, 다중 statement, TaskGroup, expand, Asset) | 모든 DAG 태스크 그래프는 REST `downstream_task_ids`로, AST는 코드/SQL에만. Jinja 플레이스홀더 치환. `sqlglot.parse` (01 §4) |
| 14 | MED | markdown-it commonmark 프리셋이 raw HTML 허용. Mermaid/KaTeX trust, HTML에 링커 적용, 프록시 뒤 IP 필터 문제 | `html: False`, 스킴 허용 목록, Mermaid strict, KaTeX trust false, 토큰 단위 링커, Tailscale IP 바인딩 (platform §4, 04 §3, ADR-011) |
| 15 | MED | 멱등 키에 모델/설정 누락. 비용이 다중 호출·thinking·캐시 최소 길이를 무시 | 키 = 시맨틱 해시 + 생성기 + 프롬프트 + 모델 + 설정 해시. 비용 전체 **$120–250**, 일일 $4–10로 수정. 캐시 동작 확인 (02 §3–4, research §5, platform §7) |
| 16 | MED | macOS에서 결정성 깨짐(대소문자 무시 APFS, NFD), YAML 순서, 타임존 | NFC id, casefold 기준 유일한 `id_fold`, `core.precomposeunicode`, 자체 YAML dumper, DAG 타임존 기준 렌더링 (02 §5, 03 §3) |
| 17 | LOW | 인용 검사가 기계적 수준 | 한계 명시. 의미상 뒷받침은 리뷰어와 골든 세트로 판단 (02 §5) |
| 18 | LOW | 정규식 마스킹이 시크릿을 놓침. 결정적 페이지에서 `default_args` 유출 가능 | 전 페이지 시크릿 스캔 후 커밋 차단. 렌더러가 시크릿으로 보이는 키 마스킹 (02 §5, platform §4) |
| 19 | LOW | pypdfium2는 표 유실 | 저충실도 표시, pdfplumber는 선택 (01 §2) |
| 20 | HIGH | 일정 추정이 낙관적(M1 3–5 d, 전체 12–20 d) | M1을 M1a/M1b로 분리. M2 7–10 d. 전체 **≈ 23–34 d** (roadmap §1, overview) |

## 3차 리뷰: 운영자 가치, 단순성, 일정 리스크

- 시나리오 점검: (1) 02:10에 DAG 실패, (2) 검색 결과가 최신이 아님, (3) 컬럼 이름 변경, (4) 신규 엔지니어 온보딩, (5) AIOps 에이전트의 컨텍스트 요청

| # | 심각도 | 지적 사항 | 수정 내용 (위치) |
|---|---|---|---|
| 1 | HIGH | 첫 릴리스에 다운스트림 뷰, 실시간 상태, 딥 링크, 온콜 정보, DAG 간 센서 없음 | M1a에 파싱 엣지와 다운스트림 목록. `ExternalTaskSensor` → `waits_for`. **요청 시 조회하는 실시간 상태(60 s 캐시)와 경과 시간**. `links:`, `oncall:` 설정 (01 §4–5, 04 §7, 06 §3, knowledge-model §4) |
| 2 | HIGH | "오늘 인덱스가 빌드·전환됐나?"를 P1에야 확인 가능. 데이터 최신성 정보 없음 | 인덱스 상태 점검과 `/api/health/indices`를 **M1a P0**로. 업스트림 최신성은 생산 DAG의 마지막 성공 시각으로 (04 §5, roadmap) |
| 3 | MED | 컬럼 이름 변경 영향을 테이블 단위로만 파악 | 태스크 속성 `columns_read/written`. `column:` 검색. *이 컬럼을 쓰는 곳* 섹션. `SELECT *` 표시 (knowledge-model §5, 04 §5) |
| 4 | HIGH | "후보 생성은 어떻게 동작하나"에 답하는 페이지가 없음 | 설정으로 정의한 **파이프라인**을 Mermaid 리니지와 함께 결정적 렌더링(M1b), LLM 개요(M2), 사람이 쓰는 "여기서 시작" 페이지 (knowledge-model §3, 02 §2, 04 §5) |
| 5 | HIGH | 에이전트가 여러 번 호출해야 함. 토큰은 M4에야 나옴 | 한 번에 받는 **`/api/context`**. 읽기 토큰은 M1b. 사람이 채우는 `runbooks:` 필드. `doc_md`/콜백/알림용 고정 `/e/<entity>` URL (05 §2, ADR-015) |
| 6 | HIGH | Batches 처리 구조는 시기상조 | v1 = **동기 스레드 풀** + 예산 게이트. Batches는 "추후" 설계로 보존. 두 방식 비용 모두 표기 (02 §3, ADR-007, platform §7, research §5) |
| 7 | MED | 단일 호스트에 리스 테이블은 과함 | `data/worker.lock`에 `fcntl.flock` (ADR-010, 03 §6) |
| 8 | MED | 리뷰 구조가 무거움(revisions 테이블, diff UI) | stale + 사실 정보 diff + 재생성/유지, 이력은 git (ADR-014, 02 §4) |
| 9 | LOW | 추가로 걷어낼 부분 | 오프셋 맵 저장 제거(쿼리 시점 재분석). redirects → M3. 웹훅 → P2. 자체 JSON-RPC 제거(추후 실제 MCP). Claude 리랭크·스코어카드 → P2. 벡터는 키워드 평가에서 부족할 때만. **일부 미반영:** KaTeX 유지(PRD가 수식 요구, 벤더링하면 부담 적음) |
| 10 | MED | 인증이 필요 이상으로 무거움 | **Tailscale 신원**(`whois`) + 역할 허용 목록, 로컬 계정은 fallback만 (Q12). tailnet에 HTTPS 인증서가 아직 없어 TLS manager 유지 (ADR-011) |
| 11 | HIGH | 2주 안에 낼 릴리스가 없음 | **R1 "온콜 카탈로그"** = M0 + M1a, 완료 기준은 드릴 통과 (roadmap §1) |
| 12 | MED | 확장 구조 없음, 설정 하나에 Airflow 하나 | **ADR-017** `TypeSpec` + 커넥터 레지스트리. `sources:` 목록 (platform §8, 01 §6) |
| 13 | HIGH | 잘못된 서술 정정이 오래 걸림 | R1에 원클릭 **섹션 정정**(`human:` 블록, `S0`로 입력)과 피드백. 팀별 리뷰 큐, 요약 알림 (ADR-014, 06 §2) |
| 14 | MED | 성공 지표, 쿼리 로그 없음 | `search_log`. 지표와 "새벽 3시 드릴" 목표 (05 §6, platform §9) |
| 15 | MED | 옵션 점수를 계획이 커지기 전에 매김 | 재검증 메모: 늘어난 범위는 옵션과 무관, 여전히 A 우세 (options §2) |
| 16 | LOW | `.npy`/mmap과 `.npz` 혼재, 예시 네임스페이스가 옛것 | ADR-004 → `vectors.npz`, mmap 미사용. frontmatter 예시 → `index_family:search-prod.products` |
| 17 | MED | 진행을 막는 질문 누락, 기본값 없음 | Q1–Q14에 기본값, ★는 R1 블로커 (overview) |

3차 리뷰 후 최종 점검:

- 링크·ADR 앵커 확인: 깨진 링크 0개, ADR 17개
- 앞선 리뷰에서 제거한 용어(leases, proposed revisions, offset maps, `/agent/call`, `vectors.npy`, `bcrypt`, `M1 |`)를 grep. 남은 결과는 대체 내용을 설명하는 메모뿐

## 리뷰 이후 변경

- 3차 리뷰 이후 요구사항 변경으로 추가된 결정. 위 리뷰 기록은 당시 상태 그대로 보존(Claude·Batches·git·$ 언급은 이력)

| 변경 | 근거 | 반영 문서 |
|---|---|---|
| **ADR-018**: LLM 상한 GPT-OSS-120B(사내 서빙, OpenAI 호환 API, `httpx` 직접 호출). LLM 없는 자동화 우선(요약·추출·분류·표준화는 규칙·템플릿, LLM은 서술 보강·잔여분만), LLM 리랭크 삭제, 비용 → 처리량 모델. ADR-007 대체 | 사용 가능한 LLM이 GPT-OSS-120B까지. Claude 전용 기능(Batches, 프롬프트 캐싱, `messages.parse` 등) 전제 무효 | 전체 문서 (overview, options, decisions, research §1·§4.2·§5, knowledge-model, platform §2·§4·§6·§7, roadmap, 01–06) |
| **ADR-019**: 한국어 검색 품질 최우선. nori `_analyze` + 사용자 사전 + 바이그램 안전망, 임베딩 기본값 KURE-v1, 한국어 평가 세트(recall@5 ≥ 0.85, MRR@10 ≥ 0.7, 무결과율 < 5 %), 옵션 재평가(한국어 검색 가중치 20 %, A 4.85), Q15 추가. ADR-005·ADR-006 갱신 | 한국어 검색 품질이 최우선 요구사항, 사내 클러스터에서 nori 사용 가능 | 전체 문서 (overview, options, decisions, research §1·§4.1·§4.3, platform §2·§9·§10, roadmap, 01·03·04·05·06) |
| **ADR-020**: git 미사용. SQLite가 유일한 기준 원본, `documents` + append-only `document_versions`, 이력·diff는 자체 화면, 백업·복구는 DB 백업 복원 + `opspedia rebuild`. ADR-003 대체 | 운영 호스트에서 git 사용 불가(개발·설계 문서는 git 허용). 배포는 빌드 아티팩트 복사·설치 | 전체 문서 (overview, options, decisions, knowledge-model, research §1, platform §1·§2·§4·§5·§10, roadmap, 01–06) |
