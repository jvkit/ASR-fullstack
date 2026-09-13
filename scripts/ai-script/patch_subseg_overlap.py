"""修复：/diarize_sent 子段切分的重叠与碎段问题。

问题（用户抓到「逆天错误」）：
1. 滑窗 0.6s/0.3s 相邻窗重叠 0.3s，换标签时新子段取新窗起点、旧子段延到
   旧窗终点 -> 每个换人点产生 0.3s 重叠区。121 子段中 68 段互相重叠，
   文字按字级时间戳分配时被撕碎。
2. 个别 0.6s 抖动窗产生独立碎段（夹在两人中间的第三标签）。

修复：
1. 相邻子段边界 = 换人两侧窗口的重叠区中点 (prev_last_win_end +
   next_first_win_start) / 2，全程无重叠、无遗漏。
2. 碎段修剪：短于 sub_min_dur(0.5s) 的子段并入更长邻居。
"""
from pathlib import Path

P = Path('/home/bo1/jvkit/asr/diarize_svc/service.py')
s = P.read_text()

# 1) 签名加 sub_min_dur
old = '''sub_min: float = Form(default=2.5), sub_win: float = Form(default=0.6), sub_shift: float = Form(default=0.3)):'''
new = '''sub_min: float = Form(default=2.5), sub_win: float = Form(default=0.6), sub_shift: float = Form(default=0.3), sub_min_dur: float = Form(default=0.5)):'''
assert old in s, 'sig anchor'
s = s.replace(old, new)

# 2) 重写 _subsegs 的填充后处理：重叠中点边界 + 碎段修剪
old = '''            wins[-1][1] = b  # 尾窗补齐
            # None 窗邻居填充（句内）
            for i in range(len(wins)):
                if wins[i][2] is None:
                    wins[i][2] = wins[i - 1][2] if i > 0 else None
            for i in range(len(wins) - 2, -1, -1):
                if wins[i][2] is None:
                    wins[i][2] = wins[i + 1][2]
            # 连续同标签合并
            subs = []
            for w in wins:
                if subs and subs[-1][2] == w[2]:
                    subs[-1][1] = w[1]
                else:
                    subs.append([w[0], w[1], w[2]])
            subs[0][0] = a
            return [(x[0], x[1], x[2]) for x in subs]'''
new = '''            wins[-1][1] = b  # 尾窗补齐
            # None 窗邻居填充（句内）
            for i in range(len(wins)):
                if wins[i][2] is None:
                    wins[i][2] = wins[i - 1][2] if i > 0 else None
            for i in range(len(wins) - 2, -1, -1):
                if wins[i][2] is None:
                    wins[i][2] = wins[i + 1][2]
            # 连续同标签合并；每子段记录 [首窗起点, 末窗终点, 标签, 首窗终点, 末窗起点]
            subs = []
            for w in wins:
                if subs and subs[-1][2] == w[2]:
                    subs[-1][1] = w[1]   # 末窗终点更新
                    subs[-1][4] = w[0]   # 末窗起点更新
                else:
                    subs.append([w[0], w[1], w[2], w[1], w[0]])
            # 碎段修剪：短于 sub_min_dur 的并入更长邻居（首尾只并入唯一邻居）
            guard = 0
            while len(subs) > 1 and guard < 100:
                guard += 1
                idx = next((i for i, x in enumerate(subs)
                            if x[1] - x[0] < sub_min_dur), None)
                if idx is None:
                    break
                if idx == 0:
                    tgt = 1
                elif idx == len(subs) - 1:
                    tgt = idx - 1
                else:
                    prev_d = subs[idx][0] - subs[idx - 1][0]
                    next_d = subs[idx + 1][1] - subs[idx + 1][0]
                    tgt = idx - 1 if prev_d >= next_d else idx + 1
                if tgt < idx:
                    subs[tgt][1] = subs[idx][1]
                    subs[tgt][4] = subs[idx][4]
                else:
                    subs[tgt][0] = subs[idx][0]
                    subs[tgt][3] = subs[idx][3]
                del subs[idx]
            # 边界 = 换人两侧窗口重叠区中点：
            #   prev 末窗 [.., prev_end] 与 next 首窗 [next_start, ..] 重叠
            #   中点 = (prev_end + next_start)/2，两侧共享同一边界值
            out = []
            for i, x in enumerate(subs):
                x0 = a if i == 0 else (subs[i - 1][1] + x[0]) / 2
                x1 = b if i == len(subs) - 1 else (x[1] + subs[i + 1][0]) / 2
                out.append((x0, x1, x[2]))
            return out'''
assert old in s, 'merge anchor'
s = s.replace(old, new)

P.write_text(s)
print('patched OK: overlap midpoint + tiny-seg pruning')
