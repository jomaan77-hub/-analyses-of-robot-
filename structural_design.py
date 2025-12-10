import ezdxf
import math
import pandas as pd
import os
import sys

# ==========================================
# ⚙️ إعدادات المشروع (CONFIGURATION)
# ==========================================
CONFIG = {
    # 1. خصائص المواد والتربة
    'FC': 30.0,             # قوة الخرسانة (MPa)
    'FY': 420.0,            # إجهاد الحديد (MPa)
    'SBC': 200.0,           # ✅ قدرة تحمل التربة (تم الاعتماد: 200 kN/m2)
    'CONC_DENSITY': 25.0,   # كثافة الخرسانة (kN/m3)

    # 2. أحمال المبنى
    'FLOORS': 5,            # عدد الطوابق
    'LOAD_m2': 1.6,         # حمل المتر المربع الشامل (طن)
    'WALL_LOAD_M': 12.0,    # حمل الجدار فوق الميدة (kN/m)

    # 3. إعدادات الميدات والتحويل
    'TRANSFER_WIDTH_LIMIT': 0.40, # ⚠️ أي ميدة عرضها 40 سم أو أكثر تعتبر تحويلية
    'PLANTED_COL_LOAD': 450.0,    # حمل العمود المزروع (للأمان 45 طن)

    # 4. الطبقات (مدخلات الرسم)
    'L_COL_IN':   'S-COL-CONC',   # طبقة الأعمدة
    'L_BEAM_IN':  'S-BEAM-MAIN',  # طبقة الميدات (ارسم الكل هنا)

    # 5. طبقات الإخراج (النتائج)
    'L_OUT_COL':  'S-DESIGN-COL',
    'L_OUT_FND':  'S-DESIGN-FND',
    'L_OUT_BEAM': 'S-DESIGN-BEAM',
    'L_TXT':      'S-DESIGN-TXT'
}

