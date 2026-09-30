"""유저·아이템 피처를 매일 새벽 집계해 피처스토어 테이블에 적재."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sensors.external_task import ExternalTaskSensor

with DAG(dag_id="feature_store_daily", schedule="0 2 * * *", tags=["reco", "feature"],
         default_args={"owner": "reco-platform", "retries": 2}) as dag:
    wait_logs = ExternalTaskSensor(task_id="wait_search_log_etl", external_dag_id="search_log_etl")
    user_features = SQLExecuteQueryOperator(task_id="build_user_features", sql="""
        INSERT OVERWRITE TABLE reco.user_features PARTITION (dt='{{ ds }}')
        SELECT u.user_id, count(e.event_id) AS clicks_7d, avg(e.dwell_ms) AS dwell_avg
        FROM logs.user_events e JOIN reco.users u ON e.user_id = u.user_id
        WHERE e.dt >= date_sub('{{ ds }}', 7) GROUP BY u.user_id
    """)
    item_features = SQLExecuteQueryOperator(task_id="build_item_features", sql="""
        INSERT OVERWRITE TABLE reco.item_features PARTITION (dt='{{ ds }}')
        SELECT i.item_id, i.category_id, sum(s.clicks) AS clicks_7d, sum(s.orders) AS orders_7d
        FROM reco.items i LEFT JOIN logs.search_clicks s ON i.item_id = s.item_id
        GROUP BY i.item_id, i.category_id
    """)
    wait_logs >> [user_features, item_features]
