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

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/{full_path:path}")
async def serve_app(full_path: str):
    return FileResponse("static/ERP_BTP.html")
