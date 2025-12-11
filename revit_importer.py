import clr
import sys
import math

# تحميل مكتبات الريفت الأساسية
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *

clr.AddReference("RevitServices")
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

# الحصول على المستند الحالي
doc = DocumentManager.Instance.CurrentDBDocument

# المدخلات: مسارات الملفات
col_csv_path = IN[0]
beam_csv_path = IN[1]

# دالة تحويل من متر (CSV) إلى قدم (Revit Internal)
def m2ft(val):
    return float(val) * 3.2808399

# دالة لقراءة البيانات من CSV
def read_csv(path):
    data = []
    with open(path, 'r') as f:
        lines = f.readlines()
        headers = lines[0].strip().split(',')
        for line in lines[1:]:
            parts = line.strip().split(',')
            if len(parts) > 1:
                item = {}
                for i, h in enumerate(headers):
                    item[h] = parts[i]
                data.append(item)
    return data

# دالة لإنشاء أو إيجاد نوع (Type) بالمقاس المطلوب
def get_or_create_type(family_name, type_name, w_m, d_m, is_col=True):
    # البحث عن الفاميلي
    collector = FilteredElementCollector(doc).OfClass(FamilySymbol)
    fam_symbol = None

    # محاولة إيجاد أي نوع من نفس العائلة أولاً
    for sym in collector:
        if sym.FamilyName == family_name:
            fam_symbol = sym
            break

    if not fam_symbol:
        return None # العائلة غير محملة في المشروع

    # تفعيل الرمز
    if not fam_symbol.IsActive: fam_symbol.Activate()

    # هل النوع موجود مسبقاً؟
    type_symbol = None
    all_types = fam_symbol.Family.GetFamilySymbolIds()
    for tid in all_types:
        sym = doc.GetElement(tid)
        if sym.Name == type_name:
            type_symbol = sym
            break

    # إذا لم يكن موجوداً، قم بمضاعفته (Duplicate) وإنشائه
    if not type_symbol:
        type_symbol = fam_symbol.Duplicate(type_name)

        # ضبط الأبعاد (b, h)
        # أسماء الباراميترات قد تختلف (b/h أو Width/Depth)
        # نحاول تعيين كليهما للأمان
        p_w = type_symbol.LookupParameter("b")
        if not p_w: p_w = type_symbol.LookupParameter("Width")

        p_d = type_symbol.LookupParameter("h")
        if not p_d: p_d = type_symbol.LookupParameter("Depth")

        if p_w: p_w.Set(m2ft(w_m))
        if p_d: p_d.Set(m2ft(d_m))

    return type_symbol

# =================================================
# بدء التنفيذ داخل الريفت (Transaction)
# =================================================
TransactionManager.Instance.EnsureInTransaction(doc)

report = []
# أسماء العائلات (تأكد أن هذه الأسماء موجودة في مشروعك أو غيرها هنا)
# عادة في القوالب المترية تكون M_Concrete...
COL_FAM_NAME = "M_Concrete-Rectangular-Column"
BEAM_FAM_NAME = "M_Concrete-Rectangular Beam"

# 1. إنشاء الأعمدة
try:
    cols_data = read_csv(col_csv_path)
    level_1 = FilteredElementCollector(doc).OfClass(Level).FirstElement() # يفترض وجود Level 1

    for row in cols_data:
        # قراءة البيانات
        w = float(row['Width'])
        d = float(row['Depth'])
        x = m2ft(row['X'])
        y = m2ft(row['Y'])
        type_n = row['FamilyType']

        # تجهيز النوع
        sym = get_or_create_type(COL_FAM_NAME, type_n, w, d, True)

        if sym:
            if not sym.IsActive: sym.Activate()
            pt = XYZ(x, y, 0)
            doc.Create.NewFamilyInstance(pt, sym, level_1, Autodesk.Revit.DB.Structure.StructuralType.Column)
        else:
            report.append(f"Missing Family: {COL_FAM_NAME}")
            break

    report.append(f"Created {len(cols_data)} Columns.")

except Exception as e:
    report.append(f"Col Error: {str(e)}")

# 2. إنشاء الميدات
try:
    beams_data = read_csv(beam_csv_path)

    for row in beams_data:
        w = float(row['Width'])
        # العمق الافتراضي 60 سم إذا لم يكن موجوداً
        d = 0.60
        x1, y1 = m2ft(row['StartX']), m2ft(row['StartY'])
        x2, y2 = m2ft(row['EndX']), m2ft(row['EndY'])
        type_n = row['FamilyType']

        sym = get_or_create_type(BEAM_FAM_NAME, type_n, w, d, False)

        if sym:
            if not sym.IsActive: sym.Activate()
            p1 = XYZ(x1, y1, 0)
            p2 = XYZ(x2, y2, 0)
            if p1.IsAlmostEqualTo(p2): continue # تجنب الخطوط الصفرية

            line = Line.CreateBound(p1, p2)
            doc.Create.NewFamilyInstance(line, sym, level_1, Autodesk.Revit.DB.Structure.StructuralType.Beam)

    report.append(f"Created {len(beams_data)} Beams.")

except Exception as e:
    report.append(f"Beam Error: {str(e)}")

TransactionManager.Instance.TransactionTaskDone()

OUT = report
