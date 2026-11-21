"""Read-only HTML views. No JavaScript, approval controls, server or CDN."""
import html
import json
from pathlib import Path
from string import Template
from urllib.parse import quote
from core import BASE, STAGES, LABELS, read_json, local_path, atomic_text, now

FOCUS = dict(zip(STAGES, [
    '确认改编范围、集数与时长、画幅风格，以及声音方案。',
    '确认故事主线、分集冲突、开场钩子与结尾悬念，留意新增设计。',
    '确认每个人的外貌、性格、服装、说话方式与音色方向。',
    '确认空间关系、识别锚点、光照变化与叙事道具。',
    '按场次阅读动作和台词，确认戏剧节奏与预计时长。',
    '先看脸、身形与服装；定妆确认后再生成其他视角。',
    '检查参考图一致性；每个场景核对唯一俯视图的布局、门窗、家具与通道。',
    '对照实际场景图检查开场人物位置、朝向、坐站和群演。',
    '确认镜头顺序、时长、动作、对白、运镜与状态衔接。',
    '逐格检查人物、构图、空间关系与前后连续性。',
    '逐段播放，核对动作、对白、音色、口型及衔接。',
    '完整播放，确认剪辑节奏、字幕、声音、连续性及交付画幅。']))
STATUS = {'approved':'已确认','pending':'待确认','stale':'需重新审核','changes':'待修改','ready':'可制作','locked':'未解锁'}

def esc(value):
    return html.escape(str(value), quote=True)

def field(label, value):
    if isinstance(value, (dict,list)):
        value = json.dumps(value, ensure_ascii=False)
    return f'<dt>{esc(label)}</dt><dd>{esc(value)}</dd>'

def fields(pairs):
    return '<dl>' + ''.join(field(k,v) for k,v in pairs) + '</dl>'

def card(title, content, cls=''):
    media_class=' card-with-media' if any(tag in content for tag in ('<img','<video','<svg','<audio')) else ''
    return (f'<div class="card-slot{media_class}"><article class="card expandable-card{media_class} {cls}" tabindex="0" aria-label="{esc(title)}">'
            f'<h3>{esc(title)}</h3><div class="card-body">{content}</div>'
            '<details class="card-expansion"><summary><span class="expand-label">展开完整内容</span>'
            '<span class="collapse-label">收起内容</span></summary></details></article></div>')

def origin(item):
    o = item.get('origin')
    if not o:
        return ''
    labels = {'source':'原文事实','adapted':'改编取舍','invented':'新增设计'}
    return '<div class="origin">' + esc(labels[o['kind']]) + ' · ' + esc(o['evidence']) + '<br>' + esc(o['reason']) + '</div>'

def media(project, path, kind='image'):
    local_path(project, path)
    url = '../' + quote(path, safe='/')
    if kind == 'image':
        return f'<img loading="lazy" src="{esc(url)}" alt="{esc(path)}">'
    if kind == 'video':
        return f'<video controls preload="metadata" playsinline src="{esc(url)}"></video>'
    return f'<audio controls preload="metadata" src="{esc(url)}"></audio>'


def camera_diagram(plan,spatial):
    shapes='<text x="15" y="25">机位路径 · 圆点为起点 · 虚线为视线</text>'
    for item in plan['layout']['items']:
        x,y,w,h=[item[k]*500 for k in ('x','y','width','height')]
        shapes+=f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#d5dfce" stroke="#95aa8f"/><text x="{x+3}" y="{y+16}">{esc(item["name"])}</text>'
    for box in plan['layout']['unknownZones']:
        x,y,w,h=[box[k]*500 for k in ('x','y','width','height')]
        shapes+=f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#e4c8b6" opacity=".6"/><text x="{x+3}" y="{y+16}">未知区域</text>'
    points=spatial['cameraPath']
    path=' '.join(f'{p["x"]*500},{p["y"]*500}' for p in points)
    shapes+=f'<polyline points="{path}" fill="none" stroke="#bf7325" stroke-width="4"/>'
    for idx,p in enumerate(points):
        x,y=p['x']*500,p['y']*500
        shapes+=f'<line x1="{x}" y1="{y}" x2="{p["lookAtX"]*500}" y2="{p["lookAtY"]*500}" stroke="#bf7325" stroke-dasharray="5 5"/><circle cx="{x}" cy="{y}" r="7" fill="#bf7325"/><text x="{x+10}" y="{y}">{idx+1}</text>'
    return '<svg class="plan" viewBox="0 0 500 500" role="img" aria-label="俯视图上的机位路径">'+shapes+'</svg>'


