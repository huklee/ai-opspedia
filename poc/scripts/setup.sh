#!/usr/bin/env bash
# ai-opspedia PoC 로컬 설치: Python 의존성 + (선택) OpenSearch · JDK · nori + (선택) Ollama.
# 사용: scripts/setup.sh [--lite] [--with-ollama]
#   --lite         Java · OpenSearch 없이 (SQLite 바이그램 검색 · LLM mock) — config.lite.yaml 로 실행
#   --with-ollama  로컬 LLM(Ollama) 설치 + qwen2.5:3b 받기 (GPT-OSS-120B 엔드포인트가 없을 때의 대역 모델)
# 설치 위치는 전부 poc/.runtime (시스템 변경 없음). RUNTIME 환경변수로 바꿀 수 있음.
set -euo pipefail
cd "$(dirname "$0")/.."
RT="${RUNTIME:-$PWD/.runtime}"
OS_VERSION="${OPENSEARCH_VERSION:-3.8.0}"
LITE=0; OLLAMA=0
for a in "$@"; do case "$a" in --lite) LITE=1 ;; --with-ollama) OLLAMA=1 ;; *) echo "알 수 없는 옵션 $a"; exit 1 ;; esac; done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "필요: $1 ($2)"; exit 1; }; }

need curl "https://curl.se"
need tar "시스템 패키지"
if ! command -v uv >/dev/null 2>&1; then
  say "uv 설치 (Python 패키지 · 버전 관리자)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

say "Python 3.12 + 의존성 설치 (uv sync)"
uv sync --python 3.12

OS="$(uname -s)"; ARCH="$(uname -m)"
# Apple Silicon 에서 Rosetta 셸로 실행돼도 네이티브 arm64 를 쓰도록 하드웨어 기준 판별
if [ "$OS" = "Darwin" ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then ARCH=arm64; fi
case "$ARCH" in arm64|aarch64) JARCH=aarch64; OARCH=arm64 ;; x86_64|amd64) JARCH=x64; OARCH=x64 ;; *) echo "지원하지 않는 CPU $ARCH"; exit 1 ;; esac
case "$OS" in Darwin) JOS=mac ;; Linux) JOS=linux ;; *) echo "지원하지 않는 OS $OS (macOS · Linux)"; exit 1 ;; esac
mkdir -p "$RT"

if [ "$LITE" = 0 ]; then
  if ! ls -d "$RT"/jdk-* >/dev/null 2>&1; then
    say "JDK 21 (Temurin, $JOS/$JARCH) 받기"
    curl -fsSL -o "$RT/jdk.tar.gz" "https://api.adoptium.net/v3/binary/latest/21/ga/$JOS/$JARCH/jdk/hotspot/normal/eclipse"
    tar xzf "$RT/jdk.tar.gz" -C "$RT" && rm "$RT/jdk.tar.gz"
  else say "JDK 있음 — 건너뜀"; fi
  if [ ! -d "$RT/opensearch-$OS_VERSION" ]; then
    # min 배포본은 순수 Java 라 macOS 에서도 리눅스 tarball 을 그대로 사용 (번들 JDK 대신 위 JDK 사용)
    say "OpenSearch $OS_VERSION (min 배포본, linux-$OARCH) 받기"
    curl -fsSL -o "$RT/os.tar.gz" "https://artifacts.opensearch.org/releases/core/opensearch/$OS_VERSION/opensearch-min-$OS_VERSION-linux-$OARCH.tar.gz"
    tar xzf "$RT/os.tar.gz" -C "$RT" && rm "$RT/os.tar.gz"
  else say "OpenSearch 있음 — 건너뜀"; fi
  if [ ! -d "$RT/opensearch-$OS_VERSION/plugins/analysis-nori" ]; then
    say "nori 한국어 분석 플러그인 설치"
    JH="$(ls -d "$RT"/jdk-*/Contents/Home 2>/dev/null || ls -d "$RT"/jdk-* | head -1)"
    OPENSEARCH_JAVA_HOME="$JH" "$RT/opensearch-$OS_VERSION/bin/opensearch-plugin" install --batch analysis-nori
  fi
fi

if [ "$OLLAMA" = 1 ]; then
  if [ "$OS" = "Darwin" ]; then
    if [ ! -x "$RT/ollama/ollama" ]; then
      say "Ollama (macOS) 받기"
      mkdir -p "$RT/ollama"
      curl -fsSL -o "$RT/ollama.tgz" https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz
      tar xzf "$RT/ollama.tgz" -C "$RT/ollama" && rm "$RT/ollama.tgz"
    fi
  elif ! command -v ollama >/dev/null 2>&1; then
    say "Ollama (Linux) — 공식 설치 스크립트 사용 (sudo 필요할 수 있음)"
    curl -fsSL https://ollama.com/install.sh | sh
  fi
  scripts/ollama.sh start
  scripts/ollama.sh pull qwen2.5:3b
fi

say "완료"
if [ "$LITE" = 1 ]; then
  echo "  uv run opspedia-poc -c config.lite.yaml seed-history"
  echo "  uv run opspedia-poc -c config.lite.yaml serve --http"
else
  echo "  scripts/opensearch.sh start"
  echo "  uv run opspedia-poc seed-history"
  echo "  uv run opspedia-poc serve --http"
fi
echo "  → http://127.0.0.1:8443"
