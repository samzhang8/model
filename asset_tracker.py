#!/usr/bin/env python3
"""
资产实时跟踪看板
- 本地 HTTP 服务 (端口 8787)
- 代理腾讯实时行情接口 (绕过 CORS)
- 支持动态增删资产标的
- 每 5 秒自动刷新
"""

import http.server
import urllib.request
import json
import re
from urllib.parse import urlparse, parse_qs

PORT = 8787

# ============================================================
#  数据层：腾讯行情解析
# ============================================================

def fetch_quotes(codes: list[str]) -> list[dict]:
    """从腾讯接口拉取实时行情，解析为结构化数据"""
    # 腾讯接口需要带市场前缀: sh/sz/bj
    # 如果用户只给了纯数字代码，自动判断市场
    query_codes = []
    for c in codes:
        c = c.strip()
        if c.startswith(("sh", "sz", "bj")):
            query_codes.append(c)
        elif c.startswith("5") or c.startswith("6") or c.startswith("9"):
            query_codes.append(f"sh{c}")
        elif c.startswith("0") or c.startswith("3") or c.startswith("2"):
            query_codes.append(f"sz{c}")
        else:
            query_codes.append(f"sh{c}")

    url = f"https://qt.gtimg.cn/q={','.join(query_codes)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="replace")
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for line in raw.strip().split("\n"):
        line = line.strip().rstrip(";")
        if not line or "=" not in line:
            continue
        m = re.match(r'v_(\w+)="(.*)"', line)
        if not m:
            continue
        full_code = m.group(1)
        fields = m.group(2).split("~")
        if len(fields) < 50:
            continue

        # 腾讯实时行情字段索引（通过实际数据验证）
        # 0:市场类型 1:名称 2:代码 3:现价 4:昨收 5:今开
        # 6:成交量(手) 7:外盘 8:内盘 9-28:五档买卖盘
        # 30:时间戳 31:涨跌额 32:涨跌幅 33:最高 34:最低
        # 37:成交额(万) 43:振幅 44:总市值(亿) 45:流通市值(亿)
        # 48:? 49:换手率
        def safe_float(idx):
            try:
                return float(fields[idx])
            except (ValueError, IndexError):
                return 0.0

        def safe_str(idx):
            try:
                return fields[idx]
            except IndexError:
                return ""

        price = safe_float(3)
        prev_close = safe_float(4)
        change = price - prev_close if prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0

        results.append({
            "code": safe_str(2),
            "full_code": full_code,
            "name": safe_str(1),
            "price": round(price, 3),
            "prev_close": round(prev_close, 3),
            "open": safe_float(5),
            "high": safe_float(33),
            "low": safe_float(34),
            "change": round(change, 3),
            "change_pct": round(change_pct, 2),
            "volume": safe_float(6),          # 成交量(手)
            "turnover": safe_float(37),       # 成交额(万)
            "market_cap": safe_str(45),       # 流通市值(亿)
            "amplitude": safe_float(43),      # 振幅(%)
            "turnover_rate": safe_float(49),  # 换手率(%)
            "timestamp": safe_str(30),        # 行情时间
        })

    return results


