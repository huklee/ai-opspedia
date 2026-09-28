# 2. 지식 합성 엔진 (`opspedia/synthesis/`)

## 1. 한눈에 보기

- `ChangeSet`마다 **표준 Markdown 문서**(frontmatter + 템플릿 본문) 생성, 엔티티·엣지·청크·임베딩도 함께 생성
- 정형 출처는 결정적(deterministic) 렌더링
- Claude는 서술 작성, 비정형 텍스트 추출, 분류, 요약에만 사용([ADR-008](../decisions.md#adr-008))

```
 ChangeSet ─▶ route by kind
   ├─ dag_code/dag_live/table/index/alias ─▶ Renderer (Jinja template, facts)  ─┐
   │                                            └─▶ LLM prose request (batched) ─┤
   ├─ manual/incident ─▶ Standardizer (LLM, source-grounded, structured output) ─┤
   ▼                                                                             ▼
 Validator (schema, citations, links) ─▶ Merge with existing file (fences, overrides, review state) ─▶ content/ + commit
   ─▶ Entity & edge writer ─▶ Chunker (+contextual header) ─▶ Embedder (cached) ─▶ index update (storage)
```

## 2. 하위 모듈

| 모듈 | 입력 → 출력 | LLM 사용 |
|---|---|---|
| `renderers/{dag,table,index_family,alias,index_template,lifecycle_policy,system,team,service}.py` | 정규화된 RawItem → 본문 섹션([knowledge-model](../knowledge-model.md) §4 템플릿) + frontmatter 사실 정보 + 파싱한 엣지. 인덱스 패밀리는 **Versions** 표와 직전 버전 대비 매핑 diff도 렌더링 | 없음 |
| `renderers/pipeline.py` | 설정에 정의한 파이프라인(DAG 목록 또는 태그) → DAG 순서, 생성 테이블·인덱스 패밀리, 파싱한 엣지 기반 Mermaid 리니지 다이어그램. LLM 개요는 M2에서 추가 | 없음(M1b), 있음(M2) |
| `standardizer.py` (Markdown Standardizer) | 매뉴얼/장애 텍스트 → 타입별 템플릿(예: incident는 *Summary · Impact · Timeline · Root cause · Fix · Follow-ups · Affected entities*) | **있음** |
| `extractor.py` (Entity & Metadata Extractor) | 모든 본문 → `{entities[], system, team, tags[], edges[] (inferred)}`. 언급된 이름은 엔티티 레지스트리와 대조해 해석 | 혼합(정규식/레지스트리 우선, 나머지 LLM) |
| `categorizer.py` (Auto Categorizer) | 문서 + 분류 체계 → 문서 `id`(= 트리 경로) | 규칙 우선(type + system/cluster), 매뉴얼/노트만 LLM |
| `summarizer.py` | 본문 → `summary`(1–3문장) + 결정적 페이지용 서술 섹션(선택) | **있음** |
| `chunker.py` (Summarizer & Chunker) | 본문 → 제목 단위 청크(~500 토큰, 최대 800, 제목마다 초기화되는 ~60토큰 오버랩) + 문맥 헤더 | 없음 |
| `embedder.py` | 청크 텍스트 → 벡터(제공자는 [ADR-006](../decisions.md#adr-006)), `(model, chunk_hash)` 단위 캐시 | 임베딩 API |
| `llm.py` | 요청 구성, 동기 스레드 풀 실행(v1), Batches 제출/수집(추후), 재시도, 비용 집계 | — |
| `lint.py` (야간) | 전체 코퍼스 → 이슈 목록(고아 페이지, 깨진 링크, 오래된 verified 페이지, owner/SLA 누락, REST↔AST 불일치, 모순) | 모순 검사만 LLM 선택 사용 |

## 3. LLM 사용 ([ADR-007](../decisions.md#adr-007))

| 호출 | 방식 | 출력 계약 |
|---|---|---|
| DAG/테이블/인덱스 **서술**(목적, 사용 방식, 장애 유형) | 동기 풀(v1) → Batches(추후) | JSON `{sections: {purpose, usage, failure_modes}, citations: {section: [source_idx]}}` |
| 매뉴얼/장애 **표준화** | 동기 풀(v1) → Batches(추후) | JSON `{title, type, summary, sections[{heading, markdown, citations[]}], entities[], tags[]}` |
| 규칙으로 못 잡은 **엔티티 추출** | 동기 풀(v1) → Batches(추후) | JSON `{mentions[{text, entity_id|null, type, confidence}], edges[{src, rel, dst, evidence}]}` |
| **분류** | 동기 풀(v1) → Batches(추후) | JSON `{tree_path, reason}`, 분류 체계 enum으로 제한 |
| **모순 lint** (P2) | 야간 Batches | JSON `{conflicts[{doc_a, doc_b, claim_a, claim_b}]}` |

- 모델: `claude-opus-5`
- 구조화 출력은 `client.messages.parse()` / `output_config.format`으로 받아 Pydantic 스키마로 검증. 실패 시 한 번 재시도, 또 실패하면 `synthesis_error`로 보류
- 캐싱을 고려한 프롬프트 순서: `[system: role + rules + output schema + taxonomy + glossary] (cached)` 다음에 `[user: source excerpts with numbered source ids + task]`. 자주 바뀌는 데이터는 캐시 분기점 뒤에만 배치
- 시스템 프롬프트의 출처 기반 작성 규칙
  - 주어진 발췌문만 사용, 모든 섹션에 `[S1]…[Sn]` 인용
  - 모르는 내용은 추측 대신 "Unknown from sources"로 표기
  - 스케줄, 컬럼명, 인덱스명은 절대 지어내지 않음
- **3.1 v1 실행 방식: 동기 호출** (3차 리뷰)
  - 실행 한 번의 LLM 요청을 모아 스레드 풀로 실행(동시 4–8개, rate-limit 헤더 준수). 서버 측 거부 대응 fallback 활성화
  - 결과는 도착 즉시 검증, 병합은 실행 종료 시점
  - 검증 실패 건은 검증기 오류를 붙여 한 번 재시도(실행당 최대 20건). 나머지는 `synthesis_error`로 보류하고 실행 페이지에 표시
- **3.2 배치 모드 (추후, 물량이 −50 % 절감을 정당화할 때)**: 대기 중 락을 잡지 않도록 작업을 둘로 분리
  1. `synth-submit`: 요청마다 `batch_items(custom_id, batch_id, doc_id, call, prompt_version, state)` 행 기록, `messages.batches.create` 호출 후 작업에 `batch_id` 저장. API가 `^[a-zA-Z0-9_-]{1,64}$` 형식을 요구하므로 `custom_id = sha1(doc_id | call | prompt_version)[:32]`
  2. `synth-collect` (열린 배치가 있는 동안 10분마다): 결과가 순서 없이 오므로 `custom_id`로 원래 요청에 대응시킨 뒤 검증·병합·커밋
  3. `errored` / `expired` 항목은 최대 3번 재제출, 그 뒤에는 보류
- 비용 가드
  - 실행마다 `count_tokens`로 비용 추정. 설정 예산(예: $20) 초과 시 관리자 승인(`opspedia run synth --approve`) 필요
  - 실행 단위 사용량 저장: input, output(thinking 포함), `cache_creation_input_tokens`, `cache_read_input_tokens`, $
- 캐싱 확인
  - 고정 prefix가 모델의 최소 캐시 가능 길이보다 길어야 함. 짧으면 경고 없이 캐시 안 됨
  - 첫 실행에서 `cache_read_input_tokens > 0` 확인
  - Batches(추후) 안의 캐시 적중은 보장되지 않으므로 예산은 캐시 효과 없음을 전제로 산정

## 4. 병합 규칙 ([ADR-014](../decisions.md#adr-014))

| 기존 페이지 상태 | 새로 생성한 결과 | 처리 |
|---|---|---|
| 없음 | 무엇이든 | 생성, `status: generated` |
| `generated` | 변경됨 | `gen` 펜스 안만 교체. 펜스 밖 사람이 쓴 글과 모든 `human:` 정정 블록은 유지 |
| `reviewed` / `verified` | 변경됨 | 사실 정보 표만 제자리 갱신, 서술은 **다시 쓰지 않음**. 페이지를 `stale`로 바꾸고 사실 diff 표시. 편집자가 **regenerate**(동기 호출) 또는 **keep** 선택 |
| 무엇이든, `<!-- human:start S -->` 정정 포함 | 무엇이든 | 정정 내용이 생성 섹션 S를 대체. 나머지 생성 시 이 정정을 권위 있는 출처 `S0`으로 LLM에 전달 |
| `human_override: true` | 무엇이든 | 건드리지 않음. lint가 "source changed" 기록 |
| 출처 삭제됨 | — | `status: archived`, 트리에 남기고 *Archived* 필터로 표시 |

- 멱등성: `(semantic_hash, generator_version, prompt_version, model_id, config_hash)`가 같으면 통째로 건너뜀(LLM 호출·커밋 없음)
- `config_hash`에 분류 체계, 용어집, 템플릿, system/team 설정 포함. 이 중 하나만 바꿔도 영향받는 페이지만 재생성
- 키는 frontmatter(`generator`, `sources[].hash`)에 저장. DB를 다시 만들어도 유지

## 5. 쓰기 전 검증

- YAML 스키마 검사([knowledge-model.md](../knowledge-model.md) §4)
- 생성 섹션마다 유효한 인용 1개 이상
  - *기계적 확인*에 한정: `[Sn]`이 존재하고 발췌문 집합 안을 가리키는지만 확인
  - 출처가 실제로 주장을 뒷받침하는지는 리뷰어와 골든 세트 채점 기준(§7)으로 판단
- 모든 `[[wiki links]]`/엔티티 ID 해석 필수. 실패 시 일반 텍스트로 격하
- 코드 블록과 표 파싱 필수
- **시크릿 스캐너**(토큰, 키, DSN, `password=`…)는 결정적 페이지를 포함한 *모든* 페이지 대상. 원격 저장소에 push하면 되돌릴 수 없기 때문
- 검증 실패 시 쓰기 차단, 실행 페이지에 표시

**결정성** (재실행 시 바이트 단위로 같은 결과를 위한 조건)
- YAML은 자체 dumper로 키 순서 고정(스키마 순서, 그다음 알 수 없는 키는 정렬)
- 부동소수점은 고정 정밀도, 날짜는 오프셋을 명시한 ISO-8601
- `updated_at`은 본문이나 사실 정보가 바뀔 때만 갱신
- 목록은 안정적인 키로 정렬
- macOS APFS는 대소문자를 구분하지 않으므로 id와 파일명은 **NFC 정규화**, 대소문자 무시 기준으로도 유일해야 함. 콘텐츠 저장소는 `core.precomposeunicode=true`
- 스케줄은 DAG 자체의 타임존(REST `timezone`)으로 렌더링, 툴팁에 UTC 병기. KST 가정 금지
- 렌더러는 `default_args`/`params`에서 시크릿처럼 보이는 키도 마스킹(`password|secret|token|key|dsn` → `***`)

## 6. 작업 목록 (일정의 기준 원본(source of truth): [roadmap.md](../roadmap.md), Pri = 마일스톤 내 우선순위)
| 마일스톤 | Pri | 작업 |
|---|---|---|
| M1a | P0 | `TypeSpec` 레지스트리([ADR-017](../decisions.md#adr-017)) 기반 렌더러(dag, table, index_family, alias, system, team). 펜스, 병합(generated 상태), 실행 단위 커밋, 멱등 키, 결정성 규칙, 파싱한 엣지를 페이지와 함께 기록 |
| M1b | P0 | 파이프라인 렌더러(설정 정의 파이프라인, Mermaid 리니지) |
| M1b | P1 | index_template, lifecycle_policy 렌더러 |
| M2 | P0 | `llm.py`(동기 스레드 풀, `parse()`, 캐싱, 비용 집계, 예산 게이트). 인용 검사 포함 검증기 |
| M2 | P0 | 엔티티 페이지용 summarizer와 서술, 파이프라인 개요, 매뉴얼·장애용 standardizer |
| M2 | P0 | chunker(문맥 헤더), embedder 제공자(voyage / local / off) + 캐시. 키워드 평가에서 부족하면 벡터 활성화 |
| M2 | P1 | extractor(레지스트리 우선), categorizer(규칙 우선), 사람이 쓴 정정(`S0`) 프롬프트 반영 |
| M4 | P0 | reviewed/verified 페이지의 stale 처리와 regenerate/keep 흐름 |
| later | P2 | 배치 모드(§3.2), 모순 lint, LLM이 쓰는 청크 문맥 줄(Contextual Retrieval), 답변을 페이지로 되쓰기 |

## 7. 평가

- 골든 세트: 사람이 확인한 페이지 20개(DAG 5, 테이블 5, 인덱스 5, 장애 5)
  - 사실 정보는 출처와 정확히 일치해야 함(자동 diff)
  - 서술은 리뷰어가 채점 기준(정확성, 인용 타당성, 유용성)으로 평가
- 회귀 테스트: fixture로 합성 재실행. 입력이 같으면 파일도 바이트 단위로 동일해야 함