def voice_panel(project):
    from voices import library
    voices=library(project)
    if not voices:return '<p class="empty">角色首次说话视频生成后，在此建立音色库。</p>'
    cards=''
    for id,voice in voices.items():
        source=voice['source']
        cards+=card(id+' · '+voice['characterName'],media(project,voice['file'],'audio')+fields([('实际音色',voice['description']),('来源分段',source['segmentId']),('截取范围',f'{source["start"]:g}–{source["end"]:g} 秒'),('来源台词',source['transcript']),('状态','已上传，可复用' if voice['status']=='ready' else '已保存，待上传'),('试听检查',voice['qc']['notes'])]))
    return '<div class="grid">'+cards+'</div>'

def scene_relation_panel(project,stage=None,image=None):
    from scene_relations import relations,context
    if image is None:
        rows=relations(project);targets=[]
    else:
        info=context(project,stage,image);rows=info['relations'];targets=info['images']
    if not rows:return ''
    body=''
    for relation in rows:
        body+=card(relation['id']+' · '+relation['fromSceneId']+' ↔ '+relation['toSceneId'],fields([
            ('连接方式',relation['connection']),('相对位置',relation['relativePosition']),
            ('方向与角度',relation['orientation']),('相对尺度',relation['relativeScale']),
            ('依据',relation['basis']+' · '+relation['evidence']),('待确认',relation['unknowns'] or '无')]))
    if targets:
        body+='<div class="grid">'+''.join(card(p['entityId']+' · '+p['id']+' · '+p['view'],media(project,p['file'])) for p in targets)+'</div>'
    return '<details class="adjacency"><summary>相邻场景关系与对照图 · '+str(len(rows))+' 条关系</summary>'+body+'</details>'


