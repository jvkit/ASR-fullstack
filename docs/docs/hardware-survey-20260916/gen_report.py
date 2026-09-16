# -*- coding: utf-8 -*-
"""生成《会议纪要系统2.0-硬件调研报告》Word（一次性脚本，产物在同目录 .docx）"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

HERE = Path(__file__).parent
OUT = HERE / "会议纪要2.0-硬件调研报告-20260916.docx"

doc = Document()

# 全局中文字体
style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)
style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def h(text, level=1):
    p = doc.add_heading(text, level=level)
    for r in p.runs:
        rPr = r._element.get_or_add_rPr()
        rPr.get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")
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


# ============ 标题 ============
t = doc.add_heading("会议纪要系统 2.0 —— 硬件设备调研报告", level=0)
for r in t.runs:
    r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")
para("（麦克风 / 摄像头选型 · 采购前调研）", center=True, size=12)
para("调研日期：2026-09-16 ｜ 调研渠道：京东自营 / 品牌官方旗舰店 ｜ 调研人：项目组", center=True, size=10)

# ============ 一、结论摘要 ============
h("一、调研结论摘要")
para("本轮调研覆盖会议全向麦克风、视频会议摄像头两个品类共 8 款在售产品（均有京东页面实拍记录，见第三节），"
     "结论如下：")
for line in [
    "1. 全链路采用有线 USB 方案：麦克风与摄像头均为免驱 USB 设备，连接会场电脑即可工作，无电池管理、无断连风险，成本最低。",
    "2. 麦克风拟选两款到货对比（总价差约 450 元，留优退次）：海康威视 DS-65VA300U（¥349.68，性价比款）与 Jabra Speak 510 UC（¥794.43，音质标杆款）。",
    "3. 摄像头拟选 MAXHUB SC51S（¥1229，1080p、10 倍光学变焦、云台遥控、无麦克风），从自营/旗舰店渠道购买。",
    "4. 全套预算约 2.2~2.4k，控制在 2~3k 预算带内，预留约 600 元余量。",
    "5. 是否采用音视频一体化设备（一台机器同时完成拾音与拍摄）作为备选路线，已在第五节列出利弊，提请会上讨论。",
]:
    para(line)

# ============ 二、需求基线 ============
h("二、需求基线（已确认）")
tbl = doc.add_table(rows=1, cols=2)
tbl.style = "Light Grid Accent 1"
hdr = tbl.rows[0].cells
hdr[0].text, hdr[1].text = "项目", "确认结论"
rows = [
    ("发言人是否扩音", "不扩音、不外放 —— 无需为回声消除（AEC）付费"),
    ("会场规模", "一个全向专业麦即可覆盖"),
    ("环境噪声", "不大，降噪不作为核心指标"),
    ("布线", "可接受有线（无线作为可选项评估后未采用，理由见第四节）"),
    ("预算", "全套 2~3k 基准、可上浮；加钱必须听得出来/看得出来"),
    ("录音质量", "要稍微专业一点 —— 麦克风为核心投入项"),
    ("录像质量", "必须 1080p 及以上，需光学变焦与云台"),
    ("部署条件", "会场提供一台可插网线的电脑，开会时打开系统网页推流"),
]
for a, b in rows:
    c = tbl.add_row().cells
    c[0].text, c[1].text = a, b

# ============ 三、调研实录 ============
h("三、调研实录（京东页面实拍）")
para("以下为本轮调研的 8 款候选产品页面实拍，按调研时间顺序排列。")

shot("f_1d6999de-e898-42ca-8f5f-14e77b2c8523.png",
     "图 1 ｜ 候选①：生华视通 SH-M04U 有线全向麦（¥643，自营）。参数达标（16k 采样、5m 半径），"
     "但品牌力弱于同价位大厂产品，列为备选，未进主推。")
shot("f_d2d2e57b-54ae-49f1-9bfd-afa41260c257.png",
     "图 2 ｜ 主推①：海康威视 DS-65VA300U 有线全向麦（¥349.68，自营，2000+ 评价）。"
     "安防头部品牌、USB 免驱、4m 半径、16kHz 采样，价格仅为同档一半。")
shot("f_16e60fce-a007-4b0a-b000-cfc758eaa291.png",
     "图 3 ｜ 无线备选：海康威视 DS-UAC-VS3（¥947，自营）。2.4G 接收器方案（非裸蓝牙）、6 麦克风阵列、5m 半径，"
     "仅当确认需要桌面无线时启用。")
shot("f_bdfa5010-e8fe-4baf-b535-6290f2abdf9b.png",
     "图 4 ｜ 排除样本：润普蓝牙级联全向麦（¥1097）。核心拾音指标（信噪比 65dB、3~5m）与 ¥350 档持平，"
     "价差主要来自蓝牙、级联、内置电池、回声消除等我们已明确不需要的功能，排除。")
shot("f_bf5c454e-c742-4a22-b145-1ae2bf0eb8c8.png",
     "图 5 ｜ 品类生态实拍（音络 INNOTRIK 品牌搜索结果）：同档位全向麦 SKU 众多，¥339~¥2812 价差"
     "几乎全部来自无线/蓝牙/级联等功能差异而非拾音质量差异 —— 印证「该品类贵≠好，需实测听感」的判断。")
shot("f_c4ad353f-52ae-44e9-827c-28bcc09cd355.png",
     "图 6 ｜ 主推②：Jabra Speak 510 UC 全向麦（¥794，自营，2000+ 评价）。会议全向麦品类的国际标杆品牌，"
     "麦克风单元品质与 DSP 调音为音质保障，与图 2 到货后同条件盲听对比，留优退次。")
shot("f_a9890d54-53d5-4c92-aab8-21d3eb913d9d.png",
     "图 7 ｜ 渠道调研：罗技 BCC950 搜索结果页。该型号为 2012 年老款，自营基本无货，页面多为二手/第三方店铺"
     "（¥386~¥2148 价差悬殊、翻新风险），故放弃该型号并转向「会议摄像机」品类（默认不含麦克风、货源规范）。")
shot("f_4759b3bf-f398-4afa-bfec-43f881ac5d82.png",
     "图 8 ｜ 主推：MAXHUB SC51S 会议摄像机（¥1229，10 倍光学变焦、云台遥控、USB 免驱）。"
     "其拾音麦为 BM 系列另购件，证明单机无麦克风；本页面为第三方专卖店且标注「使用后不支持 7 天无理由」，"
     "实际购买将切换至自营/官方旗舰店链接。")

# ============ 四、议题一：有线/无线 ============
h("四、讨论议题一：无线还是有线（本轮结论：全链路有线）")
para("老师提出倾向无线。评估过程：")
for line in [
    "· 蓝牙方案排除：蓝牙麦克风在通话时会切换到通话档（8~16kHz 窄带），音质明显下降，不满足「录音要稍微专业」的要求。",
    "· 无线唯一可用形态为 2.4G USB 接收器（图 3），但比有线款贵约 600 元，且引入电池续航管理（实测续航 6.5~8 小时，全天会议需中途充电）。",
    "· 摄像头侧，无线化（2.4G 图传/WiFi 摄像机/无线 HDMI）要么带宽不足、要么需额外开发 2~4 天、要么成本翻倍，均不划算。",
    "· 结论：麦克风随摄像头一并有线，全链路仅一根 USB 线（距离超 5 米时用一条百元级有源延长线），最简单、最稳、最便宜。",
]:
    para(line)

# ============ 五、议题二：一体化 ============
h("五、讨论议题二：是否采用音视频一体化设备（提请讨论）")
para("调研中注意到市场上存在「摄像头+麦克风一体机」（如会议摄像头一体机、音络/罗技 CC4000e 等）。"
     "结合我方会场实际情况（设备置于会议桌中部，可同时拍到投影 PPT 与白板，拾音位置与拍摄位置基本重合），"
     "一体化在该布局下并非不可行。利弊对比如下：")
tbl2 = doc.add_table(rows=1, cols=3)
tbl2.style = "Light Grid Accent 1"
h2 = tbl2.rows[0].cells
h2[0].text, h2[1].text, h2[2].text = "对比项", "分立方案（拟采购）", "一体化设备"
t2rows = [
    ("预算内可获得配置", "¥350~794 专业全向麦 + ¥1229 光学变焦云台摄像机", "2~3k 档一体机多为定焦广角、拾音为通话级，两项均妥协"),
    ("要达到同等品质", "全套约 2.2~2.4k", "需 3~4k 档（如罗技 CC4000e 级）才可兼顾变焦与拾音"),
    ("走线/操作", "两条 USB 线（可合并走线）", "一条 USB 线，更简洁"),
    ("升级与维护", "麦克风、摄像头可分别升级更换", "任一不满意需整机更换"),
    ("验收风险", "可分别实测听感与画质，单项退換", "捆绑验收，退换成本高"),
]
for a, b, c in t2rows:
    r = tbl2.add_row().cells
    r[0].text, r[1].text, r[2].text = a, b, c
para("项目组倾向：仍推荐分立方案 —— 在预算带内，分立能用每台设备的预算买到该品类里更好的单品；"
     "但若会上认为「单线极简」价值更大且预算可上浮至 3~4k，一体化路线可行，需重新按一体化标准选型。", bold=True)

# ============ 六、拟采购清单 ============
h("六、拟采购清单与预算")
tbl3 = doc.add_table(rows=1, cols=4)
tbl3.style = "Light Grid Accent 1"
h3 = tbl3.rows[0].cells
h3[0].text, h3[1].text, h3[2].text, h3[3].text = "序号", "设备", "型号 / 渠道", "参考价"
t3rows = [
    ("1", "全向麦（对比留一）", "海康威视 DS-65VA300U，京东自营", "¥349.68"),
    ("2", "全向麦（对比留一）", "Jabra Speak 510 UC，京东自营", "¥794.43"),
    ("3", "会议摄像机", "MAXHUB SC51S（10x 光变/云台），自营或官方旗舰店", "¥1,229"),
    ("4", "附件", "有源 USB 延长线（按会场实测线长）", "约 ¥100"),
]
for row in t3rows:
    c = tbl3.add_row().cells
    for i, v in enumerate(row):
        c[i].text = v
para("合计约 2.4k（麦克风两台对比、留一退一后实际约 2.0~2.4k），处于预算带内并留有余量。")
para("到货验收项：麦克风底噪/远场听感盲听对比（A/B 留一）；摄像头抗频闪设置、自动对焦稳定性、"
     "3~8 米实拍投影与白板、文字可读性实测。两项均达标后定型。", bold=False)

# ============ 七、待确认 ============
h("七、待确认事项")
for line in [
    "1. 采购流程：学校统一采购 / 指定平台 / 自行垫付报销？是否需要发票与政采渠道？周期约多久？（影响下单渠道与到货时间）",
    "2. 一体化 vs 分立的最终取舍（见第五节，会上讨论）。",
    "3. 会场电脑与网线的落实（部署条件，随采购同步准备）。",
]:
    para(line)

doc.save(str(OUT))
print("saved:", OUT)
