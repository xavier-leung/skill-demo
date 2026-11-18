# skill-demo

用于存放多个独立的测试 demo，验证技能（Skill）、脚本和相关功能。每个 demo 使用单独的目录，说明用途和运行方式。

## Demo 列表

| 目录 | 用途 | 说明 |
| --- | --- | --- |
| `skill-test-1` | 使用 Python 计算两个整数的最小公倍数 | [技能说明](skill-test-1/SKILL.md) |

## 运行示例

需要 Python 3，无需额外依赖：

```bash
python3 skill-test-1/scripts/lcm.py 12 18
```

输出 `36`。

## 目录约定

- 每个测试 demo 放在独立目录中。
- 技能类 demo 使用 `SKILL.md` 说明用途，并将辅助脚本放在 `scripts/` 中。
- 新增 demo 时更新上方列表，注明运行所需的环境和依赖。
- 密钥和本地环境配置不提交到仓库。