def content(project, stage, data, tiles_only=False):
    def get(stage):
        return read_json(Path(project) / (stage + '.json'))
    if stage == 'brief':
        labels = {'title':'剧名','sourceFile':'原文','mode':'制作模式','genre':'题材','episodeCount':'总集数','episodeSeconds':'每集秒数','scope':'本批集数','aspectRatio':'画幅','style':'画风','adaptationScope':'改编范围','audioMode':'声音方案','subtitles':'字幕','imageProvider':'图像工具'}
        conversion=read_json(local_path(project,'source/conversion.json'))
        summary=fields([('唯一小说参考','source/novel.md'),('转换方法',conversion['method']),('字符数',conversion['characters']),('正文核对',conversion['reviewNotes'])])
        from video_backend import settings
        return card('制作约定', fields([(v,data[k]) for k,v in labels.items()]+[('已发现的视频能力',settings(data))]))+card('小说已统一为 Markdown',summary+'<p><a href="../source/novel.md">查看规范小说 MD</a></p>')
    if stage == 'outline':
        body = card('故事核心', fields([('一句话主线',data['logline']),('改编说明',data['adaptationNotes'])]))
        body += '<div class="grid">' + ''.join(card(c['name'], fields([('定位',c['role']),('人物变化',c['arc'])])+origin(c)) for c in data['characters']) + '</div><h2>分集故事</h2>'
        body += ''.join(card(ep['id'], fields([('开场钩子',ep['hook']),('本集梗概',ep['synopsis']),('结尾悬念',ep['cliffhanger'])])+origin(ep), 'episode') for ep in data['episodes'])
        return body+'<h2>故事资产</h2><div class="grid">'+''.join(card(x['id']+' · '+x['name'],esc(x['purpose'])) for x in data['scenes']+data['props'])+'</div>'
    if stage == 'characters':
        labels = [('外貌','appearance'),('性格','personality'),('服装','costume'),('说话方式','speechStyle'),('音色方向','voiceDirection'),('定妆提示词','imagePrompt')]
        return '<div class="grid">'+''.join(card(c['id']+' · '+c['name'], fields([(l,c[k]) for l,k in labels])+origin(c)) for c in data['characters'])+'</div>'
    if stage == 'art':
        return scene_relation_panel(project)+'<h2>场景</h2><div class="grid">'+''.join(card(s['id']+' · '+s['name'], fields([('布局',s['layout']),('识别锚点',' / '.join(s['anchors'])),('光照',' / '.join(s['lighting'])),('出图提示词',s['imagePrompt'])])) for s in data['scenes'])+'</div><h2>叙事道具</h2><div class="grid">'+''.join(card(p['id']+' · '+p['name'], fields([('尺度',p['scale']),('状态',' / '.join(p['states'])),('出图提示词',p['imagePrompt'])])) for p in data['props'])+'</div>'
    if stage == 'script':
        names = {c['id']:c['name'] for c in get('characters')['characters']}
        body = ''
        for ep in data['episodes']:
            seconds = sum(b['seconds'] for s in ep['scenes'] for b in s['beats'])
            body += f'<h2>{esc(ep["id"])} <span class="time">预计 {seconds:g} 秒 / 目标 {ep["targetSeconds"]:g} 秒</span></h2>'
            for scene in ep['scenes']:
                beats = ''
                for b in scene['beats']:
                    speaker = names.get(b.get('speaker'), '动作')
                    text = esc(b['text'])
                    if b['type'] == 'dialogue':
                        text = '<b>'+esc(speaker)+'</b> · '+esc(b['delivery'])+'<p>“'+text+'”</p>'
                    beats += f'<div class="beat {b["type"]}"><small>{esc(b["id"])}<br>{b["seconds"]:g} 秒</small><div>{text}</div></div>'
                body += card(scene['id']+' · '+scene['locationId']+' / '+scene['lighting'], beats)
        return body
    if stage in ('anchors','assets','floorplans','frames'):
        body = ''
        grouped={}
        frame_groups={}
        images=data['images']
        entities={c['id']:c['name'] for c in get('characters')['characters']}
        entities.update({x['id']:x['name'] for x in get('art')['scenes']+get('art')['props']})
        if stage=='frames':
            board=get('storyboard')['segments']
            segs={seg['id']:seg for seg in board}
            order={shot['id']:idx for idx,shot in enumerate(s for seg in board for s in seg['shots'])}
            images=sorted(images,key=lambda img:(order[img['shotId']],img['role']=='last_frame'))
        for image in images:
            label = image.get('entityId', image.get('segmentId','')) + ' · ' + image.get('view',image.get('role',''))
            if 'entityId' in image:
                label=entities.get(image['entityId'],image['entityId'])+' / '+label
            if stage=='frames':
                seg=segs[image['segmentId']]
                label=seg['episodeId']+' / '+seg['sceneId']+' / '+seg['id']+' / '+image['shotId']+' · '+image['role']
            details = scene_relation_panel(project,stage,image)+fields([('用途',label),('提示词',image['prompt']),('参考图',' / '.join(image['referenceIds']) or '无')])
            visual=media(project,image['file'])
            if stage=='floorplans':
                scene_images={a['id']:a for a in get('assets')['images']}
                visual='<div class="two-col"><div><p>来源场景图</p>'+media(project,scene_images[image['referenceIds'][0]]['file'])+'</div><div><p>对应场景俯视图</p>'+visual+'</div></div>'
                details+=fields([('坐标方向',image['layout']['orientation']),('空间关系',image['layout']['spatialNotes']),('未知区域',image['layout']['unknownZones']),('物件布局',image['layout']['items'])])
            title=image['id']+' · '+image.get('view',image.get('role',''))
            if stage=='frames':
                role={'keyframe':'关键帧','first_frame':'首帧','last_frame':'尾帧'}[image['role']]
                title=image['shotId']+' · '+role+' · '+image['id']
            tile=card(title, visual+details)
            body += tile
            if stage=='frames':
                frame_groups.setdefault(image['segmentId'],[]).append(tile)
            if 'entityId' in image:
                grouped.setdefault(image['entityId'],[]).append(tile)
        if stage=='frames':
            episodes={}
            for seg in board:
                episodes.setdefault(seg['episodeId'],[]).append(seg)
            sections=[]
            for episode_id,segments in episodes.items():
                episode_title='第 '+str(int(episode_id[1:]))+' 集' if episode_id.startswith('E') and episode_id[1:].isdigit() else episode_id
                parts=[]
                for number,seg in enumerate(segments,1):
                    tiles=frame_groups.get(seg['id'],[])
                    seconds=sum(shot['seconds'] for shot in seg['shots'])
                    mode='逐镜关键帧' if seg['mode']=='ref2v' else '首尾帧'
                    parts.append('<section class="frame-segment"><header class="frame-segment-heading"><h3>小段 '+str(number)+' <span>'+esc(seg['id'])+'</span></h3><p>'+esc(seg['sceneId'])+' · '+mode+' · '+f'{seconds:g}'+' 秒 · '+str(len(tiles))+' 张图</p></header><div class="grid">'+(''.join(tiles) or '<p class="empty">本小段尚无分镜图</p>')+'</div></section>')
                sections.append('<section class="frame-episode"><header class="frame-episode-heading"><h2>'+esc(episode_title)+' <span>'+esc(episode_id)+'</span></h2><p>'+str(len(segments))+' 个小段</p></header>'+''.join(parts)+'</section>')
            return '<p class="lead">按集 → 小段 → 镜头顺序查看；同一小段的关键帧或首尾帧集中展示。</p>'+''.join(sections)
        if tiles_only:
            return body
        if stage=='assets':
            # Keep the unique floorplan beside this scene's state images.
            for plan in get('floorplans')['images']:
                plan_html=content(project,'floorplans',{'images':[plan]},tiles_only=True)
                grouped.setdefault(plan['entityId'],[]).append(plan_html)
        if grouped:
            return '<p><a href="asset-library.html">查看全部人物、道具与场景状态 →</a></p>'+''.join(
                '<section class="asset-group"><h2>'+esc(entities.get(id,id))+' <span class="time">'+esc(id)+' · '+str(len(tiles))+' 张</span></h2><div class="grid">'+''.join(tiles)+'</div></section>' for id,tiles in grouped.items())
        return '<div class="grid">'+body+'</div>'
    if stage == 'staging':
        assets = {a['id']:a for a in get('assets')['images']}
        plans={a['id']:a for a in get('floorplans')['images']}
        locs = {l['id']:l for l in data['locations']}
        names = {c['id']:c['name'] for c in get('characters')['characters']}
        body = ''
        for scene in data['scenes']:
            loc = locs[scene['locationId']]
            shapes = '<text x="20" y="28">俯视示意 · 上北下南</text>'
            for item in loc['items']:
                x,y,w,h = [item[k]*500 for k in ('x','y','width','height')]
                shapes += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#d5dfce" stroke="#95aa8f" rx="5"/><text x="{x+5}" y="{y+18}">{esc(item["name"])}</text>'
            for p in scene['places']:
                if p['present']:
                    x,y=p['x']*500,p['y']*500
                    shapes += f'<circle cx="{x}" cy="{y}" r="13" fill="#35735e"/><text x="{x+18}" y="{y+4}">{esc(names[p["characterId"]])} · {esc(p["facing"])}</text>'
            plan = '<svg class="plan" viewBox="0 0 500 500" role="img" aria-label="开场站位俯视图">'+shapes+'</svg>'
            pictures = '<div class="grid"><div><p>场景图</p>'+media(project,assets[loc['imageId']]['file'])+'</div><div><p>已确认俯视图</p>'+media(project,plans[loc['floorplanId']]['file'])+'</div><div><p>开场人物站位</p>'+plan+'</div></div>'
            postures={'standing':'站立','sitting':'坐姿','lying':'躺卧'}
            people='\n'.join(names[p['characterId']]+'：'+ ('开场在场，'+postures[p['posture']]+'，朝向 '+p['facing']+'，参照 '+p['itemId'] if p['present'] else '开场不在场') for p in scene['places'])
            body += card(scene['sceneId'], pictures+fields([('开场',scene['opening']),('人物位置',people),('群演',str(scene['extras'])+' · '+scene['extrasWhere'])]))
        return body
    if stage == 'storyboard':
        beats = {b['id']:b for ep in get('script')['episodes'] for s in ep['scenes'] for b in s['beats']}
        plans={p['id']:p for p in get('floorplans')['images']}
        body = ''
        for seg in data['segments']:
            timeline = '<div class="timeline">'+''.join(f'<span style="flex:{shot["seconds"]}">{esc(shot["id"])} · {shot["seconds"]:g}s</span>' for shot in seg['shots'])+'</div>'
            mode_name='ref2v · 多图参考生视频' if seg['mode']=='ref2v' else '首尾帧 · 起止状态约束'
            plan_fields=[('视频生成方式',mode_name),('选择理由',seg.get('modeReason','待补充选择理由'))]
            if seg['mode']=='ref2v':
                plan_fields.append(('需生成分镜帧',str(len(seg['shots']))+' 张（每镜一张关键帧；另携带已确认场景俯视图，不计入分镜帧数）'))
            else:
                plan_fields.append(('画面要求','首帧对应动作开始，尾帧对应动作完成；视频请求同时携带场景俯视图。'))
            shots = ''
            for shot in seg['shots']:
                dialogue = '\n'.join((beats[b].get('speaker','动作')+'：'+beats[b]['text']) for b in shot['beatIds'])
                shots += card(shot['id']+' · '+shot['size']+' · '+str(shot['seconds'])+' 秒',camera_diagram(plans[shot['spatial']['floorplanId']],shot['spatial'])+fields([('动作',shot['action']),('机位与运镜',shot['camera']),('空间依据',shot['spatial']['floorplanId']),('预期背景变化',shot['spatial']['backgroundChange']),('构图',shot['composition']),('对白与节拍',dialogue),('起始状态',shot['startState']),('结束状态',shot['endState']),('画面提示词',shot['framePrompt'])]))
            body += '<section class="storyboard-segment"><h2>'+esc(seg['episodeId']+' / '+seg['id'])+'</h2>'+fields(plan_fields)+timeline+'<div class="grid">'+shots+'</div>'+fields([('声音',seg['sound'])])+'</section>'
        return body
    if stage == 'clips':
        segments=get('storyboard')['segments']
        order={s['id']:i for i,s in enumerate(segments)}
        clips=sorted(data['clips'],key=lambda c:order[c['segmentId']])
        beats={b['id']:b for ep in get('script')['episodes'] for s in ep['scenes'] for b in s['beats']}
        names={c['id']:c['name'] for c in get('characters')['characters']}
        cards=''
        for clip in clips:
            seg=segments[order[clip['segmentId']]]
            lines='\n'.join(names.get(beats[b].get('speaker'),'动作')+'：'+beats[b]['text'] for shot in seg['shots'] for b in shot['beatIds'])
            cards+=card(seg['episodeId']+' / '+seg['id'],media(project,clip['file'],'video')+fields([('时长',str(clip['seconds'])+' 秒'),('对应动作与对白',lines),('检查',clip['qc']['notes'])]))
        return '<div class="grid">'+cards+'</div><h2>角色音色库</h2>'+voice_panel(project)
    if stage == 'edit':
        body = ''
        for ep in data['episodes']:
            caps = '\n'.join(f'{c["start"]:g}–{c["end"]:g}s  {c["text"]}' for c in ep['captions']) or '无字幕'
            body += card(ep['id'],media(project,ep['file'],'video')+fields([('剪辑顺序',' → '.join(c['clipId'] for c in ep['cuts'])),('字幕',caps),('检查',ep['qc']['notes'])]))
        return body
    raise ValueError('没有展示模板: '+stage)


