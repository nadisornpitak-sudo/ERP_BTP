"""
Import BOM Mappings and Master Catalog (FG, BU, PK) from BOM_Mapping_v2(1).xlsx to BTP ERP
Usage: python import_bom_mapping.py
"""
import sys
import json
import shutil
from datetime import datetime
from pathlib import Path

# Paths
SERVER_DIR = Path(__file__).parent
DATA_DIR = SERVER_DIR / "data"
DB_FILE = DATA_DIR / "btp_erp.json"
ROOT_DB_FILE = SERVER_DIR.parent / "data" / "erp_db.json"
EXCEL_FILE = Path("C:/Users/User/Desktop/BOM_Mapping_v2(1).xlsx")

def clean_qty(val):
    val_str = str(val).strip().lower()
    if not val_str or val_str == "nan":
        return 0.0
    val_str = val_str.replace("ml.", "").replace(" ", "")
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def load_db(path):
    if not path.exists():
        print(f"❌ Error: ไม่พบไฟล์ฐานข้อมูลที่ {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

def backup_db(path):
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.parent / f"backup_pre_bom_import_{ts}_{path.name}"
        shutil.copy2(path, backup)
        print(f"💾 สำรองข้อมูลเดิมของ {path.name} ไปที่: {backup.name}")

def main():
    if not EXCEL_FILE.exists():
        print(f"❌ Error: ไม่พบไฟล์ Excel ที่ {EXCEL_FILE}")
        sys.exit(1)
        
    print(f"📂 กำลังโหลดไฟล์ Excel: {EXCEL_FILE.name}")
    try:
        import pandas as pd
    except ImportError:
        print("❌ Error: กรุณาติดตั้ง pandas และ openpyxl เพื่อเปิดไฟล์ Excel:")
        print("   pip install pandas openpyxl")
        sys.exit(1)
        
    # Read all sheets
    df_bom = pd.read_excel(EXCEL_FILE, sheet_name="BOM")
    df_fg = pd.read_excel(EXCEL_FILE, sheet_name="FG List")
    df_bu = pd.read_excel(EXCEL_FILE, sheet_name="BU List")
    df_pk = pd.read_excel(EXCEL_FILE, sheet_name="PK List")
    
    print("📊 โหลดข้อมูลจากแผ่นงานเสร็จสิ้น:")
    print(f"   • BOM: {df_bom.shape[0]} แถว")
    print(f"   • FG List: {df_fg.shape[0]} แถว")
    print(f"   • BU List: {df_bu.shape[0]} แถว")
    print(f"   • PK List: {df_pk.shape[0]} แถว")
    
    # 1. Parse Catalogs
    fg_catalog = {}
    for idx, row in df_fg.iterrows():
        sku = str(row.iloc[0]).strip()
        name = str(row.iloc[1]).strip()
        if sku and sku != "nan" and sku != "FG SKU":
            fg_catalog[sku] = name
            
    bu_catalog = {}
    for idx, row in df_bu.iterrows():
        sku = str(row.iloc[0]).strip()
        name = str(row.iloc[1]).strip()
        if sku and sku != "nan" and sku != "BU SKU":
            bu_catalog[sku] = name
            
    pk_catalog = {}
    for idx, row in df_pk.iterrows():
        sku = str(row.iloc[0]).strip()
        name = str(row.iloc[1]).strip()
        if sku and sku != "nan" and sku != "PK SKU":
            pk_catalog[sku] = name
            
    print(f"📦 จำนวนรายการในแคตตาล็อก: FG={len(fg_catalog)}, BU={len(bu_catalog)}, PK={len(pk_catalog)}")
    
    # 2. Parse BOM Mappings
    boms = {}
    active_fg = None
    
    for idx, row in df_bom.iterrows():
        if idx < 1:  # Skip first description row
            continue
        
        fg_cell = str(row.iloc[0]).strip()
        rm_sku_cell = str(row.iloc[3]).strip()
        
        # Update active_fg if first column has SKU
        if fg_cell and fg_cell != "nan" and fg_cell != "FG SKU":
            active_fg = fg_cell
            
        if not active_fg:
            continue
            
        # Add component if RM SKU is present
        if rm_sku_cell and rm_sku_cell != "nan" and rm_sku_cell != "RM SKU ▼":
            qty_cell = str(row.iloc[5]).strip()
            qty = clean_qty(qty_cell)
            
            component = {
                "sku": rm_sku_cell,
                "qty": qty
            }
            
            if active_fg not in boms:
                boms[active_fg] = []
            boms[active_fg].append(component)
            
    print(f"🔧 ถอดสูตรการผลิต (BOM) สำเร็จ: {len(boms)} SKUs ของสินค้าสำเร็จรูป (FG)")
    
    # 3. Load DB and Create Backups
    db = load_db(DB_FILE)
    backup_db(DB_FILE)
    backup_db(ROOT_DB_FILE)
    
    # 4. Prepare prefix-to-group map from existing DB products to group new FGs nicely
    prefix_to_group = {}
    for p in db.get("products", []):
        sku = p["sku"]
        group = p.get("group", "")
        if group:
            if len(sku) >= 6:
                prefix_to_group[sku[:6]] = group
            if len(sku) >= 4:
                prefix_to_group[sku[:4]] = group
                
    # 5. Insert missing items into products database
    db_products = {p["sku"]: p for p in db.get("products", [])}
    
    added_fg = 0
    added_bu = 0
    added_pk = 0
    
    # Add BU products
    for sku, name in bu_catalog.items():
        if sku not in db_products:
            new_p = {
                "sku": sku,
                "gtin": "",
                "name": name,
                "group": "วัตถุดิบ (น้ำหอม)",
                "variant": "",
                "note": "",
                "img": "",
                "price": 0,
                "cost": 0,
                "reorder": 20,
                "stock": {"S": 0, "D": 0, "O": 0, "W": 0, "Q": 0, "F": 0}
            }
            db.setdefault("products", []).append(new_p)
            db_products[sku] = new_p
            added_bu += 1
            
    # Add PK products
    for sku, name in pk_catalog.items():
        if sku not in db_products:
            new_p = {
                "sku": sku,
                "gtin": "",
                "name": name,
                "group": "บรรจุภัณฑ์",
                "variant": "",
                "note": "",
                "img": "",
                "price": 0,
                "cost": 0,
                "reorder": 20,
                "stock": {"S": 0, "D": 0, "O": 0, "W": 0, "Q": 0, "F": 0}
            }
            db.setdefault("products", []).append(new_p)
            db_products[sku] = new_p
            added_pk += 1
            
    # Add FG products
    for sku, name in fg_catalog.items():
        if sku not in db_products:
            # Try to match group based on prefix
            group = "อื่นๆ"
            if len(sku) >= 6 and sku[:6] in prefix_to_group:
                group = prefix_to_group[sku[:6]]
            elif len(sku) >= 4 and sku[:4] in prefix_to_group:
                group = prefix_to_group[sku[:4]]
                
            new_p = {
                "sku": sku,
                "gtin": "",
                "name": name,
                "group": group,
                "variant": "",
                "note": "",
                "img": "",
                "price": 0,
                "cost": 0,
                "reorder": 20,
                "stock": {"S": 0, "D": 0, "O": 0, "W": 0, "Q": 0, "F": 0}
            }
            db.setdefault("products", []).append(new_p)
            db_products[sku] = new_p
            added_fg += 1
            
    # Add any component referenced in BOMs but still missing from DB
    added_missing_ref = 0
    for fg_sku, comps in boms.items():
        for c in comps:
            comp_sku = c["sku"]
            if comp_sku not in db_products:
                group = "อื่นๆ"
                name = f"{comp_sku} (Auto-created from BOM)"
                if comp_sku.startswith("BU"):
                    group = "วัตถุดิบ (น้ำหอม)"
                elif comp_sku.startswith("PK"):
                    group = "บรรจุภัณฑ์"
                
                new_p = {
                    "sku": comp_sku,
                    "gtin": "",
                    "name": name,
                    "group": group,
                    "variant": "",
                    "note": "Auto-created because it is used in a BOM but was missing from catalog lists",
                    "img": "",
                    "price": 0,
                    "cost": 0,
                    "reorder": 20,
                    "stock": {"S": 0, "D": 0, "O": 0, "W": 0, "Q": 0, "F": 0}
                }
                db.setdefault("products", []).append(new_p)
                db_products[comp_sku] = new_p
                added_missing_ref += 1

    print(f"📈 เพิ่มสินค้าใหม่เข้าระบบ:")
    print(f"   • สินค้าสำเร็จรูป (FG): {added_fg} SKUs")
    print(f"   • น้ำหอม (BU): {added_bu} SKUs")
    print(f"   • บรรจุภัณฑ์ (PK): {added_pk} SKUs")
    print(f"   • ส่วนประกอบที่ไม่มีในรายการแต่พบใน BOM (เพิ่มอัตโนมัติ): {added_missing_ref} SKUs")
    
    # 6. Apply BOM mappings to products
    bom_applied = 0
    missing_components = set()
    
    for p in db.get("products", []):
        sku = p["sku"]
        if sku in boms:
            p["bom"] = boms[sku]
            bom_applied += 1
            # Check if any component in BOM does not exist in DB
            for c in boms[sku]:
                if c["sku"] not in db_products:
                    missing_components.add(c["sku"])
        else:
            # If product doesn't have BOM in Excel, remove the bom key to keep it clean
            p.pop("bom", None)
            
    print(f"🧪 อัปเดตสูตรการผลิต (BOM) ลงในสินค้าในระบบสำเร็จ: {bom_applied} SKUs")
    if missing_components:
        print(f"⚠️ คำเตือน: ตรวจสอบพบส่วนประกอบ {len(missing_components)} SKUs ที่ใช้ใน BOM แต่ไม่พบชื่อสินค้าในตารางสินค้า:")
        print(f"   {sorted(list(missing_components))[:20]}...")
        
    # 7. Save DB files
    save_db(db, DB_FILE)
    print(f"✅ บันทึกข้อมูลลงฐานข้อมูลเซิร์ฟเวอร์เรียบร้อยแล้ว: {DB_FILE.name}")
    
    save_db(db, ROOT_DB_FILE)
    print(f"✅ บันทึกข้อมูลลงฐานข้อมูลโฟลเดอร์หลักเรียบร้อยแล้ว: {ROOT_DB_FILE.name}")
    
if __name__ == "__main__":
    main()
