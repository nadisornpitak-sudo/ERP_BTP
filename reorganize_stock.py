"""
Reorganize stock channels and racks in BTP ERP according to user layout:
- Floor 1: Online (O)
- Floor 2 Room 2 (Q Racks): Dealer (D)
- Floor 2 Room 1 (P & X Racks): Shop (S)
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Paths
SERVER_DIR = Path(__file__).parent
DATA_DIR = SERVER_DIR / "data"
DB_FILE = DATA_DIR / "btp_erp.json"

# Racks definition matching the frontend
WH3D_RACKS = [
    # Floor 1
    {'id':'A1','bays':1,'levels':4,'pos':5},
    {'id':'A2','bays':1,'levels':4,'pos':5},
    {'id':'A3','bays':1,'levels':4,'pos':5},
    {'id':'B1','bays':1,'levels':4,'pos':5},
    {'id':'B7','bays':1,'levels':4,'pos':5},
    {'id':'B8','bays':1,'levels':4,'pos':5},
    {'id':'B2','bays':1,'levels':4,'pos':5},
    {'id':'B3','bays':1,'levels':4,'pos':5},
    {'id':'B4','bays':1,'levels':4,'pos':5},
    {'id':'B5','bays':1,'levels':4,'pos':5},
    {'id':'B6','bays':1,'levels':4,'pos':5},
    {'id':'Y1','bays':1,'levels':4,'pos':5},
    {'id':'Y2','bays':1,'levels':4,'pos':5},
    {'id':'W1','bays':5,'levels':4,'pos':3},
    # Floor 2 Room 1
    {'id':'P1','bays':1,'levels':4,'pos':5},
    {'id':'P2','bays':1,'levels':4,'pos':5},
    {'id':'P3','bays':1,'levels':4,'pos':5},
    {'id':'P4','bays':2,'levels':4,'pos':5},
    {'id':'P5','bays':1,'levels':4,'pos':5},
    {'id':'P6','bays':1,'levels':4,'pos':5},
    {'id':'P7','bays':2,'levels':4,'pos':5},
    {'id':'P8','bays':2,'levels':4,'pos':5},
    # Floor 2 Room 2
    {'id':'Q1','bays':1,'levels':4,'pos':5},
    {'id':'Q2','bays':1,'levels':4,'pos':5},
    {'id':'Q3','bays':1,'levels':4,'pos':5},
    {'id':'Q4','bays':2,'levels':4,'pos':5},
    {'id':'Q5','bays':2,'levels':4,'pos':5},
    {'id':'Q6','bays':1,'levels':4,'pos':5},
    {'id':'Q7','bays':1,'levels':4,'pos':5},
    {'id':'Q8','bays':1,'levels':4,'pos':5},
    # Extension Floor 2 Room 1
    {'id':'X1','bays':1,'levels':4,'pos':5},
    {'id':'X2','bays':1,'levels':4,'pos':5},
    {'id':'X3','bays':1,'levels':4,'pos':5},
    {'id':'X4','bays':1,'levels':4,'pos':5},
    {'id':'X5','bays':1,'levels':4,'pos':5},
]

def generate_slots():
    slots = []
    for r in WH3D_RACKS:
        bays = r['bays']
        levels = r['levels']
        pos = r['pos']
        for l in range(levels):
            for b in range(bays):
                for p in range(pos):
                    slots.append(f"{r['id']}-{b+1}-{l+1}-{p+1}")
    return slots

def main():
    if not DB_FILE.exists():
        print(f"❌ Error: ไม่พบไฟล์ฐานข้อมูล BTP ERP ที่ {DB_FILE}")
        sys.exit(1)
        
    # Generate slots
    all_slots = generate_slots()
    print(f"📦 Generate Slots คลังสินค้าสำเร็จ: {len(all_slots)} พิกัดชั้นวาง")
    
    # Load database
    with open(DB_FILE, "r", encoding="utf-8") as f:
        db = json.load(f)
        
    db.setdefault("locations", {})
    
    # Clean invalid locations from locations map
    slot_set = set(all_slots)
    to_delete = []
    for sku, slot in db["locations"].items():
        # Keep floor locations
        if "floor" in slot.lower():
            continue
        if slot not in slot_set:
            to_delete.append(sku)
    for sku in to_delete:
        del db["locations"][sku]
        
    # Identify occupied slots
    occupied = set(db["locations"].values())
    
    # Sort products to ensure consistent slot assignment
    products = db.get("products", [])
    products_sorted = sorted(products, key=lambda x: (x.get("group", ""), x.get("sku", "")))
    
    assigned_count = 0
    
    # 1. Assign empty slots to products with stock that don't have location
    for p in products_sorted:
        sku = p["sku"]
        stock_dict = p.get("stock", {})
        total_stock = sum(int(v) for v in stock_dict.values())
        
        if total_stock > 0 and sku not in db["locations"]:
            # Find next free slot
            free_slot = None
            for s in all_slots:
                if s not in occupied:
                    free_slot = s
                    break
            if free_slot:
                db["locations"][sku] = free_slot
                occupied.add(free_slot)
                assigned_count += 1
                
    if assigned_count > 0:
        print(f"📍 จัดตำแหน่งแร็คใหม่ให้สินค้าที่ค้างอยู่สำเร็จ: {assigned_count} SKU")
        
    # 2. Distribute stock to channels based on assigned slot location
    floor1_count = 0
    floor2_r2_count = 0
    floor2_r1_count = 0
    
    for p in products:
        sku = p["sku"]
        stock_dict = p.get("stock", {})
        total_stock = sum(int(v) for v in stock_dict.values())
        
        if total_stock <= 0:
            continue
            
        slot = db["locations"].get(sku)
        if not slot:
            # Fallback to Online (O) if no slot found
            p["stock"] = {"S": 0, "D": 0, "O": total_stock, "W": 0, "Q": 0, "F": 0}
            floor1_count += 1
            continue
            
        # Re-distribute stock channels
        if slot.startswith(("A", "B", "Y", "W")) or "floor" in slot.lower():
            # Floor 1 (Online)
            p["stock"] = {"S": 0, "D": 0, "O": total_stock, "W": 0, "Q": 0, "F": 0}
            floor1_count += 1
        elif slot.startswith("Q"):
            # Floor 2 Room 2 (Dealer)
            p["stock"] = {"S": 0, "D": total_stock, "O": 0, "W": 0, "Q": 0, "F": 0}
            floor2_r2_count += 1
        elif slot.startswith(("P", "X")):
            # Floor 2 Room 1 / Ext (Shop/หน้าร้าน)
            p["stock"] = {"S": total_stock, "D": 0, "O": 0, "W": 0, "Q": 0, "F": 0}
            floor2_r1_count += 1
        else:
            # Fallback
            p["stock"] = {"S": 0, "D": 0, "O": total_stock, "W": 0, "Q": 0, "F": 0}
            floor1_count += 1

    print("\n📈 ผลการจัดแจงช่องทางสต็อกตามตำแหน่ง:")
    print(f"   • ชั้น 1 (Online - O)          : {floor1_count} SKUs")
    print(f"   • ชั้น 2 ห้อง 2 (Dealer - D)     : {floor2_r2_count} SKUs")
    print(f"   • ชั้น 2 ห้อง 1 (หน้าร้าน - S)    : {floor2_r1_count} SKUs")
    
    # Save DB with backup
    backup_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = DATA_DIR / f"backup_pre_reorganize_{backup_ts}.json"
    shutil.copy2(DB_FILE, backup_file)
    print(f"\n💾 สำรองข้อมูลเดิมไว้ที่: {backup_file.name}")
    
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
        
    print("✅ จัดสต็อกและตำแหน่งแร็คในระบบเรียบร้อยแล้ว!")
    print("\n💡 สามารถรัน python upload_data_to_railway.py เพื่อนำข้อมูลที่จัดแจงใหม่นี้ขึ้นระบบคลาวด์ได้ทันที")

if __name__ == "__main__":
    main()
