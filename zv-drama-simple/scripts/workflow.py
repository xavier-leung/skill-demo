"""Initialize, validate, review and record explicit chat decisions."""
import argparse
import json
from pathlib import Path
from core import STAGES, DEPS, LABELS, read_json, write_json, now, fingerprint, digest, lock
from validate import validate
from render import render, render_index, render_asset_library


def init(project, title):
    project=Path(project)
    with lock(project):
        if (project/'workflow.json').exists():
            raise ValueError('项目已初始化；请使用 status 恢复')
        for directory in ('source','images','videos','audio','subtitles','reports','history','jobs'):
            (project/directory).mkdir(exist_ok=True)
        write_json(project/'workflow.json',{'schemaVersion':1,'title':title,'createdAt':now(),'stages':{},'decisions':[]})
    return str(project.resolve())


def status(project):
    project=Path(project)
    workflow=read_json(project/'workflow.json')
    states=[]; by_id={}; cache={}
    for i, stage in enumerate(STAGES):
        record=workflow['stages'].get(stage)
        state='ready' if all(s['status']=='approved' for s in states) else 'locked'
        current=None
        if record:
            try:
                current=fingerprint(project, stage, cache)['fingerprint']
                valid=current==record['contentFingerprint'] and all(by_id[d]=='approved' for d in DEPS[stage])
            except (OSError, ValueError, KeyError):
                valid=False
            state=record['status'] if valid else 'stale'
        by_id[stage]=state
        states.append({'id':stage,'label':LABELS[stage],'status':state,'fingerprint':record.get('fingerprint') if record else None,'revision':record.get('revision') if record else None})
    return states


def require_prior(project, stage):
    for row in status(project)[:STAGES.index(stage)]:
        if row['status'] != 'approved':
            raise ValueError('前序步骤尚未确认或需要复核: '+row['id'])


def refresh(project):
    project=Path(project)
    states=status(project)
    workflow=read_json(project/'workflow.json')
    for stage, record in workflow['stages'].items():
        try:
            # A stale page must never claim that its current content is the approved snapshot.
            validate(project,stage)
            render(project,stage,states,record)
        except (ValueError,OSError,KeyError):
            # Remove only the generated view; invalid source remains untouched.
            page=project/'reports'/(stage+'.html')
            if page.exists():
                page.unlink()
    # Second pass updates navigation once all valid pages exist.
    for stage, record in workflow['stages'].items():
        if (project/'reports'/(stage+'.html')).exists():
            render(project,stage,states,record)
    if any((project/(s+'.json')).exists() for s in ('anchors','assets','floorplans')):
        render_asset_library(project)
    render_index(project,states)


def submit(project, stage):
    project=Path(project)
    with lock(project):
        require_prior(project,stage)
        validate(project,stage)
        fp=fingerprint(project,stage)
        workflow=read_json(project/'workflow.json')
        previous=workflow['stages'].get(stage,{})
        revision=previous.get('revision',0)+1
        record={**fp,'contentFingerprint':fp['fingerprint'],'revision':revision,'status':'pending','submittedAt':now()}
        record['fingerprint']=digest({'stage':stage,'contentFingerprint':fp['fingerprint'],'revision':revision})
        archive=project/'history'/stage/str(revision)
        write_json(archive/'data.json',read_json(project/(stage+'.json')))
        write_json(archive/'manifest.json',record)
        workflow['stages'][stage]=record
        write_json(project/'workflow.json',workflow)
        refresh(project)
        return {**record,'review':str((project/'reports'/(stage+'.html')).resolve())}


def decide(project, stage, version, message, decision):
    project=Path(project)
    if not message.strip():
        raise ValueError('需要用户在聊天里的实际确认/修改原话')
    with lock(project):
        workflow=read_json(project/'workflow.json')
        record=workflow['stages'].get(stage)
        if not record or record['status']!='pending':
            raise ValueError('没有待确认的提交版本')
        require_prior(project,stage)
        current=fingerprint(project,stage)['fingerprint']
        if current!=record['contentFingerprint'] or record['fingerprint']!=version:
            raise ValueError('版本已变化；请重新提交并展示，不能使用旧确认')
        if decision=='approved':
            validate(project,stage)
        record['status']=decision
        event={'stage':stage,'revision':record['revision'],'fingerprint':version,'decision':decision,'message':message,'at':now()}
        workflow['decisions'].append(event)
        write_json(project/'workflow.json',workflow)
        write_json(project/'history'/stage/str(record['revision'])/'decision.json',event)
        refresh(project)
        return event


def approve(project,stage,version,message):
    return decide(project,stage,version,message,'approved')


def set_scope(project,mode):
    from production_scope import episodes, required
    project=Path(project)
    if mode not in ('first','all'):
        raise ValueError('制作范围只能为 first 或 all')
    with lock(project):
        # Check completeness before changing any persisted selection.
        brief=read_json(project/'brief.json')
        wanted=['E01'] if mode=='first' else [f'E{i:02d}' for i in range(1,brief['episodeCount']+1)]
        available={e['id'] for e in read_json(project/'script.json')['episodes']}
        if not set(wanted).issubset(available):
            raise ValueError('所选范围剧本不完整，请先补齐 brief.scope 和第5步剧本并确认')
        write_json(project/'production-scope.json',{'schemaVersion':1,'mode':mode})
        refresh(project)
        return {'mode':mode,**required(project),'note':'第6步起按此范围制作；已有下游确认按指纹重新核对，原文件保留'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='command',required=True)
    a=subs.add_parser('init');a.add_argument('project');a.add_argument('--title',required=True)
    a=subs.add_parser('scope');a.add_argument('project');a.add_argument('--mode',choices=['first','all'])
    for cmd in ('status','render'):
        a=subs.add_parser(cmd);a.add_argument('project')
    for cmd in ('validate','submit','approve','changes'):
        a=subs.add_parser(cmd);a.add_argument('project');a.add_argument('stage',choices=STAGES)
        if cmd in ('approve','changes'):
            a.add_argument('--fingerprint',required=True);a.add_argument('--message',required=True)
    args=p.parse_args()
    try:
        if args.command=='init': result=init(args.project,args.title)
        elif args.command=='status': result=status(args.project)
        elif args.command=='scope':
            if args.mode: result=set_scope(args.project,args.mode)
            else:
                from production_scope import selection,required
                result={**selection(args.project),**required(args.project)}
        elif args.command=='render':
            with lock(args.project): refresh(args.project)
            result=str((Path(args.project)/'reports'/'index.html').resolve())
        elif args.command=='validate': validate(args.project,args.stage);result='校验通过'
        elif args.command=='submit': result=submit(args.project,args.stage)
        else: result=decide(args.project,args.stage,args.fingerprint,args.message,'approved' if args.command=='approve' else 'changes')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError) as exc:
        p.exit(1,str(exc)+'\n')

if __name__=='__main__': main()
