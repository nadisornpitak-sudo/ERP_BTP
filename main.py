from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import json, hashlib, secrets, sqlite3
from pathlib import Path
from datetime import datetime

app = FastAPI(title="BTP ERP API", version="3.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "btp_erp.json"
AUTH_DB = DATA_DIR / "btp_auth.db"

# ─── Auth DB ───────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff'
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    admin_hash = hashlib.sha256("admin1234".encode()).hexdigest()
    try:
        conn.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                     ("u_admin", "admin", admin_hash, "ผู้ดูแลระบบ", "admin"))
    except sqlite3.IntegrityError:
        pass
    conn.commit()
    conn.close()

init_db()

def verify_token(authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "").strip()
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบ")
    return row["user_id"]

def require_admin(user_id: str = Depends(verify_token)):
    conn = get_conn()
    row = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row or row["role"] != "admin":
        raise HTTPException(status_code=403, detail="เฉพาะผู้ดูแลระบบเท่านั้น")
    return user_id

# ─── Auth endpoints ────────────────────────────────────────
@app.post("/api/auth/login")
async def login(body: dict):
    username = body.get("username", "").strip()
    password = body.get("password", "")
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = get_conn()
    user = conn.execute(
        "SELECT id, name, role FROM users WHERE username=? AND password_hash=?",
        (username, pw_hash)
    ).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    token = secrets.token_hex(32)
    conn = get_conn()
    conn.execute("INSERT INTO sessions VALUES (?,?,?)",
                 (token, user["id"], datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"token": token, "name": user["name"], "role": user["role"], "username": username}

@app.post("/api/auth/logout")
async def logout(user_id: str = Depends(verify_token), authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "").strip()
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/auth/me")
async def me(user_id: str = Depends(verify_token)):
    conn = get_conn()
    user = conn.execute("SELECT username, name, role FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401)
    return dict(user)

# ─── Data endpoints ────────────────────────────────────────
@app.get("/api/db")
async def get_data(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        return {}
    return json.loads(DB_FILE.read_text(encoding="utf-8"))

@app.post("/api/db")
async def save_data(request: Request, user_id: str = Depends(verify_token)):
    body = await request.body()
    if DB_FILE.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = DATA_DIR / f"backup_{ts}.json"
        backup.write_bytes(DB_FILE.read_bytes())
        backups = sorted(DATA_DIR.glob("backup_*.json"))
        for old in backups[:-20]:
            old.unlink(missing_ok=True)
    DB_FILE.write_bytes(body)
    return {"ok": True}

# ─── User management (admin only) ─────────────────────────
@app.get("/api/users")
async def list_users(admin_id: str = Depends(require_admin)):
    conn = get_conn()
    rows = conn.execute("SELECT id, username, name, role FROM users ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/users")
async def create_user(body: dict, admin_id: str = Depends(require_admin)):
    uid = "u_" + secrets.token_hex(6)
    pw_hash = hashlib.sha256(body["password"].encode()).hexdigest()
    conn = get_conn()
    try:
        conn.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                     (uid, body["username"], pw_hash, body["name"], body.get("role", "staff")))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "ชื่อผู้ใช้นี้มีอยู่แล้ว")
    finally:
        conn.close()
    return {"ok": True, "id": uid}

@app.put("/api/users/{uid}")
async def update_user(uid: str, body: dict, admin_id: str = Depends(require_admin)):
    conn = get_conn()
    if "password" in body and body["password"]:
        pw_hash = hashlib.sha256(body["password"].encode()).hexdigest()
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (pw_hash, uid))
    conn.execute("UPDATE users SET name=?, role=? WHERE id=?",
                 (body.get("name", ""), body.get("role", "staff"), uid))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/users/{uid}")
async def delete_user(uid: str, admin_id: str = Depends(require_admin)):
    if uid == "u_admin":
        raise HTTPException(400, "ลบ admin หลักไม่ได้")
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ─── Health & static ───────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "3.0", "time": datetime.now().isoformat()}

# ════════════════════════════════════════════════════════════
#  TIKTOK SHOP INTEGRATION
# ════════════════════════════════════════════════════════════
import hmac, hashlib, time as time_module
import urllib.parse, urllib.request as urllib_req

TIKTOK_CONFIG_FILE = DATA_DIR / "tiktok_config.json"
TIKTOK_BASE = "https://open-api.tiktokglobalshop.com"

