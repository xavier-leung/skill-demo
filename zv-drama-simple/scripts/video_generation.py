"""Build tool-neutral video inputs and collect actual results; host tools perform generation."""
import argparse
import json
from pathlib import Path
from core import read_json,write_json,local_path,file_digest,fingerprint,digest,now,lock
from validate import validate,check
from workflow import require_prior
from video_backend import settings
from voices import choose,validate_selected
from assemble import probe


def prepare(project,segment_id):
    root=Path(project)
    with lock(root,'video'):
        require_prior(root,'clips')
        brief=validate(root,'brief');config=settings(brief,ready=True)
        board=validate(root,'storyboard');frames=validate(root,'frames')
        segment=next((s for s in board['segments'] if s['id']==segment_id),None)
        check(segment is not None,'未知分段')
        plans={p['id']:p for p in validate(root,'floorplans')['images']}
        order={s['id']:i for i,s in enumerate(segment['shots'])}
        images=sorted([i for i in frames['images'] if i['segmentId']==segment_id],key=lambda i:(order[i['shotId']],i['role']=='last_frame'))
        plan=plans[segment['shots'][0]['spatial']['floorplanId']]
        sources=[(i['id'],i['file'],i['role']) for i in images]+[(plan['id'],plan['file'],'spatial_reference')]
        check(len(sources)<=config['maxReferenceImages'],'关键帧和俯视图超出工具图片上限，请调整分镜并确认')
        media=[]
        for id,file,role in sources:
            check(Path(file).suffix.lower() in ('.png','.jpg','.jpeg','.webp'),'投产需要真实位图')
            media.append({'id':id,'file':file,'role':role,'sha256':file_digest(local_path(root,file))})
        voice_selection,voice=choose(root,segment)
        if voice:media.append({'id':voice['characterId'],'file':voice['file'],'role':'voice_reference','url':voice['url'],'sha256':voice['sha256']})
        scenes={s['id']:s for ep in read_json(root/'script.json')['episodes'] for s in ep['scenes']}
        beats={b['id']:b for b in scenes[segment['sceneId']]['beats']}
        data={'segmentId':segment_id,'frameFingerprint':fingerprint(root,'frames')['fingerprint'],
              'capability':config,'productionRequirements':{k:brief[k] for k in ('style','aspectRatio','audioMode')},'characters':read_json(root/'characters.json')['characters'],'segment':segment,'beats':[beats[b] for s in segment['shots'] for b in s['beatIds']],
              'media':media,'voiceSelection':voice_selection,'requestedVoiceCharacter':None,
              'spatialInstruction':'必须实际发送 spatial_reference 图片，只作位置、尺度、方向和运镜约束，不作为视频画面或切镜。'}
        job_id='GEN-'+segment_id+'-'+digest(data)[:16];path=local_path(root,'jobs/'+job_id+'.json',exists=False)
        if path.exists():return read_json(path)
        job={'schemaVersion':1,'jobId':job_id,'status':'prepared','createdAt':now(),**data}
        job['requestFingerprint']=digest(data);write_json(path,job);return job


def current(root,job):
    require_prior(root,'clips')
    check(job['frameFingerprint']==fingerprint(root,'frames')['fingerprint'],'素材或制作要求已改变，请重新准备任务')
    data={k:job[k] for k in ('segmentId','frameFingerprint','capability','productionRequirements','characters','segment','beats','media','voiceSelection','requestedVoiceCharacter','spatialInstruction')}
    check(digest(data)==job['requestFingerprint'],'视频输入记录已改变，请重新 prepare')
    validate_selected(root,job)
    for item in job['media']:
        check(file_digest(local_path(root,item['file']))==item['sha256'],'视频依赖文件已改变')


def start(project,job_id):
    root=Path(project)
    with lock(root,'video'):
        path=local_path(root,'jobs/'+job_id+'.json');job=read_json(path)
        check(job['status']=='prepared','任务已启动，先查询实际工具状态，不要重复生成')
        current(root,job);job['status']='submitting';job['startedAt']=now();write_json(path,job)
        return job


def collect(project,job_id,file,task_id):
    root=Path(project)
    with lock(root,'video'):
        path=local_path(root,'jobs/'+job_id+'.json');job=read_json(path)
        check(job['status']=='submitting','只收集已启动的生成任务；不得伪造结果')
        check(bool(task_id.strip()),'必须记录实际工具返回的任务或结果 ID')
        current(root,job);output=local_path(root,file);info=probe(output)
        check(info['video'],'生成结果缺少视频轨')
        brief=read_json(root/'brief.json')
        check(brief['audioMode']!='native' or info['audio'],'原生对白结果缺少音轨')
        check(abs(info['seconds']-sum(s['seconds'] for s in job['segment']['shots']))<=.35,'实际视频时长与已确认分段不符')
        job.update(status='collected',file=file,taskId=task_id,seconds=info['seconds'],collectedAt=now())
        write_json(root/'jobs'/(job_id+'-download.json'),{'file':file,'sha256':file_digest(output),'taskId':task_id})
        write_json(root/'videos'/(job_id+'.clip.json'),{'id':'V-'+job_id,'segmentId':job['segmentId'],'taskId':task_id,'file':file,'seconds':info['seconds'],'qc':{'checked':False,'notes':'待实际看片试听'}})
        write_json(path,job);return job


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('project');a.add_argument('segment_id')
    for name in ('start','collect'):
        a=sub.add_parser(name);a.add_argument('project');a.add_argument('job_id')
        if name=='collect':a.add_argument('--file',required=True);a.add_argument('--task-id',required=True)
    a=p.parse_args()
    try:
        if a.command=='prepare':result=prepare(a.project,a.segment_id)
        elif a.command=='start':result=start(a.project,a.job_id)
        else:result=collect(a.project,a.job_id,a.file,a.task_id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError) as exc:p.exit(1,str(exc)+'\n')
