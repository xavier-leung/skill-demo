"""Capture character voice from the first usable generated speaking clip and reuse it."""
import argparse
import array
import json
import math
from pathlib import Path
import tempfile
import wave
from core import BASE, STAGES, read_json, write_json, local_path, file_digest, fingerprint, digest, now, lock, atomic_text
from validate import schema_check, check
from assemble import probe, run


def character_fingerprint(character):
    # Costume/image changes do not change the locked voice; intentional voice direction changes do.
    return digest({'id':character['id'],'voiceDirection':character['voiceDirection']})


def segment_dialogue(project,segment):
    script=read_json(Path(project)/'script.json')
    beats={b['id']:b for ep in script['episodes'] for s in ep['scenes'] for b in s['beats']}
    return [beats[id] for shot in segment['shots'] for id in shot['beatIds'] if beats[id]['type']=='dialogue']


def first_speech(project):
    first={}
    for seg in read_json(Path(project)/'storyboard.json')['segments']:
        for beat in segment_dialogue(project,seg):first.setdefault(beat['speaker'],seg['id'])
    return first


def library(project,require_ready=False):
    path=local_path(project,'voices.json',exists=False)
    data=read_json(path) if path.exists() else {'schemaVersion':1,'voices':[]}
    schema_check(data,read_json(BASE/'schemas/voices.schema.json'))
    chars={c['id']:c for c in read_json(Path(project)/'characters.json')['characters']}
    result={}
    from wan3 import safe_url
    for voice in data['voices']:
        id=voice['characterId'];check(id in chars and id not in result,'音色角色不存在或重复: '+id)
        check(character_fingerprint(chars[id])==voice['characterFingerprint'],'角色音色设定已改变，请明确处理既有音色，不要偷偷换声: '+id)
        check(file_digest(local_path(project,voice['file']))==voice['sha256'],'已锁定音色文件发生变化: '+id)
        try:
            with wave.open(str(local_path(project,voice['file'])),'rb') as audio:
                check(audio.getnframes()>0 and audio.getsampwidth()==2 and audio.getnchannels()==1,'音色必须为已提取的非空单声道 PCM WAV')
        except (wave.Error,EOFError):raise ValueError('音色文件不是有效 WAV: '+id) from None
        check(voice['source']['start']<voice['source']['end'],'音色来源时间范围无效')
        check((voice['status']=='ready' and safe_url(voice['url'])) or (voice['status']=='local' and not voice['url']),'音色上传状态/URL不一致')
        if require_ready:check(voice['status']=='ready','音色已截取但尚未上传: '+id)
        result[id]=voice
    return result


def choose(project,segment,voice_character=None):
    brief=read_json(Path(project)/'brief.json')
    dialogues=segment_dialogue(project,segment)
    speakers=list(dict.fromkeys(b['speaker'] for b in dialogues))
    if voice_character:check(voice_character in speakers,'指定音色的角色必须在本段实际说话')
    from video_backend import settings
    config=settings(brief)
    if brief['audioMode']!='native' or not config.get('audio',False):
        check(voice_character is None,'当前为独立配音/静音方案，不向视频模型传角色音色')
        return {'speakers':speakers,'selectedCharacterId':None,'bootstrap':[],'unreferenced':speakers,'reason':'external_or_muted','voices':{}},None
    if not speakers:
        check(voice_character is None,'本段没有对白，不携带角色音色')
        return {'speakers':[],'selectedCharacterId':None,'bootstrap':[],'unreferenced':[],'reason':'no_dialogue','voices':{}},None
    known=library(project);first=first_speech(project)
    for id in speakers:
        if id not in known:
            check(first[id]==segment['id'],f'{id} 尚未建立音色：先生成 {first[id]} 并截取、上传该角色独立对白，不能先生成后续段')
        else:check(known[id]['status']=='ready','音色尚未上传，请先 voices publish: '+id)
    snapshots={id:digest(known[id]) for id in speakers if id in known}
    bootstrap=[id for id in speakers if id not in known]
    chosen=None
    if segment['mode']=='ref2v' and config.get('maxReferenceAudio',0)>0:
        eligible=[id for id in speakers if id in known]
        if voice_character:
            check(voice_character in known,'指定角色尚无音色，首次说话生成后再建立音色库')
            chosen=voice_character
        elif eligible:
            totals={id:sum(b['seconds'] for b in dialogues if b['speaker']==id) for id in eligible}
            chosen=max(eligible,key=lambda id:totals[id])
        reason='single_reference_audio' if chosen else 'first_voice_bootstrap'
    else:
        check(voice_character is None,'所选模式或工具不支持参考音频；不能强行加入该参数')
        reason='mode_has_no_reference_audio'
    info={'speakers':speakers,'selectedCharacterId':chosen,'bootstrap':bootstrap,
          'unreferenced':[id for id in speakers if id!=chosen], 'reason':reason,'voices':snapshots}
    return info,known.get(chosen)


