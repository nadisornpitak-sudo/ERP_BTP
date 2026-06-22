"""ตัวเชื่อม ERP — observe (อ่าน) + act (เขียนกลับ).

มี 2 โหมด:
  local : อ่าน/เขียนไฟล์ data/btp_erp.json และ data/agent_state.json ตรง ๆ
          (รันได้ทันทีบนเครื่องเดียวกับ server โดยไม่ต้องมี token)
  http  : คุยผ่าน REST API ด้วย Bearer token (btp_...) — ใช้ระยะไกล/บน Railway ได้
          และสั่ง auto-sync TikTok ได้

ใช้ urllib ของ stdlib เท่านั้น ไม่ต้องลง dependency เพิ่ม.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config, DATA_DIR

DB_FILE = DATA_DIR / "btp_erp.json"
STATE_FILE = DATA_DIR / "agent_state.json"


class ErpError(Exception):
    pass


class ErpClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        if cfg.mode == "http" and not cfg.token:
            raise ErpError("mode=http ต้องระบุ token (BTP_AGENT_TOKEN หรือ --token)")

    # ── HTTP helper ──────────────────────────────────────────
    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = self.cfg.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.cfg.token}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise ErpError(f"{method} {path} → HTTP {e.code}: {detail[:200]}")
        except urllib.error.URLError as e:
            raise ErpError(f"เชื่อมต่อ {url} ไม่ได้: {e.reason}")

    # ── OBSERVE ──────────────────────────────────────────────
    def get_db(self) -> dict:
        """ดึงฐานข้อมูล ERP ทั้งก้อน (products / moves / sales / ...)."""
        if self.cfg.mode == "local":
            if not DB_FILE.exists():
                raise ErpError(f"ไม่พบไฟล์ {DB_FILE}")
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        return self._request("GET", "/api/db")

    def health(self) -> dict:
        if self.cfg.mode == "local":
            exists = DB_FILE.exists()
            products = 0
            if exists:
                try:
                    products = len(json.loads(
                        DB_FILE.read_text(encoding="utf-8")).get("products", []))
                except (json.JSONDecodeError, OSError):
                    pass
            return {
                "status": "ok" if exists else "empty",
                "mode": "local",
                "db": {"exists": exists, "products": products,
                       "size_bytes": DB_FILE.stat().st_size if exists else 0},
            }
        return self._request("GET", "/api/health")

    # ── ACT: เขียน state กลับ ──────────────────────────────────
    def load_state(self) -> dict:
        """โหลด state รอบก่อน (หน่วยความจำของ loop) — ใช้ทำ feedback diff."""
        if self.cfg.mode == "http":
            try:
                return self._request("GET", "/api/agent/state") or {}
            except ErpError:
                return {}
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def save_state(self, state: dict) -> None:
        if self.cfg.mode == "http":
            self._request("POST", "/api/agent/state", state)
            return
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── ACT: auto-sync (http เท่านั้น) ────────────────────────
    def trigger_tiktok_sync(self) -> dict:
        if self.cfg.mode != "http":
            return {"ok": False, "skipped": "auto-sync ต้องใช้ mode=http"}
        try:
            return self._request("POST", "/api/integrations/tiktok/sync-orders")
        except ErpError as e:
            return {"ok": False, "error": str(e)}
