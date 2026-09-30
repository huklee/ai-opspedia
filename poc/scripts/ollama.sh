#!/bin/sh
# 로컬 Ollama (127.0.0.1:11434). 사용: scripts/ollama.sh start|stop|pull <model>|signin
# macOS: poc/.runtime/ollama (setup.sh --with-ollama) · Linux: 시스템에 설치된 ollama
# 모델 저장 위치는 Ollama 기본값(~/.ollama/models)
set -e
cd "$(dirname "$0")/.."
RT="${RUNTIME:-$PWD/.runtime}"
BIN="$RT/ollama/ollama"; [ -x "$BIN" ] || BIN="$(command -v ollama || true)"
[ -n "$BIN" ] || { echo "Ollama 없음 → scripts/setup.sh --with-ollama"; exit 1; }
RUN="$BIN"
# Apple Silicon: Rosetta 셸에서도 네이티브(arm64 · Metal)로 실행
if [ "$(uname -s)" = "Darwin" ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then RUN="arch -arm64 $BIN"; fi
export OLLAMA_HOST=127.0.0.1:11434
case "$1" in
  start) tmux has-session -t opspedia-ollama 2>/dev/null || tmux new-session -d -s opspedia-ollama "OLLAMA_HOST=$OLLAMA_HOST OLLAMA_KEEP_ALIVE=30m $RUN serve 2>&1 | tee '$RT/ollama.log'"
         for i in $(seq 1 30); do curl -s -m 2 http://127.0.0.1:11434/api/version && break; sleep 1; done; echo ;;
  stop) tmux kill-session -t opspedia-ollama ;;
  pull) $RUN pull "$2" ;;
  signin) $RUN signin ;;   # Ollama Cloud (gpt-oss:120b-cloud) 사용 시
  *) echo "usage: $0 start|stop|pull <model>|signin"; exit 1 ;;
esac
