import ezdxf
import math
import pandas as pd
import os

# ==========================================
# ⚙️ ثوابت المواد (Material Properties)
# ==========================================
CONST = {
    'FC': 30.0,      # f'c (MPa)
    'FY': 420.0,     # fy (MPa)
    'SBC': 200.0,    # Soil Bearing Capacity
    'PHI_B': 0.9,    # Reduction factor (Bending)
    'PHI_V': 0.75,   # Reduction factor (Shear)

    # الأحمال
    'WALL_LOAD': 12.0,   # حمل الجدار (kN/m)
    'PLANTED_P': 250.0,  # حمل المزروع (kN)
    'CONC_DEN': 25.0,    # كثافة الخرسانة
}

class AnalyticalDesigner:
    def __init__(self, filepath):
        self.filepath = filepath
        try:
            self.doc = ezdxf.readfile(filepath)
            self.msp = self.doc.modelspace()
            print("✅ جاري التحليل الإنشائي (عزوم، قص، انحناء)...")
        except: return

        # تجهيز الطبقات
        self.LAYERS = {
            'IN_COL': 'S-COL-CONC', 'IN_BEAM': 'S-BEAM-MAIN',
            'OUT_COL': 'S-RES-COL', 'OUT_FND': 'S-RES-FND',
            'OUT_BEAM': 'S-RES-BEAM', 'OUT_TXT': 'S-RES-TXT'
        }
        for k, v in self.LAYERS.items():
            if v not in self.doc.layers: self.doc.layers.new(v)

        self.beams_data = []
        self.excel_beams = []
        self.excel_cols = []

    def get_geo(self, e):
        pts = e.get_points('xy')
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        w, h = max(xs)-min(xs), max(ys)-min(ys)
        cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
        return cx, cy, w, h, min(xs), max(xs), min(ys), max(ys)

    # ========================================================
    # 🧠 المحرك الإنشائي (The Structural Engine)
    # ========================================================
    def design_section(self, span, width, is_trans):
        """
        تقوم هذه الدالة بحساب العمق المطلوب بناءً على العزم والقص
        """
        # 1. الافتراض الأولي (Start Assumption)
        depth = 0.40 # نبدأ بـ 40 سم
        min_depth_code = span / 14 # ACI min for deflection (simple)
        depth = max(depth, min_depth_code)

        # حلقة التصميم (تستمر حتى يصبح القطاع آمن)
        while True:
            # أ) تحليل الأحمال (Loads Analysis)
            # الوزن الذاتي يتغير مع تغير العمق
            self_wt = width * depth * CONST['CONC_DEN'] * 1.2 # Ultimate
            w_u = self_wt + (CONST['WALL_LOAD'] * 1.6) # Ultimate Distributed

            # ب) حساب العزم وقوى القص (Structural Analysis)
            if is_trans:
                # ميدة تحويلية (حمل موزع + حمل مركز)
                # Mu = wL^2/8 + PL/4
                Mu = (w_u * span**2 / 8) + (CONST['PLANTED_P'] * 1.4 * span / 4)
                # Vu = wL/2 + P/2
                Vu = (w_u * span / 2) + (CONST['PLANTED_P'] * 1.4 / 2)
            else:
                # ميدة عادية
                Mu = (w_u * span**2 / 8)
                Vu = (w_u * span / 2)

            # ج) التحقق من الانحناء (Flexure Check)
            # Mu <= Phi * Mn
            # نحسب أقصى عزم يتحمله القطاع الخرساني (Singly Reinforced Limit)
            # R_max تقريبي لخرسانة 30 وحديد 420 هو حوالي 5-6 MPa
            # Mn_max = R_max * b * d^2
            d = depth - 0.05 # Effective depth
            Mn_capacity = 5.0 * width * (d**2) * 1000 # kNm
            Phi_Mn = CONST['PHI_B'] * Mn_capacity

            # د) التحقق من القص (Shear Check)
            # Vc = 0.17 * sqrt(fc) * b * d
            Vc = 0.17 * math.sqrt(CONST['FC']) * (width*1000) * (d*1000) / 1000 # kN
            Phi_Vc = CONST['PHI_V'] * Vc
            # الكود يسمح بأن يتحمل الحديد القص الزائد، لكن نزيد العمق لو القص عالي جداً
            # شرط: Vu يجب ألا يتجاوز Phi*(Vc + 8*sqrt(fc)*bd) -> Limit for section size
            max_shear_capacity = 5 * Phi_Vc # فرضية أن الكانات ستتحمل الباقي

            # هـ) القرار (Decision)
            if Phi_Mn >= Mu and max_shear_capacity >= Vu:
                # القطاع آمن!
                break
            else:
                # القطاع غير آمن، زد العمق 5 سم
                depth += 0.05
                if depth > 1.5: break # سقف للأمان لكي لا يعلق الكود

        # تقريب العمق لأقرب 5 سم
        depth = math.ceil(depth * 20) / 20.0

        # حساب الحديد النهائي للقطاع المعتمد
        d = depth - 0.05
        # As = Mu / (Phi * fy * 0.9d) -- تقريب ذراع العزم j*d
        as_req = (Mu * 10**6) / (0.9 * CONST['FY'] * 0.9 * d * 1000)

        return depth, Mu, Vu, as_req

    # ----------------------------------------------------
    # معالجة الميدات (تنفيذ التصميم)
    # ----------------------------------------------------
    def process_beams(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["IN_BEAM"]}"]'
        count = 1

        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, x1, x2, y1, y2 = self.get_geo(e)

            # الأبعاد الهندسية
            width = min(w, h); span = max(w, h)
            # تجاهل الأخطاء الصغيرة
            if span < 0.5: continue

            # تصنيف النوع (لإضافة حمل المزروع)
            is_trans = (width >= 0.39) # 40 سم

            # ==============================
            # 🔥 اللحظة الحاسمة: التصميم
            # ==============================
            calc_depth, Mu, Vu, As_req = self.design_section(span, width, is_trans)

            # تحويل مساحة الحديد لعدد أسياخ
            dia = 16 if is_trans else 14
            bar_area = 201 if dia==16 else 154
            num_bars = math.ceil(As_req / bar_area)
            if num_bars < 3: num_bars = 3 # Minimum

            # تخزين البيانات
            self.beams_data.append({'box': (x1, x2, y1, y2), 'is_trans': is_trans})

            # التلوين والطبقات
            e.dxf.layer = self.LAYERS['OUT_BEAM']
            if is_trans:
                e.dxf.color = 6 # Magenta
                type_txt = "TR"
            else:
                e.dxf.color = 3 # Green
                type_txt = "B"

            # الكتابة على الرسم
            label = f"{type_txt}\n{int(width*100)}x{int(calc_depth*100)}\n{num_bars}T{dia}"
            txt_h = width * 0.30
            self.add_text(cx, cy, label, txt_h, 7)

            # إضافة للإكسل (مع القوى المحسوبة)
            self.excel_beams.append({
                'ID': f"{type_txt}-{count}",
                'Span (m)': round(span, 2),
                'Width (m)': round(width, 2),
                'Calc Depth (m)': calc_depth, # العمق المحسوب وليس المفروض!
                'Moment (kN.m)': int(Mu),
                'Shear (kN)': int(Vu),
                'Rebar': f"{num_bars} T{dia}"
            })
            count += 1

    # ----------------------------------------------------
    # معالجة الأعمدة والقواعد
    # ----------------------------------------------------
    def process_columns(self):
        query = f'LWPOLYLINE[layer=="{self.LAYERS["IN_COL"]}"]'
        count = 1

        for e in self.msp.query(query):
            if not e.is_closed: continue
            cx, cy, w, h, x1, x2, y1, y2 = self.get_geo(e)

            # كشف المزروع
            is_planted = False
            tol = 0.1
            for b in self.beams_data:
                if b['is_trans']:
                    bx1, bx2, by1, by2 = b['box']
                    if (bx1-tol < cx < bx2+tol) and (by1-tol < cy < by2+tol):
                        is_planted = True; break

            e.dxf.layer = self.LAYERS['OUT_COL']; e.dxf.color = 4

            # حساب الحمل (تراكمي للطوابق)
            # Area * Load * Floors
            pu = (w * h * 130) * CONFIG['LOAD_m2'] * CONFIG['FLOORS'] * 9.81 * 1.4

            # تسليح العمود (1% Min)
            ag = w * h * 1e6
            bars = math.ceil((0.01 * ag) / 201)
            bars = max(bars, 6)

            tag = "P" if is_planted else f"C{count}"
            self.add_text(cx, cy, f"{tag}\n{int(pu)}kN", min(w,h)*0.35, 1)

            ft_txt = "---"
            if not is_planted:
                # تصميم القاعدة (Area = P / Capacity)
                req_area = (pu/1.4) / CONFIG['SBC']
                side = math.ceil(math.sqrt(req_area)*10)/10.0
                side = max(side, 1.2)

                # سماكة القاعدة (للثقب Punching)
                # تقريب: السماكة تزيد مع الحمل
                ft_depth = 0.50 if side < 1.8 else 0.60
                if side > 2.5: ft_depth = 0.70

                # رسم القاعدة
                hw = side * 1.0 / 2 # Scale 1.0 (Meter)
                pts = [(cx-hw, cy-hw), (cx+hw, cy-hw), (cx+hw, cy+hw), (cx-hw, cy+hw)]
                self.msp.add_lwpolyline(pts, dxfattribs={'layer': self.LAYERS['OUT_FND'], 'closed':True, 'color': 2})

                self.add_text(cx, cy - side/2 - 0.2, f"F:{side}x{side}", 0.15, 7)
                ft_txt = f"{side}x{side}x{ft_depth}"

            self.excel_cols.append({
                'ID': tag,
                'Dims': f"{w:.2f}x{h:.2f}",
                'Load (kN)': int(pu),
                'Col Rebar': f"{bars} T16",
                'Footing': ft_txt
            })
            if not is_planted: count += 1

    def add_text(self, x, y, text, h, color):
        self.msp.add_mtext(text, dxfattribs={
            'insert': (x, y), 'char_height': h,
            'layer': self.LAYERS['OUT_TXT'], 'color': color, 'attachment_point': 5
        })

    def run(self):
        if not self.doc: return
        self.process_beams()
        self.process_columns()

        dxf_out = self.filepath.replace(".dxf", "_ANALYTICAL.dxf")
        xls_out = self.filepath.replace(".dxf", "_CALCS.xlsx")

        self.doc.saveas(dxf_out)
        with pd.ExcelWriter(xls_out) as writer:
            pd.DataFrame(self.excel_beams).to_excel(writer, sheet_name='Beams Analysis', index=False)
            pd.DataFrame(self.excel_cols).to_excel(writer, sheet_name='Cols & Footings', index=False)

        print(f"🎉 تم التصميم التحليلي! \n- المخطط: {dxf_out} \n- الحسابات: {xls_out}")

# إعدادات المستخدم (يمكنك تعديلها هنا)
CONFIG = {
    'FC': 30.0, 'FY': 420.0, 'SBC': 200.0,
    'FLOORS': 5, 'LOAD_m2': 1.6,
    'PLANTED_P': 450.0, 'WALL_LOAD': 12.0,
    'PHI_B': 0.9, 'PHI_V': 0.75, 'CONC_DEN': 25.0
}

if __name__ == "__main__":
    if os.path.exists("MyDrawing.dxf"):
        AnalyticalDesigner("MyDrawing.dxf").run()
    elif os.path.exists("My Drawing.dxf"):
        AnalyticalDesigner("My Drawing.dxf").run()
    else:
        print("⚠️ الملف غير موجود")
