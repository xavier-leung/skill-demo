"""Deterministic per-episode cuts, audio mixing and optional subtitles via FFmpeg."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from core import local_path, read_json, write_json, file_digest, digest, now, lock


def run(args, cwd=None):
    try:
        result=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=600)
    except FileNotFoundError:
        raise ValueError('需要安装 ffmpeg 与 ffprobe') from None
    if result.returncode:
        raise ValueError('媒体处理失败: '+result.stderr.decode(errors='replace')[-1800:])
    return result.stdout


def probe(path):
    raw=run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)])
    data=json.loads(raw)
    video=next((s for s in data['streams'] if s['codec_type']=='video'),None)
    return {'seconds':float(data['format'].get('duration',0)), 'video':bool(video),
            'audio':any(s['codec_type']=='audio' for s in data['streams']),
            'subtitles':any(s['codec_type']=='subtitle' for s in data['streams']),
            'width':video.get('width',0) if video else 0,'height':video.get('height',0) if video else 0}


def render_spec(project, episode):
    clips={c['id']:c for c in read_json(Path(project)/'clips.json')['clips']}
    files={clips[c['clipId']]['file'] for c in episode['cuts']} | {t['file'] for t in episode['audioTracks']}
    sources={name:file_digest(local_path(project,name)) for name in sorted(files)}
    spec={'episode':{k:v for k,v in episode.items() if k!='qc'},'sources':sources,
          'clipFiles':{c['clipId']:clips[c['clipId']]['file'] for c in episode['cuts']},
          'aspectRatio':read_json(Path(project)/'brief.json')['aspectRatio']}
    return {'specFingerprint':digest(spec),'sources':sources}


def render_receipt(project, episode, info):
    output=local_path(project,episode['file'])
    return {'episodeId':episode['id'],'file':episode['file'],'sha256':file_digest(output),
            **render_spec(project,episode),'at':now(),'probe':info,'visualReviewRequired':True}


def srt_time(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def captions_srt(captions):
    return '\n\n'.join(f'{idx}\n{srt_time(c["start"])} --> {srt_time(c["end"])}\n{c["text"]}' for idx,c in enumerate(captions,1))+'\n'


def assemble(project, episode_id, width=None):
    from validate import validate, check
    from workflow import require_prior
    root=Path(project).resolve()
    with lock(root,'assemble'):
        require_prior(root,'edit')
        edit=validate(root,'edit',require_outputs=False)
        episodes={e['id']:e for e in edit['episodes']}
        check(episode_id in episodes,'本批没有这一集')
        ep=episodes[episode_id]
        expected_spec=render_spec(root,ep)
        output=local_path(root,ep['file'],exists=False)
        check(not output.exists(),'成片文件已存在；请换新版本路径，保留旧成片')
        brief=read_json(root/'brief.json')
        clips={c['id']:c for c in read_json(root/'clips.json')['clips']}
        width=width or (720 if brief['aspectRatio']=='9:16' else 1280)
        check(isinstance(width,int) and 32<=width<=3840 and width%2==0,'输出宽度需为 32..3840 的偶数')
        height=round(width*(16/9 if brief['aspectRatio']=='9:16' else 9/16)/2)*2
        duration=sum(c['out']-c['in'] for c in ep['cuts'])
        sources={}
        with tempfile.TemporaryDirectory(prefix='zv-edit-') as temp:
            temp=Path(temp)
            for idx,cut in enumerate(ep['cuts']):
                source=local_path(root,clips[cut['clipId']]['file'])
                sources[str(source.relative_to(root))]=file_digest(source)
                info=probe(source)
                length=cut['out']-cut['in']
                cmd=['ffmpeg','-v','error','-y','-ss',str(cut['in']),'-i',str(source)]
                if not info['audio']:
                    cmd+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
                cmd+=['-t',str(length),'-map','0:v:0','-map','0:a:0' if info['audio'] else '1:a:0',
                      '-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24',
                      '-af','aresample=48000,apad','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
                      '-c:a','aac','-ar','48000','-ac','2',str(temp/f'part{idx}.mp4')]
                run(cmd)
            (temp/'concat.txt').write_text(''.join(f"file 'part{i}.mp4'\n" for i in range(len(ep['cuts']))))
            run(['ffmpeg','-v','error','-y','-f','concat','-safe','1','-i','concat.txt','-c','copy','joined.mp4'],temp)
            cmd=['ffmpeg','-v','error','-y','-i','joined.mp4']
            filters=[f'[0:a]volume={ep["nativeGain"]},apad,atrim=0:{duration}[a0]']
            inputs=['[a0]']
            for idx,track in enumerate(ep['audioTracks'],1):
                source=local_path(root,track['file']);sources[track['file']]=file_digest(source)
                cmd+=['-i',str(source)]
                delay=round(track['offset']*1000)
                filters.append(f'[{idx}:a]aresample=48000,volume={track["gain"]},adelay={delay}:all=1,apad,atrim=0:{duration}[a{idx}]')
                inputs.append(f'[a{idx}]')
            filters.append(''.join(inputs)+f'amix=inputs={len(inputs)}:duration=first:normalize=0,alimiter=limit=0.95[aout]')
            cmd+=['-filter_complex',';'.join(filters),'-map','0:v:0','-map','[aout]','-c:v','copy','-c:a','aac','-t',str(duration),'mixed.mp4']
            run(cmd,temp)
            (temp/'captions.srt').write_text(captions_srt(ep['captions']),encoding='utf-8')
            if ep['subtitleMode']=='soft':
                run(['ffmpeg','-v','error','-y','-i','mixed.mp4','-i','captions.srt','-map','0:v:0','-map','0:a:0','-map','1:0','-c:v','copy','-c:a','copy','-c:s','mov_text','-metadata:s:s:0','language=zho','-movflags','+faststart','final.mp4'],temp)
            elif ep['subtitleMode']=='burn':
                run(['ffmpeg','-v','error','-y','-i','mixed.mp4','-vf','subtitles=captions.srt','-c:v','libx264','-preset','veryfast','-crf','20','-c:a','copy','-movflags','+faststart','final.mp4'],temp)
            else:
                run(['ffmpeg','-v','error','-y','-i','mixed.mp4','-c','copy','-movflags','+faststart','final.mp4'],temp)
            info=probe(temp/'final.mp4')
            check(info['video'] and info['audio'] and abs(info['seconds']-duration)<.4,'合成输出轨道或时长异常')
            current_ep=next(e for e in read_json(root/'edit.json')['episodes'] if e['id']==episode_id)
            check(render_spec(root,current_ep)==expected_spec,'合成过程中剪辑或输入文件变化，请重新合成')
            output.parent.mkdir(parents=True,exist_ok=True)
            # Copy to a temporary file in the destination filesystem, then atomically publish.
            fd,staged=tempfile.mkstemp(dir=output.parent,prefix='.render-');os.close(fd)
            try:
                shutil.copyfile(temp/'final.mp4',staged);os.replace(staged,output)
            finally:
                if os.path.exists(staged):os.unlink(staged)
            if ep['captions']:
                (root/'subtitles').mkdir(exist_ok=True)
                shutil.copyfile(temp/'captions.srt',root/'subtitles'/(episode_id+'-'+file_digest(output)[:10]+'.srt'))
            report=render_receipt(root,ep,info)
            write_json(output.with_suffix('.render.json'),report)
            return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project');p.add_argument('--episode',required=True);p.add_argument('--width',type=int)
    a=p.parse_args()
    try: print(json.dumps(assemble(a.project,a.episode,a.width),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')
