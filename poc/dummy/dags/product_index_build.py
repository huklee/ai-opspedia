"""상품 테이블로 products_vN 인덱스를 새로 빌드(리인덱스)."""
from airflow import DAG
from opspedia_ops.operators import IndexBuildOperator

with DAG(dag_id="product_index_build", schedule="0 3 * * *", tags=["search", "index"],
         default_args={"owner": "search-infra", "retries": 1}) as dag:
    build = IndexBuildOperator(task_id="build_products_index", index_family="products",
                               source_table="search.products")