def _tt_config() -> dict:
    if TIKTOK_CONFIG_FILE.exists():
        return json.loads(TIKTOK_CONFIG_FILE.read_text(encoding="utf-8"))
    return {}

def _tt_sign(secret: str, path: str, params: dict, body: str = "") -> str:
    """TikTok Shop API HMAC-SHA256 signature."""
    filtered = {k: v for k, v in params.items() if k not in ("sign", "access_token")}
    param_str = "".join(f"{k}{filtered[k]}" for k in sorted(filtered))
    base = f"{secret}{path}{param_str}{body}{secret}"
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()

async def _tt_get(path: str, extra_params: dict = {}) -> dict:
    cfg = _tt_config()
    if not cfg.get("app_key") or not cfg.get("app_secret"):
        raise HTTPException(400, "ยังไม่ได้ตั้งค่า TikTok API — ไปที่ ตั้งค่า → TikTok Shop")
    params = {
        "app_key":      cfg["app_key"],
        "timestamp":    str(int(time_module.time())),
        "access_token": cfg.get("access_token", ""),
        "shop_id":      cfg.get("shop_id", ""),
        **extra_params,
    }
    params["sign"] = _tt_sign(cfg["app_secret"], path, params)
    url = TIKTOK_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib_req.Request(url, headers={"Content-Type": "application/json"})
    with urllib_req.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

async def _tt_post(path: str, body: dict, extra_params: dict = {}) -> dict:
    cfg = _tt_config()
    if not cfg.get("app_key") or not cfg.get("app_secret"):
        raise HTTPException(400, "ยังไม่ได้ตั้งค่า TikTok API")
    body_str = json.dumps(body, separators=(",", ":"))
    params = {
        "app_key":      cfg["app_key"],
        "timestamp":    str(int(time_module.time())),
        "access_token": cfg.get("access_token", ""),
        "shop_id":      cfg.get("shop_id", ""),
        **extra_params,
    }
    params["sign"] = _tt_sign(cfg["app_secret"], path, params, body_str)
    url = TIKTOK_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib_req.Request(url, data=body_str.encode(), headers={"Content-Type": "application/json"})
    with urllib_req.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

# ── Config ──────────────────────────────────────────────────
@app.get("/api/integrations/tiktok/config")
async def tt_get_config(user_id: str = Depends(verify_token)):
    cfg = _tt_config()
    return {k: v for k, v in cfg.items() if k != "app_secret"}  # hide secret

@app.post("/api/integrations/tiktok/config")
async def tt_save_config(body: dict, admin_id: str = Depends(require_admin)):
    cfg = _tt_config()
    cfg.update({k: v for k, v in body.items() if k in
                ("app_key", "app_secret", "access_token", "shop_id", "shop_name")})
    TIKTOK_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}

# ── Test connection ─────────────────────────────────────────
@app.post("/api/integrations/tiktok/test")
async def tt_test(user_id: str = Depends(verify_token)):
    try:
        resp = await _tt_get("/api/shop/get_authorized_shop")
        if resp.get("code") == 0:
            shops = resp.get("data", {}).get("shop_list", [])
            return {"ok": True, "shops": shops}
        return {"ok": False, "message": resp.get("message", "ไม่ทราบสาเหตุ"), "code": resp.get("code")}
    except Exception as e:
        return {"ok": False, "message": str(e)}

