"""유저·아이템 임베딩 근접 이웃으로 후보 아이템 500개 생성(후보생성)."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

with DAG(dag_id="candidate_gen_hourly", schedule="15 * * * *", tags=["reco", "candidate"],
         default_args={"owner": "reco-platform", "retries": 1}) as dag:
    gen = SQLExecuteQueryOperator(task_id="generate_candidates", sql="""
        INSERT OVERWRITE TABLE reco.candidates PARTITION (hr='{{ ts_nodash }}')
        SELECT u.user_id, ann_top_k(u.vec, i.vec, 500) AS items
        FROM reco.user_embeddings u CROSS JOIN reco.item_embeddings i
    """)
