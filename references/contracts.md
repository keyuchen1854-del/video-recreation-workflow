# 数据与接口

## job.json

运行 `init` 后由Codex补齐。路径分两类：`source` 和用户产品参考为用户提供的真实绝对路径；所有输出、原帧与编辑帧路径相对本任务目录，禁止逃出该目录。

顶层字段：`schema_version, source, source_sha256, duration_sec, source_probe, requested_changes, preserve, unknowns, product_references, shots, references, groups, flux, h3, image_review_complete, video_generated, user_acceptance`。

`references` 每项至少包含：

```json
{
  "id": "SHOT_01_A",
  "shot_id": "SHOT_01",
  "source_timestamp_sec": 0.5,
  "source_path": "source_frames/SHOT_01_A.png",
  "edited_path": "images/SHOT_01_A.png",
  "prompt": "按本张原帧实际姿态写完整静态整图编辑提示词",
  "product_reference_paths": [],
  "generation_status": "planned_not_generated"
}
```

仅实际看图后标 `generated_reviewed`。原帧不能当替换图。修订采用新ID或新记录路径，保留旧候选与失败证据。

## Gem五部分与43列

全局设定写明语言、比例、类型、T、叙事顺序、说话人/画外音、语速、情绪、机位/光照/色调/音乐、允许修改、保留、未知和未经验证说法。

逐句证据记录 `id,start_sec,end_sec,duration_sec,original_text,zh_translation,on_screen_text_seen,key_info_notes,clarity_notes`。改写另存，不覆盖原始转写证据。

每个Shot必须保留以下43列，不适用时明确写明：

```text
shot_id,start_sec,end_sec,duration_sec,scene_group_id,scene_title_cn,shot_title_cn,shot_goal,aspect_ratio,video_type_tag,visual_content_description,location_setting,character_desc,emotion_state,action_blocking,product_desc,must_show,on_screen_text_graphics,camera_shot_size,camera_angle,camera_movement,composition_notes,lighting_atmosphere,color_grading,dialogue_vo_original,dialogue_vo_zh,language_style,emphasis_notes,audio_bgm,audio_sfx,ambient_sound,editing_transition,pacing_notes,constraints_real_shoot,constraints_compliance,reverse_constraints,assets_needed,sentence_mapping,mapping_notes,rewrite_vo_target_language,rewrite_vo_zh_translation,ref_frame_timestamp_sec,ref_frame_basis
```

另有 `prompt_en,prompt_zh,product_visible,reference_ids`。英文动作6–10句，逐句中文校对；写明确首姿、手/身体路径、遮挡、末姿、衔接、摄影和光照。口播与画外旁白分开。

商品镜保留：

> The product must be EXACTLY the same as the product in the reference product images—same shape, size proportions, color, material, logo, packaging (if and only if visible in the reference), and readable text. Do not redesign, replace, relabel, or alter it in any way.

> No additional product-related items are allowed beyond what is visible in the reference product images (no extra box, packaging, pouch, manual, tag, accessories, or new labels). Do not invent packaging.

这些锁是生成要求，不能当效果证明。每镜还写 `Keep lighting and overall look consistent with the reference frame.`

## 本地FLUX适配

在 `flux` 中填写真实 `api,input_dir,output_dir,unet,clip,vae,clip_type,text_device,width,height,seed,steps`。模型名称从实际 `/object_info` 与磁盘检查获取；不要复制别人本机路径。当前代码适配UNETLoader/CLIPLoader/VAELoader/ReferenceLatent/EmptyFlux2LatentImage/ModelSamplingFlux等节点组合，不声称兼容任意FLUX版本。

`flux`默认dry-run读取本地节点；`--execute`正常追加队列，绝不清空或抢到队首。保存原始SaveImage文件，保留生成任务记录。输入缩放和产品参考等比缩放不属于人像保护合成。

## H3分段

每项 `groups` 填：`id,source_start,source_end,reference_duration_sec,requested_duration,resolution,ratio,image_paths,reference_video_path,prompt,output_path`。生成段连续覆盖全片；Shot与段分别编号。

`prompt`六部分为 `subject_definitions,summary,retention_analysis,detailed_description,overall_soundscape,non_diegetic_music`。`image_paths`顺序就是Picture顺序，真实视频才写Video。当前适配保守限制为4–15秒、最多9张图、参考视频合计≤15秒、提示词≤7000字符；需按服务当前资料重新核对。用户指定15秒时，不因镜头短就改成4秒。

`h3.model` 由当前真实通道确认。`h3.verified_prices` 必须包含：`source_url,checked_at,output_per_sec,reference_video_per_sec,free_images,extra_image`。示例不写固定现价。估算不等于实际账号账单。

当前Metaso适配payload是 `model,content,resolution,duration,ratio`；content内为text、reference_image的image_url及reference_video的video_url。真实本地附件在内存编码data URL，不记录完整请求体或密钥。其他提供商要根据官方文档新增适配，不能用同名H3假设API相同。

官方/提供商资料入口：

- [MiniMax H3参考提示词规范](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)
- [MiniMax H3仓库](https://github.com/MiniMax-AI/MiniMax-H3)
- [Metaso服务与当前价格](https://metaso.cn/minimax-h3)

## 授权记录

本地 `approval.json` 必须包含 `source_sha256,user_quote,upload_approved,paid_generation_approved,provider_host,allowed_groups,attempts_per_group,accepted_estimated_total`；用户给了明确数字预算时另写 `budget_ceiling`。

示例中的布尔值和估算不可默认视为授权。必须由本条真实人类指令支持，绑定原片、平台和次数；不把其他视频授权复制进来。是否先样段在任务中记录，用户明确不要样段时按最新授权执行，但不省略完整宫格检查。

## 回拼、声音与字幕

`assemble`按原片各段时长裁额外尾部，统一尺寸/帧率/音频格式并全片解码；不会把短片段自动拉长补动作。默认使用生成后的新旁白；如果任务保留原声，由Agent明确验证后采用原声回拼方式。不要把“改产品文本”与“原声全文照搬”混用。

字幕须依据实际生成音频获得的词级时间，并由Agent核对目标文本，不能直接用分镜草稿时间。CLI的 `captions --words actual_words.json` 生成ASS并烧录，字幕样式和ASR仍由Agent根据真实环境获取。

`actual_words.json` 包含 `video_sha256`（assemble产出的未排字幕视频SHA256）、`timing_source`（actual_asr 或 manually_verified_actual_audio）、`text_reviewed_against_rewrite:true`，以及 `words:[{text,start,end}]`。未经真实核对不得填这些证明字段。

任务 `subtitles` 填原片实际样式的 `play_res_x,play_res_y,font_name,font_size,x,y,outline`，不能直接套用其他视频坐标。任务需要字幕时设置 `subtitles_required:true`；compare优先使用captioned版本，缺字幕会拒绝生成最终对比。排字幕仍需实际看字形、位置、残字和声画对齐。

`compare`左原片、右复刻，播放复刻声音。全片解码通过仍须看动作、接缝、产品、发态、字幕并听语音。用户验收单独记录。
