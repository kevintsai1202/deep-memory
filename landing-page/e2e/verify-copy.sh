#!/usr/bin/env bash
# =============================================================================
# 驗證 landing-page 安裝指令「點擊複製」功能是否正常
#
# 用途：
#   - 回歸測試 copyInstall() 綁定（onclick="{{copyInstall}}"）是否真的觸發複製
#   - localhost 屬安全情境，navigator.clipboard 可用，可直接驗證剪貼簿內容
#
# 作法：
#   1. 以 python 起一個本機靜態伺服器服務 landing-page/
#   2. agent-browser 開頁、在點擊前對 clipboard.writeText 佈署 spy
#   3. 點擊安裝指令元素，讀回 spy 攔截到的字串比對
#
# 重跑：直接執行本檔即可（bash landing-page/e2e/verify-copy.sh）
# =============================================================================
set -euo pipefail

PORT=8899
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # landing-page/
EXPECTED='npx skills add kevintsai1202/deep-memory --all -g'
URL="http://localhost:${PORT}/"

# 啟動靜態伺服器（背景），結束時關閉
python -m http.server "$PORT" --directory "$ROOT_DIR" >/dev/null 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; agent-browser close 2>/dev/null || true' EXIT
sleep 1

agent-browser open "$URL"
agent-browser wait --load networkidle

# 在點擊前攔截 clipboard.writeText，記錄被寫入的字串到 window.__copied
cat <<'EOF' | agent-browser eval --stdin
(() => {
  window.__copied = null;
  // 純攔截：只記錄寫入字串並回傳成功 Promise。
  // 不呼叫真實 API——headless 無視窗焦點時真實 writeText 會 reject，
  // 那是環境限制而非程式缺陷，會干擾「handler 是否傳入正確字串」的判定。
  if (navigator.clipboard) {
    navigator.clipboard.writeText = (text) => { window.__copied = text; return Promise.resolve(); };
  }
  // 覆寫 alert / prompt，避免 modal 阻塞自動化
  window.alert = () => {};
  window.prompt = () => null;
  return 'spy-installed';
})();
EOF

# 精準點擊「膠囊」複製按鈕（以 title 辨識，避免點到同樣含指令文字的程式碼區塊）
cat <<'EOF' | agent-browser eval --stdin
(() => {
  const pill = document.querySelector('.mono[title="點擊複製安裝指令"]');
  if (!pill) return 'pill-not-found';
  pill.click();          // 觸發 React 委派的 onClick
  return 'clicked';
})();
EOF
agent-browser wait 300

# 讀回攔截結果
RESULT=$(cat <<'EOF' | agent-browser eval --stdin
window.__copied
EOF
)

echo "expected: $EXPECTED"
echo "captured: $RESULT"

if echo "$RESULT" | grep -qF "$EXPECTED"; then
  echo "PASS: 點擊已成功觸發複製，且內容正確"
  exit 0
else
  echo "FAIL: 未攔截到正確的複製內容"
  exit 1
fi
