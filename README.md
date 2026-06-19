# BTP ERP — Server Mode (Phase 2)

## Architecture

```
Browser  ──fetch──►  FastAPI (main.py)
                          │
                    ┌─────┴──────┐
                 data/          data/
              btp_erp.json   btp_auth.db
              (ERP data)     (users/sessions)
```

The HTML file works in two modes:
- **file://** (double-click) → offline, localStorage only, no login
- **http://localhost:8000** → server mode, login required, syncs to JSON file

---

## Local run (Windows)

1. Make sure Python is installed: `python --version`
2. Double-click **`start_local.bat`**
3. Open **http://localhost:8000**
4. Login: `admin` / `admin1234`
5. **Change the admin password immediately** via ระบบ → ผู้ใช้งาน

---

## Railway deployment

### Step 1 — Push to GitHub
```bash
# In BTP-ERP-Server/ folder:
git init
git add .
git commit -m "BTP ERP v3.0 backend"
git remote add origin https://github.com/YOUR_USERNAME/btp-erp-server.git
git push -u origin main
```

### Step 2 — Deploy on Railway
1. Go to **railway.app** → Sign up / Log in
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `btp-erp-server` repo
4. Railway auto-detects Python and deploys in ~2 minutes
5. Click your deployment → **Settings** → copy the public URL
   (e.g. `https://btp-erp-server.up.railway.app`)

### Step 3 — First login on Railway
- Open your Railway URL
- Login: `admin` / `admin1234`
- Go to **ระบบ → ผู้ใช้งาน** and change the admin password

### Step 4 — Migrate existing data
1. In the local HTML file: **ตั้งค่า → ส่งออกข้อมูล (JSON)**
2. Save as `btp_erp.json`
3. Copy `btp_erp.json` into the `data/` folder on Railway
   (via Railway Volumes or by importing through the app's import feature)

---

## User roles

| Role | สิทธิ์ |
|------|--------|
| **admin** | ทุกอย่าง รวมถึงจัดการผู้ใช้ |
| **manager** | ทุก module ยกเว้นตั้งค่าระบบ |
| **staff** | งานประจำวัน (คลัง, ขาย, จัดซื้อ) |
| **viewer** | ดูอย่างเดียว |

---

## Security notes

- Tokens stored in `sessionStorage` (cleared when tab closes)
- Passwords hashed with SHA-256
- Auto-backup: keeps last 20 snapshots in `data/backup_*.json`
- For production: add HTTPS (Railway provides it automatically)
- Consider rotating `secrets.token_hex` to use JWT for stateless scaling

---

## File structure

```
BTP-ERP-Server/
├── main.py              ← FastAPI server (all routes)
├── requirements.txt     ← Python dependencies
├── Procfile             ← Railway/Heroku process definition
├── runtime.txt          ← Python version for Railway
├── start_local.bat      ← Windows one-click start
├── README.md
├── data/
│   ├── .gitkeep         ← keeps folder in git (actual data excluded)
│   ├── btp_erp.json     ← ERP data (gitignored)
│   ├── btp_auth.db      ← Users & sessions (gitignored)
│   └── backup_*.json    ← Auto-backups (gitignored)
└── static/
    └── ERP_BTP.html     ← Frontend (copy of main HTML)
```
