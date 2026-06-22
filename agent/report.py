"""สร้างรายงานต่อรอบ — เขียนเป็น markdown + JSON ลง agent/reports/."""
from __future__ import annotations
from collections import Counter
from datetime import datetime
from pathlib import Path
import json

from .config import Config, REPORTS_DIR

SEV_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
RULE_TH = {"low_stock": "สต็อก/จุดสั่งซื้อ", "velocity": "ความเร็วขาย/Dead stock",
           "anomaly": "ความผิดปกติข้อมูล", "mrp": "วางแผนวัตถุดิบ (MRP)"}


def summarize(findings: list[dict]) -> dict:
    return {
        "total": len(findings),
        "by_severity": dict(Counter(f["severity"] for f in findings)),
        "by_rule": dict(Counter(f["rule"] for f in findings)),
        "by_kind": dict(Counter(f["kind"] for f in findings)),
    }


def diff(prev: list[dict], curr: list[dict]) -> dict:
    """เทียบรอบก่อน → new / resolved / ongoing (หัวใจของ feedback loop)."""
    prev_keys = {f["key"] for f in prev}
    curr_keys = {f["key"] for f in curr}
    new = [f for f in curr if f["key"] not in prev_keys]
    resolved = [f for f in prev if f["key"] not in curr_keys]
    ongoing = [f for f in curr if f["key"] in prev_keys]
    return {"new": new, "resolved": resolved, "ongoing": ongoing}


def _table(findings: list[dict], limit: int) -> list[str]:
    lines = ["| ระดับ | SKU | รายการ | ข้อความ |", "|---|---|---|---|"]
    for f in findings[:limit]:
        em = SEV_EMOJI.get(f["severity"], "")
        name = (f.get("name") or "")[:28]
        msg = f["message"].replace("|", "/")
        lines.append(f"| {em} {f['severity']} | {f.get('sku','')} | {name} | {msg} |")
    if len(findings) > limit:
        lines.append(f"| … | | | _อีก {len(findings) - limit} รายการ (ดูใน JSON)_ |")
    return lines


def render_markdown(cycle: int, ts: datetime, cfg: Config, health: dict,
                    findings: list[dict], delta: dict,
                    actions: list[str]) -> str:
    s = summarize(findings)
    L = []
    L.append(f"# 🤖 BTP ERP Agent — รอบที่ {cycle}")
    L.append(f"_{ts.strftime('%Y-%m-%d %H:%M:%S')} • mode={cfg.mode}_\n")

    L.append("## สรุป")
    sev = s["by_severity"]
    sev_txt = " ".join(f"{SEV_EMOJI[k]}{k}={v}" for k, v in
                       sorted(sev.items(), key=lambda x: x[0])) or "ไม่พบปัญหา ✅"
    L.append(f"- พบทั้งหมด **{s['total']}** รายการ — {sev_txt}")
    L.append(f"- 🆕 ใหม่ {len(delta['new'])} • ✅ คลี่คลาย {len(delta['resolved'])} "
             f"• ➡️ ค้างเดิม {len(delta['ongoing'])}")
    db = health.get("db", {})
    L.append(f"- DB: {db.get('products','?')} สินค้า, "
             f"{round(db.get('size_bytes',0)/1024)} KB\n")

    if actions:
        L.append("## การกระทำของรอบนี้ (act)")
        for a in actions:
            L.append(f"- {a}")
        L.append("")

    if delta["new"]:
        L.append("## 🆕 รายการใหม่")
        L += _table(delta["new"], cfg.max_findings_per_rule)
        L.append("")
    if delta["resolved"]:
        L.append("## ✅ คลี่คลายแล้ว")
        L += _table(delta["resolved"], 20)
        L.append("")

    # แยกตามกฎ
    for rule in ("low_stock", "velocity", "anomaly", "mrp"):
        items = [f for f in findings if f["rule"] == rule]
        if not items:
            continue
        L.append(f"## {RULE_TH.get(rule, rule)} ({len(items)})")
        L += _table(items, cfg.max_findings_per_rule)
        L.append("")

    return "\n".join(L)


def write_reports(cfg: Config, ts: datetime, markdown: str, state: dict) -> dict:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = ts.strftime("%Y%m%d_%H%M%S")
    md_path = REPORTS_DIR / f"report_{stamp}.md"
    json_path = REPORTS_DIR / f"report_{stamp}.json"
    latest_md = REPORTS_DIR / "latest.md"

    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path),
            "latest": str(latest_md)}
