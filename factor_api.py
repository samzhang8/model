#!/usr/bin/env python3
"""
Factor Data API — 908因子IC数据付费查询服务
FastAPI + API Key鉴权 + 用量计费
"""
import json, time, hashlib, secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ── Config ──
FACTOR_FILE = Path("/opt/quant/docs/factor_metrics.json")
API_KEYS_FILE = Path("/opt/quant/docs/api_keys.json")
BILLING_FILE = Path("/opt/quant/docs/api_billing.jsonl")
HOST = "0.0.0.0"
PORT = 8788

# ── Load data ──
with open(FACTOR_FILE) as f:
    FACTOR_DATA = json.load(f)

FACTORS = {f["name"]: f for f in FACTOR_DATA["factors"]}
CATEGORIES = list(set(f["category"] for f in FACTOR_DATA["factors"]))

# ── Load/create API keys ──
if API_KEYS_FILE.exists():
    with open(API_KEYS_FILE) as f:
        API_KEYS = json.load(f)
else:
    API_KEYS = {}

def save_keys():
    with open(API_KEYS_FILE, "w") as f:
        json.dump(API_KEYS, f, indent=2)

# ── Init ──
app = FastAPI(title="Factor Data API", version="1.0.0")

# ── Auth ──
def verify_key(api_key: str) -> dict:
    if api_key not in API_KEYS:
        raise HTTPException(403, "Invalid API key")
    key_info = API_KEYS[api_key]
    if key_info.get("expires_at") and datetime.fromisoformat(key_info["expires_at"]) < datetime.now():
        raise HTTPException(403, "API key expired")
    if key_info.get("suspended"):
        raise HTTPException(403, "API key suspended")
    return key_info

def log_billing(api_key: str, endpoint: str, tokens: int):
    with open(BILLING_FILE, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now().isoformat(),
            "key": api_key[:8],
            "endpoint": endpoint,
            "tokens": tokens,
        }) + "\n")

# ── Endpoints ──

@app.get("/")
def root():
    return {
        "service": "Factor Data API",
        "version": "1.0.0",
        "total_factors": FACTOR_DATA["total_factors"],
        "data_source": FACTOR_DATA["data_source"],
        "generated_at": FACTOR_DATA["generated_at"],
        "categories": CATEGORIES,
        "docs": "/docs",
    }

@app.get("/v1/factors")
def list_factors(
    api_key: str = Header(..., alias="X-API-Key"),
    category: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    min_ic_ir: Optional[float] = Query(None),
    limit: int = Query(50, le=200),
):
    key_info = verify_key(api_key)
    results = []
    for f in FACTOR_DATA["factors"]:
        if category and f["category"] != category:
            continue
        if direction and f["direction"] != direction:
            continue
        if status and f["status"] != status:
            continue
        if min_ic_ir is not None and abs(f.get("ic_ir", 0)) < min_ic_ir:
            continue
        results.append({
            "name": f["name"],
            "category": f["category"],
            "ic_mean": f["ic_mean"],
            "ic_ir": f["ic_ir"],
            "win_rate": f["win_rate"],
            "direction": f["direction"],
            "status": f["status"],
            "trend": f.get("trend"),
            "smallcap_corr": f.get("smallcap_corr"),
        })
    results.sort(key=lambda x: abs(x["ic_ir"]), reverse=True)
    if limit:
        results = results[:limit]
    log_billing(api_key, "list_factors", len(results))
    return {"count": len(results), "factors": results}

@app.get("/v1/factors/{name}")
def get_factor(
    name: str,
    api_key: str = Header(..., alias="X-API-Key"),
):
    verify_key(api_key)
    f = FACTORS.get(name)
    if not f:
        raise HTTPException(404, f"Factor '{name}' not found")
    log_billing(api_key, "get_factor", 1)
    return f

@app.get("/v1/factors/{name}/ic_history")
def get_ic_history(
    name: str,
    api_key: str = Header(..., alias="X-API-Key"),
):
    verify_key(api_key)
    f = FACTORS.get(name)
    if not f:
        raise HTTPException(404, f"Factor '{name}' not found")
    log_billing(api_key, "ic_history", len(f.get("ic_history", [])))
    return {
        "name": name,
        "ic_history": f.get("ic_history", []),
        "recent_3m": f.get("recent_3m", []),
    }

@app.get("/v1/top")
def top_factors(
    api_key: str = Header(..., alias="X-API-Key"),
    by: str = Query("ic_ir", regex="^(ic_ir|ic_mean|win_rate)$"),
    n: int = Query(20, le=100),
):
    verify_key(api_key)
    key = lambda f: abs(f.get(by, 0)) if by != "win_rate" else f.get(by, 0)
    top = sorted(FACTOR_DATA["factors"], key=key, reverse=True)[:n]
    log_billing(api_key, "top_factors", n)
    return {"by": by, "factors": [
        {"name": f["name"], "category": f["category"],
         "ic_ir": f["ic_ir"], "ic_mean": f["ic_mean"],
         "win_rate": f["win_rate"], "direction": f["direction"]}
        for f in top
    ]}

@app.get("/v1/stats")
def api_stats(api_key: str = Header(..., alias="X-API-Key")):
    key_info = verify_key(api_key)
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0)
    total = 0
    month_usage = 0
    if BILLING_FILE.exists():
        with open(BILLING_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec["key"] == api_key[:8]:
                        total += rec["tokens"]
                        if datetime.fromisoformat(rec["ts"]) >= month_start:
                            month_usage += rec["tokens"]
                except:
                    pass
    return {
        "key": api_key[:8],
        "plan": key_info.get("plan", "free"),
        "total_tokens": total,
        "month_tokens": month_usage,
        "month_limit": key_info.get("monthly_limit", 10000),
        "remaining": max(0, key_info.get("monthly_limit", 10000) - month_usage),
    }

# ── Admin endpoints (localhost only) ──

@app.post("/admin/keys")
def create_key(plan: str = "free", monthly_limit: int = 10000, expires_days: int = 365):
    key = "fa_" + secrets.token_hex(16)
    API_KEYS[key] = {
        "plan": plan,
        "monthly_limit": monthly_limit,
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(days=expires_days)).isoformat(),
        "suspended": False,
    }
    save_keys()
    return {"api_key": key, "plan": plan, "monthly_limit": monthly_limit}

if __name__ == "__main__":
    print(f"📊 Factor Data API starting on {HOST}:{PORT}")
    print(f"   {FACTOR_DATA['total_factors']} factors loaded")
    print(f"   Categories: {len(CATEGORIES)}")
    print(f"   Docs: http://localhost:{PORT}/docs")
    uvicorn.run(app, host=HOST, port=PORT)
