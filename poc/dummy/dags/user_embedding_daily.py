"""유저 피처로 유저 임베딩(64-d)을 학습·추론."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sensors.external_task import ExternalTaskSensor

with DAG(dag_id="user_embedding_daily", schedule="30 3 * * *", tags=["reco", "embedding"],
         default_args={"owner": "reco-platform"}) as dag:
    wait = ExternalTaskSensor(task_id="wait_features", external_dag_id="feature_store_daily")
    infer = SQLExecuteQueryOperator(task_id="infer_user_embedding", sql="""
        INSERT OVERWRITE TABLE reco.user_embeddings PARTITION (dt='{{ ds }}')
        SELECT user_id, embed_user(clicks_7d, dwell_avg) AS vec FROM reco.user_features WHERE dt='{{ ds }}'
    """)
    wait >> infer
