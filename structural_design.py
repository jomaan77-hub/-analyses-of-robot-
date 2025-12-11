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

class StructuralFixFinalWithExcel:
    def __init__(self, filepath):
        self.filepath = filepath
        try:
            self.doc = ezdxf.readfile(filepath)
            self.msp = self.doc.modelspace()
            print("✅ تم التحميل. جاري التحليل والرسم وحساب الكميات...")
        except Exception as e:
            print(f"❌ خطأ: {e}")
            self.doc = None
            return

        # كشف الوحدات
        sample_pts = []
        for e in self.msp.query('LWPOLYLINE'):
             if len(sample_pts) > 5: break
             sample_pts.extend(e.get_points('xy'))
        avg = sum([p[0] for p in sample_pts])/len(sample_pts) if sample_pts else 0

        if avg > 500: # مليمتر
            self.IS_MM = True; self.SCALE = 1000.0; self.LIMIT_TRANS = 390.0
        else: # متر
            self.IS_MM = False; self.SCALE = 1.0; self.LIMIT_TRANS = 0.39

        # الطبقات
        self.LAYERS = {
            'COL': 'S-COL-CONC', 'BEAM': 'S-BEAM-MAIN',
            'OUT_COL': 'S-DESIGN-COL', 'OUT_FND': 'S-DESIGN-FND',
            'OUT_BEAM': 'S-DESIGN-BEAM', 'OUT_TXT': 'S-DESIGN-TXT'
        }
        for k, name in self.LAYERS.items():
            if name not in self.doc.layers: self.doc.layers.new(name)

        self.beams_db = []

        # قوائم لتخزين بيانات التقرير (Excel Data)
        self.report_beams = []
        self.report_cols = []

    def get_geo(self, e):
        pts = e.get_points('xy')
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        w, h = max(xs)-min(xs), max(ys)-min(ys)
        cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
        return cx, cy, w, h, min(xs), max(xs), min(ys), max(ys)

    # --- 1. معالجة الميدات + تسجيل بيانات الإكسل ---
    def process_beams(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["BEAM"]}"]'
        count = 1

        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, x1, x2, y1, y2 = self.get_geo(e)

            b_width = min(w, h); span = max(w, h)
            is_trans = (b_width >= self.LIMIT_TRANS)

            self.beams_db.append({'box': (x1, x2, y1, y2), 'is_trans': is_trans})

            # الحسابات
            math_w = b_width / self.SCALE
            math_span = span / self.SCALE

            if is_trans:
                e.dxf.layer = self.LAYERS['OUT_BEAM']; e.dxf.color = 6
                depth = 0.80; dia = 16; txt_type = "TR"
                mu = (12 * math_span**2 / 8) + (CONFIG['PLANTED_COL_LOAD'] * math_span / 4)
            else:
                e.dxf.layer = self.LAYERS['OUT_BEAM']; e.dxf.color = 3
                depth = 0.60; dia = 14; txt_type = "B"
                mu = (12 * math_span**2 / 8)

            d_eff = depth - 0.05
            as_req = (mu * 10**6) / (0.85 * CONFIG['FY'] * d_eff * 1000)
            bars = math.ceil(as_req / (201 if dia==16 else 154))
            if bars < 3: bars = 3

            # الكتابة في الرسم
            txt_h = b_width * 0.35
            label = f"{txt_type}\n{int(math_w*100)}x{int(depth*100)}\n{bars}T{dia}"
            self.add_text(cx, cy, label, txt_h, 7)

            # === إضافة لتقرير الإكسل ===
            self.report_beams.append({
                'Beam ID': f"{txt_type}-{count}",
                'Type': "Transfer" if is_trans else "Tie-Beam",
                'Width (m)': round(math_w, 2),
                'Depth (m)': depth,
                'Span (m)': round(math_span, 2),
                'Moment (kN.m)': round(mu, 1),
                'Rebar': f"{bars} T{dia}"
            })
            count += 1

    # --- 2. معالجة الأعمدة والقواعد + تسجيل بيانات الإكسل ---
    def process_columns(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["COL"]}"]'
        count = 1

        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, x1, x2, y1, y2 = self.get_geo(e)

            # كشف المزروع
            is_planted = False
            tol = min(w, h) * 0.5
            for b in self.beams_db:
                if b['is_trans']:
                    bx1, bx2, by1, by2 = b['box']
                    if (bx1-tol < cx < bx2+tol) and (by1-tol < cy < by2+tol):
                        is_planted = True; break

            e.dxf.layer = self.LAYERS['OUT_COL']; e.dxf.color = 4

            math_w = w / self.SCALE; math_h = h / self.SCALE
            pu = (math_w * math_h * 130) * CONFIG['LOAD_m2'] * CONFIG['FLOORS'] * 9.81 * 1.4

            txt_h = min(w, h) * 0.35
            col_tag = "Planted" if is_planted else f"C{count}"
            self.add_text(cx, cy, f"{col_tag}\n{int(pu)}kN", txt_h, 1)

            # القواعد والتقرير
            footing_info = "None (Planted)"
            if not is_planted:
                p_srv = pu / 1.4; area = p_srv / CONFIG['SBC']
                side = math.sqrt(area); side = max(side, 1.2)
                side = math.ceil(side * 10) / 10.0

                draw_side = side * self.SCALE
                depth = 0.60

                hw = draw_side / 2
                pts = [(cx-hw, cy-hw), (cx+hw, cy-hw), (cx+hw, cy+hw), (cx-hw, cy+hw)]
                self.msp.add_lwpolyline(pts, dxfattribs={'layer': self.LAYERS['OUT_FND'], 'closed':True, 'color': 2})

                lbl_f = f"F: {side}x{side}x{depth}"
                self.add_text(cx, cy - draw_side/2 - txt_h, lbl_f, txt_h, 7)
                footing_info = f"{side}x{side}x{depth}"

            # === إضافة لتقرير الإكسل ===
            self.report_cols.append({
                'Col ID': col_tag if is_planted else f"C{count}",
                'Status': "Planted on Beam" if is_planted else "On Foundation",
                'Dims (m)': f"{round(math_w,2)}x{round(math_h,2)}",
                'Load (kN)': int(pu),
                'Footing Dims': footing_info
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

        # 1. حفظ ملف DXF
        dxf_out = self.filepath.replace(".dxf", "_FINAL.dxf")
        self.doc.saveas(dxf_out)

        # 2. حفظ ملف Excel
        xls_out = self.filepath.replace(".dxf", "_REPORT.xlsx")

        # نستخدم Pandas لكتابة شيتين (Sheets) داخل ملف إكسل واحد
        with pd.ExcelWriter(xls_out) as writer:
            pd.DataFrame(self.report_beams).to_excel(writer, sheet_name='Beams & Rebar', index=False)
            pd.DataFrame(self.report_cols).to_excel(writer, sheet_name='Columns & Footings', index=False)

        print(f"🎉 تم! \n- المخطط: {dxf_out} \n- التقرير: {xls_out}")

if __name__ == "__main__":
    if os.path.exists("MyDrawing.dxf"):
        StructuralFixFinalWithExcel("MyDrawing.dxf").run()
    elif os.path.exists("My Drawing.dxf"):
        StructuralFixFinalWithExcel("My Drawing.dxf").run()
    else:
        print("⚠️ ملف MyDrawing.dxf غير موجود")
