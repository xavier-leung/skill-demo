"""Validate the bundled JSON Schema subset and cross-stage production contracts."""
import argparse
from collections import Counter
import math
import re
import struct
import xml.etree.ElementTree as ET
from core import BASE, STAGES, read_json, stage_path, local_path, file_refs


def check(ok, message):
    if not ok:
        raise ValueError(message)


def schema_check(value, schema, path='$'):
    if 'const' in schema:
        check(type(value) is type(schema['const']) and value == schema['const'], f'{path}: 必须为 {schema["const"]}')
    if 'enum' in schema:
        check(value in schema['enum'], f'{path}: 不在允许值中')
    kind = schema.get('type')
    types = {'object':dict, 'array':list, 'string':str, 'integer':int, 'boolean':bool}
    if kind == 'number':
        check(type(value) in (int, float) and math.isfinite(value), f'{path}: 需要有限数字')
    elif kind:
        check(type(value) is types[kind], f'{path}: 需要 {kind}')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 'minimum' in schema:
        check(value >= schema['minimum'], f'{path}: 小于最小值')
    if kind == 'string':
        check(len(value.strip()) >= schema.get('minLength', 0), f'{path}: 字符串不能为空')
    if kind == 'object':
        for key in schema.get('required', []):
            check(key in value, f'{path}: 缺少 {key}')
        props = schema.get('properties', {})
        for key, val in value.items():
            check(key in props or schema.get('additionalProperties', True), f'{path}: 未定义字段 {key}')
            if key in props:
                schema_check(val, props[key], path + '.' + key)
    if kind == 'array':
        check(len(value) >= schema.get('minItems', 0), f'{path}: 条目不足')
        for idx, val in enumerate(value):
            schema_check(val, schema['items'], f'{path}[{idx}]')


