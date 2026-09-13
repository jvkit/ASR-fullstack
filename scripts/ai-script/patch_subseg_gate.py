"""修复2：子段切分的误切抑制（用户抓到纯单人句被切成三人）。

机制（diag_false_split.py 实证）：
1. 整句合并 embedding 已高置信（001-M 0.442/margin 0.15）时不该拆，但没有预检
2. 滑窗个别窗 margin 0.000~0.006 的纯抖动被当作换人点（005-F/006-F 误切）

修复：
1. 整句预检：合并 argmax 的 sim>=name_threshold 且 margin>=merge_margin(0.10)
   -> 判单人句，直接整句命名不拆
2. 换人门限：新标签需 sim>=name_threshold 且 margin>=change_margin(0.04)
   且下一窗同标签（持续 0.6s+），否则维持前标签
"""
from pathlib import Path

P = Path('/home/bo1/jvkit/asr/diarize_svc/service.py')
s = P.read_text()

# 1) 签名加 merge_margin / change_margin
old = '''sub_min_dur: float = Form(default=0.5)):'''
new = '''sub_min_dur: float = Form(default=0.5), merge_margin: float = Form(default=0.10), change_margin: float = Form(default=0.04)):'''
assert old in s, 'sig anchor'
s = s.replace(old, new)

# 2) _embed 返回 margin
old = '''            sims = {n: float(np.dot(e, refs[n])) for n in names}
            best = max(sims, key=sims.get)
            return best, sims[best]'''
new = '''            sims = {n: float(np.dot(e, refs[n])) for n in names}
            best = max(sims, key=sims.get)
            runner = max((v for k, v in sims.items() if k != best), default=0.0)
            return best, sims[best], sims[best] - runner'''
assert old in s, 'embed anchor'
s = s.replace(old, new)

# 3) 主循环：整句预检 + _embed 三元组适配 + 换人门限
old = '''        out_subs: list[list[tuple]] = []  # per sent: [(a,b,name|None)]
        results = []  # per sent 首标签（供跨句匿名平滑）
        for a, b in sent_list:
            a = max(0.0, float(a))
            b = float(b)
            if not refs or b - a < max(sub_min, 0.8):
                r = _embed(a, b)
                if r is None:
                    out_subs.append([(a, b, None)])
                    results.append((None, 0.0))
                else:
                    best, sim = r
                    name = best if sim >= name_threshold else None
                    out_subs.append([(a, b, name)])
                    results.append((name, sim))
            else:
                subs = _subsegs(a, b)
                out_subs.append(subs)
                first = next((x[2] for x in subs if x[2]), None)
                results.append((first, 1.0 if first else 0.0))'''
new = '''        def _name_of(best: str, sim: float, margin: float) -> str | None:
            return best if (sim >= name_threshold and margin >= change_margin) else None

        out_subs: list[list[tuple]] = []  # per sent: [(a,b,name|None)]
        results = []  # per sent 首标签（供跨句匿名平滑）
        for a, b in sent_list:
            a = max(0.0, float(a))
            b = float(b)
            r = _embed(a, b) if refs else None
            if r is None:
                out_subs.append([(a, b, None)])
                results.append((None, 0.0))
                continue
            merged_best, merged_sim, merged_margin = r
            # 整句预检：合并 embedding 高置信 -> 单人句，不拆
            if (merged_sim >= name_threshold and merged_margin >= merge_margin) \\
                    or b - a < max(sub_min, 0.8):
                name = merged_best if merged_sim >= name_threshold else None
                out_subs.append([(a, b, name)])
                results.append((name, merged_sim))
            else:
                subs = _subsegs(a, b)
                out_subs.append(subs)
                first = next((x[2] for x in subs if x[2]), None)
                results.append((first, 1.0 if first else 0.0))'''
assert old in s, 'main anchor'
s = s.replace(old, new)

# 4) _subsegs：窗内带 margin，换人需 margin 门限 + 下一窗持续
old = '''            wins = []  # [t0, t1, name|None]
            t = a
            while t < b - 0.05:
                t1 = min(t + sub_win, b)
                if t1 - t < 0.3:
                    break
                r = _embed(t, t1)
                if r is None:
                    break
                best, sim = r
                wins.append([t, t1, best if sim >= name_threshold else None])
                t += sub_shift'''
new = '''            wins = []  # [t0, t1, name|None]
            t = a
            while t < b - 0.05:
                t1 = min(t + sub_win, b)
                if t1 - t < 0.3:
                    break
                r = _embed(t, t1)
                if r is None:
                    break
                best, sim, margin = r
                wins.append([t, t1, _name_of(best, sim, margin)])
                t += sub_shift'''
assert old in s, 'wins anchor'
s = s.replace(old, new)

# 5) None 填充后加换人门限回扫：单窗低置信跳变回退为前一标签
old = '''            for i in range(len(wins) - 2, -1, -1):
                if wins[i][2] is None:
                    wins[i][2] = wins[i + 1][2]'''
new = '''            for i in range(len(wins) - 2, -1, -1):
                if wins[i][2] is None:
                    wins[i][2] = wins[i + 1][2]
            # 换人门限：某窗标签与前一窗不同，且次窗不同标签 -> 视为抖动，回退前标签
            for i in range(1, len(wins) - 1):
                if wins[i][2] != wins[i - 1][2] and wins[i][2] != wins[i + 1][2]:
                    wins[i][2] = wins[i - 1][2]'''
assert old in s, 'gate anchor'
s = s.replace(old, new)

P.write_text(s)
print('patched OK: merge pre-check + change-point gating')
