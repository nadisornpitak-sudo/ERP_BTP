"""Entry point — รัน MRP กับ data/btp_erp.json (อ่านอย่างเดียว ไม่แก้ db).

    python -m mrp.run            # รัน 1 รอบ เขียนรายงาน + mrp_state.json
    python -m mrp.run --json     # พิมพ์ผลเป็น JSON ลง stdout
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime

from .config import Config, DB_FILE, STATE_FILE
from .engine import mrp_run
from . import report


def main(argv=None):
    ap = argparse.ArgumentParser(description="BTP ERP — MRP planning run")
    ap.add_argument("--json", action="store_true", help="พิมพ์ผล JSON ลง stdout")
    ap.add_argument("--no-min-max", action="store_true", help="ปิดการเติมแบบ Min-Max (เฉพาะ demand)")
    args = ap.parse_args(argv)

    if not DB_FILE.exists():
        print(f"❌ ไม่พบ {DB_FILE}", file=sys.stderr)
        return 2
    db = json.loads(DB_FILE.read_text(encoding="utf-8"))

    cfg = Config()
    if args.no_min_max:
        cfg.enable_min_max = False

    result = mrp_run(db, cfg, now=datetime.now())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    STATE_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = report.write_reports(result)
    s = result["summary"]
    print(f"🏭 MRP รัน {result['generated_at'][:19]} | "
          f"Planned Orders {s['planned_orders']} (฿{s['est_total_cost']:,.0f}) | "
          f"Exceptions {s['exceptions']} {s['exceptions_by_kind']}")
    print(f"   → {paths['latest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
