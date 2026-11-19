"""2D path checks against reviewed plans; visual/3D consistency still needs review."""

def require(ok,message):
    if not ok:raise ValueError(message)


def inside(point,box):
    return box['x']<point['x']<box['x']+box['width'] and box['y']<point['y']<box['y']+box['height']


def crosses(a,b,box):
    # Intersect the segment with an open axis-aligned rectangle (edges may be touched).
    low,high=0.0,1.0
    for axis,size in (('x','width'),('y','height')):
        start=a[axis];delta=b[axis]-start
        left=box[axis];right=left+box[size]
        if abs(delta)<1e-12:
            if not left<start<right:return False
        else:
            t1,t2=sorted(((left-start)/delta,(right-start)/delta))
            low=max(low,t1);high=min(high,t2)
    return low<high and high>0 and low<1


def layout_check(layout):
    for item in layout['items']+layout['unknownZones']:
        require(item['x']+item['width']<=1 and item['y']+item['height']<=1,'俯视图物件/未知区域坐标必须位于0..1')
    for item in layout['items']:
        if item['kind']=='wall':require(item['blocksCamera'],'墙体必须标为机位障碍')


def camera_check(shot,plan):
    space=shot['spatial'];layout=plan['layout'];points=space['cameraPath']
    items={i['id']:i for i in layout['items']}
    for p in points:
        require(all(0<=v<=1 for v in p.values()),'机位及视线坐标必须位于俯视图0..1内')
    for id in space['visibleItemIds']:
        require(id in items,'镜头引用未知背景物件: '+id)
        require(items[id]['basis']=='observed','不能把未见区域的推测物件作为确定背景: '+id)
    barriers=[i for i in layout['items'] if i['blocksCamera']]+layout['unknownZones']
    for box in barriers:
        require(not any(inside(p,box) for p in points),'机位落入障碍或未经确认区域')
        for a,b in zip(points,points[1:]):
            require(not crosses(a,b,box),'机位移动穿过障碍或未经确认区域')
