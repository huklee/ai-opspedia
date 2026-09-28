# 2. 지식 합성 엔진 (`opspedia/synthesis/`)

## 1. 한눈에 보기

- `ChangeSet`마다 **표준 Markdown 문서** 생성
  - 구성: frontmatter + 템플릿 본문
  - 엔티티·엣지·청크·임베딩도 함께 생성
- 정형 출처는 결정적(deterministic) 렌더링
- 원칙은 **LLM 없는 자동화 우선**([ADR-018](../decisions.md#adr-018))
  - 규칙·파서·템플릿으로 먼저 처리
  - LLM은 그 잔여분만 담당
- LLM은 GPT-OSS-120B(사내 서빙)([ADR-008](../decisions.md#adr-008))
  - 용도는 서술 작성, 규칙 실패분의 추출·분류, 요약 보강에 한정
- 결과는 SQLite에 직접 기록([ADR-020](../decisions.md#adr-020))
  - `documents`: 현재 Markdown 전문
  - `document_versions`: append-only 버전
- `llm=off`여도 전부 동작
  - 결정적 렌더링·표준화·추출·분류·청킹·인덱싱

```
 ChangeSet ─▶ route by kind
   ├─ dag_code/dag_live/table/index/alias ─▶ Renderer (Jinja template, facts, template summary) ─┐
   │                                            └─▶ LLM prose request (optional, GPT-OSS-120B) ─┤
   ├─ manual/incident ─▶ Standardizer (field→template mapping first; LLM for leftovers, source-grounded) ─┤
   ▼                                                                                             ▼
 Validator (schema, citations, links) ─▶ Merge with existing document (fences, overrides, review state) ─▶ SQLite documents + document_versions (1 transaction)
   ─▶ Entity & edge writer ─▶ Chunker (+contextual header) ─▶ Embedder (cached) ─▶ index update (storage)
```

## 2. 하위 모듈

| 모듈 | 입력 → 출력 | LLM 사용 |
|---|---|---|
| `renderers/{dag,table,index_family,alias,index_template,lifecycle_policy,system,team,service}.py` | 정규화된 RawItem → 본문 섹션([knowledge-model](../knowledge-model.md) §4 템플릿) + frontmatter 사실 정보 + 파싱한 엣지 · 인덱스 패밀리는 **Versions** 표와 직전 버전 대비 매핑 diff도 렌더링 | 없음 |
| `renderers/pipeline.py` | 설정에 정의한 파이프라인(DAG 목록 또는 태그) → DAG 순서, 생성 테이블·인덱스 패밀리, 파싱한 엣지 기반 Mermaid 리니지 다이어그램 · LLM 개요는 M2에서 추가 | 없음(M1b), 선택(M2, 개요 서술만) |
| `standardizer.py` (Markdown Standardizer) | 매뉴얼/장애 텍스트 → 타입별 템플릿(예시는 표 아래 보충) · 장애: Jira 필드 → 템플릿 섹션 매핑 + 코멘트 시각순 타임라인 · 매뉴얼: 제목·목차 구조 보존 변환 | 템플릿 우선 · LLM은 장애 근본 원인·요약 서술, 매뉴얼 요약·태그 보강만 |
| `extractor.py` (Entity & Metadata Extractor) | 모든 본문 → `{entities[], system, team, tags[], edges[] (inferred)}` · 언급된 이름은 엔티티 레지스트리와 대조해 해석 | 규칙 우선(레지스트리 + Aho–Corasick + 정규식) · LLM은 미해결 멘션만 |
| `categorizer.py` (Auto Categorizer) | 문서 + 분류 체계 → 문서 `id`(= 트리 경로) | 규칙 우선(type + system/cluster) · LLM은 규칙 실패한 매뉴얼·노트만 |
| `summarizer.py` | 본문 → `summary`(1–3문장) + 결정적 페이지용 서술 섹션(선택) · 기본: frontmatter 사실 정보 + 템플릿 문장으로 1–2문장 생성 | 템플릿 우선 · LLM은 서술 보강만 |
| `chunker.py` (Summarizer & Chunker) | 본문 → 제목 단위 청크(~500 토큰, 최대 800, 제목마다 초기화되는 ~60토큰 오버랩) + 문맥 헤더 | 없음 |
| `embedder.py` | 청크 텍스트 → 벡터(제공자는 [ADR-006](../decisions.md#adr-006)) · `(model, chunk_hash)` 단위 캐시 | 임베딩 모델(LLM 아님) |
| `llm.py` | `LLMClient` 인터페이스 · GPT-OSS-120B용 OpenAI 호환 Chat Completions 클라이언트(세부는 표 아래 보충) | — |
| `lint.py` (야간) | 전체 코퍼스 → 이슈 목록(고아 페이지, 깨진 링크, 오래된 verified 페이지, owner/SLA 누락, REST↔AST 불일치, 모순) | 모순 검사만 LLM 선택 사용(P2) |

- **보충:**
  - `standardizer.py` 타입별 템플릿 예: incident는 *Summary · Impact · Timeline · Root cause · Fix · Follow-ups · Affected entities*
  - `llm.py` 세부
    - `httpx` 직접 호출, 별도 SDK 없음
    - endpoint·모델명은 설정값
    - 동시 요청 풀(4–8), 입력 해시 기반 결과 캐시, 재시도, 처리량 게이트
    - 토큰 수·처리 시간 집계
- **LLM 없는 자동화 우선** ([ADR-018](../decisions.md#adr-018))

| 작업 | LLM 없이 (기본) | LLM (보조, 규칙 실패분만) |
|---|---|---|
| 요약(`summary`) | frontmatter 사실 정보 + 템플릿 문장으로 1–2문장 생성 | 서술 보강 |
| 엔티티 추출 | 레지스트리 + Aho–Corasick + 정규식 | 미해결 멘션만 |
| 분류(트리 경로) | 타입 + 시스템 규칙 | 규칙 실패한 매뉴얼·노트만 |
| 장애 문서 표준화 | Jira 필드 → 템플릿 섹션 매핑 · 코멘트 시각순 타임라인 | 근본 원인·요약 서술만 |
| 매뉴얼 표준화 | 제목·목차 구조 보존 변환(Markdown/Confluence 변환기) | 요약·태그 보강 |
| 청킹·인덱싱·링크·그래프·상태 점검 | 전부 결정적 처리 | 사용 안 함 |
| 리랭크 | RRF만 | 사용 안 함 |
| 모순 lint | — | 나중에(P2), 선택 |

## 3. LLM 사용 ([ADR-018](../decisions.md#adr-018))

| 호출 | 방식 | 출력 계약 |
|---|---|---|
| DAG/테이블/인덱스 **서술**(목적, 사용 방식, 장애 유형) | 동시 요청 풀 · 골든 세트 한국어 품질 평가 통과 후 활성화 | JSON `{sections: {purpose, usage, failure_modes}, citations: {section: [source_idx]}}` |
| 매뉴얼/장애 **표준화** 잔여분(근본 원인·요약 서술, 요약·태그 보강) | 동시 요청 풀 · 템플릿 매핑 이후 빈 섹션만 | JSON `{title, type, summary, sections[{heading, markdown, citations[]}], entities[], tags[]}` |
| 규칙으로 못 잡은 **엔티티 추출** | 동시 요청 풀 · 미해결 멘션만 | JSON `{mentions[{text, entity_id|null, type, confidence}], edges[{src, rel, dst, evidence}]}` |
| **분류** | 동시 요청 풀 · 규칙 실패분만 | JSON `{tree_path, reason}` · 분류 체계 enum으로 제한 |
| **모순 lint** (P2) | 야간 · 선택 | JSON `{conflicts[{doc_a, doc_b, claim_a, claim_b}]}` |

- 모델: `gpt-oss-120b`
  - 사내 서빙, OpenAI 호환 Chat Completions API
  - endpoint·모델명은 설정값
  - `LLMClient` 뒤에 숨겨 이후 모델 교체는 설정 변경만
- 구조화 출력
  - 서빙이 지원하면 JSON schema 강제(`response_format` / guided decoding)
  - 미지원 시 pydantic 스키마 검증 + 1회 재시도
    - 또 실패하면 `synthesis_error`로 보류
- 프롬프트 구성: `[system: role + rules + output schema + taxonomy + glossary]` + `[user: source excerpts with numbered source ids + task]`
- 프롬프트 버전 관리
  - 버전 파일로 관리(`@version`이 붙은 `synthesis/prompts/*.md`)
  - `prompt_version`은 멱등 키와 결과 캐시 키에 포함(§4)
- 시스템 프롬프트의 출처 기반 작성 규칙([ADR-008](../decisions.md#adr-008))
  - 주어진 발췌문만 사용, 모든 섹션에 `[S1]…[Sn]` 인용
  - 모르는 내용은 추측 대신 "Unknown from sources"로 표기
  - 스케줄, 컬럼명, 인덱스명은 절대 지어내지 않음
- **3.1 v1 실행 방식: 동시 요청 풀** (3차 리뷰)
  - 실행 한 번의 LLM 요청을 모아 동시 요청 풀로 실행(동시 4–8개, 서빙 동시성 한도 준수)
  - 입력 해시 기반 결과 캐시
    - `hash(model_id, prompt_version, prompt + excerpts)`가 같으면 재호출 없이 캐시 결과 사용
  - 검증·병합 시점
    - 검증: 결과 도착 즉시
    - 병합: 실행 종료 시점
  - 검증 실패 건
    - 검증기 오류를 붙여 한 번 재시도(실행당 최대 20건)
    - 나머지는 `synthesis_error`로 보류, 실행 페이지에 표시
- **3.2 Batch 모드: 해당 없음** (GPT-OSS-120B 서빙에는 배치 API 없음, [ADR-018](../decisions.md#adr-018))
- 처리량 게이트 (토큰 과금 없음, 제약은 GPU 처리량)
  - 실행마다 LLM 호출 수·예상 소요 시간 추정
  - 설정 상한 초과 시 관리자 승인(`opspedia run synth --approve`) 필요
  - 실행 단위 사용량 저장: 호출 수, input/output 토큰 수, 처리 시간, 결과 캐시 적중 수

## 4. 병합 규칙 ([ADR-014](../decisions.md#adr-014))

| 기존 페이지 상태 | 새로 생성한 결과 | 처리 |
|---|---|---|
| 없음 | 무엇이든 | 생성, `status: generated` |
| `generated` | 변경됨 | `gen` 펜스 안만 교체 · 펜스 밖 사람이 쓴 글과 모든 `human:` 정정 블록은 유지 |
| `reviewed` / `verified` | 변경됨 | 사실 정보 표만 제자리 갱신 · 서술은 **다시 쓰지 않음** · 페이지를 `stale`로 바꾸고 사실 diff 표시 · 편집자가 **regenerate**(동기 호출) 또는 **keep** 선택 |
| 무엇이든, `<!-- human:start S -->` 정정 포함 | 무엇이든 | 정정 내용이 생성 섹션 S를 대체 · 나머지 생성 시 이 정정을 권위 있는 출처 `S0`으로 LLM에 전달 |
| `human_override: true` | 무엇이든 | 건드리지 않음 · lint가 "source changed" 기록 |
| 출처 삭제됨 | — | `status: archived` · 트리에 남기고 *Archived* 필터로 표시 |

- 멱등성
  - 키: `(semantic_hash, generator_version, prompt_version, model_id, config_hash)`
  - 키가 같으면 통째로 건너뜀(LLM 호출·새 버전 없음)
  - `model_id`는 현재 `gpt-oss-120b`, 모델 교체 시 LLM 서술 페이지만 재생성
- `config_hash`
  - 포함 항목: 분류 체계, 용어집, 템플릿, system/team 설정
  - 이 중 하나만 바꿔도 영향받는 페이지만 재생성
- 키 저장 위치
  - frontmatter(`generator`, `sources[].hash`)에 저장
  - `documents` 전문의 일부라 `opspedia rebuild`로 파생 데이터를 다시 만들어도 유지
- 버전 기록([ADR-020](../decisions.md#adr-020))
  - 병합 결과를 `documents`에 기록
  - 같은 트랜잭션에서 `document_versions`에 전체 Markdown 추가(`run_id`, `change_note`)
  - 내용 해시가 같으면 새 버전 미생성
  - 보관 기간
    - 사람 수정본은 영구 보관
    - 생성 버전은 문서당 최근 50개(설정값)
  - 이력·diff
    - `/api/pages/{id}/history` + `difflib` diff 화면
    - stale 페이지의 사실 diff도 같은 버전 데이터 기준

## 5. 쓰기 전 검증

- YAML 스키마 검사([knowledge-model.md](../knowledge-model.md) §4)
- 생성 섹션마다 유효한 인용 1개 이상
  - *기계적 확인*에 한정: `[Sn]`이 존재하고 발췌문 집합 안을 가리키는지만 확인
  - 출처가 실제로 주장을 뒷받침하는지는 리뷰어와 골든 세트 채점 기준(§7)으로 판단
- 링크 해석
  - 모든 `[[wiki links]]`/엔티티 ID 해석 필수
  - 실패 시 일반 텍스트로 격하
- 코드 블록과 표 파싱 필수
- **시크릿 스캐너**(토큰, 키, DSN, `password=`…)
  - 대상: 결정적 페이지를 포함한 *모든* 페이지
  - 이유: `document_versions`에 버전이 남고 검색·API로 바로 노출
- 검증 실패 시
  - 쓰기 차단
  - 실행 페이지에 표시
- **결정성** (재실행 시 바이트 단위로 같은 결과를 위한 조건)
  - YAML은 자체 dumper로 키 순서 고정(스키마 순서, 그다음 알 수 없는 키는 정렬)
  - 부동소수점은 고정 정밀도
  - 날짜는 오프셋을 명시한 ISO-8601
  - `updated_at`은 본문이나 사실 정보가 바뀔 때만 갱신
  - 목록은 안정적인 키로 정렬
  - id 규칙(URL·ID 일관성 목적)
    - **NFC 정규화**
    - 대소문자 무시 기준으로도 유일해야 함
    - `opspedia export --markdown` 파일명도 같은 규칙
  - 스케줄
    - DAG 자체의 타임존(REST `timezone`)으로 렌더링
    - 툴팁에 UTC 병기
    - KST 가정 금지
  - 렌더러는 `default_args`/`params`에서 시크릿처럼 보이는 키도 마스킹(`password|secret|token|key|dsn` → `***`)

## 6. 작업 목록 (일정의 기준 원본(source of truth): [roadmap.md](../roadmap.md), Pri = 마일스톤 내 우선순위)
| 마일스톤 | Pri | 작업 |
|---|---|---|
| M1a | P0 | `TypeSpec` 레지스트리([ADR-017](../decisions.md#adr-017)) 기반 렌더러(dag, table, index_family, alias, system, team) · 펜스, 병합(generated 상태) · `documents` + `document_versions` 트랜잭션 기록 · 멱등 키, 결정성 규칙 · 파싱한 엣지를 페이지와 함께 기록 |
| M1b | P0 | 파이프라인 렌더러(설정 정의 파이프라인, Mermaid 리니지) |
| M1b | P1 | index_template, lifecycle_policy 렌더러 |
| M2 | P0 | `llm.py`(GPT-OSS-120B용 OpenAI 호환 클라이언트, `httpx`, 동시 요청 풀, 결과 캐시, JSON schema 강제 또는 pydantic 검증 + 1회 재시도, 처리량 게이트) · 인용 검사 포함 검증기 |
| M2 | P0 | 템플릿 우선 summarizer · 매뉴얼·장애용 standardizer(필드 → 템플릿 매핑, 구조 보존 변환) · 엔티티 페이지 서술·파이프라인 개요 등 LLM 서술은 골든 세트 한국어 품질 평가 통과 후 활성화 |
| M2 | P0 | chunker(문맥 헤더) · embedder 제공자(local KURE-v1 / bge-m3 / voyage / off, [ADR-006](../decisions.md#adr-006)) + 캐시 · 키워드 평가에서 부족하면 벡터 활성화 |
| M2 | P1 | extractor(레지스트리 + Aho–Corasick + 정규식 우선) · categorizer(규칙 우선) · 사람이 쓴 정정(`S0`) 프롬프트 반영 |
| M4 | P0 | reviewed/verified 페이지의 stale 처리와 regenerate/keep 흐름 |
| later | P2 | 모순 lint · LLM이 쓰는 청크 문맥 줄(Contextual Retrieval) · 답변을 페이지로 되쓰기 |

## 7. 평가

- 골든 세트: 사람이 확인한 페이지 20개(DAG 5, 테이블 5, 인덱스 5, 장애 5)
  - 사실 정보는 출처와 정확히 일치해야 함(자동 diff)
  - 서술은 리뷰어가 채점 기준(정확성, 인용 타당성, 유용성)으로 평가
- GPT-OSS-120B 한국어 서술 품질
  - LLM 섹션 활성화 전 골든 세트로 평가
  - 평가 항목: 문장 자연스러움, 정확성, 인용 타당성
  - 기준 미달 시 LLM 섹션은 끄고 템플릿 요약만 사용
- 회귀 테스트
  - fixture로 합성 재실행
  - 입력이 같으면 저장되는 Markdown도 바이트 단위로 동일
  - 새 버전도 미생성
