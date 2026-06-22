"""
โอนข้อมูล btp_erp.json ขึ้น Railway — รันครั้งเดียว
"""
import urllib.request, json, getpass, sys
from pathlib import Path

RAILWAY_URL = "https://web-production-74d03.up.railway.app"
DATA_FILE   = Path(__file__).parent / "data" / "btp_erp.json"

def login(username, password):
    body = json.dumps({"username": username, "password": password}).encode()
    req  = urllib.request.Request(
        f"{RAILWAY_URL}/api/auth/login",
        data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["token"]

def upload(token, data_bytes):
    req = urllib.request.Request(
        f"{RAILWAY_URL}/api/admin/restore-initial-data",
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def check_health(token):
    req = urllib.request.Request(
        f"{RAILWAY_URL}/api/health",
        headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

if __name__ == "__main__":
    print("=" * 50)
    print("  Upload btp_erp.json → Railway")
    print("=" * 50)

    if not DATA_FILE.exists():
        print(f"\n✗ ไม่พบไฟล์: {DATA_FILE}")
        sys.exit(1)

    data_bytes = DATA_FILE.read_bytes()
    data_size  = len(data_bytes)
    products   = len(json.loads(data_bytes).get("products", []))
    print(f"\n📦 ไฟล์ข้อมูล: {data_size:,} bytes · {products} SKUs")

    print(f"\n🔑 Login เป็น admin ที่ {RAILWAY_URL}")
    username = input("Username [admin]: ").strip() or "admin"
    password = getpass.getpass("Password: ")

    try:
        token = login(username, password)
        print("  ✓ Login สำเร็จ")
    except Exception as e:
        print(f"  ✗ Login ล้มเหลว: {e}")
        sys.exit(1)

    # Check current state
    h = check_health(token)
    db = h.get("db", {})
    print(f"\n📊 สถานะ Railway ปัจจุบัน:")
    print(f"   DB file: {'มีอยู่แล้ว' if db.get('exists') else 'ไม่มี'} | {db.get('products', 0)} products")

    if db.get("products", 0) > 0:
        confirm = input(f"\n⚠️  มีข้อมูลอยู่แล้ว {db['products']} products — ทับได้? (y/N): ")
        if confirm.strip().lower() != "y":
            print("ยกเลิก")
            sys.exit(0)

    print("\n⏳ กำลังโอนข้อมูล...")
    try:
        result = upload(token, data_bytes)
        print(f"  ✓ สำเร็จ! โอน {result['products']} SKUs ({result['size']:,} bytes)")
    except Exception as e:
        print(f"  ✗ ล้มเหลว: {e}")
        sys.exit(1)

    # Verify
    h2 = check_health(token)
    print(f"\n✅ ตรวจสอบผล: {h2['db']['products']} products อยู่บน Railway แล้ว")
    print(f"\nเปิด {RAILWAY_URL} ได้เลยครับ")
