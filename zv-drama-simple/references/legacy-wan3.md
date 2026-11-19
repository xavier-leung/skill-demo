# 旧 Wan3 项目适配说明

新项目先发现视频能力，不因本文件存在而默认使用 Wan3。

## 服务合同与范围

仅供已有 brief.wan3 项目明确选择旧适配器时使用；不参与默认服务选择：

- POST `https://www.aiveyrahub.com/external-api/ai/videos/wan/generations/async`。
- GET 同一路径加 `/<taskId>` 查询；来自现有本地集成约定，实际新任务需核实响应字段。
- model=`wan3.0-video`，顶层 `disable_content_filter=true`（是否由服务端实际执行不能由本地脚本保证）。
- JSON 的 media 为 `{type,url}`：ref2v 1–9 个 reference_image，可选一个 reference_audio；fl2v 为 first_frame 和 last_frame 各一个。
- duration、resolution、ratio、audio 由已确认制作数据决定。本版按整数 1–15 秒、480P/720P/1080P 组织请求，不宣称服务未来限制不变。
- 认证从 `~/.config/aiveyra/wan3.env（可用 ZV_WAN3_ENV 指定其他路径）` 读取 AIVEYRA_AUTHORIZATION 完整值。不 shell source 文件、不输出密钥、不复制配置到技能或项目。
- 本地素材上传由使用者配置 ZV_UPLOAD_URL；可选 ZV_UPLOAD_PKG 对应 x-pkg 请求头。此兼容上传器使用 multipart 的 file 字段，响应 code=2000 时取 data URL。其他协议使用实际可用上传工具，不向任何预设私人地址发送素材。

是否可以付费调用以当前使用者的实际授权为准，不继承技能作者的授权；frames 等创作步骤仍需确认。没有真实生成需求时，不因执行 demo/test 就提交任务。

## 分段制作命令

所有命令以实际项目路径和脚本绝对路径运行。先 `workflow status` 确认 frames 已通过。

```bash
python3 {skill}/scripts/wan3.py prepare {project} SEG01
# 输出 jobId，保存请求和素材清单，但不读凭据、不上传、不提交。
python3 {skill}/scripts/wan3.py submit {project} SEG01-实际指纹后缀
python3 {skill}/scripts/wan3.py poll {project} SEG01-实际指纹后缀
python3 {skill}/scripts/wan3.py collect {project} SEG01-实际指纹后缀
```

ref2v 的角色音色由 prepare 自动从 voices.json 选择并携带，见 [角色音色库](voices.md)。多人段可用 --voice-character 指定优先人物；最多一条音频。fl2v 不混入 reference_audio。

prepare 从已确认镜头逐字拼出对白及画面要求。默认每个镜头的分镜图作为 reference_image，并预留一张给场景俯视图，总共最多九张（最多八张关键帧加一张俯视图）；角色/场景的身份通过分镜图承载。需要其他参考策略时先明确修改媒体合同，不无声截断素材。

jobs 文件记录状态、任务 ID、素材哈希与上传 URL。submit 前再次检查 frames 指纹及所有媒体字节。相同请求 prepare 会复用已有 job，不自动重提。

## 异步状态与恢复

- prepared：未发送视频请求，可以 submit。
- uploading/upload-failed：视频尚未发出，可用同一 job 的 submit 恢复。程序锁拒绝仍在运行的并发提交；不要修改 submitting/unknown 成 prepared。
- submitting/unknown：请求可能已经被接收。不得自动重试；查服务端任务列表或已返回的任务凭据，用 `attach ... --task-id '真实任务ID'` 恢复。
- submitted/running：继续 poll，一次查询后短暂间隔再查；不要长时间阻塞聊天，按工具能力持续跟进。
- failed：明确失败。修正原因后 `retry ... --reason '实际原因'` 建立新尝试，再 submit。反复同因失败时停止盲目重发，检查服务与请求。
- completed：有结果 URL，collect 下载并用 ffprobe 验证视频。下载失败只重试下载。
- needs-result-url/unknown：响应不符合已知映射，不假装成功。核对服务的实际响应约定后补充适配和测试；不要猜数字状态含义。
- collected：已保存本地视频。`videos/<jobId>.clip.json` 是待检查条目草稿，qc.checked=false。

已完成片段质量不合格时，可 retry 新尝试，保留旧视频与原因。上游修改则重新 prepare，用新指纹建立任务。不要删除旧任务 ID 来“解决”重复提交拦截。

本版集成通过模拟网络测试，未提交真实付费任务；不会将任务 ID 或 HTTP 200 当成视频制作完成。
