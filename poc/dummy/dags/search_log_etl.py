"""검색·클릭 원시 로그를 정제해 로그 테이블에 적재."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

with DAG(dag_id="search_log_etl", schedule="0 1 * * *", tags=["search", "logs"],
         default_args={"owner": "search-infra"}) as dag:
    clicks = SQLExecuteQueryOperator(task_id="load_search_clicks", sql="""
        INSERT OVERWRITE TABLE logs.search_clicks PARTITION (dt='{{ ds }}')
        SELECT query, item_id, count(*) AS clicks, sum(ordered) AS orders FROM logs.raw_search_events GROUP BY query, item_id
    """)
    events = SQLExecuteQueryOperator(task_id="load_user_events", sql="""
        INSERT OVERWRITE TABLE logs.user_events PARTITION (dt='{{ ds }}')
        SELECT event_id, user_id, item_id, dwell_ms, clicked FROM logs.raw_search_events
    """)
