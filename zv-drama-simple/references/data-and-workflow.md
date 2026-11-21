# 数据格式与操作

## 项目目录

```text
project/
  source/novel.md                # 后续唯一小说参考源
  source/conversion.json         # 转换及完整性核对记录
  source/originals/              # 原始输入归档，不供下游引用
  brief.json outline.json characters.json art.json script.json
  anchors.json assets.json floorplans.json staging.json storyboard.json frames.json
  clips.json edit.json voices.json  # 音色库含本地音频、URL、来源与试听记录
  workflow.json                 # 工具维护，不手工改审批状态
  images/ videos/ audio/ subtitles/
  reports/index.html            # 总览
  reports/<stage>.html          # 各步骤只读快照
  history/<stage>/<revision>/   # 历史 JSON、指纹和聊天决定
  jobs/<jobId>.json             # 视频任务状态；不是审批记录
```

所有业务 JSON 顶层 `schemaVersion=1`。schema 禁止未定义字段；需要新字段应先明确用途并同时更新 schema、校验与模板，不通过隐藏自由文本字段规避合同。

## 字段快速索引

完整字段和枚举以 schemas 为准。以下帮助找到对应层，不重复整个 schema。

| 文件 | 核心结构 |
|---|---|
| brief | title/sourceFile/mode/genre/episodeCount/episodeSeconds/scope/aspectRatio/style/adaptationScope/audioMode/subtitles/imageProvider/videoGeneration（旧 wan3 仅兼容） |
| outline | logline/adaptationNotes；characters[id,name,role,arc,origin]；scenes/props；episodes[id,synopsis,hook,cliffhanger,characterIds,sceneIds,propIds,origin] |
| characters | characters[id,name,appearance,personality,costume,speechStyle,voiceDirection,imagePrompt,origin] |
| art | scenes[id,name,layout,anchors,lighting,imagePrompt]；props[id,name,scale,states,imagePrompt] |
| script | episodes[id,targetSeconds,scenes[id,locationId,lighting,characterIds,propIds,beats]] |
| anchors/assets | images[id,entityId,view,file,prompt,referenceIds] |
| floorplans | images[id,entityId,view=overhead,file,prompt,referenceIds,layout] |
| staging | locations[id,imageId,floorplanId,items]；scenes[sceneId,locationId,places,extras,extrasWhere,opening] |
| storyboard | segments[id,episodeId,sceneId,mode,sound,shots] |
| frames | images[id,segmentId,shotId,role,file,prompt,referenceIds] |
| clips | clips[id,segmentId,taskId,file,seconds,qc] |
| edit | episodes[id,cuts,audioTracks,nativeGain,captions,subtitleMode,file,qc] |

origin = `{kind: source|adapted|invented, evidence, reason}`。
视频及成片 qc = `{checked: boolean, notes: string}`，实际审片后才能标记 checked=true，用户批准另行记录。图片不要求 qc；旧图片 qc 仅兼容存档，不参与验收、候选文件依赖或 HTML 展示。见 [图片展示与用户确认](image-review.md)。

ID 只用字母开头的英数字、下划线、连字符，最多 80 字符。建议 C01 人物、S01 地点、P01 道具、E01 集、SC01 场次、B01 节拍、A01 素材、SEG01 段、SH01 镜头、F01 分镜图、V01 视频。集 ID 使用 E01 这样的数字编号；跨批次复制时保持既有 ID。

`file`/`sourceFile` 必须是项目内部相对文件路径；拒绝绝对路径、越界 .. 和越界符号链接。不能直接写公网 URL 代替本地审核素材。全剧设定与本批 scope 不混淆：script 按 brief.scope 编写；第 6 步起按 [制作范围](production-scope.md) 默认只制作 E01，edit 只含所选集数；outline 含完整集数。

## 状态与确认

执行与回复遵循 SKILL.md 的“持续推进与回合结尾”：无待确认项就继续；确需停下时给出首页/本步链接、具体确认内容及确认后的下一步产出。用户确认后直接记录并继续下一步，不另问“是否开始”。任务提交成功但仍在生成时继续轮询，不提前结束为等待确认。

```text
ready → submit → pending → approve → approved
                    └→ changes → 修改 → submit → pending
已提交文件或依赖变化 → stale → 修订/复核 → submit → pending
```

前序步骤未确认时，不允许提交后一步。审批记录与 JSON 字节、登记素材内容、实际依赖的递归指纹绑定。不同文件格式化也会改变指纹，这是保守的版本判断。

供 approve 使用的 fingerprint 同时绑定提交 revision；即使数据没变，重新 submit 后也不能拿上一版页面的指纹批准新版。contentFingerprint 单独表示内容依赖版本，由工具维护。

- `status` 动态检查文件，不仅看 workflow 里写的状态。
- `submit` 校验、归档当前 JSON 和指纹、生成审核页，状态为 pending。
- `approve` 只接受当前 pending 且未变化的完整指纹，以及用户的真实聊天原话。
- `changes` 记录用户对待审核版的修改意见，不自动编辑内容。
- `render` 重建页面，不批准、不生成新素材、不发网络请求。

历史保留 JSON 和哈希清单；大型素材不重复复制到 history，因此**必须为新素材使用新文件名**。若覆盖旧文件，工具会使确认失效，但不能从哈希恢复被覆盖的视频。

此记录是本地协作约束，不是防篡改签名，也无法验证是谁敲了 CLI。模型不得伪造用户原话；测试里的模拟批准只允许在明确的测试临时项目。

## 修改传播

实际内容依赖与制作顺序分开：例如角色定妆依赖 brief/characters，剧本依赖 outline/characters/art。art 变化不会直接重写 characters，但会使相关剧本、参考素材、站位、分镜及视频失效。

失效粒度是步骤级。用户只改 C01 的衣服时，模型依据 assetIds/referenceIds 判断需要重做哪些图；未受影响的图保留，重新提交本步骤。不能因为一个条目改了就无条件重发全部已成功视频任务。

重渲染时失效步骤明确显示“需重新审核”；源文件结构不合法或素材丢失时不生成该步骤页面，先修数据。静态页面在再次生成之前无法实时感知磁盘变化，必须向用户说明它是快照。

## 跨目录使用

技能脚本从自身路径定位 schema/templates，当前工作目录无关。把整个 zv-drama-simple 文件夹移动到其他位置后仍可使用；不得只复制 SKILL.md。项目输出与技能目录分开，所有命令可传带空格的绝对路径，执行时正确引用路径。

小说输入一律先按 [输入规范](novel-input.md) 归一到 source/novel.md。brief.sourceFile 固定为该路径；所有后续阶段同时检查转换核对状态，不读取归档原文件。

floorplans.json 归属第 7 步 assets，与场景图共同校验、归档及确认，详见 [俯视图与机位合同](floorplans.md)。旧项目的独立 floorplans 记录仅保留历史、不再显示为步骤；assets 重新提交确认，更新 staging.floorplanId/items、shot.spatial 以及帧参考图后重新审核；不要复用旧分镜确认。

角色音色在 clips 阶段建立，规范见 [角色音色库](voices.md)。它不增加审批步骤；音频和库文件纳入 clips 指纹，后续视频任务自动带允许的参考音频。
