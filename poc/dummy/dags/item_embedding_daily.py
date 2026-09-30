"""아이템 피처로 아이템 임베딩 생성."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sensors.external_task import ExternalTaskSensor

with DAG(dag_id="item_embedding_daily", schedule="30 3 * * *", tags=["reco", "embedding"],
         default_args={"owner": "reco-platform"}) as dag:
    wait = ExternalTaskSensor(task_id="wait_features", external_dag_id="feature_store_daily")
    infer = SQLExecuteQueryOperator(task_id="infer_item_embedding", sql="""
        INSERT OVERWRITE TABLE reco.item_embeddings PARTITION (dt='{{ ds }}')
        SELECT item_id, embed_item(category_id, clicks_7d, orders_7d) AS vec FROM reco.item_features WHERE dt='{{ ds }}'
    """)
    wait >> infer
