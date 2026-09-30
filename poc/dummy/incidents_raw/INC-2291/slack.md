# #reco-ops 스레드 export (2026-08-14)

[05:40] 김운영: ranking_score_daily 실패 알림 확인. 로그에 OutOfMemoryError
[05:52] 김운영: 재시도해도 같은 에러. 추천 피드는 어제 인덱스 그대로입니다
[06:30] 박추천: candidate_gen_hourly 가 04시에 두 번 실행됐어요. reco.candidates 파티션이 평소의 3배
[06:44] 박추천: 중복 파티션 삭제했습니다
[06:46] 박추천: ranking_score_daily score_candidates 재실행
[07:31] 박추천: 성공. reco_feed_publish 수동 트리거
[07:55] 김운영: reco-feed alias 전환 완료, 해소 공지 올립니다
