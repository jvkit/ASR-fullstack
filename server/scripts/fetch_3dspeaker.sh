#!/usr/bin/env bash
# fetch_3dspeaker.sh — 拉取 3D-Speaker 源码到 server/third_party/（接入一期 A 阶段，2026-09-19）
#
# 用法（项目根 meeting-asr/）：
#   bash server/scripts/fetch_3dspeaker.sh
#
# 行为：
#   1. 验代理 http://127.0.0.1:7897 活没活；死了自动改直连（modelscope/codeload 国内多数可直连）
#   2. 从 codeload.github.com 拉 modelscope/3D-Speaker main tarball
#   3. 解压到 server/third_party/3D-Speaker/（third_party/ 已 gitignore，第三方源码不入库）
#
# 注意：git clone 走代理在本机 GnuTLS 报错，一律 tarball（scd_bench 阶段1 的坑）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="$ROOT/server/third_party"
PROXY="http://127.0.0.1:7897"
URL="https://codeload.github.com/modelscope/3D-Speaker/tar.gz/refs/heads/main"

CURL_OPTS=(-sL --max-time 300)
if curl -x "$PROXY" -sI --max-time 10 https://codeload.github.com -o /dev/null -w '%{http_code}' | grep -qE '2|3'; then
  echo "[fetch] 代理 $PROXY 可用"
  CURL_OPTS+=(-x "$PROXY")
else
  echo "[fetch] 代理不可用，改直连"
fi

mkdir -p "$DEST"
TMP_TAR="$(mktemp /tmp/3dspeaker.XXXXXX.tar.gz)"
trap 'rm -f "$TMP_TAR" "${TMP_TAR%.tar.gz}.extract"' EXIT

echo "[fetch] 下载 $URL"
curl "${CURL_OPTS[@]}" -o "$TMP_TAR" "$URL"
tar tzf "$TMP_TAR" >/dev/null  # 校验包完整（勿用 | head：pipefail 下 SIGPIPE 会中断）

rm -rf "$DEST/3D-Speaker" "$DEST/3D-Speaker-main"
tar xzf "$TMP_TAR" -C "$DEST"
mv "$DEST/3D-Speaker-main" "$DEST/3D-Speaker"

echo "[fetch] 完成：$DEST/3D-Speaker"
ls "$DEST/3D-Speaker" | head -8