class StructuralProject:
    def __init__(self, filepath):
        self.filepath = filepath
        try:
            self.doc = ezdxf.readfile(filepath)
            self.msp = self.doc.modelspace()
            print("✅ تم تحميل الملف. جاري العمل بالنظام الشامل...")
        except Exception as e:
            print(f"❌ خطأ: تعذر قراءة الملف: {e}")
            sys.exit(1)

        # إنشاء الطبقات الجديدة
        for l in [CONFIG['L_OUT_COL'], CONFIG['L_OUT_FND'], CONFIG['L_OUT_BEAM'], CONFIG['L_TXT']]:
            if l not in self.doc.layers: self.doc.layers.new(l)

        self.columns_db = []

    def get_dims(self, entity):
        """دالة مساعدة لاستخراج الأبعاد بالمتر"""
        pts = entity.get_points('xy')
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]

        raw_w = max(xs)-min(xs)
        raw_h = max(ys)-min(ys)

        # التمييز بين العرض والطول
        if raw_w < raw_h: width, length = raw_w, raw_h
        else: width, length = raw_h, raw_w

        cx = sum(xs)/len(xs); cy = sum(ys)/len(ys)

        if length > 10: width /= 1000.0; length /= 1000.0

        return cx, cy, width, length

    # ==========================================
    # المرحلة 1: تصميم الأعمدة
    # ==========================================
    def design_columns(self):
        print("1️⃣  جاري تصميم الأعمدة...")
        query = f'LWPOLYLINE[layer=="{CONFIG["L_COL_IN"]}"]'

        for entity in self.msp.query(query):
            if not entity.is_closed: continue
            cx, cy, w, h = self.get_dims(entity)

            # حساب الحمل (Ultimate)
            trib_area = (w * h) * 130
            pu_kN = trib_area * CONFIG['LOAD_m2'] * CONFIG['FLOORS'] * 9.81 * 1.4

            self.columns_db.append({'center': (cx, cy), 'load_ult': pu_kN})

            # رسم العمود (Cyan)
            self.draw_rect(cx, cy, w, h, CONFIG['L_OUT_COL'], 4)
            self.add_text(cx, cy, f"C\n{int(pu_kN)}kN", 0.15)

    # ==========================================
    # المرحلة 2: تصميم القواعد (بناءً على SBC=200)
    # ==========================================
    def design_footings(self):
        print("2️⃣  جاري تصميم القواعد...")

        for col in self.columns_db:
            cx, cy = col['center']
            pu = col['load_ult']

            # الحمل التشغيلي
            p_service = pu / 1.4

            # تصميم المساحة بناءً على قوة التربة 200
            req_area = p_service / CONFIG['SBC']

            # أبعاد القاعدة
            side = math.sqrt(req_area)
            side = max(side, 1.20)
            side = math.ceil(side * 10) / 10.0

            depth = 0.50 if side < 2.0 else 0.60

            # رسم القاعدة (Yellow)
            self.draw_rect(cx, cy, side, side, CONFIG['L_OUT_FND'], 2)
            self.add_text(cx, cy-0.8, f"F: {side}x{side}x{depth}", 0.20)

    # ==========================================
    # المرحلة 3: تصميم الميدات (قاعدة الـ 40 سم)
    # ==========================================
    def design_beams(self):
        print("3️⃣  جاري تصميم الميدات...")
        query = f'LWPOLYLINE[layer=="{CONFIG["L_BEAM_IN"]}"]'
        report = []

        for entity in self.msp.query(query):
            if not entity.is_closed: continue
            cx, cy, width, span = self.get_dims(entity)
            if span < 1.0: continue

            # === المنطق الهندسي ===
            if width >= CONFIG['TRANSFER_WIDTH_LIMIT']:
                # ميدة تحويلية (عرض >= 40 سم)
                b_type = "TRANSFER"
                color = 6 # Magenta
                depth = 0.80
                w_dist = (1.2 * width * depth * 25) + (1.6 * CONFIG['WALL_LOAD_M'])
                mu = (w_dist * span**2 / 8) + (CONFIG['PLANTED_COL_LOAD'] * span / 4)
                dia = 16
            else:
                # ميدة عادية (عرض < 40 سم)
                b_type = "TIE-BEAM"
                color = 3 # Green
                depth = 0.60
                w_dist = (1.2 * width * depth * 25) + (1.6 * CONFIG['WALL_LOAD_M'])
                mu = (w_dist * span**2 / 8)
                dia = 14

            # الحديد
            d = depth - 0.05
            as_req = (mu * 10**6) / (0.85 * CONFIG['FY'] * d * 1000)
            bar_area = 201 if dia==16 else 154
            num_bars = math.ceil(as_req / bar_area)
            if num_bars < 3: num_bars = 3

            # الرسم
            self.draw_rect(cx, cy, span, width, CONFIG['L_OUT_BEAM'], color)

            lbl = f"{b_type}\n{int(width*100)}x{int(depth*100)}\n{num_bars}T{dia}"
            self.add_text(cx, cy, lbl, 0.15)
            report.append({'Type': b_type, 'Size': f"{width}x{depth}", 'Rebar': f"{num_bars}T{dia}"})

        pd.DataFrame(report).to_excel(self.filepath.replace(".dxf", "_QTY.xlsx"))

    # ==========================================
    # أدوات مساعدة
    # ==========================================
    def draw_rect(self, cx, cy, w, h, lay, col):
        hw, hh = w/2, h/2
        pts = [(cx-hw, cy-hh), (cx+hw, cy-hh), (cx+hw, cy+hh), (cx-hw, cy+hh)]
        self.msp.add_lwpolyline(pts, dxfattribs={'layer': lay, 'closed': True, 'color': col})

    def add_text(self, cx, cy, txt, h):
        self.msp.add_mtext(txt, dxfattribs={
            'insert': (cx, cy), 'char_height': h,
            'layer': CONFIG['L_TXT'], 'color': 7, 'attachment_point': 5
        })

    def run(self):
        self.design_columns()
        self.design_footings()
        self.design_beams()
        out = self.filepath.replace(".dxf", "_FINAL_DESIGN.dxf")
        self.doc.saveas(out)
        print(f"🎉 تم الحفظ في: {out}")

# تشغيل
if __name__ == "__main__":
    file_name = "My Drawing.dxf"
    if os.path.exists(file_name):
        StructuralProject(file_name).run()
    else:
        print(f"⚠️ ملف DXF غير موجود: {file_name}")