# ============================================================
#  HTML 前端
# ============================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>资产跟踪看板</title>
<style>
  :root {
    --bg: #0a0e1a;
    --card-bg: #121829;
    --card-border: #1e2740;
    --text: #e0e6f0;
    --text-dim: #6b7a99;
    --green: #00c853;
    --red: #ff1744;
    --blue: #2979ff;
    --yellow: #ffd600;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 20px;
  }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
  }
  .header h1 {
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 2px;
  }
  .header h1 span { color: var(--blue); }
  .header .clock {
    font-size: 14px;
    color: var(--text-dim);
  }
  .header .status {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    margin-left: 8px;
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  /* Add asset bar */
  .add-bar {
    display: flex;
    gap: 8px;
    margin-bottom: 20px;
  }
  .add-bar input {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    color: var(--text);
    padding: 8px 14px;
    border-radius: 6px;
    font-family: inherit;
    font-size: 14px;
    outline: none;
    width: 140px;
  }
  .add-bar input:focus { border-color: var(--blue); }
  .add-bar input::placeholder { color: var(--text-dim); }
  .add-bar button {
    background: var(--blue);
    color: #fff;
    border: none;
    padding: 8px 20px;
    border-radius: 6px;
    font-family: inherit;
    font-size: 14px;
    cursor: pointer;
    transition: opacity 0.2s;
  }
  .add-bar button:hover { opacity: 0.85; }

  /* Asset grid */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 16px;
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 20px;
    position: relative;
    transition: border-color 0.3s;
  }
  .card:hover { border-color: var(--blue); }
  .card .remove-btn {
    position: absolute;
    top: 12px; right: 12px;
    background: none;
    border: none;
    color: var(--text-dim);
    font-size: 18px;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.2s;
  }
  .card:hover .remove-btn { opacity: 1; }
  .card .remove-btn:hover { color: var(--red); }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 16px;
  }
  .card-name {
    font-size: 16px;
    font-weight: 600;
  }
  .card-code {
    font-size: 12px;
    color: var(--text-dim);
    margin-top: 2px;
  }

  .price-section {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 16px;
  }
  .price {
    font-size: 32px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .change-badge {
    font-size: 14px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 4px;
  }
  .up { color: var(--green); }
  .down { color: var(--red); }
  .flat { color: var(--text-dim); }
  .change-badge.up { background: rgba(0,200,83,0.12); }
  .change-badge.down { background: rgba(255,23,68,0.12); }

  .stats {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 8px;
  }
  .stat {
    text-align: center;
    padding: 8px 4px;
    background: rgba(255,255,255,0.02);
    border-radius: 6px;
  }
  .stat-label {
    font-size: 11px;
    color: var(--text-dim);
    margin-bottom: 4px;
  }
  .stat-value {
    font-size: 14px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .card-time {
    margin-top: 12px;
    font-size: 11px;
    color: var(--text-dim);
    text-align: right;
  }

  .empty {
    text-align: center;
    color: var(--text-dim);
    padding: 60px 20px;
    font-size: 14px;
  }

  /* Flash animation on price update */
  @keyframes flash-up { 0%{background:rgba(0,200,83,0.15)} 100%{background:transparent} }
  @keyframes flash-down { 0%{background:rgba(255,23,68,0.15)} 100%{background:transparent} }
  .flash-up { animation: flash-up 0.6s; }
  .flash-down { animation: flash-down 0.6s; }
</style>
</head>
<body>

<div class="header">
  <h1>📊 <span>资产跟踪</span>看板</h1>
  <div class="clock" id="clock">--:--:--<span class="status" id="status-dot"></span></div>
</div>

<div class="add-bar">
  <input type="text" id="code-input" placeholder="输入代码 如 510300" maxlength="10" onkeydown="if(event.key==='Enter')addAsset()">
  <button onclick="addAsset()">+ 添加资产</button>
</div>

<div class="grid" id="grid"></div>

<script>
let assets = [];          // [{code, name, price}]
let prevPrices = {};      // code -> last price (for flash animation)

// ---- Persistence (localStorage) ----
function saveAssets() {
  localStorage.setItem('tracked_assets', JSON.stringify(assets));
}
function loadAssets() {
  try {
    const saved = localStorage.getItem('tracked_assets');
    if (saved) assets = JSON.parse(saved);
  } catch(e) {}
  if (!assets.length) {
    // Default: 3 assets
    assets = [
      {code:'510300', name:'沪深300ETF'},
      {code:'510500', name:'中证500ETF'},
      {code:'511880', name:'银华日利ETF'},
    ];
    saveAssets();
  }
}

// ---- Add / Remove ----
function addAsset() {
  const input = document.getElementById('code-input');
  let code = input.value.trim();
  if (!code) return;
  // Remove possible prefix
  code = code.replace(/^(sh|sz|bj)/i, '');
  if (assets.find(a => a.code === code)) {
    input.value = '';
    return;
  }
  assets.push({code, name: ''});
  saveAssets();
  input.value = '';
  refresh();
}

function removeAsset(code) {
  assets = assets.filter(a => a.code !== code);
  delete prevPrices[code];
  saveAssets();
  refresh();
}

// ---- Fetch & Render ----
async function refresh() {
  if (!assets.length) {
    document.getElementById('grid').innerHTML = '<div class="empty">暂无跟踪资产，请在上方添加代码</div>';
    return;
  }
  const codes = assets.map(a => a.code).join(',');
  try {
    const resp = await fetch(`/api/quotes?codes=${codes}`);
    const data = await resp.json();
    renderCards(data);
  } catch(e) {
    document.getElementById('status-dot').style.background = 'var(--red)';
  }
}

function renderCards(quotes) {
  const grid = document.getElementById('grid');
  // Update asset names from API
  quotes.forEach(q => {
    if (q.name) {
      const a = assets.find(a => a.code === q.code);
      if (a) a.name = q.name;
    }
  });
  saveAssets();

  grid.innerHTML = quotes.map(q => {
    if (q.error) {
      return `<div class="card"><div class="card-name">错误</div><div style="color:var(--red);margin-top:8px">${q.error}</div></div>`;
    }
    const cls = q.change > 0 ? 'up' : q.change < 0 ? 'down' : 'flat';
    const arrow = q.change > 0 ? '▲' : q.change < 0 ? '▼' : '—';
    const flashClass = '';
    // Flash effect
    const prev = prevPrices[q.code];
    let flash = '';
    if (prev !== undefined && prev !== q.price) {
      flash = q.price > prev ? 'flash-up' : 'flash-down';
    }
    prevPrices[q.code] = q.price;

    return `
    <div class="card ${flash}">
      <button class="remove-btn" onclick="removeAsset('${q.code}')">✕</button>
      <div class="card-header">
        <div>
          <div class="card-name">${q.name || q.code}</div>
          <div class="card-code">${q.full_code || q.code}</div>
        </div>
      </div>
      <div class="price-section">
        <div class="price ${cls}">${q.price.toFixed(3)}</div>
        <div class="change-badge ${cls}">${arrow} ${q.change.toFixed(3)} (${q.change_pct.toFixed(2)}%)</div>
      </div>
      <div class="stats">
        <div class="stat">
          <div class="stat-label">今开</div>
          <div class="stat-value">${q.open.toFixed(3)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">最高</div>
          <div class="stat-value up">${q.high.toFixed(3)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">最低</div>
          <div class="stat-value down">${q.low.toFixed(3)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">成交量(万手)</div>
          <div class="stat-value">${(q.volume/10000).toFixed(1)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">换手率</div>
          <div class="stat-value">${q.turnover_rate.toFixed(2)}%</div>
        </div>
        <div class="stat">
          <div class="stat-label">振幅</div>
          <div class="stat-value">${q.amplitude.toFixed(2)}%</div>
        </div>
      </div>
      <div class="card-time">${q.timestamp || '--'}</div>
    </div>`;
  }).join('');

  document.getElementById('status-dot').style.background = 'var(--green)';
}

// ---- Clock ----
function updateClock() {
  const now = new Date();
  document.getElementById('clock').innerHTML =
    now.toLocaleTimeString('zh-CN', {hour12:false}) +
    '<span class="status" id="status-dot"></span>';
}

// ---- Init ----
loadAssets();
refresh();
updateClock();
setInterval(refresh, 5000);
setInterval(updateClock, 1000);
</script>
</body>
</html>"""


# ============================================================
#  HTTP Server
# ============================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif parsed.path == "/api/quotes":
            params = parse_qs(parsed.query)
            codes = params.get("codes", [""])[0].split(",")
            codes = [c.strip() for c in codes if c.strip()]
            if not codes:
                self._json([])
                return
            data = fetch_quotes(codes)
            self._json(data)

        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"📊 资产跟踪看板已启动: http://localhost:{PORT}")
    print(f"   按 Ctrl+C 停止")
    server.serve_forever()
