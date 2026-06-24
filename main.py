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
        CREATE TABLE IF NOT EXISTS api_tokens (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used TEXT
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
    # 1. Check session token (from login)
    row = conn.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
    if row:
        conn.close()
        return row["user_id"]
    # 2. Check API token (for Make.com / n8n / Zapier)
    row = conn.execute("SELECT created_by FROM api_tokens WHERE token=?", (token,)).fetchone()
    if row:
        conn.execute("UPDATE api_tokens SET last_used=? WHERE token=?",
                     (datetime.now().isoformat(), token))
        conn.commit()
        conn.close()
        return row["created_by"]
    conn.close()
    raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบ")

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
    db_exists = DB_FILE.exists()
    db_size   = DB_FILE.stat().st_size if db_exists else 0
    products  = 0
    if db_exists:
        try:
            d = json.loads(DB_FILE.read_text(encoding="utf-8"))
            products = len(d.get("products", []))
        except Exception:
            pass
    return {
        "status": "ok", "version": "3.0",
        "time": datetime.now().isoformat(),
        "db": {"exists": db_exists, "size_bytes": db_size, "products": products},
    }

@app.post("/api/admin/restore-initial-data")
async def restore_initial_data(request: Request, admin_id: str = Depends(require_admin)):
    """Upload btp_erp.json from local machine to Railway (run once after Volume mount)."""
    body = await request.body()
    if not body:
        raise HTTPException(400, "ไม่มีข้อมูล")
    try:
        data = json.loads(body)
        if not data.get("products"):
            raise HTTPException(400, "ไม่พบ products ในข้อมูล")
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON ไม่ถูกต้อง")
    DB_FILE.write_bytes(body)
    return {"ok": True, "products": len(data["products"]), "size": len(body)}

# ════════════════════════════════════════════════════════════
#  API TOKENS (for Make.com / n8n / Zapier)
# ════════════════════════════════════════════════════════════
@app.get("/api/tokens")
async def list_api_tokens(admin_id: str = Depends(require_admin)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, created_at, last_used FROM api_tokens ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/tokens")
async def create_api_token(body: dict, admin_id: str = Depends(require_admin)):
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "กรุณาตั้งชื่อ token")
    new_token = "btp_" + secrets.token_hex(24)
    tid = secrets.token_hex(8)
    conn = get_conn()
    conn.execute("INSERT INTO api_tokens VALUES (?,?,?,?,?,?)",
                 (tid, name, new_token, admin_id, datetime.now().isoformat(), None))
    conn.commit()
    conn.close()
    return {"id": tid, "name": name, "token": new_token}  # shown only once

@app.delete("/api/tokens/{tid}")
async def delete_api_token(tid: str, admin_id: str = Depends(require_admin)):
    conn = get_conn()
    conn.execute("DELETE FROM api_tokens WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ════════════════════════════════════════════════════════════
#  GENERIC ORDER INTAKE — Make.com / Zapier / n8n
#  POST /api/integrations/orders
#  Body: { source, order_id, date, customer_name,
#           customer_address, items:[{sku,name,qty,price}] }
# ════════════════════════════════════════════════════════════
def _uid(): return secrets.token_hex(10)

@app.post("/api/integrations/orders")
async def intake_order(body: dict, user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    source  = str(body.get("source", "External")).strip()
    order_id = str(body.get("order_id", ""))
    if not order_id:
        raise HTTPException(400, "ต้องระบุ order_id")

    # Idempotent — skip if already imported
    if any(s.get("external_id") == order_id for s in db.get("sales", [])):
        return {"ok": True, "status": "skipped", "reason": "already imported"}

    raw_items = body.get("items", [])
    sale_items = []
    now_iso = datetime.now().isoformat()

    for item in raw_items:
        sku   = str(item.get("sku", "")).strip()
        qty   = max(1, int(float(item.get("qty", 1))))
        price = float(item.get("price", 0))
        name  = str(item.get("name", sku))
        sale_items.append({"sku": sku, "name": name, "qty": qty, "price": price})

        # Cut Online (O) stock
        for p in db.get("products", []):
            if p["sku"] == sku:
                p["stock"]["O"] = max(0, p["stock"].get("O", 0) - qty)
                db.setdefault("moves", []).append({
                    "id": _uid(), "date": now_iso, "type": "sale",
                    "sku": sku, "qty": qty, "channel": "O",
                    "actor": f"{source} Auto-sync", "lot": "",
                    "from_loc": "", "to_loc": "",
                    "doc_ref": f"{source[:3].upper()}-{order_id[-6:]}",
                    "note": f"{source} Order #{order_id}",
                })
                break

    seq = db.setdefault("seq", {})
    seq["sale"] = seq.get("sale", 1) + 1
    sale_no = f"SAL-ORD-{datetime.now().year + 543}-{str(seq['sale']).zfill(5)}"

    db.setdefault("sales", []).append({
        "id": f"ext_{_uid()}", "no": sale_no,
        "date": str(body.get("date", now_iso[:10]))[:10],
        "channel": source,
        "customerId": "", "customer": {
            "name":    str(body.get("customer_name", f"{source} Customer")),
            "taxId":   "", "branch": "-",
            "address": str(body.get("customer_address", "")),
        },
        "items": sale_items, "discount": 0,
        "status": "ยืนยันแล้ว",
        "external_id": order_id,
    })

    DB_FILE.write_text(
        json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return {"ok": True, "status": "created", "sale_no": sale_no}

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
    return {k: v for k, v in cfg.items() if k != "app_secret"}

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

@app.post("/api/integrations/tiktok/sync-orders")
async def tt_sync_orders(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    # Check DEMO Mode
    cfg = _tt_config()
    app_key = cfg.get("app_key", "")
    shop_id = cfg.get("shop_id", "")
    if app_key in ("DEMO", "MOCK") or shop_id == "DEMO" or not app_key:
        import random
        num_orders = random.randint(1, 2)
        created = 0
        for _ in range(num_orders):
            try:
                _generate_simulated_order(db, "TikTok")
                created += 1
            except Exception:
                pass
        DB_FILE.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        
        # update last sync time
        cfg["last_sync"] = datetime.now().isoformat()
        TIKTOK_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "created": created, "skipped": 0, "total": created, "message": "ดึงข้อมูลจากระบบจำลอง (DEMO) สำเร็จ มีการตัดสต็อก Online เรียบร้อย"}

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

    # Check DEMO Mode
    cfg = _tt_config()
    app_key = cfg.get("app_key", "")
    shop_id = cfg.get("shop_id", "")
    if app_key in ("DEMO", "MOCK") or shop_id == "DEMO" or not app_key:
        return {"ok": True, "updated": len(db.get("products", [])), "skipped": 0, "errors": [], "message": "ดันยอดสต็อกไปยังระบบจำลอง (DEMO) สำเร็จ"}

    # First: get TikTok product list to map seller_sku → product_id
    try:
        resp = await _tt_get("/product/202309/products/search", {"page_size": "100"})
    except Exception as e:
        raise HTTPException(502, f"TikTok API error: {e}")

    if resp.get("code") != 0:
        raise HTTPException(502, resp.get("message", "TikTok error"))

    tt_products = resp.get("data", {}).get("orders", [])
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

# ════════════════════════════════════════════════════════════
#  SHOPEE INTEGRATION
# ════════════════════════════════════════════════════════════
SHOPEE_CONFIG_FILE = DATA_DIR / "shopee_config.json"

def _sp_config() -> dict:
    if SHOPEE_CONFIG_FILE.exists():
        return json.loads(SHOPEE_CONFIG_FILE.read_text(encoding="utf-8"))
    return {}

# ── Shopee Direct Integration Helpers ───────────────────────
def _sp_sign(secret: str, path: str, params: dict) -> str:
    partner_id = params.get("partner_id", "")
    timestamp = params.get("timestamp", "")
    access_token = params.get("access_token", "")
    shop_id = params.get("shop_id", "")
    base_str = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(secret.encode("utf-8"), base_str.encode("utf-8"), hashlib.sha256).hexdigest()

async def _sp_get(path: str, extra_params: dict = {}) -> dict:
    cfg = _sp_config()
    partner_id = cfg.get("partner_id", "")
    app_secret = cfg.get("app_secret", "")
    shop_id = cfg.get("shop_id", "")
    access_token = cfg.get("access_token", "")
    
    timestamp = str(int(time_module.time()))
    sign_params = {
        "partner_id": str(partner_id),
        "timestamp": timestamp,
        "access_token": access_token,
        "shop_id": str(shop_id)
    }
    sign = _sp_sign(app_secret, path, sign_params)
    
    url_params = {
        "partner_id": str(partner_id),
        "timestamp": timestamp,
        "sign": sign,
        "shop_id": str(shop_id),
        **extra_params
    }
    if access_token:
        url_params["access_token"] = access_token
        
    url = "https://partner.shopeemobile.com" + path + "?" + urllib.parse.urlencode(url_params)
    req = urllib_req.Request(url, headers={"Content-Type": "application/json"})
    with urllib_req.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

async def _sp_post(path: str, body: dict, extra_params: dict = {}) -> dict:
    cfg = _sp_config()
    partner_id = cfg.get("partner_id", "")
    app_secret = cfg.get("app_secret", "")
    shop_id = cfg.get("shop_id", "")
    access_token = cfg.get("access_token", "")
    
    timestamp = str(int(time_module.time()))
    sign_params = {
        "partner_id": str(partner_id),
        "timestamp": timestamp,
        "access_token": access_token,
        "shop_id": str(shop_id)
    }
    sign = _sp_sign(app_secret, path, sign_params)
    
    url_params = {
        "partner_id": str(partner_id),
        "timestamp": timestamp,
        "sign": sign,
        "shop_id": str(shop_id),
        **extra_params
    }
    if access_token:
        url_params["access_token"] = access_token
        
    url = "https://partner.shopeemobile.com" + path + "?" + urllib.parse.urlencode(url_params)
    body_str = json.dumps(body, separators=(",", ":"))
    req = urllib_req.Request(url, data=body_str.encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib_req.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

@app.get("/api/integrations/shopee/config")
async def sp_get_config(user_id: str = Depends(verify_token)):
    cfg = _sp_config()
    return {k: v for k, v in cfg.items() if k != "app_secret"}

@app.post("/api/integrations/shopee/config")
async def sp_save_config(body: dict, admin_id: str = Depends(require_admin)):
    cfg = _sp_config()
    cfg.update({k: v for k, v in body.items() if k in
                ("app_key", "app_secret", "partner_id", "shop_id", "shop_name")})
    SHOPEE_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}

@app.post("/api/integrations/shopee/test")
async def sp_test(user_id: str = Depends(verify_token)):
    cfg = _sp_config()
    if not cfg.get("app_key") or not cfg.get("shop_id"):
        return {"ok": False, "message": "ยังไม่ได้กรอก App Key / Shop ID"}
    if cfg.get("app_key") in ("DEMO", "MOCK") or cfg.get("shop_id") == "DEMO":
        return {"ok": True, "shop_name": cfg.get("shop_name", "Shopee Demo Shop")}
    return {"ok": False, "message": "เชื่อมต่อล้มเหลว: การเชื่อมต่อจริงต้องการ Shopee Partner Approval (โปรดใช้โหมด Demo หรือกรอก DEMO ใน App Key)"}

@app.post("/api/integrations/shopee/sync-orders")
async def sp_sync_orders(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    cfg = _sp_config()
    app_key = cfg.get("app_key", "")
    shop_id = cfg.get("shop_id", "")

    # Check DEMO Mode
    if app_key in ("DEMO", "MOCK") or shop_id == "DEMO" or not app_key:
        import random
        num_orders = random.randint(1, 2)
        created = 0
        for _ in range(num_orders):
            try:
                _generate_simulated_order(db, "Shopee")
                created += 1
            except Exception:
                pass
        DB_FILE.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return {
            "ok": True,
            "created": created,
            "skipped": 0,
            "total": created,
            "message": "ดึงข้อมูลจากระบบจำลอง (DEMO) สำเร็จ มีการตัดสต็อก Online เรียบร้อย"
        }

    # Real Integration
    now_ts = int(time_module.time())
    from_ts = now_ts - 7 * 86400
    try:
        resp = await _sp_get("/api/v2/order/get_order_list", {
            "time_range_field": "create_time",
            "time_from": str(from_ts),
            "time_to": str(now_ts),
            "page_size": "50"
        })
    except Exception as e:
        raise HTTPException(502, f"Shopee API error: {e}")

    if "error" in resp and resp["error"]:
        raise HTTPException(502, f"Shopee: {resp.get('message')} (error {resp.get('error')})")

    order_list = resp.get("response", {}).get("order_list", [])
    if not order_list:
        return {"ok": True, "created": 0, "skipped": 0, "total": 0, "message": "ดึงข้อมูลจาก Shopee สำเร็จ แต่ไม่มีออเดอร์ใหม่"}

    existing_refs = {s.get("external_id") for s in db.get("sales", [])}
    order_sns = [o["order_sn"] for o in order_list]
    sns_to_fetch = [sn for sn in order_sns if sn not in existing_refs]

    if not sns_to_fetch:
        return {"ok": True, "created": 0, "skipped": len(order_sns), "total": len(order_sns), "message": "ออเดอร์ทั้งหมดเคยนำเข้าแล้ว"}

    created = skipped = 0
    now_iso = datetime.now().isoformat()

    for i in range(0, len(sns_to_fetch), 50):
        batch = sns_to_fetch[i:i+50]
        try:
            detail_resp = await _sp_get("/api/v2/order/get_order_detail", {
                "order_sn_list": ",".join(batch),
                "response_optional_fields": "item_list,recipient_address"
            })
        except Exception as e:
            raise HTTPException(502, f"Shopee details API error: {e}")

        if "error" in detail_resp and detail_resp["error"]:
            raise HTTPException(502, f"Shopee: {detail_resp.get('message')}")

        orders_details = detail_resp.get("response", {}).get("order_list", [])
        for order in orders_details:
            oid = order.get("order_sn", "")
            if oid in existing_refs:
                skipped += 1
                continue

            sale_items = []
            for item in order.get("item_list", []):
                sku = item.get("model_sku", item.get("item_sku", "")).strip()
                qty = int(item.get("model_quantity_purchased", 1))
                price = float(item.get("model_original_price", 0.0))
                name = item.get("item_name", sku)
                sale_items.append({"sku": sku, "name": name, "qty": qty, "price": price})

                # cut O stock
                for p in db.get("products", []):
                    if p["sku"] == sku:
                        p["stock"]["O"] = max(0, p["stock"].get("O", 0) - qty)
                        db.setdefault("moves", []).append({
                            "id": f"sp_{oid}_{sku}_{int(time_module.time())}",
                            "date": now_iso, "type": "sale",
                            "sku": sku, "qty": qty, "channel": "O",
                            "actor": "Shopee Auto-sync", "lot": "",
                            "from_loc": "", "to_loc": "",
                            "doc_ref": f"SP-{oid[-6:]}", "note": f"Shopee #{oid}",
                        })
                        break

            seq = db.setdefault("seq", {})
            seq["sale"] = seq.get("sale", 1) + 1
            sale_no = f"SAL-ORD-{(datetime.now().year+543)}-{str(seq['sale']).zfill(5)}"
            addr = order.get("recipient_address", {})

            db.setdefault("sales", []).append({
                "id": f"sp_{oid}", "no": sale_no,
                "date": datetime.fromtimestamp(order.get("create_time", now_ts)).strftime("%Y-%m-%d"),
                "channel": "Shopee",
                "customerId": "", "customer": {
                    "name": addr.get("name", "Shopee Customer"),
                    "taxId": "", "address": addr.get("full_address", ""), "branch": "-"
                },
                "items": sale_items, "discount": 0,
                "status": "ยืนยันแล้ว", "external_id": oid,
            })
            created += 1

    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"ok": True, "created": created, "skipped": len(order_sns) - created, "total": len(order_sns)}

@app.post("/api/integrations/shopee/push-stock")
async def sp_push_stock(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    cfg = _sp_config()
    app_key = cfg.get("app_key", "")
    shop_id = cfg.get("shop_id", "")

    # Check DEMO Mode
    if app_key in ("DEMO", "MOCK") or shop_id == "DEMO" or not app_key:
        return {"ok": True, "updated": len(db.get("products", [])), "skipped": 0, "errors": [], "message": "ดันยอดสต็อกไปยังระบบจำลอง (DEMO) สำเร็จ"}

    # Real Integration
    try:
        resp = await _sp_get("/api/v2/product/get_item_list", {"page_size": "100", "item_status": "NORMAL"})
    except Exception as e:
        raise HTTPException(502, f"Shopee API error: {e}")

    if "error" in resp and resp["error"]:
        raise HTTPException(502, resp.get("message", "Shopee error"))

    item_list = resp.get("response", {}).get("item", [])
    item_ids = [item["item_id"] for item in item_list]
    if not item_ids:
        return {"ok": True, "updated": 0, "skipped": 0, "errors": ["ไม่พบสินค้าใน Shopee"]}

    updated = skipped = 0
    errors = []
    local_sku_map = {p["sku"]: p for p in db.get("products", [])}

    for item_id in item_ids:
        try:
            info_resp = await _sp_get("/api/v2/product/get_item_base_info", {"item_id": str(item_id)})
            info = info_resp.get("response", {})
            seller_sku = info.get("item_sku", "")

            has_model = info.get("has_model", False)
            if has_model:
                models_resp = await _sp_get("/api/v2/product/get_model_list", {"item_id": str(item_id)})
                models = models_resp.get("response", {}).get("model", [])
                for model in models:
                    model_sku = model.get("model_sku", "")
                    if model_sku in local_sku_map:
                        online_stock = local_sku_map[model_sku]["stock"].get("O", 0)
                        up_resp = await _sp_post("/api/v2/product/update_stock", {
                            "item_id": item_id,
                            "stock_list": [{
                                "model_id": model["model_id"],
                                "normal_stock": max(0, int(online_stock))
                            }]
                        })
                        if "error" in up_resp and up_resp["error"]:
                            errors.append(f"Model {model_sku}: {up_resp.get('message')}")
                        else:
                            updated += 1
                    else:
                        skipped += 1
            else:
                if seller_sku in local_sku_map:
                    online_stock = local_sku_map[seller_sku]["stock"].get("O", 0)
                    up_resp = await _sp_post("/api/v2/product/update_stock", {
                        "item_id": item_id,
                        "stock_list": [{
                            "normal_stock": max(0, int(online_stock))
                        }]
                    })
                    if "error" in up_resp and up_resp["error"]:
                        errors.append(f"Item {seller_sku}: {up_resp.get('message')}")
                    else:
                        updated += 1
                else:
                    skipped += 1
        except Exception as e:
            errors.append(f"Item {item_id}: {str(e)}")

    return {"ok": True, "updated": updated, "skipped": skipped, "errors": errors[:5]}

# ════════════════════════════════════════════════════════════
#  LAZADA INTEGRATION
# ════════════════════════════════════════════════════════════
LAZADA_CONFIG_FILE = DATA_DIR / "lazada_config.json"

def _lz_config() -> dict:
    if LAZADA_CONFIG_FILE.exists():
        return json.loads(LAZADA_CONFIG_FILE.read_text(encoding="utf-8"))
    return {}

# ── Lazada Direct Integration Helpers ───────────────────────
def _lz_sign(secret: str, path: str, params: dict) -> str:
    sorted_keys = sorted(params.keys())
    base_str = path
    for k in sorted_keys:
        base_str += f"{k}{params[k]}"
    return hmac.new(secret.encode("utf-8"), base_str.encode("utf-8"), hashlib.sha256).hexdigest().upper()

async def _lz_get(path: str, extra_params: dict = {}) -> dict:
    cfg = _lz_config()
    app_key = cfg.get("app_key", "")
    app_secret = cfg.get("app_secret", "")
    access_token = cfg.get("access_token", "")
    
    timestamp = str(int(time_module.time() * 1000))
    params = {
        "app_key": app_key,
        "timestamp": timestamp,
        "sign_method": "sha256",
        **extra_params
    }
    if access_token:
        params["access_token"] = access_token
        
    sign = _lz_sign(app_secret, path, params)
    params["sign"] = sign
    
    url = "https://api.lazada.co.th/rest" + path + "?" + urllib.parse.urlencode(params)
    req = urllib_req.Request(url, headers={"Content-Type": "application/json"})
    with urllib_req.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

async def _lz_post(path: str, body: dict, extra_params: dict = {}) -> dict:
    cfg = _lz_config()
    app_key = cfg.get("app_key", "")
    app_secret = cfg.get("app_secret", "")
    access_token = cfg.get("access_token", "")
    
    timestamp = str(int(time_module.time() * 1000))
    params = {
        "app_key": app_key,
        "timestamp": timestamp,
        "sign_method": "sha256",
        **extra_params
    }
    if access_token:
        params["access_token"] = access_token
        
    sign = _lz_sign(app_secret, path, params)
    params["sign"] = sign
    
    url = "https://api.lazada.co.th/rest" + path + "?" + urllib.parse.urlencode(params)
    body_str = json.dumps(body, separators=(",", ":"))
    req = urllib_req.Request(url, data=body_str.encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib_req.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

@app.get("/api/integrations/lazada/config")
async def lz_get_config(user_id: str = Depends(verify_token)):
    cfg = _lz_config()
    return {k: v for k, v in cfg.items() if k != "app_secret"}

@app.post("/api/integrations/lazada/config")
async def lz_save_config(body: dict, admin_id: str = Depends(require_admin)):
    cfg = _lz_config()
    cfg.update({k: v for k, v in body.items() if k in
                ("app_key", "app_secret", "shop_id", "shop_name", "access_token")})
    LAZADA_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}

@app.post("/api/integrations/lazada/test")
async def lz_test(user_id: str = Depends(verify_token)):
    cfg = _lz_config()
    if not cfg.get("app_key") or not cfg.get("shop_id"):
        return {"ok": False, "message": "ยังไม่ได้กรอก App Key / Shop ID"}
    if cfg.get("app_key") in ("DEMO", "MOCK") or cfg.get("shop_id") == "DEMO":
        return {"ok": True, "shop_name": cfg.get("shop_name", "Lazada Demo Shop")}
    return {"ok": False, "message": "เชื่อมต่อล้มเหลว: การเชื่อมต่อจริงต้องการ Lazada Developer Credentials (โปรดใช้โหมด Demo หรือกรอก DEMO ใน App Key)"}

@app.post("/api/integrations/lazada/sync-orders")
async def lz_sync_orders(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    cfg = _lz_config()
    app_key = cfg.get("app_key", "")
    shop_id = cfg.get("shop_id", "")

    # Check DEMO Mode
    if app_key in ("DEMO", "MOCK") or shop_id == "DEMO" or not app_key:
        import random
        num_orders = random.randint(1, 2)
        created = 0
        for _ in range(num_orders):
            try:
                _generate_simulated_order(db, "Lazada")
                created += 1
            except Exception:
                pass
        DB_FILE.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return {
            "ok": True,
            "created": created,
            "skipped": 0,
            "total": created,
            "message": "ดึงข้อมูลจากระบบจำลอง (DEMO) สำเร็จ มีการตัดสต็อก Online เรียบร้อย"
        }

    # Real Integration
    from datetime import datetime, timedelta
    from_date = (datetime.now() - timedelta(days=7)).isoformat()
    try:
        resp = await _lz_get("/orders/get", {
            "created_after": from_date,
            "limit": "50"
        })
    except Exception as e:
        raise HTTPException(502, f"Lazada API error: {e}")

    if resp.get("code") != "0" and resp.get("code") != 0 and "code" in resp:
        raise HTTPException(502, f"Lazada: {resp.get('message')} (code {resp.get('code')})")

    orders = resp.get("data", {}).get("orders", [])
    if not orders:
        return {"ok": True, "created": 0, "skipped": 0, "total": 0}

    existing_refs = {s.get("external_id") for s in db.get("sales", [])}
    created = skipped = 0
    now_iso = datetime.now().isoformat()

    for order in orders:
        oid = str(order.get("order_id", ""))
        if oid in existing_refs:
            skipped += 1
            continue

        try:
            items_resp = await _lz_get("/order/items/get", {"order_id": oid})
            items_data = items_resp.get("data", [])
        except Exception as e:
            continue

        sale_items = []
        for line in items_data:
            sku = line.get("sku", "").strip()
            qty = 1
            price = float(line.get("paid_price", 0.0))
            name = line.get("name", sku)

            found_item = False
            for s_item in sale_items:
                if s_item["sku"] == sku:
                    s_item["qty"] += qty
                    found_item = True
                    break
            if not found_item:
                sale_items.append({"sku": sku, "name": name, "qty": qty, "price": price})

            # cut O stock
            for p in db.get("products", []):
                if p["sku"] == sku:
                    p["stock"]["O"] = max(0, p["stock"].get("O", 0) - qty)
                    db.setdefault("moves", []).append({
                        "id": f"lz_{oid}_{sku}_{int(time_module.time())}",
                        "date": now_iso, "type": "sale",
                        "sku": sku, "qty": qty, "channel": "O",
                        "actor": "Lazada Auto-sync", "lot": "",
                        "from_loc": "", "to_loc": "",
                        "doc_ref": f"LZ-{oid[-6:]}", "note": f"Lazada #{oid}",
                    })
                    break

        seq = db.setdefault("seq", {})
        seq["sale"] = seq.get("sale", 1) + 1
        sale_no = f"SAL-ORD-{(datetime.now().year+543)}-{str(seq['sale']).zfill(5)}"

        addr_shipping = order.get("address_shipping", {})
        cust_name = f"{addr_shipping.get('first_name', '')} {addr_shipping.get('last_name', '')}".strip() or "Lazada Customer"
        cust_addr = addr_shipping.get("address1", "")

        db.setdefault("sales", []).append({
            "id": f"lz_{oid}", "no": sale_no,
            "date": order.get("created_at", now_iso[:10])[:10],
            "channel": "Lazada",
            "customerId": "", "customer": {
                "name": cust_name,
                "taxId": "", "address": cust_addr, "branch": "-"
            },
            "items": sale_items, "discount": 0,
            "status": "ยืนยันแล้ว", "external_id": oid,
        })
        created += 1

    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"ok": True, "created": created, "skipped": skipped, "total": len(orders)}

@app.post("/api/integrations/lazada/push-stock")
async def lz_push_stock(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    cfg = _lz_config()
    app_key = cfg.get("app_key", "")
    shop_id = cfg.get("shop_id", "")

    # Check DEMO Mode
    if app_key in ("DEMO", "MOCK") or shop_id == "DEMO" or not app_key:
        return {"ok": True, "updated": len(db.get("products", [])), "skipped": 0, "errors": [], "message": "ดันยอดสต็อกไปยังระบบจำลอง (DEMO) สำเร็จ"}

    # Real Integration
    updated = skipped = 0
    errors = []

    skus_payload = []
    for p in db.get("products", []):
        sku = p.get("sku")
        if not sku:
            continue
        online_stock = p["stock"].get("O", 0)
        skus_payload.append({
            "SellerSku": sku,
            "Quantity": max(0, int(online_stock))
        })

    for i in range(0, len(skus_payload), 50):
        batch = skus_payload[i:i+50]
        body = {
            "Request": {
                "Product": {
                    "Skus": {
                        "Sku": batch
                    }
                }
            }
        }
        try:
            r = await _lz_post("/product/stock/update", body)
            if r.get("code") != "0" and r.get("code") != 0 and "code" in r:
                errors.append(r.get("message", "Lazada error"))
            else:
                updated += len(batch)
        except Exception as e:
            errors.append(str(e))

    return {"ok": True, "updated": updated, "skipped": len(skus_payload) - updated, "errors": errors[:5]}

# ════════════════════════════════════════════════════════════
#  DEMO SIMULATION INTEGRATION (Shopee, Lazada, TikTok)
# ════════════════════════════════════════════════════════════
def _generate_simulated_order(db: dict, platform: str) -> dict:
    """Generates a random simulated sales order and cuts stock for testing."""
    import random
    products = db.get("products", [])
    if not products:
        raise ValueError("ไม่มีสินค้าในระบบสำหรับการจำลอง")
    
    valid_products = [p for p in products if p.get("sku") and p.get("name")]
    if not valid_products:
        valid_products = products

    # Select 1 or 2 products
    selected_p = random.sample(valid_products, min(len(valid_products), random.choice([1, 2])))
    
    # Create order ID and customer details
    order_id = f"DEMO-{platform[:3].upper()}-{int(time_module.time())}{random.randint(10,99)}"
    
    thai_names = ["สมชาย ใจดี", "สมหญิง รักสงบ", "วิชัย บุญมา", "นภา สว่างจิต", "เกรียงไกร มีสุข", "พัชรา รักชาติ"]
    thai_addresses = [
        "123/45 ถนนพหลโยธิน แขวงสามเสนใน เขตพญาไท กรุงเทพฯ 10400",
        "99 ม.2 ต.บางกรวย อ.บางกรวย จ.นนทบุรี 11130",
        "456 ซอยสุขุมวิท 21 แขวงคลองเตยเหนือ เขตวัฒนา กรุงเทพฯ 10110",
        "88/9 ถนนมิตรภาพ ต.ในเมือง อ.เมือง จ.ขอนแก่น 40000",
        "12/1 หมู่บ้านสุขใจ ถ.ห้วยแก้ว ต.สุเทพ อ.เมือง จ.เชียงใหม่ 50200"
    ]
    
    cust_name = random.choice(thai_names)
    cust_addr = random.choice(thai_addresses)
    
    sale_items = []
    now_iso = datetime.now().isoformat()
    
    for p in selected_p:
        sku = p["sku"]
        qty = random.choice([1, 2])
        price = float(p.get("price") or 290.0)
        if price <= 0:
            price = 290.0
        
        sale_items.append({"sku": sku, "name": p["name"], "qty": qty, "price": price})
        
        # Deduct Online (O) stock
        p["stock"]["O"] = max(0, p["stock"].get("O", 0) - qty)
        db.setdefault("moves", []).append({
            "id": f"demo_{order_id}_{sku}_{int(time_module.time())}", 
            "date": now_iso, "type": "sale",
            "sku": sku, "qty": qty, "channel": "O",
            "actor": f"{platform} Demo Sync", "lot": f"L_DEMO_{platform[:3].upper()}",
            "from_loc": "", "to_loc": "",
            "doc_ref": f"{platform[:3].upper()}-{order_id[-6:]}",
            "note": f"ออเดอร์ขายจำลองจาก {platform} #{order_id}",
        })
        
    seq = db.setdefault("seq", {})
    seq["sale"] = seq.get("sale", 1) + 1
    sale_no = f"SAL-ORD-{datetime.now().year + 543}-{str(seq['sale']).zfill(5)}"
    
    db.setdefault("sales", []).append({
        "id": f"demo_{order_id}", "no": sale_no,
        "date": now_iso[:10],
        "channel": platform,
        "customerId": "", "customer": {
            "name": cust_name,
            "taxId": "", "branch": "-",
            "address": cust_addr,
        },
        "items": sale_items, "discount": 0,
        "status": "ยืนยันแล้ว",
        "external_id": order_id,
    })
    return {
        "sale_no": sale_no,
        "order_id": order_id,
        "customer": cust_name,
        "items_count": len(sale_items)
    }

@app.post("/api/integrations/demo-sync")
async def demo_sync_orders(body: dict, user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        raise HTTPException(400, "ฐานข้อมูลว่าง")
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    platform = str(body.get("platform", "Shopee")).strip() # "Shopee", "Lazada", "TikTok"
    
    try:
        res = _generate_simulated_order(db, platform)
    except ValueError as e:
        raise HTTPException(400, str(e))
    
    DB_FILE.write_text(
        json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return {
        "ok": True, 
        "status": "created", 
        "sale_no": res["sale_no"], 
        "order_id": res["order_id"],
        "customer": res["customer"],
        "items_count": res["items_count"]
    }

# ════════════════════════════════════════════════════════════
#  AGENT STATE — สำหรับ agent feedback loop (agent/loop.py)
#  เก็บแยกไฟล์ data/agent_state.json ไม่ยุ่งกับ btp_erp.json
# ════════════════════════════════════════════════════════════
AGENT_STATE_FILE = DATA_DIR / "agent_state.json"

@app.get("/api/agent/state")
async def agent_get_state(user_id: str = Depends(verify_token)):
    if not AGENT_STATE_FILE.exists():
        return {}
    return json.loads(AGENT_STATE_FILE.read_text(encoding="utf-8"))

@app.post("/api/agent/state")
async def agent_save_state(request: Request, user_id: str = Depends(verify_token)):
    body = await request.body()
    AGENT_STATE_FILE.write_bytes(body)
    return {"ok": True}

# ════════════════════════════════════════════════════════════
#  MRP — รันแผนวัตถุดิบ (planning layer) จากข้อมูลปัจจุบัน
#  อ่านอย่างเดียว: คืน planned orders + exceptions (ไม่แก้ db)
# ════════════════════════════════════════════════════════════
@app.get("/api/mrp/plan")
async def mrp_plan(user_id: str = Depends(verify_token)):
    if not DB_FILE.exists():
        return {"planned_orders": [], "exceptions": [],
                "summary": {"planned_orders": 0, "exceptions": 0}}
    from mrp.engine import mrp_run          # lazy import — ไม่กระทบ startup
    from mrp.config import Config
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))
    return mrp_run(db, Config(), datetime.now())

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

# หน้าขายหน้าร้าน (POS) — แอปแยกจาก ERP แต่ใช้ข้อมูลเดียวกัน
@app.get("/pos")
async def serve_pos():
    return FileResponse("static/POS_BTP.html")

@app.get("/{full_path:path}")
async def serve_app(full_path: str):
    return FileResponse("static/ERP_BTP.html")
