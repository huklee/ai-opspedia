-- 로그·검색 테이블 (Hive)
CREATE TABLE logs.raw_search_events (event_id STRING, user_id BIGINT, query STRING COMMENT '검색어', item_id BIGINT, dwell_ms INT, clicked BOOLEAN, ordered INT) COMMENT '검색·클릭 원시 로그' PARTITIONED BY (dt STRING);
CREATE TABLE logs.user_events (event_id STRING, user_id BIGINT, item_id BIGINT, dwell_ms INT COMMENT '체류 시간(ms)', clicked BOOLEAN) COMMENT '정제된 유저 이벤트' PARTITIONED BY (dt STRING);
CREATE TABLE logs.search_clicks (query STRING COMMENT '검색어', item_id BIGINT, clicks INT, orders INT) COMMENT '검색어별 클릭 집계' PARTITIONED BY (dt STRING);
CREATE TABLE search.products (item_id BIGINT COMMENT '상품 ID', title STRING COMMENT '상품명', brand STRING, price DECIMAL(12,2), in_stock BOOLEAN COMMENT '재고 여부') COMMENT '검색용 상품 테이블';
