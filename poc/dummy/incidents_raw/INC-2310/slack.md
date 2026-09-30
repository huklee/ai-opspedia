# #search-ops 스레드 export (2026-09-02)

[04:10] 이검색: products alias 전환 실패 알림. 새 인덱스 products_v19 docs_count 가 78만, 기존 120만
[04:25] 이검색: 서비스는 이전 인덱스로 정상. 사용자 영향 없음
[05:20] 이검색: search.products 적재가 아직 진행 중이네요. 재고 여부 필터 때문에 대량 제외된 듯. 적재 끝나면 재빌드
[06:05] 이검색: 적재 완료 확인, product_index_build 재실행
[06:32] 이검색: 문서 수 121만 확인. `POST _aliases` 로 수동 전환 완료
