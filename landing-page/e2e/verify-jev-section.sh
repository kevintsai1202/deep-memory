#!/usr/bin/env bash
# =============================================================================
# 驗證 landing-page 的「啟用 Jev 雲端重排」段落與 2026-10-26 changelog
#
# 檢查項目（中文與英文各一次）：
#   - #jev 段落存在，標題文字正確，沒有殘留未替換的 {{ }} 樣板
#   - 程式碼區塊含 TYPESAFE_API_KEY 設定與 config.json 的 "provider": "jev"
#   - 導覽列有 #jev 連結；changelog 有 2026-10-26 這一列
#   - 手機寬度（390px）下整頁沒有水平捲軸
# 另輸出桌面與手機截圖到 OUT_DIR（預設系統暫存目錄），供人工目視檢查。
#
# 重跑：bash landing-page/e2e/verify-jev-section.sh
# =============================================================================
set -euo pipefail

PORT=8898
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # landing-page/
OUT_DIR="${OUT_DIR:-${TMPDIR:-/tmp}}"
URL="http://localhost:${PORT}/"

python -m http.server "$PORT" --directory "$ROOT_DIR" >/dev/null 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; agent-browser close 2>/dev/null || true' EXIT
sleep 1

FAIL=0

# 依語言檢查 #jev 段落；$1 = 語言代碼，$2 = 預期標題
check_lang() {
  local lang="$1" title="$2"
  # 語言存在 localStorage，先寫入再重新載入
  agent-browser eval "localStorage.setItem('lang', '${lang}'); location.reload(); 'ok'" >/dev/null
  agent-browser wait --load networkidle
  agent-browser wait 500
  local result
  result=$(cat <<EOF | agent-browser eval --stdin
(() => {
  const sec = document.querySelector('#jev');
  if (!sec) return 'FAIL: #jev not found';
  const text = sec.innerText;
  const errs = [];
  const h2 = sec.querySelector('h2')?.innerText.trim();
  if (h2 !== ${title@Q}) errs.push('title=' + h2);
  if (text.includes('{{')) errs.push('unrendered template');
  if (!text.includes('TYPESAFE_API_KEY')) errs.push('no TYPESAFE_API_KEY');
  if (!text.includes('"provider": "jev"')) errs.push('no provider jev');
  if (!document.querySelector('a[href="#jev"]')) errs.push('no nav link');
  if (!document.body.innerText.includes('2026-10-26')) errs.push('no changelog 2026-10-26');
  return errs.length ? 'FAIL: ' + errs.join('; ') : 'PASS';
})();
EOF
)
  echo "[${lang}] ${result}"
  [[ "$result" == *PASS* ]] || FAIL=1
}

agent-browser set viewport 1440 900
agent-browser open "$URL"
agent-browser wait --load networkidle

check_lang zh "啟用 Jev 雲端重排"
check_lang en "Enable the Jev Cloud Re-ranker"

# 桌面截圖：捲到 #jev
agent-browser eval "localStorage.setItem('lang','zh'); location.reload(); 'ok'" >/dev/null
agent-browser wait --load networkidle
agent-browser eval "document.querySelector('#jev').scrollIntoView(); 'ok'" >/dev/null
agent-browser wait 300
agent-browser screenshot "${OUT_DIR}/jev-section-desktop.png" >/dev/null

# 手機寬度：檢查水平溢出並截圖
agent-browser set viewport 390 844
agent-browser wait 500
# 外層容器可能用 overflow:hidden 把溢出藏起來，所以除了整頁捲軸，也檢查每張卡片的右緣
OVERFLOW=$(agent-browser eval "Math.max(document.documentElement.scrollWidth - window.innerWidth, ...[...document.querySelectorAll('#jev .card')].map(c => Math.ceil(c.getBoundingClientRect().right - window.innerWidth)))")
echo "[mobile] 最大超出寬度 = ${OVERFLOW}px"
if [[ "${OVERFLOW//[^0-9-]/}" -gt 0 ]]; then
  echo "FAIL: 手機寬度下 Jev 卡片或整頁超出畫面"
  FAIL=1
fi
agent-browser eval "document.querySelector('#jev').scrollIntoView(); 'ok'" >/dev/null
agent-browser wait 300
agent-browser screenshot "${OUT_DIR}/jev-section-mobile.png" >/dev/null

echo "截圖：${OUT_DIR}/jev-section-desktop.png、${OUT_DIR}/jev-section-mobile.png"
if [[ $FAIL -eq 0 ]]; then
  echo "PASS: Jev 段落與 changelog 在中英文皆正確呈現"
else
  echo "FAIL"
fi
exit $FAIL
