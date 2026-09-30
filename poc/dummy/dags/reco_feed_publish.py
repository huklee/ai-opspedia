"""랭킹 결과를 reco-feed 인덱스로 빌드하고 alias 전환."""
from airflow import DAG
from airflow.sensors.external_task import ExternalTaskSensor
from opspedia_ops.operators import IndexBuildOperator, AliasSwapOperator

with DAG(dag_id="reco_feed_publish", schedule="0 6 * * *", tags=["reco", "index"],
         default_args={"owner": "reco-platform", "retries": 1}) as dag:
    wait = ExternalTaskSensor(task_id="wait_ranking", external_dag_id="ranking_score_daily")
    build = IndexBuildOperator(task_id="build_reco_feed_index", index_family="reco-feed",
                               source_table="reco.ranked_items")
    swap = AliasSwapOperator(task_id="swap_reco_feed_alias", alias="reco-feed", index_family="reco-feed")
    wait >> build >> swap