def render(project, stage, states, record=None):
    project = Path(project)
    data = read_json(project / (stage+'.json'))
    record = record or {}
    title = read_json(project / 'workflow.json')['title']
    state_map = {x['id']:x['status'] for x in states}
    nav = ''
    for idx,s in enumerate(STAGES,1):
        text = f'<b>{idx:02d}</b>{LABELS[s]}<i>{STATUS[state_map[s]]}</i>'
        if (project / (s+'.json')).exists() and (s == stage or (project/'reports'/(s+'.html')).exists()):
            nav += f'<a href="{s}.html" class="{"active" if s==stage else ""}">{text}</a>'
        else:
            nav += '<span>'+text+'</span>'
    partial = Template((BASE/'templates'/(stage+'.html')).read_text()).substitute(label=LABELS[stage],content=content(project,stage,data))
    if STAGES.index(stage)>=STAGES.index('anchors'):
        from production_scope import selection
        scope=selection(project)
        scope_label='仅第一集（默认）' if scope['mode']=='first' else '所有集'
        partial=card('本次制作范围：'+scope_label,'<p>'+esc('、'.join(scope['episodeIds']))+'</p><p>范围适用于人物、道具、场景、俯视图、站位、分镜图及后续视频。若需切换，请在聊天中说明“仅第一集”或“所有集”；此页只展示。</p>')+partial
    page = Template((BASE/'templates'/'review.html').read_text()).substitute(
        title=esc(title), stage_label=LABELS[stage], css=(BASE/'templates'/'style.css').read_text(),
        nav=nav, number=f'{STAGES.index(stage)+1:02d}', status=STATUS[state_map[stage]], focus=FOCUS[stage],
        revision=record.get('revision','—'),time=esc(now()),fingerprint=esc(record.get('fingerprint','未提交')),
        content=partial,raw=esc(json.dumps(data,ensure_ascii=False,indent=2)))
    dest=project/'reports'/(stage+'.html')
    atomic_text(dest,page)
    if stage in ('anchors','assets','floorplans'):
        render_asset_library(project)
    return str(dest.resolve())


