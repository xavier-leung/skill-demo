"""Bounded image review: model supplies visual observations; code enforces selection."""
import argparse
import json
from core import DEPS, digest, fingerprint, file_digest, local_path, read_json, write_json, lock


def context_hash(project, stage, image):
    from asset_groups import peers
    from scene_relations import context
    return digest({'target':{k:v for k,v in image.items() if k not in ('file','qc')},
                   'criteria':image['qc']['criteria'],
                   'groupPeers':peers(project,stage,image),
                   'adjacentScenes':context(project,stage,image),
                   'upstream':{s:fingerprint(project,s)['fingerprint'] for s in DEPS[stage]}})


def passed(attempt):
    return all(c['result']=='pass' for c in attempt['checks'])


def rank(attempt, criteria):
    checks={c['criterionId']:c['result'] for c in attempt['checks']}
    critical_misses=sum(c['priority']=='critical' and checks[c['id']]!='pass' for c in criteria)
    score=sum(c['weight'] for c in criteria if checks[c['id']]=='pass')
    unknown=sum(v=='uncertain' for v in checks.values())
    return (-critical_misses,score,-unknown,-attempt['attempt'])


def inspect_review(project, stage, image, selected=True):
    def need(ok,message):
        if not ok:raise ValueError(image['id']+': '+message)
    qc=image['qc'];criteria=qc['criteria'];attempts=qc['attempts']
    need(qc['contextFingerprint']==context_hash(project,stage,image),'图像检查对应的描述/参考素材已变化，需要重新验收')
    ids=[c['id'] for c in criteria]
    need(len(ids)==len(set(ids)) and bool(ids),'验收项 ID 重复或为空')
    need(any(c['priority']=='critical' for c in criteria),'至少明确一项关键描述要求')
    from asset_groups import peers
    for peer in peers(project,stage,image):
        key='group_'+peer['id']
        need(any(c['id']==key and c['priority']=='critical' for c in criteria),
             '缺少组内图片一致性关键验收项: '+key)
    from scene_relations import requirements
    for requirement in requirements(project,stage,image):
        need(any(c['id']==requirement['id'] and c['priority']=='critical' for c in criteria),
             '缺少相邻场景一致性关键验收项: '+requirement['id'])
    need(1<=len(attempts)<=4,'首次生成加最多重做3次，总候选数必须为1–4')
    need([a['attempt'] for a in attempts]==list(range(len(attempts))),'重做序号必须从0连续到最多3')
    files=[];hashes=[]
    for a in attempts:
        from validate import image_check
        p=local_path(project,a['file']);image_check(p)
        need(file_digest(p)==a['sha256'],'候选图片内容已变化，需要重新看图检查')
        files.append(a['file'])
        hashes.append(a['sha256'])
        seen=[c['criterionId'] for c in a['checks']]
        need(len(seen)==len(ids) and set(seen)==set(ids),'每个候选必须逐项检查同一组要求')
        need(all(c['observation'].strip() for c in a['checks']),'每项必须记录实际画面观察')
        if a['attempt']:
            need(bool(a['correction'].strip()),'重做必须记录针对上一版问题的修正')
    need(len(files)==len(set(files)),'每次生成必须保留独立候选文件，不能覆盖或重复登记同一路径')
    need(len(hashes)==len(set(hashes)),'不能把同一张图片复制后冒充重做候选')
    successes=[a for a in attempts if passed(a)]
    if successes:
        best=successes[0]
        need(best is attempts[-1],'已经全部符合要求后应停止重做')
        outcome='passed'
    else:
        best=max(attempts,key=lambda a:rank(a,criteria));outcome='best_available'
        if selected:need(len(attempts)==4,'尚有不符合/无法判断项，应继续重做，最多3次')
    if selected:
        need(qc['selectedAttempt']==best['attempt'],'必须选择全部通过或按关键要求及加权符合度排名最优的候选')
        need(qc['outcome']==outcome,'检查结论与实际验收项不一致')
        need(image['file']==best['file'],'正式图片路径必须指向选中的候选')
        need(qc['checked'],'尚未完成看图检查')
    return best,outcome


def select(project,stage,image_id):
    from validate import load
    with lock(project):
        data=load(project,stage)
        image=next((i for i in data['images'] if i['id']==image_id),None)
        if image is None:raise ValueError('未知图片 ID')
        best,outcome=inspect_review(project,stage,image,selected=False)
        if outcome=='best_available' and len(image['qc']['attempts'])<4:
            return {'action':'redo','nextAttempt':len(image['qc']['attempts']),'remainingRedos':4-len(image['qc']['attempts'])}
        image['file']=best['file'];image['qc'].update({'checked':True,'selectedAttempt':best['attempt'],'outcome':outcome})
        inspect_review(project,stage,image)
        write_json(local_path(project,stage+'.json'),data)
        return {'action':'selected','file':best['file'],'outcome':outcome,'selectedAttempt':best['attempt'],
                'remainingIssues':[c for c in best['checks'] if c['result']!='pass']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project');p.add_argument('stage',choices=['anchors','assets','floorplans','frames']);p.add_argument('image_id');p.add_argument('--context',action='store_true')
    a=p.parse_args()
    try:
        if a.context:
            data=read_json(local_path(a.project,a.stage+'.json'))
            image=next((i for i in data['images'] if i['id']==a.image_id),None)
            if image is None:raise ValueError('未知图片 ID')
            result={'contextFingerprint':context_hash(a.project,a.stage,image)}
        else:result=select(a.project,a.stage,a.image_id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError) as exc:p.exit(1,str(exc)+'\n')
