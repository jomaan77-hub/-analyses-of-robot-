import ezdxf
import math
import os

class SmartStructuralFix:
    def __init__(self, filepath):
        self.filepath = filepath
        try:
            self.doc = ezdxf.readfile(filepath)
            self.msp = self.doc.modelspace()
            print("✅ تم تحميل الملف. جاري المعالجة الذكية...")
        except Exception as e:
            print(f"Error loading file: {e}")
            self.doc = None
            return

        # 1. كشف الوحدات تلقائياً (Auto-Detect Units)
        # نقيس متوسط إحداثيات العناصر. إذا كانت ضخمة (>1000) فهي مليمتر.
        sample_pts = []
        for e in self.msp.query('LWPOLYLINE'):
             if len(sample_pts) > 10: break
             sample_pts.extend(e.get_points('xy'))

        avg_coord = sum([p[0] for p in sample_pts])/len(sample_pts) if sample_pts else 0

        if avg_coord > 500:
            self.UNIT_SCALE = "MM"
            self.LIMIT_40CM = 400.0  # حد الميدة التحويلية
            print("Detected Units: MILLIMETERS (MM)")
        else:
            self.UNIT_SCALE = "M"
            self.LIMIT_40CM = 0.40   # حد الميدة التحويلية
            print("Detected Units: METERS (M)")

        # إعداد الطبقات
        self.LAYERS = {
            'COL_IN': 'S-COL-CONC', 'BEAM_IN': 'S-BEAM-MAIN',
            'COL_OUT': 'S-DESIGN-COL', 'FND_OUT': 'S-DESIGN-FND',
            'BEAM_OUT': 'S-DESIGN-BEAM', 'TXT': 'S-DESIGN-TXT'
        }
        for k, name in self.LAYERS.items():
            if name not in self.doc.layers: self.doc.layers.new(name)

        self.beams_db = []

    def get_geometry(self, entity):
        pts = entity.get_points('xy')
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        w, h = maxx-minx, maxy-miny
        cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
        return cx, cy, w, h, minx, maxx, miny, maxy

    # --- 1. تحليل الميدات (بدقة الوحدات) ---
    def analyze_beams(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["BEAM_IN"]}"]'
        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, x1, x2, y1, y2 = self.get_geometry(e)

            # العرض هو الضلع الأصغر
            beam_width = min(w, h)

            # الشرط الفاصل (حسب الوحدة المكتشفة)
            is_transfer = (beam_width >= (self.LIMIT_40CM - 0.01)) # هامش خطأ بسيط

            self.beams_db.append({
                'rect': (x1, x2, y1, y2), # حدود الميدة
                'is_transfer': is_transfer,
                'width': beam_width,
                'entity': e
            })

    # --- 2. معالجة الأعمدة والقواعد ---
    def process_columns(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["COL_IN"]}"]'
        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, _, _, _, _ = self.get_geometry(e)

            # فحص: هل العمود مزروع؟
            is_planted = False
            for b in self.beams_db:
                if b['is_transfer']:
                    bx1, bx2, by1, by2 = b['rect']
                    # هل مركز العمود داخل الميدة؟
                    if (bx1 < cx < bx2) and (by1 < cy < by2):
                        is_planted = True
                        break

            # نقل العمود للطبقة الجديدة وتلوينه
            e.dxf.layer = self.LAYERS['COL_OUT']
            e.dxf.color = 4 # Cyan

            # حجم النص الذكي (دائماً متناسب مع حجم العمود)
            txt_h = min(w, h) * 0.40

            # كتابة النص
            lbl = "Planted" if is_planted else "C1"
            self.add_text(cx, cy, lbl, txt_h, 1) # Yellow text

            # رسم القاعدة (فقط إذا لم يكن مزروعاً)
            if not is_planted:
                self.draw_footing(cx, cy, w, h)

    # --- 3. رسم القاعدة ---
    def draw_footing(self, cx, cy, col_w, col_h):
        # حجم القاعدة (نسبة وتناسب للرسم النظيف)
        # القاعدة تكون 3 أضعاف العمود تقريباً
        f_side = max(col_w, col_h) * 3.0

        hw = f_side / 2
        pts = [(cx-hw, cy-hw), (cx+hw, cy-hw), (cx+hw, cy+hw), (cx-hw, cy+hw)]
        self.msp.add_lwpolyline(pts, dxfattribs={'layer': self.LAYERS['FND_OUT'], 'closed':True, 'color': 2})

        # نص القاعدة
        txt_h = min(col_w, col_h) * 0.30
        self.add_text(cx, cy - f_side/2 - txt_h, f"F", txt_h, 7)

    # --- 4. تلوين الميدات ---
    def draw_beams(self):
        for b in self.beams_db:
            e = b['entity']
            e.dxf.layer = self.LAYERS['BEAM_OUT']

            if b['is_transfer']:
                e.dxf.color = 6 # Magenta
                txt = "TR"
            else:
                e.dxf.color = 3 # Green
                txt = "B"

            # كتابة نص الميدة
            cx, cy, w, h, _, _, _, _ = self.get_geometry(e)
            txt_h = min(w, h) * 0.30
            self.add_text(cx, cy, txt, txt_h, 7)

    def add_text(self, x, y, text, h, color):
        self.msp.add_mtext(text, dxfattribs={
            'insert': (x, y), 'char_height': h,
            'layer': self.LAYERS['TXT'], 'color': color, 'attachment_point': 5
        })

    def run(self):
        if self.doc is None:
            print("❌ لم يتم تحميل الملف بنجاح.")
            return
        self.analyze_beams()
        self.process_columns()
        self.draw_beams()
        out = self.filepath.replace(".dxf", "_CLEAN.dxf")
        self.doc.saveas(out)
        print(f"🎉 تم التنظيف! الملف: {out}")

if __name__ == "__main__":
    filename = "My Drawing.dxf" if os.path.exists("My Drawing.dxf") else "MyDrawing.dxf"
    if os.path.exists(filename):
        SmartStructuralFix(filename).run()
    else:
        print(f"File not found: {filename}")
