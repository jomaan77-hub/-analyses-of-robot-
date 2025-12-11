import ezdxf
import pandas as pd
import os

class RevitDataBridge:
    def __init__(self, filepath):
        self.filepath = filepath
        try:
            self.doc = ezdxf.readfile(filepath)
            self.msp = self.doc.modelspace()
            print("✅ جاري تجهيز بيانات الريفت (Extraction)...")
        except:
            print("❌ خطأ: الملف غير موجود."); return

        # طبقاتك المعتمدة
        self.LAYERS = {
            'COL': 'S-COL-CONC',
            'BEAM': 'S-BEAM-MAIN'
        }

        self.revit_cols = []
        self.revit_beams = []

    def get_geo(self, e):
        """استخراج الهندسة بدقة المليمتر"""
        pts = e.get_points('xy')
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        w, h = max(xs)-min(xs), max(ys)-min(ys)
        cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
        return cx, cy, w, h, min(xs), max(xs), min(ys), max(ys)

    def run(self):
        # --- 1. استخراج الأعمدة ---
        query_col = f'LWPOLYLINE[layer=="{self.LAYERS["COL"]}"]'
        count = 1

        for e in self.msp.query(query_col):
            if not e.is_closed: continue
            cx, cy, w, h, _, _, _, _ = self.get_geo(e)

            # تصحيح الوحدات (إذا كان الرسم بالمتر، نتركها. إذا ملم نحولها)
            # بما أنك ترسم بالمتر، سنعتمد الأرقام كما هي
            # لكن للاحتياط: إذا الرقم ضخم (>100) نقسم على 1000
            scale = 1000.0 if w > 50 else 1.0

            final_w = w / scale
            final_h = h / scale
            final_x = cx / scale
            final_y = cy / scale

            # اسم العائلة في الريفت (Type Name)
            # مثال: C_600x300
            type_name = f"{int(final_w*1000)}x{int(final_h*1000)}"

            self.revit_cols.append({
                'ID': count,
                'FamilyType': type_name,
                'X': round(final_x, 3),
                'Y': round(final_y, 3),
                'Z_Base': 0.0,  # منسوب القاعدة
                'Z_Top': 3.2,   # ارتفاع الدور
                'Width': final_w,
                'Depth': final_h
            })
            count += 1

        # --- 2. استخراج الميدات (كنقاط بداية ونهاية) ---
        query_beam = f'LWPOLYLINE[layer=="{self.LAYERS["BEAM"]}"]'
        b_count = 1

        for e in self.msp.query(query_beam):
            if not e.is_closed: continue
            cx, cy, w, h, min_x, max_x, min_y, max_y = self.get_geo(e)

            scale = 1000.0 if w > 50 else 1.0

            # تحديد الاتجاه لرسم الخط في الريفت
            width = min(w, h) / scale
            span = max(w, h) / scale

            # تحديد نقاط البداية والنهاية (Line Based)
            # الريفت يحتاج خط ليرسم الكمرة عليه
            if w > h: # أفقي
                start_x, start_y = min_x/scale, cy/scale
                end_x, end_y = max_x/scale, cy/scale
            else: # رأسي
                start_x, start_y = cx/scale, min_y/scale
                end_x, end_y = cx/scale, max_y/scale

            type_name = f"{int(width*1000)}x600" # عمق افتراضي 600

            self.revit_beams.append({
                'ID': b_count,
                'FamilyType': type_name,
                'StartX': round(start_x, 3),
                'StartY': round(start_y, 3),
                'EndX': round(end_x, 3),
                'EndY': round(end_y, 3),
                'Width': width
            })
            b_count += 1

        # --- 3. الحفظ CSV ---
        # هذه الملفات هي التي ستدخلها في Dynamo
        pd.DataFrame(self.revit_cols).to_csv("Revit_Columns_Data.csv", index=False)
        pd.DataFrame(self.revit_beams).to_csv("Revit_Beams_Data.csv", index=False)

        print("🎉 البيانات جاهزة!")
        print("📂 حمل الملفين: Revit_Columns_Data.csv و Revit_Beams_Data.csv")

if __name__ == "__main__":
    if os.path.exists("MyDrawing.dxf"):
        RevitDataBridge("MyDrawing.dxf").run()
    elif os.path.exists("My Drawing.dxf"):
        RevitDataBridge("My Drawing.dxf").run()
    else:
        print("⚠️ ملف DXF غير موجود")
