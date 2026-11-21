"""Shared project contracts; Python standard library only."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent.parent
STAGES = ['brief','outline','characters','art','script','anchors','assets','staging','storyboard','frames','clips','edit']
LABELS = dict(zip(STAGES, ['制作要求','改编大纲','角色设定','场景与道具','分集剧本','角色定妆','制作参考图与俯视图','场次站位','分镜方案','分镜图','视频片段','每集成片']))
DEPS = {'brief':[], 'outline':['brief'], 'characters':['outline'], 'art':['outline'],
        'script':['brief','outline','characters','art'], 'anchors':['brief','characters','script'],
        'assets':['brief','anchors','art','script'], 'floorplans':['brief','anchors','art','script'], 'staging':['script','assets'],
        'storyboard':['brief','script','staging','assets'], 'frames':['storyboard','assets'],
        'clips':['frames','storyboard'], 'edit':['brief','script','clips']}

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')

def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def local_path(project, name, exists=True):
    root = Path(project).resolve()
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '\\' in name:
        raise ValueError(f'不安全的项目路径: {name}')
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError(f'路径越出项目: {name}')
    if exists and (not candidate.is_file() or candidate.stat().st_size == 0):
        raise ValueError(f'文件不存在或为空: {name}')
    return candidate

def stage_path(project, stage):
    if stage not in STAGES and stage != 'floorplans':
        raise ValueError('未知步骤: ' + stage)
    return local_path(project, stage + '.json')

def file_refs(data):
    if isinstance(data, dict):
        for key, value in data.items():
            if key=='qc':
                continue  # Historical review candidates are not production dependencies.
            if key in ('file', 'sourceFile') and isinstance(value, str):
                yield value
            else:
                yield from file_refs(value)
    elif isinstance(data, list):
        for value in data:
            yield from file_refs(value)

def digest(data):
    if not isinstance(data, bytes):
        data = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(data).hexdigest()

def file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def fingerprint(project, stage, cache=None):
    cache = {} if cache is None else cache
    if stage in cache:
        return cache[stage]
    p = stage_path(project, stage)
    data = read_json(p)
    files = {name:file_digest(local_path(project, name)) for name in sorted(set(file_refs(data)))}
    if stage=='assets':
        plans=local_path(project,'floorplans.json')
        files['floorplans.json']=file_digest(plans)
        for name in file_refs(read_json(plans)):
            files[name]=file_digest(local_path(project,name))
    if stage=='brief':
        files['source/conversion.json']=file_digest(local_path(project,'source/conversion.json'))
    if stage=='clips' and local_path(project,'voices.json',exists=False).exists():
        files['voices.json']=file_digest(local_path(project,'voices.json'))
        for name in file_refs(read_json(local_path(project,'voices.json'))):
            files[name]=file_digest(local_path(project,name))
    deps = {s:fingerprint(project, s, cache)['fingerprint'] for s in DEPS[stage]}
    value = {'dataHash':file_digest(p), 'files':files, 'dependencies':deps}
    if stage=='floorplans' or STAGES.index(stage)>=STAGES.index('anchors'):
        from production_scope import selection
        value['productionScope']=selection(project)
    value['fingerprint'] = digest(value)
    cache[stage] = value
    return value

@contextlib.contextmanager
def lock(project, name='workflow'):
    root = Path(project)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ('.' + name + '.lock')).open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('另一个进程正在修改该项目，请稍后重试') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
