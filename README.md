# Video Recreation Workflow · 视频复刻工作流

供其他电脑上的 Codex 使用：**原视频 → Gem拆解与改写 → FLUX整图替换关键帧 → 编号宫格检查 → H3生成 → 完整视频与对比视频。**

Codex负责理解、看原片、写分镜和真实视觉/听觉检查；Python工具负责探测、字段检查、提帧、实际生成调用、宫格、回拼和对比片。效果依赖素材、模型与实际验收，不能承诺一次还原。

## 给其他Codex的开工指令

克隆本项目后，连同自己的原片和参考图，把这段话交给Codex：

> 先完整读取项目的 SKILL.md、references/process.md、references/contracts.md 和 references/verification.md，检查我的真实环境。先完成Gem五部分、43列分镜和逐镜动作提示词，再生成全片替换单图和编号宫格并实际看图；宫格没完成，不进入H3。上传和费用按本条授权执行。最终交付完整视频、原片对比视频与真实检查记录，不把图文或片段算成完整交付。

然后补充：

```text
原片路径：自己的视频
产品参考：自己的产品图片
修改项：明确需要改变什么
保留项：人物、发长/卷度进程、衣服、背景、其他工具、剪辑
动作：产品结构冲突时，确认能否最小接触动作适配
声音文字：明确是否改品牌、型号、规格、操作和语言
参数与预算：按本次要求，不继承别人授权
输出：完整MP4 + 左原片/右复刻对比MP4
```

## 环境与执行

需要Python 3.10+、Pillow、FFmpeg/FFprobe；真实生成还需要已有ComfyUI/FLUX模型和H3服务账号。本项目不自动下载模型、安装大型环境、登录账号或充值。

```shell
python -m pip install -e .
python workflow.py doctor
python workflow.py init /my/source.mp4 jobs/my-video --changes "只换产品，头发改红色" --product /my/product.jpg
```

FFmpeg不在PATH时，设置 `FFMPEG`、`FFPROBE` 指向已有可执行文件。`jobs/`、视频、音频、模型与 `.env` 默认不进Git。由Codex看完真实素材并补齐 `job.json` 后执行：

```shell
python workflow.py validate jobs/my-video/job.json
python workflow.py export-shots jobs/my-video/job.json
python workflow.py extract jobs/my-video/job.json
python workflow.py flux jobs/my-video/job.json SHOT_01_A
python workflow.py flux jobs/my-video/job.json SHOT_01_A --execute
# 全片单图生成、实际看图和记录后
python workflow.py grids jobs/my-video/job.json
python workflow.py validate jobs/my-video/job.json --require-images
python workflow.py references jobs/my-video/job.json
python workflow.py h3 jobs/my-video/job.json G01
```

H3默认不会发生成请求。真实执行前，在进程环境中设置 `H3_API_KEY`、`H3_ENDPOINT` 和 `H3_QUERY_ENDPOINT`，提供绑定本条原片和范围的授权文件。不要把密钥写进提示词、仓库或命令日志。

```shell
python workflow.py h3 jobs/my-video/job.json G01 --execute --approval jobs/my-video/approval.json
# 有task_id但超时时只查原任务
python workflow.py h3 jobs/my-video/job.json G01 --execute --approval jobs/my-video/approval.json --resume
python workflow.py assemble jobs/my-video/job.json
# 使用实际新音频的词级时间及对应视频SHA256，按实际原片字幕样式配置
python workflow.py captions jobs/my-video/job.json --words jobs/my-video/actual_words.json
python workflow.py compare jobs/my-video/job.json
```

详细字段、字幕对齐和第三方适配边界见 [配置与接口](references/contracts.md)。检查依据见 [验证范围](references/verification.md)。本地测试不代表别人的设备或付费账号已经完成端到端生成。

```shell
python -m unittest discover -s tests -v
```

代码按MIT提供。模型、第三方服务和用户素材各自适用其权利与费用条件；仓库不分发模型权重、密钥、账号授权或私人案例素材。
