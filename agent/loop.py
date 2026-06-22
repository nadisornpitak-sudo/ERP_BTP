"""Agent feedback loop — entry point.

วงจร 1 รอบ:
  observe  → ดึง db + health จาก ERP
  recall   → โหลด state รอบก่อน (หน่วยความจำ)
  analyze  → รันกฎทั้งหมด → findings
  diff     → เทียบรอบก่อน: new / resolved / ongoing  (= feedback)
  act      → (auto-sync ถ้าเปิด) + เขียน state กลับ ERP + เขียนรายงาน
  sleep    → รอ interval แล้ววนใหม่

รันแบบรอบเดียว:   python -m agent.loop --once
รันวนต่อเนื่อง:    python -m agent.loop --interval 900
โหมด http:         python -m agent.loop --mode http --base-url http://localhost:8000 --token btp_xxx
"""
from __future__ import annotations
import argparse
import sys
import time
from datetime import datetime, timezone


def _utcnow() -> datetime:
    """เวลา UTC แบบ naive — ให้เทียบกับวันที่ของ moves (ลงท้าย Z) ได้ตรงกัน."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

from .config import Config
from .erp_client import ErpClient, ErpError
from . import rules, report


def run_cycle(client: ErpClient, cfg: Config, cycle: int) -> dict:
    ts = datetime.now()
    actions: list[str] = []

    # ── ACT ก่อน (auto-sync) เพื่อให้ observe เห็นข้อมูลล่าสุด ──
    if cfg.auto_sync:
        res = client.trigger_tiktok_sync()
        if res.get("ok"):
            actions.append(f"🔁 Auto-sync TikTok: สร้าง {res.get('created',0)} / "
                           f"ข้าม {res.get('skipped',0)} ออเดอร์")
        else:
            actions.append(f"🔁 Auto-sync ข้าม: {res.get('error') or res.get('skipped')}")

    # ── OBSERVE ──
    health = client.health()
    db = client.get_db()

    # ── RECALL (รอบก่อน) ──
    prev_state = client.load_state()
    prev_findings = prev_state.get("findings", [])

    # ── ANALYZE ──
    findings = rules.run_all(db, cfg, now=_utcnow())

    # ── DIFF (feedback) ──
    delta = report.diff(prev_findings, findings)
    summary = report.summarize(findings)

    # ── ACT: ประกอบ state + เขียนกลับ ──
    history = prev_state.get("history", [])
    history.append({"cycle": cycle, "time": ts.isoformat(),
                    "summary": summary,
                    "new": len(delta["new"]), "resolved": len(delta["resolved"])})
    history = history[-cfg.history_limit:]

    state = {
        "updated_at": ts.isoformat(),
        "cycle": cycle,
        "mode": cfg.mode,
        "summary": summary,
        "delta": {"new": len(delta["new"]), "resolved": len(delta["resolved"]),
                  "ongoing": len(delta["ongoing"])},
        "findings": findings,
        "history": history,
    }
    client.save_state(state)
    actions.append(f"💾 เขียน state กลับ ERP ({cfg.mode})")

    md = report.render_markdown(cycle, ts, cfg, health, findings, delta, actions)
    paths = report.write_reports(cfg, ts, md, state)
    actions.append(f"📝 รายงาน: {paths['latest']}")

    # ── log บรรทัดเดียว ──
    sev = summary["by_severity"]
    print(f"[รอบ {cycle}] {ts:%H:%M:%S} | พบ {summary['total']} "
          f"(🆕{len(delta['new'])} ✅{len(delta['resolved'])}) "
          f"| critical={sev.get('critical',0)} high={sev.get('high',0)} "
          f"| → {paths['latest']}")
    return state


def build_config(args) -> Config:
    cfg = Config()
    if args.mode:
        cfg.mode = args.mode
    if args.base_url:
        cfg.base_url = args.base_url.rstrip("/")
    if args.token:
        cfg.token = args.token
    if args.interval is not None:
        cfg.interval = args.interval
    if args.auto_sync:
        cfg.auto_sync = True
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description="BTP ERP — rule-based agent feedback loop")
    ap.add_argument("--once", action="store_true", help="รันรอบเดียวแล้วจบ")
    ap.add_argument("--interval", type=int, help="วินาทีต่อรอบ (default จาก config = 900)")
    ap.add_argument("--mode", choices=["local", "http"], help="local (อ่านไฟล์) | http (REST API)")
    ap.add_argument("--base-url", help="URL ของ ERP (mode=http)")
    ap.add_argument("--token", help="API token btp_... (mode=http)")
    ap.add_argument("--auto-sync", action="store_true", help="สั่ง sync TikTok ทุกรอบ (mode=http)")
    args = ap.parse_args(argv)

    cfg = build_config(args)
    try:
        client = ErpClient(cfg)
    except ErpError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    print(f"🤖 BTP ERP Agent เริ่มทำงาน | mode={cfg.mode} "
          f"| interval={cfg.interval}s | auto_sync={cfg.auto_sync}")
    print(f"   กฎที่เปิด: low_stock={cfg.rule_low_stock} "
          f"velocity={cfg.rule_velocity} anomaly={cfg.rule_anomaly} "
          f"mrp={getattr(cfg, 'rule_mrp', True)}")

    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle(client, cfg, cycle)
        except ErpError as e:
            print(f"⚠️ [รอบ {cycle}] ERP error: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — loop ต้องไม่ตายเพราะ error รอบเดียว
            print(f"⚠️ [รอบ {cycle}] error: {e}", file=sys.stderr)

        if args.once:
            break
        try:
            time.sleep(cfg.interval)
        except KeyboardInterrupt:
            print("\n👋 หยุดการทำงาน")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