def render_asset_library(project):
    project=Path(project);groups={};names={}
    for stage,key in [('characters','characters'),('art','scenes'),('art','props')]:
        path=project/(stage+'.json')
        if path.exists():names.update({x['id']:x['name'] for x in read_json(path)[key]})
    for stage in ('anchors','assets','floorplans'):
        path=project/(stage+'.json')
        if not path.exists():continue
        for img in read_json(path)['images']:
            detail=scene_relation_panel(project,stage,img)+fields([('状态 / 视角',img['view']),('提示词',img['prompt'])])
            try:visual=media(project,img['file'])
            except (ValueError,OSError):visual='<p class="notice">图片文件不可用，请修复后重新审核。</p>'
            tile=card(img['id'],visual+detail)
            groups.setdefault(img['entityId'],[]).append(tile)
    body=''.join('<section class="asset-group"><h2>'+esc(names.get(id,id))+' <span class="time">'+esc(id)+' · '+str(len(tiles))+' 张状态图</span></h2><div class="grid">'+''.join(tiles)+'</div></section>' for id,tiles in groups.items())
    css=(BASE/'templates/style.css').read_text()
    atomic_text(project/'reports/asset-library.html','<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>人物、道具与场景状态库</title><style>'+css+'</style><body class="overview"><main class="dashboard"><a class="back-link" href="index.html">← 返回制作总览</a><div class="eyebrow">ASSET LIBRARY</div><h1>人物、道具与场景状态库</h1><p class="lead">同一实体的定妆、视角、剧情状态与俯视图归在同组。图片由用户在对应步骤确认；同一场景的状态复用唯一俯视图。</p>'+body+'<footer>只读快照 · '+esc(now())+'</footer></main></body></html>')


