"""Episode selection for production from step six; story planning stays intact."""
from pathlib import Path
from core import read_json, local_path


def selection(project):
    path=local_path(project,'production-scope.json',exists=False)
    data=read_json(path) if path.exists() else {'schemaVersion':1,'mode':'first'}
    if data.get('schemaVersion')!=1 or data.get('mode') not in ('first','all') or set(data)!={'schemaVersion','mode'}:
        raise ValueError('production-scope.json 需要 schemaVersion=1，mode=first 或 all')
    brief=read_json(Path(project)/'brief.json')
    ids=['E01'] if data['mode']=='first' else [f'E{i:02d}' for i in range(1,brief['episodeCount']+1)]
    return {**data,'episodeIds':ids}


def episodes(project):
    selected=selection(project)['episodeIds']
    data=read_json(Path(project)/'script.json')['episodes']
    missing=set(selected)-{e['id'] for e in data}
    if missing:
        raise ValueError('所选制作范围缺少已编写剧本: '+', '.join(sorted(missing))+'；请先补齐 brief.scope 和第5步剧本并确认，不能把部分集数称为所有集')
    return [next(e for e in data if e['id']==id) for id in selected]


def required(project):
    scenes=[s for ep in episodes(project) for s in ep['scenes']]
    return {'episodeIds':selection(project)['episodeIds'],
            'sceneIds':[s['id'] for s in scenes],
            'characterIds':sorted({c for s in scenes for c in s['characterIds']}),
            'locationIds':sorted({s['locationId'] for s in scenes}),
            'propIds':sorted({p for s in scenes for p in s['propIds']})}
