# meeting-asr 经验库索引

动手前先扫"触发条件"列，命中就读对应文件；没命中就直接干活，不要全量加载。

| 模块 | 触发条件 | 文件 |
|---|---|---|
| （示例行，替换为真实模块）前端视频轨道 | 改抽帧/OCR/轨道拆分相关代码、修播放器页面 bug | frontend-video-track.md |

## 模块经验文件命名

一个模块一个 md，文件名 = 模块名（英文短横线或中文均可）。
可用 CLI 维护（纯本地）：

```bash
CLI=~/.agents/lib/fireworks-skill-memory/cli/skill_memory.py
AI_MEMORY_HOME=<项目根>/docs/experience python3 $CLI inject --skill <模块名>
AI_MEMORY_HOME=<项目根>/docs/experience python3 $CLI flush --skill <模块名> --summary-file <summary.md>
```

summary 里写 `## Lessons` section，每条一行：`- [触发标签] 结论 —— 原因/细节。`