def render_index(project, states):
    project=Path(project)
    workflow=read_json(project/'workflow.json')
    title=esc(workflow['title'])
    approved=sum(s['status']=='approved' for s in states)
    current=next((s for s in states if s['status']!='approved'),None)
    demo=workflow.get('demo',False)
    phases=''
    groups=[('01 / STORY','故事策划','先确定故事，再定义人物与世界。',states[:5]),
            ('02 / VISUAL','视觉制作','让人物、场景和镜头保持一致。',states[5:10]),
            ('03 / DELIVERY','视频交付','生成片段，校准声音，完成剪辑。',states[10:])]
    for label,name,description,rows in groups:
        items=''
        for row in rows:
            stage=row['id'];number=STAGES.index(stage)+1
            exists=(project/'reports'/(stage+'.html')).exists()
            state='示例预览' if demo and exists else STATUS[row['status']]
            text=f'<span class="step-number">{number:02d}</span><span class="step-copy"><strong>{LABELS[stage]}</strong><small>{state}</small></span><span class="step-arrow" aria-hidden="true">↗</span>'
            cls='step-row is-'+row['status']
            items+=(f'<a class="{cls}" href="{stage}.html">{text}</a>' if exists else f'<div class="{cls} unavailable">{text}</div>')
        phases+=f'<section class="phase"><div class="phase-heading"><div class="eyebrow">{label}</div><h3>{name}</h3><p>{description}</p></div><div class="steps">{items}</div></section>'
    scope='仅第一集 · 默认';format_label='制作参数待确定'
    if (project/'brief.json').exists():
        from production_scope import selection
        selected=selection(project)
        scope='仅第一集 · E01' if selected['mode']=='first' else '所有集 · '+str(len(selected['episodeIds']))+' 集'
        brief=read_json(project/'brief.json')
        format_label=brief['aspectRatio']+' 画幅 · '+str(brief['episodeSeconds'])+' 秒 / 集'
    current_text='全部步骤已确认' if current is None else '当前步骤 · '+LABELS[current['id']]+' / '+STATUS[current['status']]
    if demo:current_text='模板演示 · 不代表真实制作进度'
    target='script' if demo else (current['id'] if current else 'edit')
    has_target=(project/'reports'/(target+'.html')).exists()
    continue_link=f'<a class="primary-link" href="{target}.html">'+('预览分集剧本' if demo else '查看当前步骤')+' <span aria-hidden="true">→</span></a>' if has_target else '<span class="empty">本步骤产出后，可在此查看</span>'
    voices='<a class="secondary-link" href="voices.html">进入音色库 ↗</a>' if (project/'reports/voices.html').exists() else '<span class="empty">声音样本建立后开放</span>'
    if (project/'reports/asset-library.html').exists():
        voices='<a class="secondary-link" href="asset-library.html">查看素材状态库 ↗</a> '+voices
    page=Template((BASE/'templates/index.html').read_text()).substitute(
        title=title,css=(BASE/'templates/style.css').read_text(),scope=esc(scope),format=esc(format_label),
        demo='示例项目 · 非真实制作' if demo else '在聊天中逐步确认',approved=approved,percent=round(approved/len(STAGES)*100),
        current=esc(current_text),continue_link=continue_link,phases=phases,voices=voices,time=esc(now()))
    atomic_text(project/'reports/index.html',page)
