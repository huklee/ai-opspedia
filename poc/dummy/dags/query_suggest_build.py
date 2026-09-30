"""검색 클릭 로그로 검색어 자동완성 인덱스를 주간 빌드."""
from airflow import DAG
from opspedia_ops.operators import IndexBuildOperator

with DAG(dag_id="query_suggest_build", schedule="0 2 * * 0", tags=["search", "index"],
         default_args={"owner": "search-infra"}) as dag:
    build = IndexBuildOperator(task_id="build_query_suggest", index_family="query-suggest",
                               source_table="logs.search_clicks")
