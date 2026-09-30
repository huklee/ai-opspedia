# #reco-ops 스레드 export (2026-09-29)

[07:08] 김운영: @here 추천 피드 신선도 알림(RecoFeedStale 26.7h) 떴습니다. 확인 중
[07:09] 김운영: opspedia 보니 ranking_score_daily 가 05:21 에 failed, reco_feed_publish 는 upstream_failed 네요. 알려진 장애에 INC-2291 이 같은 증상
[07:11] 이검색: 검색 쪽도 products_v42 가 문서 수 30% 줄어서 04시에 alias 전환 막혔어요. 서비스는 v41 로 정상입니다
[07:12] 김운영: SEV2 로 올리고 INC-2341 만들었습니다. 사용자 영향: 추천 피드가 어제 결과로 나가는 중
[07:20] 박추천: score_candidates 로그에 "exceeding memory limits (14.2 GB of 12 GB)" 찍혀 있어요. OOM 맞습니다
[07:34] 박추천: reco.candidates 파티션 보니까 hr=2026092904 가 두 개 있네요. 행 수가 평소 1.2억인데 3.4억
[07:36] 박추천: candidate_gen_hourly 가 04시에 두 번 돌았어요. 스케줄러 재시작 때 catchup 이 켜져 있었던 듯
[07:41] 김운영: 런북 RB-RECO-002 대로 중복 파티션 지우고 재실행 가죠
[07:52] 박추천: `ALTER TABLE reco.candidates DROP PARTITION (hr='2026092904_dup')` 실행 완료
[07:55] 박추천: `airflow tasks clear ranking_score_daily -s 2026-09-29 -e 2026-09-29 --only-failed --yes`
[08:31] 박추천: score_candidates 성공 (36분). reco.ranked_items 오늘 파티션 생성 확인
[08:33] 김운영: reco_feed_publish clear 합니다
[08:50] 김운영: swap_reco_feed_alias 성공, reco-feed → reco-feed-2026.09.29 확인. 신선도 정상화
[08:52] 이검색: products 쪽은 search.products 적재가 늦게 끝나서 재고 필터가 대량 제외된 걸로 보여요. INC-2310 이랑 같은 원인. 적재 끝나면 product_index_build 재실행 예정
[08:55] 김운영: 추천은 해소, 검색은 진행 중. 30분 뒤 업데이트
[09:40] 박추천: 재발 방지: candidate_gen_hourly max_active_runs=1 로 바꾸고 catchup=False 확인 필요. 액션아이템 등록할게요
