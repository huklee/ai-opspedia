#!/bin/sh
# PoC 전용 로컬 OpenSearch (단일 노드 · 127.0.0.1:9200 · nori). 사용: scripts/opensearch.sh start|stop|status
# 설치는 scripts/setup.sh. 보안 플러그인 없는 min 배포본이라 인증 없음 → 반드시 localhost 바인딩으로만 사용.
set -e
cd "$(dirname "$0")/.."
RT="${RUNTIME:-$PWD/.runtime}"
OS="$(ls -d "$RT"/opensearch-* 2>/dev/null | head -1)"
[ -n "$OS" ] || { echo "OpenSearch 없음 → scripts/setup.sh 실행"; exit 1; }
JH="$(ls -d "$RT"/jdk-*/Contents/Home 2>/dev/null | head -1)"
[ -n "$JH" ] || JH="$(ls -d "$RT"/jdk-* 2>/dev/null | head -1)"
export OPENSEARCH_JAVA_HOME="$JH"
export OPENSEARCH_JAVA_OPTS="${OPENSEARCH_JAVA_OPTS:--Xms768m -Xmx768m}"
PID="$RT/opensearch.pid"
case "$1" in
  start)
    [ -d "$OS/plugins/analysis-nori" ] || "$OS/bin/opensearch-plugin" install --batch analysis-nori
    "$OS/bin/opensearch" -d -p "$PID" -Ediscovery.type=single-node -Enetwork.host=127.0.0.1 \
      -Ehttp.port=9200 -Epath.data="$RT/os-data" -Epath.logs="$RT/os-logs" -Ecluster.name=opspedia-poc
    for i in $(seq 1 60); do curl -s -m 2 http://127.0.0.1:9200 >/dev/null 2>&1 && break; sleep 1; done
    curl -s -m 5 http://127.0.0.1:9200/_cluster/health; echo ;;
  stop) [ -f "$PID" ] && kill "$(cat "$PID")" && rm -f "$PID" ;;
  status) curl -s -m 5 http://127.0.0.1:9200/_cluster/health || echo down ;;
  *) echo "usage: $0 start|stop|status"; exit 1 ;;
esac
