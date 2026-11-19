"""Provider-neutral capability configuration. Discovery is performed by the host agent."""

def settings(brief,ready=False):
    from validate import check
    if 'videoGeneration' in brief:
        config=brief['videoGeneration']
    elif 'wan3' in brief:
        config={'status':'ready','provider':'legacy-wan3','tool':'scripts/wan3.py','model':'wan3.0-video',
                'evidence':'Legacy project explicitly configured wan3', 'maxReferenceImages':9,'maxReferenceAudio':1,
                'integerSeconds':True, **brief['wan3']}
    else:
        config={'status':'unavailable','reason':'尚未发现可用的视频生成工具或技能'}
    if config['status']=='ready':
        for key in ('provider','tool','evidence','maxSegmentSeconds','maxReferenceImages','maxReferenceAudio','integerSeconds','audio'):
            check(key in config,'视频能力配置缺少 '+key)
        check(config['maxSegmentSeconds']>0,'视频能力单段时长必须大于0')
        check(config['maxReferenceImages']>=2,'视频能力必须能同时接收关键帧和俯视图')
    elif ready:
        raise ValueError('需要视频生成能力：'+config.get('reason','请连接可用的视频生成工具或安装相应技能；需支持关键帧和俯视图参考'))
    return config
