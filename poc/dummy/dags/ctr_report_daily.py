"""추천 피드 CTR 일간 리포트."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

with DAG(dag_id="ctr_report_daily", schedule="0 9 * * *", tags=["reco", "report"],
         default_args={"owner": "reco-platform"}) as dag:
    report = SQLExecuteQueryOperator(task_id="build_ctr_report", sql="""
        INSERT OVERWRITE TABLE reco.ctr_report PARTITION (dt='{{ ds }}')
        SELECT r.user_id, avg(e.clicked) AS ctr FROM reco.ranked_items r JOIN logs.user_events e ON r.user_id = e.user_id
        GROUP BY r.user_id
    """)
