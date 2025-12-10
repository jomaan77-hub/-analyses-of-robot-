import ezdxf
import math
import os

# === إعدادات المشروع ===
CONFIG = {
    'FC': 30.0, 'FY': 420.0, 'SBC': 200.0,
    'FLOORS': 3, 'LOAD_m2': 1.6,
    'WALL_LOAD_M': 12.0, 'PLANTED_COL_LOAD': 450.0,
    'TRANSFER_WIDTH_LIMIT': 0.40,

    # الطبقات
    'L_COL_IN':   'S-COL-CONC',
    'L_BEAM_IN':  'S-BEAM-MAIN',

    # المخرجات
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
            print("✅ تم التحميل. جاري التحليل الإنشائي لتوجيه الأعمدة...")
        except Exception as e:
            print(f"Error loading file: {e}")
            self.doc = None
            return

        for l in [CONFIG['L_OUT_COL'], CONFIG['L_OUT_FND'], CONFIG['L_OUT_BEAM'], CONFIG['L_TXT']]:
            if l not in self.doc.layers: self.doc.layers.new(l)

        self.beams_data = [] # لتخزين بيانات الميدات
        self.cols_optimized = [] # لتخزين الأعمدة بعد تعديل اتجاهها

    def get_bbox(self, entity):
        pts = entity.get_points('xy')
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return sum(xs)/len(xs), sum(ys)/len(ys), max(xs)-min(xs), max(ys)-min(ys)

    # 1. تحليل شبكة الميدات (Beam Network Analysis)
    def analyze_beams_network(self):
        print("🔍 تحليل شبكة الميدات لتحديد البحور الطويلة...")
        query = f'LWPOLYLINE[layer=="{CONFIG["L_BEAM_IN"]}"]'

        for entity in self.msp.query(query):
            if not entity.is_closed: continue
            cx, cy, raw_w, raw_h = self.get_bbox(entity)

            # تحديد: هل هذه الميدة أفقية أم رأسية؟ وما هو طولها؟
            if raw_w > raw_h:
                orient = 'H' # Horizontal Beam
                span = raw_w
                width = raw_h
            else:
                orient = 'V' # Vertical Beam
                span = raw_h
                width = raw_w

            # تصحيح الوحدات
            if span > 10: span/=1000; width/=1000; cx/=1000; cy/=1000 # تحويل مؤقت للحسابات

            is_transfer = (width >= CONFIG['TRANSFER_WIDTH_LIMIT'])

            self.beams_data.append({
                'center': (cx, cy),
                'span': span,
                'width': width,
                'orient': orient,
                'is_transfer': is_transfer,
                'entity': entity # نحتفظ بالرسم الأصلي
            })

    # 2. الخوارزمية الذكية لتوجيه الأعمدة (Smart Orientation Algorithm)
    def optimize_columns(self):
        print("🧠 جاري اتخاذ القرار لتوجيه ضرب الأعمدة...")
        query = f'LWPOLYLINE[layer=="{CONFIG["L_COL_IN"]}"]'

        for entity in self.msp.query(query):
            if not entity.is_closed: continue
            cx, cy, raw_w, raw_h = self.get_bbox(entity)

            # أبعاد العمود (بغض النظر عن اتجاهه الحالي)
            col_short = min(raw_w, raw_h)
            col_long = max(raw_w, raw_h)

            # --- التحليل الإنشائي: ماذا يحيط بالعمود؟ ---
            max_span_H = 0.0 # أقصى بحر أفقي متصل
            max_span_V = 0.0 # أقصى بحر رأسي متصل

            # نبحث عن الميدات التي "تلمس" أو تقترب من هذا العمود
            # نحول إحداثيات العمود للمتر للمقارنة
            ccx, ccy = (cx/1000, cy/1000) if cx > 1000 else (cx, cy)

            is_planted = False

            for beam in self.beams_data:
                bx, by = beam['center']
                # المسافة بين مركز العمود ومركز الميدة
                dist_x = abs(ccx - bx)
                dist_y = abs(ccy - by)

                # هل العمود يحمل هذه الميدة؟ (نقطة اتصال)
                # شرط الاتصال: المسافة تكون نصف طول الميدة تقريباً
                is_connected = False
                if beam['orient'] == 'H' and dist_y < 0.5 and dist_x < (beam['span']/2 + 0.5):
                    max_span_H = max(max_span_H, beam['span'])
                    is_connected = True
                elif beam['orient'] == 'V' and dist_x < 0.5 and dist_y < (beam['span']/2 + 0.5):
                    max_span_V = max(max_span_V, beam['span'])
                    is_connected = True

                # هل هو مزروع؟ (يقع في منتصف ميدة تحويلية)
                if beam['is_transfer'] and dist_x < 0.5 and dist_y < 0.5: # قريب جداً من المركز
                     is_planted = True

            # --- قرار التوجيه (Decision Making) ---
            if is_planted:
                # العمود المزروع يتبع اتجاه الميدة التي تحمله
                # (سنتركه كما رسمته أنت، أو نوجهه مع الميدة)
                final_w, final_h = raw_w, raw_h # نحافظ عليه كما هو للأمان
                col_type = "Planted"
                color = 4

            elif max_span_H > max_span_V:
                # البحر الأفقي هو الأكبر -> وجه العمود أفقياً لتقصير البحر
                final_w, final_h = col_long, col_short
                col_type = "C (Opt-H)"
                color = 130 # لون مميز

            elif max_span_V > max_span_H:
                # البحر الرأسي هو الأكبر -> وجه العمود رأسياً
                final_w, final_h = col_short, col_long
                col_type = "C (Opt-V)"
                color = 130

            else:
                # متعادل أو لا توجد ميدات (عمود منفصل) -> اترك كما رسم
                final_w, final_h = raw_w, raw_h
                col_type = "C"
                color = 4

            # --- الحسابات والتخزين ---
            pu = (col_long/1000 * col_short/1000 * 130) * CONFIG['LOAD_m2'] * CONFIG['FLOORS'] * 9.81 * 1.4
            if cx > 1000: pu *= 1000*1000 # تصحيح إذا حدث خطأ وحدات

            self.cols_optimized.append({
                'center': (cx, cy),
                'dim': (final_w, final_h),
                'load': pu,
                'is_planted': is_planted,
                'type': col_type,
                'color': color
            })

    # 3. مرحلة الرسم النهائي (Execution Phase)
    def draw_results(self):
        print("✍️ رسم المخطط النهائي المحسن...")

        # أ) رسم الأعمدة الموجهة والقواعد
        for col in self.cols_optimized:
            cx, cy = col['center']
            w, h = col['dim']

            # رسم العمود الجديد
            self.draw_rect(cx, cy, w, h, CONFIG['L_OUT_COL'], col['color'])
            self.add_text(cx, cy, f"{col['type']}\n{int(col['load'])}kN", 0.15)

            # رسم القاعدة (لغير المزروع)
            if not col['is_planted']:
                p_srv = col['load'] / 1.4
                req_area = p_srv / CONFIG['SBC']
                side = math.sqrt(req_area)
                side = max(side, 1.2); side = math.ceil(side*10)/10.0
                depth = 0.60

                # رسم القاعدة
                self.draw_rect(cx, cy, side, side, CONFIG['L_OUT_FND'], 2)
                self.add_text(cx, cy-0.8, f"F:{side}x{side}", 0.20)

        # ب) رسم الميدات (نفس الميدات الأصلية مع التلوين)
        for beam in self.beams_data:
            # هنا سنعيد رسم مستطيل الميدة الأصلي في الطبقة الجديدة
            # (للتبسيط سنرسم مستطيلاً جديداً بنفس الأبعاد)
            cx, cy = beam['center']

            # نحتاج الأبعاد الأصلية بالمليمتر إذا كان الملف ملم
            span_draw = beam['span'] * 1000 if cx > 1000 else beam['span']
            width_draw = beam['width'] * 1000 if cx > 1000 else beam['width']

            # تحديد الأبعاد (w, h) بناء على الاتجاه
            if beam['orient'] == 'H': w, h = span_draw, width_draw
            else: w, h = width_draw, span_draw

            layer = CONFIG['L_OUT_BEAM']
            color = 6 if beam['is_transfer'] else 3
            type_txt = "TRANSFER" if beam['is_transfer'] else "BM"

            self.draw_rect(cx, cy, w, h, layer, color)
            self.add_text(cx, cy, f"{type_txt}", 0.15)

    def draw_rect(self, cx, cy, w, h, lay, col):
        hw, hh = w/2, h/2
        pts = [(cx-hw, cy-hh), (cx+hw, cy-hh), (cx+hw, cy+hh), (cx-hw, cy+hh)]
        self.msp.add_lwpolyline(pts, dxfattribs={'layer': lay, 'closed': True, 'color': col})

    def add_text(self, cx, cy, txt, h):
        if cx > 1000: h *= 1000
        self.msp.add_mtext(txt, dxfattribs={
            'insert': (cx, cy), 'char_height': h,
            'layer': CONFIG['L_TXT'], 'color': 7, 'attachment_point': 5
        })

    def run(self):
        if self.doc is None: return
        self.analyze_beams_network()
        self.optimize_columns()
        self.draw_results()
        self.doc.saveas(self.filepath.replace(".dxf", "_OPTIMIZED.dxf"))
        print("Done.")

if __name__ == "__main__":
    if os.path.exists("My Drawing.dxf"):
        StructuralProject("My Drawing.dxf").run()
    elif os.path.exists("MyDrawing.dxf"):
        StructuralProject("MyDrawing.dxf").run()
    else:
        print("File 'My Drawing.dxf' or 'MyDrawing.dxf' not found.")
