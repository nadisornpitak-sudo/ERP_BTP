"""
Import TikTok CSV → BTP ERP
รัน: python import_tiktok_csv.py "path/to/file.csv"
"""
import sys, json, getpass, urllib.request, urllib.error
import pandas as pd
from pathlib import Path

RAILWAY_URL = "https://web-production-74d03.up.railway.app"

# ─── Auth ────────────────────────────────────────────────────
def login(username, password):
    body = json.dumps({"username": username, "password": password}).encode()
    req  = urllib.request.Request(
        f"{RAILWAY_URL}/api/auth/login",
        data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["token"]

def post_order(token, order):
    body = json.dumps(order, ensure_ascii=False).encode("utf-8")
    req  = urllib.request.Request(
        f"{RAILWAY_URL}/api/integrations/orders",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        }
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

# ─── Parse CSV ───────────────────────────────────────────────
def parse_csv(filepath):
    df = pd.read_csv(filepath, encoding="utf-8-sig", dtype=str)
    df = df.fillna("")

    orders = []
    for order_id, grp in df.groupby("Order ID"):
        items = []
        for _, row in grp.iterrows():
            sku = row.get("Seller SKU", "").strip()
            if not sku or sku == "nan":
                continue
            qty_raw  = int(row.get("Quantity", "1") or "1")
            ret_raw  = int(row.get("Sku Quantity of return", "0") or "0")
            net_qty  = qty_raw - ret_raw
            if net_qty <= 0:
                continue
            try:
                price = float(row.get("SKU Subtotal After Discount", "0") or "0")
            except ValueError:
                price = 0.0
            items.append({
                "sku":   sku,
                "name":  row.get("Product Name", sku).strip(),
                "qty":   net_qty,
                "price": price,
            })

        if not items:
            continue

        first = grp.iloc[0]
        addr_parts = [
            first.get("Detail Address", ""),
            first.get("Additional address information", ""),
            first.get("Districts", ""),
            first.get("District", ""),
            first.get("Province", ""),
            first.get("Zipcode", ""),
        ]
        address = " ".join(p.strip() for p in addr_parts if p.strip() and p != "nan")

        created = str(first.get("Created Time", ""))
        date_str = created[:10] if len(created) >= 10 else ""

        orders.append({
            "source":           "TikTok",
            "order_id":         str(order_id),
            "date":             date_str,
            "customer_name":    first.get("Recipient", "TikTok Customer").strip() or "TikTok Customer",
            "customer_address": address,
            "items":            items,
        })

    return orders

# ─── Main ────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_tiktok_csv.py <path_to_csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"ไม่พบไฟล์: {csv_path}")
        sys.exit(1)

    print(f"\n📂 ไฟล์: {csv_path.name}")
    orders = parse_csv(csv_path)
    total_items = sum(len(o["items"]) for o in orders)
    print(f"✓ parse ได้ {len(orders)} orders · {total_items} line items")

    if not orders:
        print("ไม่มีข้อมูลที่นำเข้าได้")
        sys.exit(0)

    # Preview
    print("\nตัวอย่าง 3 orders แรก:")
    for o in orders[:3]:
        print(f"  {o['order_id'][-8:]}  {o['customer_name'][:20]:20}  "
              f"{len(o['items'])} items  {o['date']}")

    print(f"\n🔑 Login: {RAILWAY_URL}")
    username = input("Username [admin]: ").strip() or "admin"
    password = getpass.getpass("Password: ")

    try:
        token = login(username, password)
        print("  ✓ Login สำเร็จ\n")
    except Exception as e:
        print(f"  ✗ Login ล้มเหลว: {e}")
        sys.exit(1)

    # Upload
    created = skipped = errors = 0
    for i, order in enumerate(orders):
        try:
            res = post_order(token, order)
            if res.get("status") == "skipped":
                skipped += 1
            else:
                created += 1
            if (i+1) % 20 == 0 or (i+1) == len(orders):
                print(f"  [{i+1}/{len(orders)}] ✓ {created} created · {skipped} skipped · {errors} errors")
        except urllib.error.HTTPError as e:
            errors += 1
            if errors <= 3:
                print(f"  ✗ order {order['order_id'][-8:]}: HTTP {e.code}")
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ✗ {e}")

    print(f"\n{'='*50}")
    print(f"✅ เสร็จแล้ว!")
    print(f"   สร้างใหม่ : {created} orders")
    print(f"   ข้ามซ้ำ  : {skipped} orders")
    print(f"   ผิดพลาด  : {errors} orders")
    print(f"\nเปิด {RAILWAY_URL} → ใบขาย เพื่อดูผลครับ")
