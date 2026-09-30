"""랭킹 모델 주간 재학습. 학습 데이터는 피처 + 클릭 로그."""
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.operators.bash import BashOperator

with DAG(dag_id="ranking_train_weekly", schedule="0 4 * * 1", tags=["reco", "training"],
         default_args={"owner": "reco-platform"}) as dag:
    build = SQLExecuteQueryOperator(task_id="build_training_set", sql="""
        INSERT OVERWRITE TABLE reco.ranking_training_set
        SELECT f.*, e.clicked FROM reco.user_features f JOIN logs.user_events e ON f.user_id = e.user_id
    """)
    train = BashOperator(task_id="train_model", bash_command="python train.py --out s3://models/ranking/")
    register = SQLExecuteQueryOperator(task_id="register_model", sql="""
        INSERT INTO reco.model_registry SELECT 'ranking', '{{ ds }}', 's3://models/ranking/' FROM reco.ranking_training_set LIMIT 1
    """)
    build >> train >> register
