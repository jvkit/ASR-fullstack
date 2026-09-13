"""一次性补丁：_load_db_multi 支持 enroll_dir 参数（按目录分键缓存），
/diarize_sent 加 enroll_dir 表单参数。用于 AISHELL-4 基准测试，
测试声纹放独立目录，不污染生产 speaker_enrolls。"""
from pathlib import Path

P = Path('/home/bo1/jvkit/asr/diarize_svc/service.py')
s = P.read_text()

old = '''_DB_MULTI_CACHE: dict | None = None
_DB_MULTI_MTIME: dict | None = None


def _load_db_multi() -> dict[str, np.ndarray]:'''
new = '''_DB_MULTI_CACHE: dict[str, dict] = {}
_DB_MULTI_MTIME: dict[str, dict] = {}


def _load_db_multi(enroll_dir: str | None = None) -> dict[str, np.ndarray]:'''
assert old in s, 'anchor1'
s = s.replace(old, new)

old = '''    global _DB_MULTI_CACHE, _DB_MULTI_MTIME
    import glob as _glob
    d = Path(_SPEAKER_ENROLL_DIR)
    if not d.is_dir():
        return {}
    mt = {p.name: p.stat().st_mtime for p in d.iterdir()}
    if _DB_MULTI_CACHE is not None and mt == _DB_MULTI_MTIME:
        return _DB_MULTI_CACHE'''
new = '''    import glob as _glob
    d = Path(enroll_dir or _SPEAKER_ENROLL_DIR)
    if not d.is_dir():
        return {}
    cache_key = str(d)
    mt = {p.name: p.stat().st_mtime for p in d.iterdir()}
    if cache_key in _DB_MULTI_CACHE and mt == _DB_MULTI_MTIME.get(cache_key):
        return _DB_MULTI_CACHE[cache_key]'''
assert old in s, 'anchor2'
s = s.replace(old, new)

old = '''    _DB_MULTI_CACHE, _DB_MULTI_MTIME = refs, mt
    return refs'''
new = '''    _DB_MULTI_CACHE[cache_key] = refs
    _DB_MULTI_MTIME[cache_key] = mt
    return refs'''
assert old in s, 'anchor3'
s = s.replace(old, new)

old = '''async def diarize_sent(file: UploadFile = File(...), sents: str = Form(default="[]"), name_threshold: float = Form(default=0.30)):'''
new = '''async def diarize_sent(file: UploadFile = File(...), sents: str = Form(default="[]"), name_threshold: float = Form(default=0.30), enroll_dir: str = Form(default="")):'''
assert old in s, 'anchor4'
s = s.replace(old, new)

old = '''        sent_list = json.loads(sents) if sents else []
        if not sent_list:
            return {"segs": [], "registered": sorted(_load_db_multi().keys())}

        ere = _ere()
        refs = _load_db_multi()'''
new = '''        sent_list = json.loads(sents) if sents else []
        if not sent_list:
            return {"segs": [], "registered": sorted(_load_db_multi(enroll_dir or None).keys())}

        ere = _ere()
        refs = _load_db_multi(enroll_dir or None)'''
assert old in s, 'anchor5'
s = s.replace(old, new)

P.write_text(s)
print('patched OK')