def indexed(items, key='id'):
    values = [x[key] for x in items]
    check(len(values) == len(set(values)), f'{key}: ID 重复')
    for value in values:
        check(re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', value), f'无效 ID: {value}')
    return dict(zip(values, items))


def refs(values, allowed, label):
    check(len(values) == len(set(values)), f'{label}: 重复引用')
    for value in values:
        check(value in allowed, f'{label}: 未知引用 {value}')


def exact(actual, expected, label):
    check(set(actual) == set(expected) and len(actual) == len(set(actual)), f'{label}: 缺失/重复/多余条目 {set(actual) ^ set(expected)}')


def load(project, stage):
    data = read_json(stage_path(project, stage))
    schema_check(data, read_json(BASE / 'schemas' / (stage + '.schema.json')))
    return data


def script_scenes(project):
    from production_scope import episodes
    return [s for ep in episodes(project) for s in ep['scenes']]


def image_check(path):
    with open(path,'rb') as handle:
        head=handle.read(32)
    suffix=path.suffix.lower()
    valid=False
    if suffix=='.png':
        valid=len(head)>=24 and head[:8]==b'\x89PNG\r\n\x1a\n' and head[12:16]==b'IHDR' and all(struct.unpack('>II',head[16:24]))
    elif suffix in ('.jpg','.jpeg'):
        valid=head.startswith(b'\xff\xd8\xff')
    elif suffix=='.webp':
        valid=head[:4]==b'RIFF' and head[8:12]==b'WEBP'
    elif suffix=='.svg':
        try: valid=ET.parse(path).getroot().tag in ('svg','{http://www.w3.org/2000/svg}svg')
        except ET.ParseError: valid=False
    check(valid,'不是可识别的图片文件: '+str(path.name))


def media_index(project):
    return indexed(load(project, 'anchors')['images'] + load(project, 'assets')['images'] + load(project,'floorplans')['images'])


def validate(project, stage, require_outputs=True):
    data = load(project, stage)
    from import_novel import canonical_source
    source=canonical_source(project,data if stage=='brief' else load(project,'brief'))
    if require_outputs:
        reviewed = data.get('clips', [])
        if stage == 'edit':
            reviewed += data['episodes']
        for item in reviewed:
            check(item['qc']['checked'], f'{item["id"]}: 尚未完成看片检查')
    for name in file_refs(data):
        # During assembly the final output is the only file allowed not to exist yet.
        output_files = {e['file'] for e in data.get('episodes', [])} if stage == 'edit' else set()
        local_path(project, name, exists=require_outputs or name not in output_files)
    if stage == 'brief':
        from video_backend import settings
        config=settings(data)
        if 'wan3' in data and 'videoGeneration' not in data:
            check(data['wan3']['maxSegmentSeconds'] <= 15, '旧 Wan3 适配器单段上限为15秒')
        check(len(set(data['scope'])) == len(data['scope']), 'scope 重复')
        check(all(re.fullmatch(r'E\d{2,}', x) for x in data['scope']), '集数 ID 需要 E01 格式')
        check(all(1 <= int(x[1:]) <= data['episodeCount'] for x in data['scope']), 'scope 超出总集数')
        if data['audioMode'] == 'native' and config['status']=='ready':
            check(config['audio'], '原生对白方案需要所选能力支持生成音频')
        return data
    brief = load(project, 'brief')
    if stage == 'outline':
        chars, scenes, props = [indexed(data[k]) for k in ('characters','scenes','props')]
        eps = indexed(data['episodes'])
        exact(eps, [f'E{i:02d}' for i in range(1, brief['episodeCount']+1)], '全剧集数')
        for ep in eps.values():
            for key, allowed in [('characterIds',chars),('sceneIds',scenes),('propIds',props)]:
                refs(ep[key], allowed, key)
        for item in data['characters'] + data['episodes']:
            origin = item['origin']
            if origin['kind'] == 'source':
                check(origin['evidence'] in source, f'{item["id"]}: 原文引文不存在')
        return data
    outline = load(project, 'outline')
    chars, locations, props = [indexed(outline[k]) for k in ('characters','scenes','props')]
    if stage == 'characters':
        exact(indexed(data['characters']), chars, '角色清单')
    elif stage == 'art':
        from scene_relations import relations
        relations(project)
        exact(indexed(data['scenes']), locations, '场景清单')
        exact(indexed(data['props']), props, '道具清单')
    elif stage == 'script':
        exact(indexed(data['episodes']), brief['scope'], '本批集数')
        art = indexed(load(project, 'art')['scenes'])
        all_scenes, all_beats = [], []
        for ep in data['episodes']:
            total = 0
            check(abs(ep['targetSeconds'] - brief['episodeSeconds']) < .01, '剧本目标时长须与 brief 一致')
            for scene in ep['scenes']:
                all_scenes.append(scene)
                check(scene['locationId'] in art, '未知场景: ' + scene['locationId'])
                check(scene['lighting'] in art[scene['locationId']]['lighting'], '未知光照状态')
                refs(scene['characterIds'], chars, '出场角色')
                refs(scene['propIds'], props, '出场道具')
                check(any(b['type'] == 'action' for b in scene['beats']), '每场至少需要一个动作节拍')
                for beat in scene['beats']:
                    all_beats.append(beat)
                    total += beat['seconds']
                    if beat['type'] == 'dialogue':
                        check(beat.get('speaker') in scene['characterIds'], '说话者未在场景登记: ' + str(beat.get('speaker')))
                        check(bool(beat.get('delivery')), '台词需要 delivery 语气')
                        # A budget check, not proof of actual spoken timing.
                        letters = sum(c.isalnum() for c in beat['text'])
                        check(beat['seconds'] >= letters / 6, f'{beat["id"]}: 台词估时过短')
            check(abs(total-ep['targetSeconds']) <= max(.5, ep['targetSeconds']*.15), f'{ep["id"]}: 剧本估时偏离目标超过 15%')
        indexed(all_scenes); indexed(all_beats)
    elif stage in ('anchors', 'assets'):
        from production_scope import required
        scope=required(project)
        needed=set(scope['characterIds']+scope['locationIds']+scope['propIds'])
        indexed(data['images'])
        for img in data['images']:
            check(img['entityId'] in needed, '素材不属于所选制作范围: '+img['entityId'])
            check(img['entityId'] in chars or img['entityId'] in locations or img['entityId'] in props, '未知素材实体: ' + img['entityId'])
            image_check(local_path(project, img['file']))
        if stage == 'anchors':
            exact([x['entityId'] for x in data['images']], scope['characterIds'], '所选集每个角色一个定妆锚点')
            check(all(x['view'] in ('character-sheet','front-full') and not x['referenceIds'] for x in data['images']), '定妆图使用 character-sheet（兼容旧 front-full），派生引用为空')
        else:
            anchors = indexed(load(project, 'anchors')['images'])
            indexed(list(anchors.values()) + data['images'])
            available=dict(anchors)
            for img in data['images']:
                refs(img['referenceIds'], available, '已建立参考图')
                from scene_relations import connected
                check(all(available[r]['entityId']==img['entityId'] or connected(project,available[r]['entityId'],img['entityId']) for r in img['referenceIds']), '状态图只能引用同组实体或已登记相邻场景参考图')
                if img['entityId'] in chars:
                    check(any(r in anchors and anchors[r]['entityId'] == img['entityId'] for r in img['referenceIds']), '角色派生图须引用同角色定妆锚点')
                available[img['id']]=img
            for c in scope['characterIds']:
                has_sheet=any(x['entityId']==c and x['view']=='character-sheet' for x in anchors.values())
                check(has_sheet or any(x['entityId'] == c and x['view'] == 'portrait' for x in data['images']), c + ': 缺少正脸近照或包含正面半身像的角色设定图')
            used_scenes = {s['locationId'] for s in script_scenes(project)}
            used_props = {p for s in script_scenes(project) for p in s['propIds']}
            for entity in used_scenes | used_props:
                check(any(x['entityId'] == entity for x in data['images']), entity + ': 缺少本批参考图')
            validate(project,'floorplans',require_outputs=require_outputs)
    elif stage == 'floorplans':
        from spatial import layout_check
        assets=indexed(load(project,'assets')['images'])
        indexed(data['images']);media_index(project)
        exact([p['entityId'] for p in data['images']],{s['locationId'] for s in script_scenes(project)},'本批场景俯视图覆盖')
        for plan in data['images']:
            image_check(local_path(project,plan['file']))
            refs(plan['referenceIds'],assets,'俯视图场景参考')
            for id in plan['referenceIds']:
                check(assets[id]['entityId']==plan['entityId'],'俯视图不能引用其他场景')
                check(plan['file']!=assets[id]['file'],'俯视图必须另行制作，不能冒用场景原图')
            indexed(plan['layout']['items']);layout_check(plan['layout'])
    elif stage == 'staging':
        scenes = indexed(script_scenes(project))
        exact(indexed(data['scenes'], 'sceneId'), scenes, '场次站位')
        locs = indexed(data['locations'])
        exact(locs, {x['locationId'] for x in scenes.values()}, '站位地点')
        assets = media_index(project)
        plans=indexed(load(project,'floorplans')['images'])
        for loc in locs.values():
            check(loc['imageId'] in assets and assets[loc['imageId']]['entityId'] == loc['id'], '站位图必须对应实际地点')
            check(loc['floorplanId'] in plans and plans[loc['floorplanId']]['entityId']==loc['id'],'站位必须使用本场景已确认俯视图')
            plan=plans[loc['floorplanId']]
            check(loc['imageId'] in plan['referenceIds'],'站位场景图必须为俯视图的来源图')
            expected_items=[{k:item[k] for k in ('id','name','x','y','width','height','sittable')} for item in plan['layout']['items']]
            check(loc['items']==expected_items,'站位物件必须沿用俯视图布局，不能另造一套坐标')
            indexed(loc['items'])
            for item in loc['items']:
                check(item['x']+item['width'] <= 1 and item['y']+item['height'] <= 1, '平面物件坐标必须在 0..1 内')
        for scene in data['scenes']:
            upstream = scenes[scene['sceneId']]
            check(scene['locationId'] == upstream['locationId'], '站位地点与剧本不一致')
            exact([x['characterId'] for x in scene['places']], upstream['characterIds'], '站位人物')
            items = indexed(locs[scene['locationId']]['items'])
            positions = []
            for place in scene['places']:
                check(place['x'] <= 1 and place['y'] <= 1, '人物坐标必须在 0..1 内')
                if place['present']:
                    check(place['itemId'] in items, '人物站位依赖未识别的物件')
                    if place['posture'] == 'sitting':
                        check(items[place['itemId']]['sittable'], '坐姿需可坐的物件')
                    positions.append((place['x'],place['y']))
            check(len(positions) == len(set(positions)), '开场人物位置重叠')
    elif stage == 'storyboard':
        from video_backend import settings
        config=settings(brief,ready=True)
        scenes = indexed(script_scenes(project))
        from production_scope import episodes
        eps = episodes(project)
        scene_eps = {s['id']:ep['id'] for ep in eps for s in ep['scenes']}
        beats = {b['id']:b for s in scenes.values() for b in s['beats']}
        expected = [b['id'] for ep in eps for s in ep['scenes'] for b in s['beats']]
        claimed, shots = [], []
        assets = media_index(project)
        plans=indexed(load(project,'floorplans')['images'])
        staging={l['id']:l for l in load(project,'staging')['locations']}
        indexed(data['segments'])
        for seg in data['segments']:
            if 'videoGeneration' in brief:
                check(seg['mode'] in config.get('supportedModes',['ref2v']), '所选工具尚未确认支持该生成方式')
                if seg['mode']=='fl2v':
                    check(config.get('supportsFirstLastWithReference',False) and config['maxReferenceImages']>=3,
                          '首尾帧模式需确认工具支持同时携带首帧、尾帧与俯视图')
            check(seg['sceneId'] in scenes and scene_eps[seg['sceneId']] == seg['episodeId'], '分段场次/集数不匹配')
            own = {b['id'] for b in scenes[seg['sceneId']]['beats']}
            seconds = sum(s['seconds'] for s in seg['shots'])
            check(0 < seconds <= config['maxSegmentSeconds'] and (not config['integerSeconds'] or seconds == int(seconds)), '分段时长不符合所选视频能力')
            if seg['mode'] == 'ref2v':
                check(len(seg['shots'])+1 <= config['maxReferenceImages'], '关键帧与俯视图总数超出所选视频能力，请拆段')
            for shot in seg['shots']:
                shots.append(shot)
                refs(shot['beatIds'], own, '分镜节拍')
                refs(shot['assetIds'], assets, '镜头参考图')
                plan_id=shot['spatial']['floorplanId']
                check(plan_id in plans and plans[plan_id]['entityId']==scenes[seg['sceneId']]['locationId'],'镜头必须引用本场景俯视图')
                check(plan_id==staging[scenes[seg['sceneId']]['locationId']]['floorplanId'],'镜头与站位必须共用同一俯视图')
                check(plan_id in shot['assetIds'],'分镜图参考素材必须包括俯视图')
                from spatial import camera_check
                camera_check(shot,plans[plan_id])
                check(sum(beats[b]['seconds'] for b in shot['beatIds']) <= shot['seconds']+.01, '镜头装不下节拍时长')
                claimed.extend(shot['beatIds'])
        indexed(shots)
        check(claimed == expected, '节拍必须按剧本顺序完整覆盖一次，不能遗漏、重复或跨场')
    elif stage == 'frames':
        segments = indexed(load(project, 'storyboard')['segments'])
        assets = media_index(project)
        indexed(data['images'])
        expected, actual = [], []
        for seg in segments.values():
            if seg['mode'] == 'ref2v':
                expected.extend((seg['id'],s['id'],'keyframe') for s in seg['shots'])
            else:
                expected += [(seg['id'],seg['shots'][0]['id'],'first_frame'),(seg['id'],seg['shots'][-1]['id'],'last_frame')]
        for img in data['images']:
            image_check(local_path(project,img['file']))
            actual.append((img['segmentId'],img['shotId'],img['role']))
            refs(img['referenceIds'], assets, '分镜图引用')
            check(img['segmentId'] in segments,'分镜图引用未知段')
            shot=next((s for s in segments[img['segmentId']]['shots'] if s['id']==img['shotId']),None)
            check(shot is not None,'分镜图引用未知镜头')
            exact(img['referenceIds'],shot['assetIds'],'镜头参考图')
        exact(actual, expected, '分镜图覆盖')
    elif stage == 'clips':
        if brief['audioMode']=='native':
            from voices import library,first_speech
            known=library(project,require_ready=True)
            check(set(first_speech(project)).issubset(known),'说话角色尚未完成音色提取和上传')
        indexed(data['clips'])
        exact([c['segmentId'] for c in data['clips']], indexed(load(project, 'storyboard')['segments']), '视频片段覆盖')
        from assemble import probe
        for clip in data['clips']:
            info = probe(local_path(project, clip['file']))
            check(abs(info['seconds']-clip['seconds']) <= .35, '视频声明时长与实际不符')
            check(info['video'], '视频文件没有画面轨')
            if brief['audioMode'] == 'native':
                check(info['audio'], '原生对白方案的视频缺少音轨')
    elif stage == 'edit':
        from production_scope import selection
        exact(indexed(data['episodes']), selection(project)['episodeIds'], '所选成片集数')
        clips = indexed(load(project, 'clips')['clips'])
        segments = load(project, 'storyboard')['segments']
        by_segment = {c['segmentId']:c['id'] for c in clips.values()}
        for ep in data['episodes']:
            expected = [by_segment[s['id']] for s in segments if s['episodeId'] == ep['id']]
            check([c['clipId'] for c in ep['cuts']] == expected, '剪辑必须按本集分段顺序覆盖；重剪结构需先修改分镜')
            for cut in ep['cuts']:
                check(0 <= cut['in'] < cut['out'] <= clips[cut['clipId']]['seconds']+.02, '裁切区间超出片段')
            duration = sum(c['out']-c['in'] for c in ep['cuts'])
            check(abs(duration-brief['episodeSeconds']) <= max(.5,brief['episodeSeconds']*.15), '成片时长偏离目标超过 15%')
            for cap in ep['captions']:
                check(cap['start'] < cap['end'] <= duration+.02, '字幕时间超出成片')
            check(all(a['start'] <= b['start'] for a,b in zip(ep['captions'], ep['captions'][1:])), '字幕未按时间排序')
            if brief['subtitles']:
                check(ep['subtitleMode'] != 'none' and bool(ep['captions']), '制作要求需要字幕')
            if brief['audioMode'] == 'external':
                check(any(t['role'] == 'dialogue' for t in ep['audioTracks']), '独立对白方案缺少对白音轨')
            if require_outputs:
                from assemble import probe, render_spec
                info = probe(local_path(project, ep['file']))
                check(info['video'] and info['audio'] and abs(info['seconds']-duration) <= .4, '成片实际时长/音视频轨不匹配')
                target_ratio=9/16 if brief['aspectRatio']=='9:16' else 16/9
                check(abs(info['width']/max(1,info['height'])-target_ratio)<.02,'成片画幅不匹配')
                if ep['subtitleMode']=='soft':
                    check(info['subtitles'],'合成结果缺少要求的字幕轨')
                from core import file_digest
                receipt_path=local_path(project,ep['file']).with_suffix('.render.json')
                check(receipt_path.is_file(),'缺少合成记录，请使用 assemble 重新合成')
                receipt=read_json(receipt_path)
                check(receipt.get('specFingerprint')==render_spec(project,ep)['specFingerprint'] and receipt.get('sha256')==file_digest(local_path(project,ep['file'])),'合成规格、输入或输出已变化，请重新合成')
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project'); parser.add_argument('stage', choices=STAGES)
    args = parser.parse_args()
    try:
        validate(args.project, args.stage)
        print('校验通过（结构与引用；不替代看图看片）')
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc)+'\n')