def validate_selected(project,job):
    board=read_json(Path(project)/'storyboard.json')
    seg=next((s for s in board['segments'] if s['id']==job['segmentId']),None)
    check(seg is not None,'音色绑定的分段不存在')
    current,_=choose(project,seg,job.get('requestedVoiceCharacter'))
    check(current==job.get('voiceSelection'),'角色音色库已变化，请重新 prepare，以免遗漏或使用旧音色 URL')


def voice_pending(project,segment_id):
    brief=read_json(Path(project)/'brief.json')
    from video_backend import settings
    config=settings(brief)
    if brief['audioMode']!='native' or not config.get('audio',False):return []
    first=first_speech(project);known=library(project)
    return [id for id,seg in first.items() if seg==segment_id and id not in known]


def publish(project,character_id,transport=None,url=None):
    from wan3 import upload_file,http_json,safe_url
    with lock(project,'voices'):
        known=library(project);check(character_id in known,'还没有截取该角色音色')
        voice=known[character_id]
        # Explicit publish may refresh an expired URL; audio itself remains immutable.
        if url is None:url=upload_file(local_path(project,voice['file']),transport or http_json)
        check(safe_url(url),'需要实际上传完成后的有效音频 URL')
        voice.update({'status':'ready','url':url})
        write_json(local_path(project,'voices.json'),{'schemaVersion':1,'voices':list(known.values())})
    render_library(project)
    return voice


