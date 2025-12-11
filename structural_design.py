import ezdxf
import math
import pandas as pd
import os

# ==========================================
# ⚙️ إعدادات التصميم
# ==========================================
CONFIG = {
    'FC': 30.0, 'FY': 420.0, 'SBC': 200.0,
    'FLOORS': 5, 'LOAD_m2': 1.6,
    'WALL_LOAD_M': 12.0, 'PLANTED_COL_LOAD': 450.0,
}

class StructuralFixFinalV2:
    def __init__(self, filepath):
        self.filepath = filepath
        try:
            self.doc = ezdxf.readfile(filepath)
            self.msp = self.doc.modelspace()
            print("✅ تم التحميل. جاري التحليل...")
        except Exception as e:
            print(f"❌ خطأ: {e}")
            self.doc = None
            return

        # ---------------------------------------------------------
        # 1. كشف الوحدات الذكي (Smart Unit Detection v2)
        # ---------------------------------------------------------
        # بدلاً من الإحداثيات، سنقيس "أبعاد" العناصر نفسها
        widths = []
        for e in self.msp.query('LWPOLYLINE'):
            if len(widths) > 20: break
            # حساب أصغر بعد (العرض)
            pts = e.get_points('xy')
            if not pts: continue
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            w, h = max(xs)-min(xs), max(ys)-min(ys)
            if w > 0 and h > 0:
                widths.append(min(w, h))

        avg_w = sum(widths)/len(widths) if widths else 0.0

        # الحكم المنطقي
        if avg_w > 10.0:
            # مستحيل أن يكون عرض عمود 200 متر! إذن هو مليمتر
            self.IS_MM = True
            self.SCALE = 1000.0
            self.LIMIT_TRANS = 390.0 # حد التحويلية (ملم)
            print(f"   - الوحدات المكتشفة: مليمتر (متوسط العرض {int(avg_w)})")
        else:
            # عرض 0.20 أو 0.40 منطقي للمتر
            self.IS_MM = False
            self.SCALE = 1.0
            self.LIMIT_TRANS = 0.39  # حد التحويلية (متر)
            print(f"   - الوحدات المكتشفة: متر (متوسط العرض {avg_w:.2f})")

        # الطبقات
        self.LAYERS = {
            'COL': 'S-COL-CONC', 'BEAM': 'S-BEAM-MAIN',
            'OUT_COL': 'S-DESIGN-COL', 'OUT_FND': 'S-DESIGN-FND',
            'OUT_BEAM': 'S-DESIGN-BEAM', 'OUT_TXT': 'S-DESIGN-TXT'
        }
        for k, name in self.LAYERS.items():
            if name not in self.doc.layers: self.doc.layers.new(name)

        self.beams_db = []
        self.report_beams = []
        self.report_cols = []

    def get_geo(self, e):
        pts = e.get_points('xy')
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        w, h = max(xs)-min(xs), max(ys)-min(ys)
        cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
        return cx, cy, w, h, min(xs), max(xs), min(ys), max(ys)

    # --- معالجة الميدات ---
    def process_beams(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["BEAM"]}"]'
        count = 1

        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, x1, x2, y1, y2 = self.get_geo(e)

            b_width = min(w, h); span = max(w, h)

            # تحديد التحويلية
            is_trans = (b_width >= self.LIMIT_TRANS)

            self.beams_db.append({'box': (x1, x2, y1, y2), 'is_trans': is_trans})

            # الحسابات (بالمتر دائماً)
            math_w = b_width / self.SCALE
            math_span = span / self.SCALE

            # تجاهل العناصر الصغيرة جداً (أخطاء رسم)
            if math_span < 0.5: continue

            if is_trans:
                e.dxf.layer = self.LAYERS['OUT_BEAM']; e.dxf.color = 6 # Magenta
                depth = 0.80; dia = 16; txt_type = "TR"
                mu = (12 * math_span**2 / 8) + (CONFIG['PLANTED_COL_LOAD'] * math_span / 4)
            else:
                e.dxf.layer = self.LAYERS['OUT_BEAM']; e.dxf.color = 3 # Green
                depth = 0.60; dia = 14; txt_type = "B"
                mu = (12 * math_span**2 / 8)

            d_eff = depth - 0.05
            as_req = (mu * 10**6) / (0.85 * CONFIG['FY'] * d_eff * 1000)
            bars = math.ceil(as_req / (201 if dia==16 else 154))
            if bars < 3: bars = 3

            # الكتابة (Dynamic Text Size)
            # نستخدم نسبة من العرض لضمان القراءة
            txt_h = b_width * 0.35

            label = f"{txt_type}\n{int(math_w*100)}x{int(depth*100)}\n{bars}T{dia}"
            self.add_text(cx, cy, label, txt_h, 7)

            # التقرير
            self.report_beams.append({
                'ID': f"{txt_type}-{count}",
                'Type': "Transfer" if is_trans else "Tie",
                'W(m)': round(math_w, 2),
                'D(m)': depth,
                'Span(m)': round(math_span, 2),
                'Mu(kN.m)': int(mu),
                'Rebar': f"{bars}T{dia}"
            })
            count += 1

    # --- معالجة الأعمدة ---
    def process_columns(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["COL"]}"]'
        count = 1

        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, x1, x2, y1, y2 = self.get_geo(e)

            # كشف المزروع (هامش تلامس)
            is_planted = False
            tol = min(w, h) * 0.5
            for b in self.beams_db:
                if b['is_trans']:
                    bx1, bx2, by1, by2 = b['box']
                    if (bx1-tol < cx < bx2+tol) and (by1-tol < cy < by2+tol):
                        is_planted = True; break

            e.dxf.layer = self.LAYERS['OUT_COL']; e.dxf.color = 4

            math_w = w / self.SCALE; math_h = h / self.SCALE

            # تجاهل الصغير جدا
            if math_w < 0.1: continue

            pu = (math_w * math_h * 130) * CONFIG['LOAD_m2'] * CONFIG['FLOORS'] * 9.81 * 1.4

            txt_h = min(w, h) * 0.35
            col_tag = "Planted" if is_planted else f"C{count}"
            self.add_text(cx, cy, f"{col_tag}\n{int(pu)}kN", txt_h, 1)

            footing_txt = "Planted"
            # رسم القاعدة (لغير المزروع)
            if not is_planted:
                p_srv = pu / 1.4; area = p_srv / CONFIG['SBC']
                side = math.sqrt(area); side = max(side, 1.2)
                side = math.ceil(side * 10) / 10.0

                draw_side = side * self.SCALE
                depth = 0.60

                hw = draw_side / 2
                pts = [(cx-hw, cy-hw), (cx+hw, cy-hw), (cx+hw, cy+hw), (cx-hw, cy+hw)]
                self.msp.add_lwpolyline(pts, dxfattribs={'layer': self.LAYERS['OUT_FND'], 'closed':True, 'color': 2})

                self.add_text(cx, cy - draw_side/2 - txt_h, f"F:{side}x{side}", txt_h, 7)
                footing_txt = f"{side}x{side}x{depth}"

            self.report_cols.append({
                'ID': col_tag,
                'Dim(m)': f"{round(math_w,2)}x{round(math_h,2)}",
                'Load(kN)': int(pu),
                'Footing': footing_txt
            })
            count += 1

    def add_text(self, x, y, text, h, color):
        self.msp.add_mtext(text, dxfattribs={
            'insert': (x, y), 'char_height': h,
            'layer': self.LAYERS['OUT_TXT'], 'color': color, 'attachment_point': 5
        })

    def run(self):
        if not self.doc: return
        self.process_beams()
        self.process_columns()

        dxf_out = self.filepath.replace(".dxf", "_FINAL_V2.dxf")
        xls_out = self.filepath.replace(".dxf", "_DATA_V2.xlsx")

        self.doc.saveas(dxf_out)
        with pd.ExcelWriter(xls_out) as writer:
            pd.DataFrame(self.report_beams).to_excel(writer, sheet_name='Beams', index=False)
            pd.DataFrame(self.report_cols).to_excel(writer, sheet_name='Columns', index=False)

        print(f"🎉 تم الإصلاح! \n- DXF: {dxf_out} \n- Excel: {xls_out}")

if __name__ == "__main__":
    if os.path.exists("MyDrawing.dxf"):
        StructuralFixFinalV2("MyDrawing.dxf").run()
    elif os.path.exists("My Drawing.dxf"):
        StructuralFixFinalV2("My Drawing.dxf").run()
    else:
        print("⚠️ يرجى رفع الملف باسم MyDrawing.dxf")
