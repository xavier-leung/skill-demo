"""Wan3 async lifecycle. No automatic resubmission, no credential-bearing logs."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import uuid
from core import read_json, write_json, local_path, file_digest, fingerprint, digest, now, lock
from validate import validate, check

ENDPOINT='https://www.aiveyrahub.com/external-api/ai/videos/wan/generations/async'
UPLOAD=os.environ.get('ZV_UPLOAD_URL','')
CREDENTIAL=Path(os.environ.get('ZV_WAN3_ENV',str(Path.home()/'.config/aiveyra/wan3.env'))).expanduser()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def credential(path=CREDENTIAL):
    for line in Path(path).read_text().splitlines():
        match=re.match(r'^\s*(?:export\s+)?AIVEYRA_AUTHORIZATION\s*=\s*(.*)$',line)
        if match:
            parts=shlex.split(match.group(1),comments=True)
            value=' '.join(parts)
            check(bool(value) and '\n' not in value and '\r' not in value,'无效鉴权配置')
            return value
    raise ValueError('固定配置文件缺少 AIVEYRA_AUTHORIZATION')


def http_json(url, method='GET', body=None, headers=None):
    headers=dict(headers or {})
    if isinstance(body,dict):
        body=json.dumps(body,ensure_ascii=False).encode();headers['Content-Type']='application/json'
    request=urllib.request.Request(url,data=body,headers=headers,method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=90) as response:
            raw=response.read(4_000_001)
            check(len(raw)<=4_000_000,'响应过大')
            # Never persist provider responses: extract only allowlisted fields later.
            return json.loads(raw)
    except Exception:
        # Network/HTTP exceptions can contain URLs and headers; do not expose their repr.
        raise ValueError('服务请求失败或结果不明；请检查任务状态，不要自动重提') from None


def safe_url(value):
    if not isinstance(value,str):return False
    parsed=urlsplit(value)
    return parsed.scheme in ('http','https') and bool(parsed.hostname) and not parsed.username and not parsed.password


def sanitized(data, authorization):
    raw=json.dumps(data,ensure_ascii=False)
    for secret in (authorization, authorization.split(' ',1)[-1]):
        if secret:
            raw=raw.replace(secret,'[redacted]')
    return json.loads(raw)


def request_fingerprint(job):
    media=[{k:m[k] for k in ('type','file','sha256')} for m in job['media']]
    return digest({'fingerprint':job['frameFingerprint'],'media':media,'payload':job['payload'],'voiceSelection':job['voiceSelection'],'requestedVoiceCharacter':job.get('requestedVoiceCharacter')})


def upload_file(path, transport=http_json):
    check(safe_url(UPLOAD),'需要素材上传能力：配置 ZV_UPLOAD_URL，或使用已连接上传工具并登记实际 URL')
    boundary='zv'+uuid.uuid4().hex
    filename='asset'+Path(path).suffix.lower()
    mime=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()+Path(path).read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    headers={'Content-Type':'multipart/form-data; boundary='+boundary}
    if os.environ.get('ZV_UPLOAD_PKG'):headers['x-pkg']=os.environ['ZV_UPLOAD_PKG']
    result=transport(UPLOAD,'POST',body,headers)
    check(result.get('code')==2000 and safe_url(result.get('data')),'上传未返回有效资源 URL')
    return result['data']


def task_id(data):
    if isinstance(data,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}',data):return data
    if not isinstance(data,dict):return None
    for key in ('task_id','taskId','job_id','jobId','id'):
        value=data.get(key)
        if isinstance(value,(str,int)) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}',str(value)):
            return str(value)
    for key in ('data','task','result'):
        if key in data:
            result=task_id(data[key])
            if result:return result
    return None


def normalized_result(data):
    node=data.get('data',data)
    if not isinstance(node,dict):return {'status':'unknown'}
    state=str(node.get('status',node.get('taskStatus',node.get('task_status',node.get('state',''))))).lower()
    if state in ('failed','failure','error','cancelled','canceled'):return {'status':'failed'}
    url=node.get('video_url') or node.get('videoUrl') or node.get('url')
    for key in ('result','output'):
        inner=node.get(key)
        if isinstance(inner,dict):url=url or inner.get('video_url') or inner.get('url')
        if isinstance(inner,list) and inner:
            first=inner[0]
            url=url or (first.get('url') if isinstance(first,dict) else first)
    if state in ('completed','succeeded','success','done'):
        return {'status':'completed','url':url} if safe_url(url) else {'status':'needs-result-url'}
    if state in ('pending','queued','submitted','running','processing','in_progress'):
        return {'status':'running'}
    return {'status':'unknown'}


def job_path(project, job_id):
    check(bool(re.fullmatch(r'[A-Za-z0-9_-]{1,180}',job_id)),'无效 job ID')
    return local_path(project,'jobs/'+job_id+'.json',exists=False)


def prepare(project, segment_id, reference_audio=None, voice_character=None):
    from workflow import require_prior
    from voices import choose,library
    root=Path(project)
    with lock(root,'wan3'),lock(root,'voices'):
        require_prior(root,'clips')
        brief=validate(root,'brief')
        check('videoGeneration' not in brief and 'wan3' in brief,'此脚本仅用于明确配置的旧 Wan3 项目；新项目按发现的视频工具执行')
        board=validate(root,'storyboard');frames=validate(root,'frames')
        seg=next((s for s in board['segments'] if s['id']==segment_id),None)
        check(seg is not None,'未知分段')
        check(seg['mode']=='ref2v','视频必须实际携带场景俯视图；当前首尾帧协议不支持额外参考图，请将分镜调整为 ref2v 并重新确认')
        refs=[f for f in frames['images'] if f['segmentId']==segment_id]
        order={s['id']:i for i,s in enumerate(seg['shots'])}
        refs.sort(key=lambda f:(order[f['shotId']],f['role']=='last_frame'))
        media=[];descriptions=[]
        for idx,frame in enumerate(refs,1):
            check(Path(frame['file']).suffix.lower() in ('.png','.jpg','.jpeg','.webp'),'Wan3 投产需要真实位图；SVG 示意图不能提交')
            role='reference_image' if seg['mode']=='ref2v' else frame['role']
            media.append({'type':role,'file':frame['file'],'sha256':file_digest(local_path(root,frame['file']))})
            descriptions.append(f'图{idx}：{frame["shotId"]}，{frame["role"]}')
        plans={p['id']:p for p in validate(root,'floorplans')['images']}
        plan=plans[seg['shots'][0]['spatial']['floorplanId']]
        if seg['mode']=='ref2v':
            check(len(media)<9,'俯视图需要一个参考图名额，请先拆段')
            check(Path(plan['file']).suffix.lower() in ('.png','.jpg','.jpeg','.webp'),'视频参考俯视图需要真实位图')
            media.append({'type':'reference_image','file':plan['file'],'sha256':file_digest(local_path(root,plan['file']))})
            descriptions.append(f'图{len(media)}：{plan["id"]}，场景俯视布局，仅用于空间关系和运镜约束，不是视频画面或关键帧，不要切到俯视图或显示标注')
        voice_selection,voice=choose(root,seg,voice_character)
        if reference_audio:
            check(seg['mode']=='ref2v','参考音频只支持 ref2v')
            check(voice_character is not None and voice is not None and voice['file']==reference_audio,'音色文件必须先从角色首个说话视频登记，并用 --voice-character 指定；不能传未绑定人物的音频')
        if voice:
            media.append({'type':'reference_audio','file':voice['file'],'sha256':voice['sha256'],'url':voice['url']})
        scenes={s['id']:s for ep in read_json(root/'script.json')['episodes'] for s in ep['scenes']}
        beats={b['id']:b for b in scenes[seg['sceneId']]['beats']}
        names={c['id']:c['name'] for c in read_json(root/'characters.json')['characters']}
        opening=next(s['opening'] for s in read_json(root/'staging.json')['scenes'] if s['sceneId']==seg['sceneId'])
        earlier=[s for s in board['segments'][:board['segments'].index(seg)] if s['sceneId']==seg['sceneId']]
        if earlier:
            opening='承接前段：'+earlier[-1]['shots'][-1]['endState']+'。本段起始：'+seg['shots'][0]['startState']
        lines=['画风：'+brief['style'],'画幅：'+brief['aspectRatio'],'参考素材：'+'；'.join(descriptions),'开场站位：'+opening]
        known=library(root) if voice_selection['voices'] else {}
        for id in voice_selection['speakers']:
            if id in known:
                lines.append(f'角色 {id}（{names[id]}）固定音色：{known[id]["description"]}。保持其原有声线，不要与其他人物互换声音。')
        if voice:
            lines.append(f'所附 reference_audio 只属于 {voice["characterId"]}（{names[voice["characterId"]]}），仅复用该角色音色；按本段台词说话，不复述参考音频内容，不将此音色套给其他角色。')
        if voice_selection['bootstrap']:
            lines.append('首次建立音色的角色：'+ '、'.join(voice_selection['bootstrap'])+'。按角色设定生成清楚自然的独立对白，尽量避免重叠说话和强背景音乐，供后续提取音色。')
        layout=plan['layout']
        lines+=['场景空间约束：'+layout['orientation']+'。'+layout['spatialNotes'],
                '俯视布局（归一化坐标，非画面像素）：'+json.dumps(layout['items'],ensure_ascii=False),
                '不可擅自补全的未知区域：'+json.dumps(layout['unknownZones'],ensure_ascii=False),
                '保持墙、门、窗与家具相对位置；移动镜头时背景按机位自然显露与遮挡，不得穿墙、穿家具、镜像或凭空新增开口。']
        elapsed=0
        for shot in seg['shots']:
            lines.append(f'{elapsed:g}–{elapsed+shot["seconds"]:g}秒，{shot["size"]}，{shot["camera"]}。{shot["composition"]}。{shot["action"]}。起始：{shot["startState"]}。结束：{shot["endState"]}。')
            lines.append('本镜机位路径与注视点：'+json.dumps(shot['spatial']['cameraPath'],ensure_ascii=False)+'。预期背景变化：'+shot['spatial']['backgroundChange'])
            for beat_id in shot['beatIds']:
                beat=beats[beat_id]
                if beat['type']=='dialogue':
                    lines.append(f'{names[beat["speaker"]]}（{beat["delivery"]}）说：“{beat["text"]}”')
            elapsed+=shot['seconds']
        lines+=['声音：'+seg['sound'],'保持人物身份、服装、场景及动作连续；画面不要生成文字或字幕。切点为目标节奏。']
        if not brief['wan3']['audio']:lines.append('输出静音视频；对白由后期独立音轨提供。')
        payload={'model':'wan3.0-video','prompt':'\n'.join(lines),'duration':int(elapsed),'resolution':brief['wan3']['resolution'],'ratio':brief['aspectRatio'],'audio':brief['wan3']['audio'],'disable_content_filter':True}
        version=fingerprint(root,'frames')['fingerprint']
        job={'schemaVersion':1,'segmentId':segment_id,'frameFingerprint':version,'status':'prepared','payload':payload,'media':media,'voiceSelection':voice_selection,'requestedVoiceCharacter':voice_character,'createdAt':now()}
        identity=request_fingerprint(job)
        job_id=segment_id+'-'+identity[:16]
        path=job_path(root,job_id)
        if path.exists():return read_json(path)
        job.update({'jobId':job_id,'requestFingerprint':identity})
        write_json(path,job);return job


def submit(project, job_id, transport=http_json, auth=None):
    from workflow import require_prior
    from voices import validate_selected
    root=Path(project)
    with lock(root,'wan3'),lock(root,'voices'):
        path=job_path(root,job_id);job=read_json(path)
        check(job['status'] in ('prepared','uploading','upload-failed'),'已有提交或结果不明，禁止重复提交；先 poll 或 attach 恢复')
        check(request_fingerprint(job)==job['requestFingerprint'],'请求内容已变化，请重新 prepare；禁止修改已准备任务')
        require_prior(root,'clips')
        check(fingerprint(root,'frames')['fingerprint']==job['frameFingerprint'],'审核素材版本已变化，请重新 prepare')
        board=validate(root,'storyboard')
        segment=next((s for s in board['segments'] if s['id']==job['segmentId']),None)
        check(segment is not None and segment['mode']=='ref2v','视频必须携带俯视图；请改为 ref2v 并重新确认、prepare')
        plans={p['id']:p for p in validate(root,'floorplans')['images']}
        plan=plans[segment['shots'][0]['spatial']['floorplanId']]
        check(any(m['type']=='reference_image' and m['file']==plan['file'] and m['sha256']==file_digest(local_path(root,plan['file'])) for m in job['media']),
              '请求缺少当前场景俯视图，禁止提交；请重新 prepare')
        check(not any(m['type'] in ('first_frame','last_frame') for m in job['media']), '不能将首尾帧字段与俯视参考图混用')
        validate_selected(root,job)
        # Reused audio URLs are bound to the registered character, unlike upload-populated image URLs.
        if job['voiceSelection']['selectedCharacterId']:
            from voices import library
            voice=library(root)[job['voiceSelection']['selectedCharacterId']]
            audio=[m for m in job['media'] if m['type']=='reference_audio']
            check(len(audio)==1 and audio[0].get('url')==voice['url'],'角色参考音频 URL 被改变，请重新 prepare')
        for m in job['media']:
            check(file_digest(local_path(root,m['file']))==m['sha256'],'素材已变化，请重新 prepare')
        authorization=auth if auth is not None else credential()
        # Upload failures cannot have created a video task and may safely resume uploads.
        job['status']='uploading';write_json(path,job)
        try:
            for m in job['media']:
                if not m.get('url'):
                    m['url']=upload_file(local_path(root,m['file']),transport)
                    write_json(path,job)
        except Exception:
            job['status']='upload-failed';write_json(path,job)
            raise ValueError('素材上传未完成，可恢复提交；视频请求尚未发送') from None
        payload={**job['payload'],'media':[{'type':m['type'],'url':m['url']} for m in job['media']]}
        # A crash from this write onward must be treated as ambiguous, never retried blindly.
        job['status']='submitting';job['submittedAt']=now();write_json(path,job)
        try:
            response=sanitized(transport(ENDPOINT,'POST',payload,{'Authorization':authorization}),authorization)
            check(response.get('code',200)==200,'供应商未确认接受请求')
            remote=task_id(response.get('data',response))
            check(remote is not None,'没有返回任务 ID')
            job['taskId']=remote;job['status']='submitted'
        except Exception:
            job['status']='unknown';write_json(path,job)
            raise ValueError('视频提交结果不明。已保存 unknown；找到服务端任务 ID 后用 attach 恢复，禁止自动重提') from None
        write_json(path,job);return job


def attach(project,job_id,remote):
    check(bool(re.fullmatch(r'[A-Za-z0-9_-]{1,200}',remote)),'无效服务端任务 ID')
    with lock(project,'wan3'):
        path=job_path(project,job_id);job=read_json(path)
        check(job['status'] in ('submitting','unknown') and not job.get('taskId'),'仅恢复结果不明且没有任务 ID 的记录')
        job.update({'taskId':remote,'status':'submitted','recoveredAt':now()});write_json(path,job);return job


def poll(project,job_id,transport=http_json,auth=None):
    with lock(project,'wan3'):
        path=job_path(project,job_id);job=read_json(path)
        check(bool(job.get('taskId')),'没有任务 ID，需先恢复')
        if job['status']=='collected':return job
        authorization=auth if auth is not None else credential()
        check(bool(re.fullmatch(r'[A-Za-z0-9_-]{1,200}',job['taskId'])),'无效服务端任务 ID')
        result=sanitized(transport(ENDPOINT+'/'+job['taskId'],'GET',None,{'Authorization':authorization}),authorization)
        normalized=normalized_result(result)
        job.update(normalized);job['polledAt']=now();write_json(path,job);return job


def collect(project,job_id):
    from assemble import probe
    root=Path(project).resolve()
    with lock(root,'wan3'):
        path=job_path(root,job_id);job=read_json(path)
        check(job['status'] in ('completed','collected') and safe_url(job.get('url')),'任务未返回可下载成片')
        name='videos/'+job_id+'.mp4'
        dest=local_path(root,name,exists=False);dest.parent.mkdir(exist_ok=True)
        receipt_path=local_path(root,'jobs/'+job_id+'-download.json',exists=False)
        temp=local_path(root,'videos/'+job_id+'.download',exists=False)
        if dest.exists():
            check(receipt_path.exists(),'目标文件已存在但没有下载凭据；请核对，不覆盖未知文件')
            receipt=read_json(receipt_path)
            check(receipt.get('jobId')==job_id and receipt.get('sha256')==file_digest(dest),'已有下载与记录不匹配')
            info=probe(dest)
            check(info['video'],'已下载文件不是有效视频')
        else:
            try:
                with urllib.request.build_opener(NoRedirect).open(job['url'],timeout=90) as src, temp.open('wb') as out:
                    while chunk:=src.read(1024*1024):out.write(chunk)
                info=probe(temp)
                check(info['video'],'服务端结果不是有效视频')
                # Persist verification before publishing: every subsequent step is resumable.
                write_json(receipt_path,{'jobId':job_id,'file':name,'sha256':file_digest(temp),'probe':info})
                temp.replace(dest)
            except Exception:
                temp.unlink(missing_ok=True)
                raise ValueError('下载或视频验证失败；保留任务 ID，可再次 collect，不会重新生成') from None
        draft=dest.with_suffix('.clip.json')
        if not draft.exists():
            write_json(draft,{'id':'V-'+job['segmentId'],'segmentId':job['segmentId'],'taskId':job['taskId'],'file':name,'seconds':info['seconds'],'qc':{'checked':False,'notes':'待逐段看片、听对白、核对口型与连续性'}})
        job.update({'status':'collected','file':name,'seconds':info['seconds'],'collectedAt':now()})
        from voices import voice_pending
        job['voiceCaptureNeeded']=voice_pending(root,job['segmentId'])
        write_json(path,job)
        return job


def retry(project,job_id,reason):
    check(bool(reason.strip()),'需要记录重做原因')
    with lock(project,'wan3'):
        old=read_json(job_path(project,job_id))
        check(old['status'] in ('failed','completed','collected'),'只有明确失败或完成的任务可以重做；unknown 先恢复')
        new_id=old['segmentId']+'-'+uuid.uuid4().hex[:16]
        job={k:old[k] for k in ('schemaVersion','segmentId','frameFingerprint','requestFingerprint','payload','media','voiceSelection','requestedVoiceCharacter')}
        job.update({'jobId':new_id,'status':'prepared','createdAt':now(),'retryOf':job_id,'reason':reason})
        write_json(job_path(project,new_id),job);return job


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('project');a.add_argument('segment');a.add_argument('--reference-audio');a.add_argument('--voice-character')
    for command in ('submit','poll','collect','attach','retry'):
        a=sub.add_parser(command);a.add_argument('project');a.add_argument('job')
        if command=='attach':a.add_argument('--task-id',required=True)
        if command=='retry':a.add_argument('--reason',required=True)
    a=p.parse_args()
    try:
        if a.command=='prepare':result=prepare(a.project,a.segment,a.reference_audio,a.voice_character)
        elif a.command=='attach':result=attach(a.project,a.job,a.task_id)
        elif a.command=='retry':result=retry(a.project,a.job,a.reason)
        else:result=globals()[a.command](a.project,a.job)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except ValueError as exc:
        p.exit(1,str(exc)+'\n')
    except (OSError,KeyError):
        # Keep credential, request headers and raw provider error bodies out of terminal output.
        p.exit(1,'操作未完成。请查看 jobs 中的状态记录；unknown/submitting 不可重复提交。配置错误时检查固定凭据文件是否可读。\n')

if __name__=='__main__':main()
