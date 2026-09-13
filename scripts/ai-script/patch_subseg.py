"""补丁：/diarize_sent 增加长句滑窗二次切分（sub-segmentation）。

背景：VAD 会把极速轮换（如报名字，每人 1~1.5s 间隙 0.05~0.1s）并成
一整段，混合声纹 argmax 整段只出一个人（实测 5 人对全部 0.47~0.61）。
滑窗 0.6s/0.3s 实验同段 25/27 窗判对，故对长句(>=sub_min,默认2.5s)做
滑窗子段切分：逐窗 argmax -> None 窗邻居填充 -> 连续同标签合并 -> 子段。

返回 segs 结构变化：[[start,end,sent_idx,name]]，sent_idx 可能重复
（一个长句拆成多个子段）。客户端(reannotate.py)需按 sent_idx 分组并用
字级时间戳分配文字。
"""
from pathlib import Path

P = Path('/home/bo1/jvkit/asr/diarize_svc/service.py')
s = P.read_text()

# 1) 端点签名加参数
old = '''async def diarize_sent(file: UploadFile = File(...), sents: str = Form(default="[]"), name_threshold: float = Form(default=0.30), enroll_dir: str = Form(default="")):'''
new = '''async def diarize_sent(file: UploadFile = File(...), sents: str = Form(default="[]"), name_threshold: float = Form(default=0.30), enroll_dir: str = Form(default=""), sub_min: float = Form(default=2.5), sub_win: float = Form(default=0.6), sub_shift: float = Form(default=0.3)):'''
assert old in s, 'sig anchor'
s = s.replace(old, new)

# 2) 替换打分主体（从“逐句 embedding + argmax”到 return 之前）
start_anchor = '''        # 逐句 embedding + argmax
        results = []  # [(name|None, best_sim)]'''
end_anchor = '''        segs = [[float(a), float(b), i, n or "说话人1"] for i, ((a, b), n) in enumerate(zip(sent_list, out_names))]
        return {"segs": segs, "registered": sorted(refs.keys())}'''
i0 = s.index(start_anchor)
i1 = s.index(end_anchor) + len(end_anchor)

new_body = '''        def _embed(t0: float, t1: float):
            clip = audio[int(max(0.0, t0) * sr):int(t1 * sr)]
            if len(clip) < sr * 0.25:
                return None
            e = ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"]
            e = np.asarray(e, dtype=np.float32).ravel()
            e = e / (np.linalg.norm(e) + 1e-9)
            sims = {n: float(np.dot(e, refs[n])) for n in names}
            best = max(sims, key=sims.get)
            return best, sims[best]

        # 子句滑窗切分：返回 [(sub_a, sub_b, name|None)]，覆盖 [a,b]
        def _subsegs(a: float, b: float):
            wins = []  # [t0, t1, name|None]
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
                t += sub_shift
            if not wins:
                return [(a, b, None)]
            wins[-1][1] = b  # 尾窗补齐
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
            return [(x[0], x[1], x[2]) for x in subs]

        # 逐句：短句整句 argmax；长句滑窗切分
        out_subs: list[list[tuple]] = []  # per sent: [(a,b,name|None)]
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
                results.append((first, 1.0 if first else 0.0))

        # 匿名平滑：整句皆无名的继承相邻句（跨句，同原逻辑）
        sent_names = [r[0] for r in results]
        for i in range(1, len(sent_names)):
            if sent_names[i] is None:
                sent_names[i] = sent_names[i - 1]
        for i in range(len(sent_names) - 2, -1, -1):
            if sent_names[i] is None:
                sent_names[i] = sent_names[i + 1]
        # 跨句平滑结果回填到全无名子段
        for i, subs in enumerate(out_subs):
            if all(x[2] is None for x in subs) and sent_names[i] is not None:
                out_subs[i] = [(a, b, sent_names[i]) for a, b, _ in subs]
        if sent_names and sent_names[0] is None:
            sent_names[0] = "说话人1"
            for i, subs in enumerate(out_subs):
                if all(x[2] is None for x in subs):
                    out_subs[i] = [(a, b, sent_names[0]) for a, b, _ in subs]

        segs = []
        for i, subs in enumerate(out_subs):
            for a, b, name in subs:
                segs.append([float(a), float(b), i, name or "说话人1"])
        return {"segs": segs, "registered": sorted(refs.keys())}'''

s = s[:i0] + new_body + s[i1:]
P.write_text(s)
print('patched OK: /diarize_sent sub-segmentation')
