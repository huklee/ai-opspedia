-- 추천 시스템 테이블 (Hive)
CREATE TABLE reco.users (user_id BIGINT COMMENT '유저 ID', signup_dt STRING COMMENT '가입일', segment STRING COMMENT '유저 세그먼트') COMMENT '유저 마스터';
CREATE TABLE reco.items (item_id BIGINT COMMENT '아이템 ID', category_id INT COMMENT '카테고리', price DECIMAL(12,2) COMMENT '가격') COMMENT '아이템 마스터';
CREATE TABLE reco.user_features (user_id BIGINT COMMENT '유저 ID', clicks_7d INT COMMENT '최근 7일 클릭 수', dwell_avg DOUBLE COMMENT '평균 체류 시간(ms)') COMMENT '유저 피처(피처스토어)' PARTITIONED BY (dt STRING);
CREATE TABLE reco.item_features (item_id BIGINT COMMENT '아이템 ID', category_id INT COMMENT '카테고리', clicks_7d INT COMMENT '최근 7일 클릭 수', orders_7d INT COMMENT '최근 7일 주문 수') COMMENT '아이템 피처(피처스토어)' PARTITIONED BY (dt STRING);
CREATE TABLE reco.user_embeddings (user_id BIGINT, vec ARRAY<FLOAT> COMMENT '64차원 유저 임베딩') COMMENT '유저 임베딩' PARTITIONED BY (dt STRING);
CREATE TABLE reco.item_embeddings (item_id BIGINT, vec ARRAY<FLOAT> COMMENT '64차원 아이템 임베딩') COMMENT '아이템 임베딩' PARTITIONED BY (dt STRING);
CREATE TABLE reco.candidates (user_id BIGINT, items ARRAY<BIGINT> COMMENT '후보 아이템 500개') COMMENT '후보생성 결과' PARTITIONED BY (hr STRING);
CREATE TABLE reco.ranking_training_set (user_id BIGINT, clicks_7d INT, dwell_avg DOUBLE, clicked BOOLEAN COMMENT '라벨') COMMENT '랭킹 모델 학습 데이터';
CREATE TABLE reco.model_registry (model STRING, version STRING, uri STRING) COMMENT '모델 레지스트리';
CREATE TABLE reco.ranked_items (user_id BIGINT, top50 ARRAY<BIGINT> COMMENT '랭킹 상위 50개') COMMENT '랭킹 결과' PARTITIONED BY (dt STRING);
CREATE TABLE reco.ctr_report (user_id BIGINT, ctr DOUBLE COMMENT '클릭률') COMMENT '추천 CTR 리포트' PARTITIONED BY (dt STRING);
