# -*- coding: utf-8 -*-
"""生成《硬件采购决断方案》Word（一次性脚本，产物在同目录 .docx）"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

HERE = Path(__file__).parent
OUT = HERE / "会议纪要2.0-硬件采购决断方案-20260918.docx"

doc = Document()
style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)
style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def h(text, level=1):
    p = doc.add_heading(text, level=level)
    for r in p.runs:
        r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")
    return p


def para(text, bold=False, center=False, size=None):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = bold
    if size:
        r.font.size = Pt(size)
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p


def shot(fname, caption):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(str(HERE / fname), width=Cm(14.5))
    c = doc.add_paragraph()
    c.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = c.add_run(caption)
    r.font.size = Pt(9)
    r.italic = True


def table(headers, rows):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, x in enumerate(headers):
        t.rows[0].cells[i].text = x
    for row in rows:
        c = t.add_row().cells
        for i, v in enumerate(row):
            c[i].text = v
    return t


# ===== 标题 =====
t = doc.add_heading("会议纪要系统 2.0 —— 硬件采购决断方案", level=0)
for r in t.runs:
    r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")
para("（提请审定 · 审定后即可下单）", center=True, size=12)
para("日期：2026-09-18 ｜ 渠道：京东自营/官方旗舰店 ｜ 编制：项目组", center=True, size=10)

# ===== 一、决断结论 =====
h("一、决断结论")
para("经两轮调研（共考察麦克风、摄像头品类 14 款在售产品，逐款与客服核实关键参数），"
     "推荐采购以下两件，即可完整满足会议纪要的视听采集需求：")
table(["设备", "推荐型号", "规格要点", "价格", "渠道"], [
    ["全向麦克风", "飞利浦 AECS7108 两台装", "无线级联一拖二（最大可扩 3 台）、免驱即插即用、双台覆盖 20~50㎡", "¥796",
     "飞利浦办公设备旗舰店"],
    ["会议摄像机", "Tenveo 腾为 4K（3 倍光学变焦款）", "4K、3x 光学变焦、云台 355°、菜单支持 50/60Hz 抗频闪、免驱 UVC", "¥909.86",
     "Tenveo 京东自营旗舰店"],
])
para("另需附件：有源 USB 延长线一条（约 ¥100，按会场实测线长购买）。", bold=True)
para("全套合计约 ¥1,806，控制在 2~3k 预算带内并留有余量。")

# ===== 二、需求对照 =====
h("二、需求逐项对照（全部满足）")
table(["已确认需求", "本方案如何满足"], [
    ["可级联（扩展预留）", "AECS7108 两台装即级联形态，同系列最大支持 3 台；会场扩大加购单台即可"],
    ["免驱即插即用", "两款设备客服均确认免驱：麦克风「是免驱动的、即插即用」，摄像头「免驱」（见证据截图）"],
    ["录音质量专业", "飞利浦品牌桌面会议麦，双台 360° 覆盖全场，人坐任何位置都在 3 米拾音半径内"],
    ["录像 1080p 以上", "摄像头为 4K 传感器，远超 1080p 要求；光学变焦无损放大板书文字"],
    ["拍投影不滚条纹", "摄像头菜单内置 50/60Hz 抗频闪（客服确认），拍投影/大屏画面可用"],
    ["拍白板可转向", "云台 355° 旋转 + 遥控器，白板在哪转到哪"],
    ["布线可接受", "麦克风主机 USB 有线连电脑（副机无线扩展），摄像头 USB 有线，全链路仅两根线"],
    ["预算 2~3k", "全套 ¥1,806，余量 ~¥1.2k"],
])

# ===== 三、选型理由 =====
h("三、选型理由（要点）")
for line in [
    "1. 麦克风选两台装而非单台：两台级联 = 每侧参会者都在 3m 拾音半径内，双 4 阵列麦克风覆盖 20~50㎡，正好匹配会场；且「两台装」本身就是级联需求的直接落地。",
    "2. 未选同系列 AECS8109（¥2,678 单台）：其优势（8m 半径、6 台级联上限）对当前 4m 桌长的会场属于过剩能力，¥1,882 差价留着将来扩会场时再加购更有价值。",
    "3. 摄像头选 3 倍光学而非 10 倍：实测测算会场拍摄距离约 3m，2x~2.5x 即可完整取景 80~100 寸幕布；大变焦镜头光圈更小、暗光更差，3x 款 + 4K 传感器是「正好且有余」。",
    "4. 淘汰了 MAXHUB SC51S（¥1,249）：客服确认无 50/60Hz 抗频闪，拍投影有滚条纹风险——抗频闪是本项目硬门槛。",
    "5. 全链路免驱 USB：两款设备对电脑即插即用，会场电脑无需安装任何软件，打开系统网页即可开始推流。",
]:
    para(line)

# ===== 四、证据 =====
h("四、调研证据（页面与客服实拍）")
shot("ev_飞利浦家族对比表.png",
     "图 1 ｜ 飞利浦全向麦家族对比表：AECS7108（3m 半径/4 阵列/无线级联最大 3 台）与 AECS8109（8m/6 阵列/USB 级联 6 台）的规格差异与差价依据。")
shot("ev_AECS7108两台装页面.png",
     "图 2 ｜ 决断型号购买页：AECS7108 两台装（20~50㎡ 无线级联），¥796，飞利浦办公设备旗舰店。")
shot("ev_飞利浦客服对话.png",
     "图 3 ｜ 客服确认：免驱动、即插即用。")
shot("ev_Tenveo摄像头页面.png",
     "图 4 ｜ 决断型号购买页：Tenveo 4K 3 倍光学变焦款，¥909.86，京东自营（200+ 评价 100% 好评，7 天无理由）。")
shot("ev_Tenveo客服对话.png",
     "图 5 ｜ 客服确认：免驱；摄像头菜单可按现场调整、支持抗频闪（拍投影场景的关键门槛）。")

# ===== 五、部署与验收 =====
h("五、部署条件与到货验收")
para("部署条件：开会时场内提供一台可插网线的电脑，打开系统网页、授权麦克风与摄像头即可开始，无需专人值守。")
para("到货验收（两项均通过后定型）：")
for line in [
    "· 麦克风：双台级联在系统中枚举为单一音频设备；3m 距离实听底噪与人声清晰度；接入系统重放测试确认识别率无劣化。",
    "· 摄像头：实拍投影画面验证抗频闪无条纹；自动对焦稳定性；3m 距离拍屏/白板，AI 可读 14pt 以上文字。",
    "· 任一项不达标：7 天无理由退货换备选（摄像头备选：海康自营云台款；麦克风备选：海康 DS-65VA300U/飞利浦 AECS8109）。",
]:
    para(line)

# ===== 六、待确认 =====
h("六、提请审定事项")
for line in [
    "1. 本方案两件设备是否同意采购？（同意即可按上述渠道下单）",
    "2. 采购流程：学校统一采购 / 指定平台 / 自行垫付报销？是否需要发票与政采渠道？",
    "3. 会场电脑与网线的落实时间（随设备到货同步准备）。",
]:
    para(line)

doc.save(str(OUT))
print("saved:", OUT)