def capture(project,character_id,job_id,start,end,beat_ids,description,notes,transport=None):
    from wan3 import job_path
    from workflow import require_prior
    root=Path(project).resolve()
    check(math.isfinite(start) and math.isfinite(end) and 0<=start<end,'音色截取时间无效')
    check(bool(description.strip()) and bool(notes.strip()),'需记录实际音色描述和已试听检查结果')
    require_prior(root,'clips')
    with lock(root,'voices'):
        known=library(root)
        if character_id in known:
            existing=known[character_id]
            check(existing['source']['jobId']==job_id and existing['source']['start']==start and existing['source']['end']==end and existing['source']['beatIds']==beat_ids,'角色已有锁定音色，不能用后续视频自动覆盖')
            if existing['status']=='ready':return existing
        else:
            job=read_json(job_path(root,job_id))
            check(job['status']=='collected','先下载并检查说话视频，再截取音色')
            check(job.get('frameFingerprint')==fingerprint(root,'frames')['fingerprint'],'来源视频的画面/剧本版本已变化，请核对来源后重新制作')
            first=first_speech(root)
            check(first.get(character_id)==job['segmentId'],'应从该角色首个说话分段建立音色')
            seg=next(s for s in read_json(root/'storyboard.json')['segments'] if s['id']==job['segmentId'])
            dialogue={b['id']:b for b in segment_dialogue(root,seg)}
            check(bool(beat_ids) and len(set(beat_ids))==len(beat_ids),'需要本角色实际说出的独立对白节拍')
            check(all(id in dialogue and dialogue[id]['speaker']==character_id for id in beat_ids),'所选对白节拍包含其他角色或不存在')
            positions=[list(dialogue).index(id) for id in beat_ids]
            check(positions==sorted(positions),'音色台词必须保持原说话顺序')
            source=local_path(root,job['file']);info=probe(source)
            receipt=read_json(local_path(root,'jobs/'+job_id+'-download.json'))
            check(receipt['sha256']==file_digest(source),'音色来源视频与下载记录不匹配')
            check(info['audio'] and end<=info['seconds']+.01 and end-start>=.3,'来源缺少音轨、越界或可用对白过短')
            chars={c['id']:c for c in read_json(root/'characters.json')['characters']}
            check(character_id in chars,'未知音色角色')
            key=digest({'source':file_digest(source),'start':start,'end':end,'character':character_id})[:16]
            name=f'audio/voices/{character_id}-{key}.wav';dest=local_path(root,name,exists=False)
            dest.parent.mkdir(parents=True,exist_ok=True)
            # Preserve the original timbre: crop only, without pitch shifting or synthesized replacement.
            with tempfile.TemporaryDirectory(prefix='zv-voice-') as temp:
                audio=Path(temp)/'voice.wav'
                run(['ffmpeg','-v','error','-y','-i',str(source),'-ss',str(start),'-t',str(end-start),'-map','0:a:0','-vn','-ac','1','-ar','48000','-c:a','pcm_s16le',str(audio)])
                with wave.open(str(audio),'rb') as wav:
                    samples=array.array('h',wav.readframes(wav.getnframes()))
                    check(bool(samples) and any(samples),'截取结果为空或全静音；不能登记为角色音色')
                if dest.exists():check(file_digest(dest)==file_digest(audio),'候选音色路径已存在不同内容，未覆盖')
                else:dest.write_bytes(audio.read_bytes())
            voice={'characterId':character_id,'characterName':chars[character_id]['name'],'characterFingerprint':character_fingerprint(chars[character_id]),
                   'description':description,'file':name,'sha256':file_digest(dest),'url':'','status':'local',
                   'source':{'jobId':job_id,'segmentId':job['segmentId'],'taskId':job['taskId'],'videoFile':job['file'],'videoSha256':file_digest(source),'start':start,'end':end,'beatIds':beat_ids,'transcript':'\n'.join(dialogue[id]['text'] for id in beat_ids)},
                   'qc':{'checked':True,'singleSpeaker':True,'cleanSpeech':True,'notes':notes},'createdAt':now()}
            known[character_id]=voice
            write_json(local_path(root,'voices.json',exists=False),{'schemaVersion':1,'voices':list(known.values())})
    # Persist local extraction first: an upload failure resumes publish, never re-generates video.
    render_library(root)
    return publish(root,character_id,transport)


def render_library(project):
    from render import voice_panel,esc
    css=(BASE/'templates/style.css').read_text()
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>角色音色库</title><style>'+css+'main{margin:auto}</style><main><h1>角色音色库</h1><p>从角色首个说话视频保留的声音参考。请试听，在聊天里反馈；参考音频不能保证模型完全复现音色。</p>'+voice_panel(project)+'<footer>本地只读快照 · '+esc(now())+'</footer></main></html>'
    atomic_text(Path(project)/'reports/voices.html',page)
    return str((Path(project)/'reports/voices.html').resolve())


def main():
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='command',required=True)
    a=subs.add_parser('capture');a.add_argument('project');a.add_argument('character');a.add_argument('--job',required=True);a.add_argument('--start',type=float,required=True);a.add_argument('--end',type=float,required=True);a.add_argument('--beat',action='append',required=True);a.add_argument('--description',required=True);a.add_argument('--notes',required=True)
    a=subs.add_parser('publish');a.add_argument('project');a.add_argument('character');a.add_argument('--url')
    for name in ('status','render'):
        a=subs.add_parser(name);a.add_argument('project')
    args=p.parse_args()
    try:
        if args.command=='capture':result=capture(args.project,args.character,args.job,args.start,args.end,args.beat,args.description,args.notes)
        elif args.command=='publish':result=publish(args.project,args.character,url=args.url)
        elif args.command=='render':result=render_library(args.project)
        else:result=library(args.project)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError) as exc:p.exit(1,str(exc)+'\n')

if __name__=='__main__':main()