# ── Sync orders → BTP ───────────────────────────────────────
@app.post("/api/integrations/tiktok/sync-orders")
async def tt_sync_orders(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    # Pull last 7 days of shipped orders
    now_ts = int(time_module.time())
    from_ts = now_ts - 7 * 86400
    try:
        resp = await _tt_get("/order/202309/orders", {
            "order_status": "COMPLETED",
            "create_time_from": str(from_ts),
            "create_time_to":   str(now_ts),
            "page_size": "50",
            "sort_by": "CREATE_TIME", "sort_type": "DESC",
        })
    except Exception as e:
        raise HTTPException(502, f"TikTok API error: {e}")

    if resp.get("code") != 0:
        raise HTTPException(502, f"TikTok: {resp.get('message')} (code {resp.get('code')})")

    orders = resp.get("data", {}).get("orders", [])
    existing_refs = {s.get("ref_tiktok") for s in db.get("sales", [])}

    created = skipped = 0
    now_iso = datetime.now().isoformat()

    for order in orders:
        oid = order.get("id", "")
        if oid in existing_refs:
            skipped += 1
            continue

        sale_items = []
        for line in order.get("line_items", []):
            sku    = line.get("seller_sku", "")
            qty    = int(line.get("quantity", 1))
            price  = float(line.get("sale_price", 0))
            name   = line.get("product_name", sku)
            sale_items.append({"sku": sku, "name": name, "qty": qty, "price": price})
            # cut O stock
            for p in db["products"]:
                if p["sku"] == sku:
                    p["stock"]["O"] = max(0, p["stock"].get("O", 0) - qty)
                    db.setdefault("moves", []).append({
                        "id": f"tt_{oid}_{sku}_{int(time_module.time())}",
                        "date": now_iso, "type": "sale",
                        "sku": sku, "qty": qty, "channel": "O",
                        "actor": "TikTok Auto-sync", "lot": "",
                        "from_loc": "", "to_loc": "",
                        "doc_ref": f"TT-{oid[-6:]}", "note": f"TikTok #{oid}",
                    })
                    break

        seq = db.setdefault("seq", {})
        seq["sale"] = seq.get("sale", 1) + 1
        sale_no = f"SAL-ORD-{(datetime.now().year+543)}-{str(seq['sale']).zfill(5)}"
        addr = order.get("recipient_address", {})
        db.setdefault("sales", []).append({
            "id": f"tt_{oid}", "no": sale_no,
            "date": datetime.fromtimestamp(order.get("create_time", now_ts)).strftime("%Y-%m-%d"),
            "channel": "TikTok",
            "customerId": "", "customer": {
                "name": addr.get("name", "TikTok Customer"),
                "taxId": "", "address": addr.get("full_address", ""), "branch": "-"
            },
            "items": sale_items, "discount": 0,
            "status": "ยืนยันแล้ว", "ref_tiktok": oid,
        })
        created += 1

    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # update last sync time
    cfg = _tt_config()
    cfg["last_sync"] = datetime.now().isoformat()
    TIKTOK_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, "created": created, "skipped": skipped, "total": len(orders)}

# ── Push stock → TikTok ─────────────────────────────────────
@app.post("/api/integrations/tiktok/push-stock")
async def tt_push_stock(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    # First: get TikTok product list to map seller_sku → product_id
    try:
        resp = await _tt_get("/product/202309/products/search", {"page_size": "100"})
    except Exception as e:
        raise HTTPException(502, f"TikTok API error: {e}")

    if resp.get("code") != 0:
        raise HTTPException(502, resp.get("message", "TikTok error"))

    tt_products = resp.get("data", {}).get("products", [])
    # Build map: seller_sku → {product_id, sku_id}
    sku_map = {}
    for tp in tt_products:
        for sku_obj in tp.get("skus", []):
            seller_sku = sku_obj.get("seller_sku", "")
            if seller_sku:
                sku_map[seller_sku] = {
                    "product_id": tp["id"],
                    "sku_id": sku_obj["id"],
                }

    updated = skipped = 0
    stock_updates = []
    local_sku_map = {p["sku"]: p for p in db.get("products", [])}

    for seller_sku, tt_info in sku_map.items():
        p = local_sku_map.get(seller_sku)
        if not p:
            skipped += 1
            continue
        online_stock = p["stock"].get("O", 0)
        stock_updates.append({
            "product_id": tt_info["product_id"],
            "skus": [{"id": tt_info["sku_id"], "inventory": [{"quantity": max(0, online_stock)}]}]
        })
        updated += 1

    # Batch update stock (TikTok allows up to 20 per request)
    errors = []
    for i in range(0, len(stock_updates), 20):
        batch = stock_updates[i:i+20]
        for item in batch:
            try:
                r = await _tt_post("/product/202309/stocks", {
                    "product_id": item["product_id"], "skus": item["skus"]
                })
                if r.get("code") != 0:
                    errors.append(f"{item['product_id']}: {r.get('message')}")
            except Exception as e:
                errors.append(str(e))

    return {"ok": True, "updated": updated, "skipped": skipped, "errors": errors[:5]}

# ── Stock export (for n8n or other tools) ──────────────────
@app.get("/api/stock/export")
async def stock_export(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        return []
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))
    return [
        {"sku": p["sku"], "name": p["name"],
         "stock_S": p["stock"].get("S", 0), "stock_D": p["stock"].get("D", 0),
         "stock_O": p["stock"].get("O", 0),
         "stock_total": sum(p["stock"].values()),
         "reorder": p.get("reorder", 20)}
        for p in db.get("products", [])
    ]

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/{full_path:path}")
async def serve_app(full_path: str):
    return FileResponse("static/ERP_BTP.html")
