"""후보 아이템에 랭킹 점수를 매겨 유저별 상위 50개 선정."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sensors.external_task import ExternalTaskSensor

with DAG(dag_id="ranking_score_daily", schedule="0 5 * * *", tags=["reco", "ranking"],
         default_args={"owner": "reco-platform", "retries": 2}) as dag:
    wait = ExternalTaskSensor(task_id="wait_embeddings", external_dag_id="user_embedding_daily")
    score = SQLExecuteQueryOperator(task_id="score_candidates", sql="""
        INSERT OVERWRITE TABLE reco.ranked_items PARTITION (dt='{{ ds }}')
        SELECT c.user_id, rank_items(c.items, f.clicks_7d, m.version) AS top50
        FROM reco.candidates c JOIN reco.user_features f ON c.user_id = f.user_id
        CROSS JOIN reco.model_registry m
    """)
    wait >> score
