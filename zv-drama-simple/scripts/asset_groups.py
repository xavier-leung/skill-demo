"""Entity groups and version-bound comparison targets; no automatic vision judgement."""
from pathlib import Path
from core import read_json,local_path,file_digest


def peers(project,stage,image):
    if stage not in ('anchors','assets','floorplans') or 'entityId' not in image:
        return []
    stages={'anchors':['anchors'],'assets':['anchors','assets'],'floorplans':['assets','floorplans']}[stage]
    result=[]
    for source in stages:
        path=Path(project)/(source+'.json')
        if not path.exists():continue
        for other in read_json(path)['images']:
            if other['entityId']==image['entityId'] and other['id']!=image['id']:
                result.append({'id':other['id'],'stage':source,'view':other['view'],
                               'file':other['file'],'sha256':file_digest(local_path(project,other['file'])),
                               'prompt':other['prompt']})
    return result
