# skill-demo

用于存放多个独立的测试 demo，验证技能（Skill）、脚本和相关功能。每个 demo 使用单独的目录，说明用途和运行方式。

## Demo 列表

| 目录 | 用途 | 说明 |
| --- | --- | --- |
| `skill-test-1` | 使用 Python 计算两个整数的最小公倍数 | [技能说明](skill-test-1/SKILL.md) |
| `skill-test-2` | 使用 Node.js 计算两个整数的最大公约数 | [技能说明](skill-test-2/SKILL.md) |
| `zv-drama-simple` | 小说到短剧的独立制作技能，版本 1.01；按 12 步生成并审核 | [介绍与修改记录](zv-drama-simple/README.md) · [技能说明](zv-drama-simple/SKILL.md) |

## 运行示例

需要 Python 3，无需额外依赖：

```bash
python3 skill-test-1/scripts/lcm.py 12 18
```

输出 `36`。

最大公约数 demo 需要支持 BigInt 的 Node.js，无需额外依赖：

```bash
node skill-test-2/scripts/gcd.js 12 18
```

输出 `6`。

## 目录约定

- 每个测试 demo 放在独立目录中。
- 技能类 demo 使用 `SKILL.md` 说明用途，并将辅助脚本放在 `scripts/` 中。
- 新增 demo 时更新上方列表，注明运行所需的环境和依赖。
- 密钥和本地环境配置不提交到仓库。

## 小说到短剧技能 1.01

将 `zv-drama-simple` 目录安装到宿主的技能目录。需要 Python 3.10+、ffmpeg/ffprobe，以及宿主可用的图像与视频生成能力。视频工具动态发现，不默认绑定服务；素材上传由使用者配置，凭据和付费授权不随技能提供。

包含流程脚本、只读 HTML 模板、JSON 格式、分镜知识库及角色版式参考。测试、演示脚本、生成项目及设计草稿仅保存在开发者本地，已由 `.gitignore` 排除。
