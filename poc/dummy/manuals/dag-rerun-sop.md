# DAG 재실행 표준 절차 (SOP)

> 문서 ID SOP-DP-001 · 담당 데이터 플랫폼 · 최종 검토 2026-08-30

Airflow DAG 실패 후 재실행(clear · trigger) 시의 표준. 잘못된 clear 는 하위 DAG 전체 재처리를 일으키므로 범위를 먼저 확인.

## 1. 결정 표

| 상황 | 명령 | 비고 |
|---|---|---|
| 실패 태스크만 재시도 | `airflow tasks clear <dag> -s <ds> -e <ds> --only-failed --yes` | 가장 흔함 |
| 특정 태스크부터 다운스트림까지 | `airflow tasks clear <dag> -t <task> --downstream -s <ds> -e <ds> --yes` | |
| 새 파라미터로 1회 실행 | `airflow dags trigger <dag> -e <ds> -c '<json>'` | 메모리 상향 등 |
| upstream_failed 연쇄 해소 | 업스트림 성공 후 하위 DAG `clear` | 센서 재평가 |

## 2. 사전 확인

- opspedia DAG 페이지의 **다운스트림(3홉)** 표로 재처리 범위 확인
- 일시정지(paused) 여부: `airflow dags list | grep <dag>`
- 같은 dag_run 중복 실행 여부: `max_active_runs`

## 3. 금지 사항

- 운영 시간(09:00–22:00) 대량 `--reset-dagruns` 금지
- 원인 미확인 상태의 반복 clear (3회 이상) 금지 → 런북 원인 분기 먼저

## 4. 기록

- 재실행 이유 · 명령 · 결과를 장애 티켓 코멘트에 남김 → opspedia 장애 페이지 타임라인에 자동 반영
