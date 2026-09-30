"""새 products 인덱스 검증 후 products alias 전환. 문서 수가 20 % 넘게 줄면 중단."""
from airflow import DAG
from airflow.sensors.external_task import ExternalTaskSensor
from opspedia_ops.operators import AliasSwapOperator

with DAG(dag_id="product_index_alias_swap", schedule="0 4 * * *", tags=["search", "index"],
         default_args={"owner": "search-infra"}) as dag:
    wait = ExternalTaskSensor(task_id="wait_build", external_dag_id="product_index_build")
    swap = AliasSwapOperator(task_id="swap_products_alias", alias="products", index_family="products",
                             max_doc_drop=0.2)
    wait >> swap
