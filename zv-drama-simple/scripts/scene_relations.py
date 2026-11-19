"""Adjacent scene contracts and actual image dependencies, without geometric inference."""
from pathlib import Path
from core import read_json,local_path,file_digest


def relations(project):
    from validate import schema_check,check,indexed
    from core import BASE
    art=read_json(Path(project)/'art.json')
    schema_check(art,read_json(BASE/'schemas/art.schema.json'))
    rows=art.get('sceneRelations',[])
    indexed(rows)
    ids={s['id'] for s in art['scenes']}
    for row in rows:
        check(row['fromSceneId'] in ids and row['toSceneId'] in ids,'相邻关系引用未知场景')
        check(row['fromSceneId']!=row['toSceneId'],'相邻关系必须连接不同场景')
        check(row['basis']!='unknown' or bool(row['unknowns']),'未知相邻关系须列出待确认项')
    return rows


def connected(project,a,b):
    return any({r['fromSceneId'],r['toSceneId']}=={a,b} for r in relations(project))


def context(project,stage,image):
    if stage not in ('assets','floorplans'):return {'relations':[],'images':[]}
    relevant=[r for r in relations(project) if image.get('entityId') in (r['fromSceneId'],r['toSceneId'])]
    targets=[]
    for relation in relevant:
        neighbor=relation['toSceneId'] if image['entityId']==relation['fromSceneId'] else relation['fromSceneId']
        for source in (['assets'] if stage=='assets' else ['assets','floorplans']):
            path=Path(project)/(source+'.json')
            if not path.exists():continue
            for other in read_json(path)['images']:
                if other['entityId']==neighbor:
                    targets.append({'relationId':relation['id'],'id':other['id'],'entityId':neighbor,'view':other['view'],
                                    'file':other['file'],'sha256':file_digest(local_path(project,other['file'])),
                                    'prompt':other['prompt'],'stage':source})
    return {'relations':relevant,'images':targets}


def requirements(project,stage,image):
    result=[]
    for peer in context(project,stage,image)['images']:
        for aspect in ('position','angle','scale','connection'):
            result.append({'id':'adj_'+peer['relationId']+'_'+peer['id']+'_'+aspect,'peer':peer,'aspect':aspect})
    return result
