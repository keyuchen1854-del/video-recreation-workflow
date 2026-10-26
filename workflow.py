"""Portable execution helpers. Creative analysis and visual review belong to the agent.

Paid generation is dry-run by default. No model download, environment installation,
image compositing, or account recharge is performed by this module.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

FIELDS = "shot_id,start_sec,end_sec,duration_sec,scene_group_id,scene_title_cn,shot_title_cn,shot_goal,aspect_ratio,video_type_tag,visual_content_description,location_setting,character_desc,emotion_state,action_blocking,product_desc,must_show,on_screen_text_graphics,camera_shot_size,camera_angle,camera_movement,composition_notes,lighting_atmosphere,color_grading,dialogue_vo_original,dialogue_vo_zh,language_style,emphasis_notes,audio_bgm,audio_sfx,ambient_sound,editing_transition,pacing_notes,constraints_real_shoot,constraints_compliance,reverse_constraints,assets_needed,sentence_mapping,mapping_notes,rewrite_vo_target_language,rewrite_vo_zh_translation,ref_frame_timestamp_sec,ref_frame_basis".split(",")
PRODUCT_LOCK = "The product must be EXACTLY the same as the product in the reference product images"
PACKAGE_LOCK = "Do not invent packaging."


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def output_path(root, relative):
    """All generated artifacts must stay within this job, including symlinks."""
    root = Path(root).resolve()
    result = (root / relative).resolve()
    if result == root or not result.is_relative_to(root):
        raise ValueError("Artifact output escapes job directory")
    return result


def executable(name):
    path = os.getenv(name.upper()) or shutil.which(name)
    if not path or not Path(path).is_file():
        raise RuntimeError(f"{name} unavailable; set {name.upper()} to the existing executable")
    return path


def run(argv, cwd=None):
    proc = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError(f"Local command failed ({Path(str(argv[0])).name}): {proc.stderr[-2400:]}")
    return proc.stdout


def probe(path):
    return json.loads(run([executable("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", path]))


def duration(path):
    return float(probe(path)["format"]["duration"])


def http_json(url, data=None, headers=None, timeout=120):
    req = Request(url, data=json.dumps(data).encode() if data is not None else None,
                  headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except HTTPError as e:
        # Never echo request bodies, credentials, signed URLs, or provider raw errors.
        raise RuntimeError(f"HTTP {e.code} from configured service; no automatic retry") from None


def init_job(source, root, changes, products):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("Job directory already exists; resume it instead of initializing over it")
    source = Path(source).resolve()
    info = probe(source)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    if not videos:
        raise ValueError("Input contains no video stream")
    assets = []
    for path in products:
        path = Path(path).resolve()
        assets.append({"path": str(path), "sha256": digest(path), "role": "user_product_reference"})
    root.mkdir(parents=True)
    job = {"schema_version": 1, "source": str(source), "source_sha256": digest(source),
           "duration_sec": float(videos[0].get("duration", info["format"]["duration"])),
           "source_probe": info, "requested_changes": changes, "product_references": assets,
           "preserve": [], "unknowns": [], "shots": [], "references": [], "groups": [],
           "image_review_complete": False, "video_generated": False, "user_acceptance": False}
    write(root / "job.json", job)
    return {"job": str(root / "job.json"), "source_sha256": job["source_sha256"], "duration_sec": job["duration_sec"]}


def validate(job, root, require_images=False):
    errors, ratios = [], []
    shots = job.get("shots", [])
    if not shots:
        errors.append("No confirmed shots; automatic scene candidates are not confirmed shots")
    previous, seen = 0.0, set()
    refs = {r["id"]: r for r in job.get("references", [])}
    if len(refs) != len(job.get("references", [])):
        errors.append("Duplicate reference IDs")
    for s in shots:
        sid = s.get("shot_id", "MISSING")
        if sid in seen:
            errors.append(f"{sid}: duplicate shot ID")
        seen.add(sid)
        if set(FIELDS) - s.keys():
            errors.append(f"{sid}: missing 43-column fields")
            continue
        start, end, du = map(float, (s["start_sec"], s["end_sec"], s["duration_sec"]))
        if abs(start - previous) > .0002 or end <= start or abs(end - start - du) > .0002:
            errors.append(f"{sid}: gap, overlap or invalid duration")
        previous = end
        if not start <= float(s["ref_frame_timestamp_sec"]) < end:
            errors.append(f"{sid}: source reference timestamp outside shot")
        han = lambda t: len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", str(t)))
        a, b = han(s["dialogue_vo_zh"]), han(s["rewrite_vo_zh_translation"])
        if a:
            ratio = b / a
            ratios.append({"shot_id": sid, "ratio": ratio})
            if not .9 <= ratio <= 1.1:
                errors.append(f"{sid}: Chinese rewrite ratio outside 90-110%")
        if s.get("product_visible") and not all(x in s.get("prompt_en", "") for x in [PRODUCT_LOCK, PACKAGE_LOCK]):
            errors.append(f"{sid}: product / no-packaging locks missing")
        if not s.get("reference_ids") or any(rid not in refs for rid in s.get("reference_ids", [])):
            errors.append(f"{sid}: reference mapping missing")
    if abs(previous - float(job["duration_sec"])) > .0002:
        errors.append("Final shot does not end at full source duration")
    for r in refs.values():
        if r.get("shot_id") not in seen:
            errors.append(f"{r['id']}: unknown shot")
        if require_images:
            for key in ["source_path", "edited_path"]:
                if not r.get(key) or not output_path(root, r[key]).is_file():
                    errors.append(f"{r['id']}: missing {key}")
            if r.get("generation_status") != "generated_reviewed":
                errors.append(f"{r['id']}: actual image review incomplete")
            sp, ep = r.get("source_path"), r.get("edited_path")
            if sp and ep and output_path(root, sp).is_file() and output_path(root, ep).is_file():
                if digest(output_path(root, sp)) == digest(output_path(root, ep)):
                    errors.append(f"{r['id']}: edited file equals original frame")
    return {"structure_pass": not errors, "errors": errors, "ratios": ratios,
            "visual_quality": "Requires actual image/video review; never implied by this check"}


def export_shots(job, destination):
    with Path(destination).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(job["shots"])


def extract(job, root):
    created = []
    for r in job["references"]:
        t = float(r["source_timestamp_sec"])
        if not 0 <= t < job["duration_sec"]:
            raise ValueError("Reference timestamp outside source")
        path = output_path(root, r["source_path"])
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        run([executable("ffmpeg"), "-v", "error", "-n", "-ss", t, "-i", job["source"], "-frames:v", 1, path])
        created.append(r["id"])
    return {"extracted": created}


def grids(job, root):
    from PIL import Image, ImageDraw
    refs, paths = job["references"], []
    if not refs:
        raise ValueError("No references")
    for r in refs:
        if not output_path(root, r["edited_path"]).is_file():
            raise ValueError(f"{r['id']}: edited image missing; never substitute source")
    for n in range(math.ceil(len(refs) / 6)):
        group = refs[n * 6:n * 6 + 6]
        sheet = Image.new("RGB", (768, math.ceil(len(group) / 3) * 486), "white")
        draw = ImageDraw.Draw(sheet)
        for i, r in enumerate(group):
            im = Image.open(output_path(root, r["edited_path"])).convert("RGB")
            im.thumbnail((256, 458))
            x, y = i % 3 * 256, i // 3 * 486
            sheet.paste(im, (x, y + 28))
            draw.text((x + 2, y + 4), f"{r['id']} @ {r['source_timestamp_sec']:.3f}s", fill="black")
        path = output_path(root, f"grids/page_{n+1:02}.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(path, quality=95)
        paths.append(str(path))
    return {"grids": paths, "reviewed": False}


def flux_graph(image_name, prompt, config):
    w, h = int(config["width"]), int(config["height"])
    if w % 16 or h % 16:
        raise ValueError("Configured FLUX dimensions must be multiples of 16")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": config["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": config["clip"], "type": config["clip_type"], "device": config.get("text_device", "cpu")}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": config["vae"]}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "8": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["6", 0], "latent": ["5", 0]}},
        "9": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["5", 0]}},
        "10": {"class_type": "ModelSamplingFlux", "inputs": {"model": ["1", 0], "max_shift": math.log(16), "base_shift": math.log(16), "width": w, "height": h}},
        "11": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["10", 0], "positive": ["8", 0], "negative": ["9", 0], "latent_image": ["11", 0], "seed": int(config["seed"]), "steps": int(config["steps"]), "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": config["prefix"]}},
    }


def add_flux_reference(graph, name):
    # Model-native whole-image reference conditioning. No spatial mask or pasteback.
    n = max(map(int, graph)) + 1
    oldp, oldn = graph["12"]["inputs"]["positive"], graph["12"]["inputs"]["negative"]
    graph[str(n)] = {"class_type": "LoadImage", "inputs": {"image": name}}
    graph[str(n+1)] = {"class_type": "VAEEncode", "inputs": {"pixels": [str(n), 0], "vae": ["3", 0]}}
    graph[str(n+2)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": oldp, "latent": [str(n+1), 0]}}
    graph[str(n+3)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": oldn, "latent": [str(n+1), 0]}}
    graph["12"]["inputs"].update(positive=[str(n+2), 0], negative=[str(n+3), 0])


def flux(job, root, rid, execute=False, wait_seconds=3600):
    from PIL import Image
    r = next(r for r in job["references"] if r["id"] == rid)
    c = dict(job["flux"])
    if urlparse(c["api"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("This local FLUX adapter only accepts a loopback API")
    endpoint = c["api"].rstrip("/")
    nodes = http_json(endpoint + "/object_info")
    name = f"recreate_{rid}_{job['source_sha256'][:10]}.png"
    c["prefix"] = f"recreate_{job['source_sha256'][:10]}_{rid}"
    graph = flux_graph(name, r["prompt"], c)
    assets = []
    for i, path in enumerate(r.get("product_reference_paths", [])):
        path = Path(path).resolve()
        asset_name = f"recreate_asset_{digest(path)[:12]}.png"
        assets.append((path, asset_name))
        add_flux_reference(graph, asset_name)
    missing = {n["class_type"] for n in graph.values()} - nodes.keys()
    if missing:
        raise ValueError(f"Existing ComfyUI lacks nodes: {sorted(missing)}; do not silently install or switch models")
    record_path = output_path(root, f"records/flux_{rid}.json")
    if not execute:
        return {"dry_run": True, "nodes": len(graph), "product_images": len(assets), "execution": False}
    destination = output_path(root, r["edited_path"])
    if destination.exists():
        raise ValueError("Image already exists; review it or create an explicit revision ID")
    if record_path.exists():
        pid = read(record_path).get("prompt_id")
        if not pid:
            raise RuntimeError("Prior unknown attempt; investigate before any resubmission")
    else:
        input_dir = Path(c["input_dir"]).resolve()
        if not input_dir.is_dir():
            raise ValueError("Existing ComfyUI input directory unavailable")
        Image.open(output_path(root, r["source_path"])).convert("RGB").resize((c["width"], c["height"])).save(input_dir / name)
        for path, asset_name in assets:
            im = Image.open(path).convert("RGB")
            im.thumbnail((512, 688))
            canvas = Image.new("RGB", (math.ceil(im.width/16)*16, math.ceil(im.height/16)*16), "white")
            canvas.paste(im, (0, 0))
            canvas.save(input_dir / asset_name)
        write(record_path.with_name(record_path.stem + "_graph.json"), graph)
        write(record_path, {"status": "attempt_reserved", "source_sha256": job["source_sha256"]})
        # Normal append, never front/number/interrupt/clear operations.
        result = http_json(endpoint + "/prompt", {"prompt": graph, "client_id": "video-recreation-workflow"})
        pid = result["prompt_id"]
        write(record_path, {"prompt_id": pid, "status": "submitted"})
    start = time.monotonic()
    while time.monotonic() - start < wait_seconds:
        history = http_json(endpoint + "/history/" + pid).get(pid)
        if history:
            if history.get("status", {}).get("status_str") == "error":
                raise RuntimeError("FLUX task failed; no automatic retry")
            images = history.get("outputs", {}).get("14", {}).get("images", [])
            if images:
                item = images[0]
                raw_root = Path(c["output_dir"]).resolve()
                raw = output_path(raw_root, str(Path(item.get("subfolder", "")) / item["filename"]))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(raw, destination)
                write(record_path, {"prompt_id": pid, "status": "generated_not_reviewed", "sha256": digest(destination), "postprocessing": "none; byte-identical raw SaveImage PNG"})
                return read(record_path)
        time.sleep(5)
    raise TimeoutError("Known FLUX prompt_id retained; resume this ID, never blindly retry")


def data_url(path, kind):
    mime = mimetypes.guess_type(path)[0] or ""
    if not mime.startswith(kind + "/"):
        raise ValueError(f"Expected a real {kind} file")
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def group_payload(job, root, group):
    images = [output_path(root, p) for p in group["image_paths"]]
    if not 1 <= len(images) <= 9 or any(not p.is_file() for p in images):
        raise ValueError("Require 1-9 actual reviewed image attachments; never planned placeholders")
    if len(group["prompt"]) > 7000:
        raise ValueError("Prompt exceeds the conservative 7000-character profile")
    seconds = int(group["requested_duration"])
    if not 4 <= seconds <= 15:
        raise ValueError("This tested profile covers 4-15s; do not silently change the user's parameters")
    video = output_path(root, group["reference_video_path"])
    reference_duration = duration(video)
    if reference_duration > 15.05:
        raise ValueError("Reference-video duration exceeds the profile's 15-second input allowance")
    content = [{"type": "text", "text": group["prompt"]}]
    content.extend({"type": "image_url", "image_url": {"url": data_url(str(p), "image")}, "role": "reference_image"} for p in images)
    content.append({"type": "video_url", "video_url": {"url": data_url(str(video), "video")}, "role": "reference_video"})
    return {"model": job["h3"]["model"], "content": content, "duration": seconds,
            "resolution": group["resolution"], "ratio": group["ratio"]}


def nested(value, names):
    if isinstance(value, dict):
        for name in names:
            if isinstance(value.get(name), (str, int)):
                return str(value[name])
        for item in value.values():
            found = nested(item, names)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = nested(item, names)
            if found:
                return found
    return ""


def estimate(job):
    prices = job["h3"]["verified_prices"]
    if not prices.get("source_url") or not prices.get("checked_at"):
        raise ValueError("Current provider price source and check date required")
    total = 0.0
    for g in job["groups"]:
        total += (g["requested_duration"] * prices["output_per_sec"]
                  + g["reference_duration_sec"] * prices["reference_video_per_sec"]
                  + max(0, len(g["image_paths"]) - prices["free_images"]) * prices["extra_image"])
    return round(total, 4)


def authorize(job, approval, group_id, provider_host):
    if approval.get("source_sha256") != job["source_sha256"] or digest(job["source"]) != job["source_sha256"]:
        raise ValueError("Approval/source SHA256 mismatch")
    if not approval.get("user_quote") or not approval.get("upload_approved") or not approval.get("paid_generation_approved"):
        raise ValueError("Explicit current-source upload/payment authorization required")
    if group_id not in approval.get("allowed_groups", []) or approval.get("provider_host") != provider_host:
        raise ValueError("Group/provider outside approved scope")
    if approval.get("attempts_per_group") != 1:
        raise ValueError("This workflow does not automatically retry paid attempts")
    cost = estimate(job)
    if cost > float(approval["accepted_estimated_total"]):
        raise ValueError("Current estimate exceeds the estimate accepted for this pass")
    if approval.get("budget_ceiling") is not None and cost > float(approval["budget_ceiling"]):
        raise ValueError("Estimated total exceeds explicit budget ceiling")
    return cost


def h3(job, root, gid, execute=False, approval_path=None, resume=False, wait_seconds=1800):
    group = next(g for g in job["groups"] if g["id"] == gid)
    endpoint = os.getenv("H3_ENDPOINT", "")
    query = os.getenv("H3_QUERY_ENDPOINT", "")
    if not endpoint or not query or urlparse(endpoint).scheme != "https" or urlparse(query).scheme != "https":
        raise ValueError("Set explicit HTTPS H3_ENDPOINT and H3_QUERY_ENDPOINT for the verified adapter")
    if urlparse(endpoint).hostname != urlparse(query).hostname:
        raise ValueError("Generation/query hosts differ; verify channel before use")
    payload = group_payload(job, root, group)
    if not execute:
        return {"dry_run": True, "network_generation_calls": 0, "estimated_full_pass": estimate(job),
                "group": gid, "duration": payload["duration"], "content_types": [x["type"] for x in payload["content"]],
                "credential_present": bool(os.getenv("H3_API_KEY")), "actual_account_quote": "unknown"}
    report = validate(job, root, require_images=True)
    if not report["structure_pass"] or not job.get("image_review_complete"):
        raise ValueError("Complete actual keyframes, grids and image review before paid video generation")
    if not approval_path:
        raise ValueError("--approval required for real generation/query")
    authorize(job, read(approval_path), gid, urlparse(endpoint).hostname)
    key = os.getenv("H3_API_KEY", "")
    if not key:
        raise ValueError("H3_API_KEY absent")
    record = output_path(root, f"records/h3_{gid}.json")
    destination = output_path(root, group["output_path"])
    if destination.exists():
        raise ValueError("Output already exists; inspect it, do not overwrite/retry")
    headers = {"Authorization": "Bearer " + key}
    if resume:
        existing = read(record)
        task_id = existing.get("task_id")
        if not task_id:
            raise ValueError("Unknown previous result; check provider history, never POST again")
        result = {"task_id": task_id}
    else:
        if record.exists():
            raise ValueError("Attempt already reserved/submitted; use --resume for the known task")
        record.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects against concurrent duplicate POSTs.
        with record.open("x", encoding="utf-8") as f:
            json.dump({"status": "attempt_reserved", "group": gid, "source_sha256": job["source_sha256"]}, f)
        result = http_json(endpoint, payload, headers)
        task_id = nested(result, ("task_id", "video_id", "request_id", "id"))
        write(record, {"status": "submitted", "task_id": task_id, "group": gid})
    started = time.monotonic()
    while True:
        url = nested(result, ("video_url", "download_url"))
        status = nested(result, ("status", "state", "task_status")).lower()
        if url:
            if urlparse(url).scheme != "https":
                raise ValueError("Provider returned an insecure download URL")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with urlopen(url, timeout=180) as response, destination.open("xb") as f:
                shutil.copyfileobj(response, f)
            run([executable("ffmpeg"), "-v", "error", "-i", destination, "-f", "null", "-"])
            safe = {"task_id": task_id, "group": gid, "status": "downloaded_decode_pass",
                    "output": str(destination), "sha256": digest(destination), "actual_billing": "not yet verified", "visual_review": False}
            write(record, safe)
            return safe
        if status in {"failed", "fail", "error", "rejected", "canceled", "cancelled"}:
            write(record, {"task_id": task_id, "group": gid, "status": status, "automatic_retry": False})
            raise RuntimeError("Provider task failed; no automatic paid retry")
        if not task_id or time.monotonic() - started > wait_seconds:
            raise TimeoutError("Task ID retained; use --resume or provider history, do not resubmit")
        result = http_json(query + ("&" if "?" in query else "?") + urlencode({"task_id": task_id}), headers=headers)
        time.sleep(10)


def silent_references(job, root):
    created = []
    for g in job["groups"]:
        a, b = float(g["source_start"]), float(g["source_end"])
        if not 0 <= a < b <= job["duration_sec"] or b - a > 15.0002:
            raise ValueError("Invalid source interval")
        path = output_path(root, g["reference_video_path"])
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        run([executable("ffmpeg"), "-v", "error", "-n", "-ss", a, "-i", job["source"], "-t", b-a,
             "-an", "-c:v", "libx264", "-crf", 20, "-pix_fmt", "yuv420p", path])
        created.append(g["id"])
    return {"silent_reference_clips": created, "source_shots_are_not_generation_groups": True}


def assemble(job, root, width=720, height=1280, fps=30):
    groups = job["groups"]
    if not groups:
        raise ValueError("No generation groups")
    clips, prev = [], 0.0
    for i, g in enumerate(groups):
        a, b = float(g["source_start"]), float(g["source_end"])
        if abs(a-prev) > .0002:
            raise ValueError("Generation groups have a gap/overlap")
        prev = b
        raw = output_path(root, g["output_path"])
        info = probe(raw)
        if not any(s["codec_type"] == "audio" for s in info["streams"]):
            raise ValueError("Generated clip has no narration track; don't silently supply old product speech")
        if duration(raw) + .08 < b-a:
            raise ValueError("Generated clip shorter than source; don't silently speed/stretch/pad missing action")
        clip = output_path(root, f"assembly/aligned_{i:03}.mp4")
        clip.parent.mkdir(parents=True, exist_ok=True)
        if clip.exists():
            raise ValueError("Aligned output exists; inspect previous assembly before replacing")
        run([executable("ffmpeg"), "-v", "error", "-n", "-i", raw, "-t", b-a,
             "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}",
             "-af", "aresample=48000", "-c:v", "libx264", "-crf", 18, "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", 48000, "-ac", 2, clip])
        clips.append(clip)
    if abs(prev-job["duration_sec"]) > .0002:
        raise ValueError("Assembly does not cover the complete source")
    listing = output_path(root, "assembly/concat.txt")
    if any("'" in str(p) or "\n" in str(p) for p in clips):
        raise ValueError("Use a job path without quotes/newlines for FFmpeg concat")
    listing.write_text("\n".join("file '" + p.as_posix() + "'" for p in clips), encoding="utf-8")
    destination = output_path(root, "delivery/full_video.mp4")
    destination.parent.mkdir(exist_ok=True)
    run([executable("ffmpeg"), "-v", "error", "-n", "-f", "concat", "-safe", 0, "-i", listing, "-c", "copy", "-movflags", "+faststart", destination])
    run([executable("ffmpeg"), "-v", "error", "-i", destination, "-f", "null", "-"])
    write(output_path(root, "delivery/technical_check.json"), {"full_decode_pass": True, "probe": probe(destination), "visual_acceptance": False, "narration_wording_verified": False, "subtitle_alignment_verified": False})
    return {"video": str(destination), "technical_decode_pass": True, "user_acceptance": False}


def comparison(job, root):
    captioned = output_path(root, "delivery/full_video_captioned.mp4")
    new = captioned if captioned.exists() else output_path(root, "delivery/full_video.mp4")
    if job.get("subtitles_required") and not captioned.exists():
        raise ValueError("This job requires captions; finish actual subtitle alignment before final comparison")
    dest = output_path(root, "delivery/source_vs_recreation.mp4")
    # Source on left, recreated on right; recreated narration only, avoiding two competing voices.
    filters = "[0:v]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[l];[1:v]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[r];[l][r]hstack=inputs=2[v]"
    run([executable("ffmpeg"), "-v", "error", "-n", "-i", job["source"], "-i", new, "-filter_complex", filters,
         "-map", "[v]", "-map", "1:a:0", "-t", job["duration_sec"], "-c:v", "libx264", "-crf", 19, "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", dest])
    run([executable("ffmpeg"), "-v", "error", "-i", dest, "-f", "null", "-"])
    return {"comparison": str(dest), "left": "source", "right": "recreation", "audio": "recreation only", "decode_pass": True}


def captions(job, root, words_path):
    data = read(words_path)
    base = output_path(root, "delivery/full_video.mp4")
    if data.get("timing_source") not in {"actual_asr", "manually_verified_actual_audio"} or not data.get("text_reviewed_against_rewrite"):
        raise ValueError("Actual generated-audio timing and text review required; draft timing is not final")
    if data.get("video_sha256") != digest(base):
        raise ValueError("Word timing belongs to another rendered video")
    style = job["subtitles"]
    def stamp(t):
        n = round(float(t)*100)
        return f"{n//360000}:{n//6000%60:02}:{n//100%60:02}.{n%100:02}"
    header = f"[Script Info]\nScriptType: v4.00+\nPlayResX: {style['play_res_x']}\nPlayResY: {style['play_res_y']}\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Main,{style['font_name']},{style['font_size']},&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,1,{style.get('outline',0)},0,5,0,0,0,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    lines, previous = [], 0.0
    for word in data["words"]:
        a, b = float(word["start"]), float(word["end"])
        if not 0 <= a < b <= job["duration_sec"]+.05 or a < previous-.02:
            raise ValueError("Word times overlap or escape video timeline")
        previous = b
        text = str(word["text"]).replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")
        lines.append(f"Dialogue: 0,{stamp(a)},{stamp(b)},Main,,0,0,0,,{{\\pos({style['x']},{style['y']})}}{text}")
    if not lines:
        raise ValueError("No actual caption words")
    path = output_path(root, "delivery/captions.ass")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
    dest = output_path(root, "delivery/full_video_captioned.mp4")
    run([executable("ffmpeg"), "-v", "error", "-n", "-i", base, "-vf", "ass=delivery/captions.ass", "-c:v", "libx264", "-crf", 18, "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", dest], cwd=root)
    run([executable("ffmpeg"), "-v", "error", "-i", dest, "-f", "null", "-"])
    return {"captioned_video": str(dest), "captions": str(path), "actual_timing_used": True, "visual_style_review_required": True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("init");q.add_argument("source");q.add_argument("job_dir");q.add_argument("--changes", required=True);q.add_argument("--product", action="append", default=[])
    q = sub.add_parser("doctor");q.add_argument("--comfy-api")
    for name in ["validate", "extract", "grids", "export-shots", "references", "flux", "h3", "assemble", "captions", "compare"]:
        q = sub.add_parser(name);q.add_argument("job")
        if name == "validate":q.add_argument("--require-images", action="store_true")
        if name in ["flux", "h3"]:q.add_argument("id");q.add_argument("--execute", action="store_true")
        if name == "h3":q.add_argument("--approval");q.add_argument("--resume", action="store_true")
        if name == "captions":q.add_argument("--words", required=True)
    a = p.parse_args(argv)
    try:
        if a.command == "doctor":
            result = {"python": sys.version.split()[0], "ffmpeg": executable("ffmpeg"), "ffprobe": executable("ffprobe"), "model_installed_or_loaded": "not inferred"}
            if a.comfy_api:
                if urlparse(a.comfy_api).hostname not in {"localhost", "127.0.0.1", "::1"}:raise ValueError("Use a loopback ComfyUI endpoint")
                q = http_json(a.comfy_api.rstrip("/") + "/queue")
                result["queue"] = {"running": len(q.get("queue_running", [])), "pending": len(q.get("queue_pending", []))}
        elif a.command == "init":result = init_job(a.source, a.job_dir, a.changes, a.product)
        else:
            path = Path(a.job).resolve();root = path.parent;job = read(path)
            if a.command == "validate":
                result = validate(job, root, a.require_images);write(root/"validation.json", result)
                if not result["structure_pass"]:print(json.dumps(result, ensure_ascii=False, indent=2));return 1
            elif a.command == "extract":result = extract(job, root)
            elif a.command == "grids":result = grids(job, root)
            elif a.command == "export-shots":export_shots(job, root/"shots_43.csv");result = {"csv": str(root/"shots_43.csv")}
            elif a.command == "references":result = silent_references(job, root)
            elif a.command == "flux":result = flux(job, root, a.id, a.execute)
            elif a.command == "h3":result = h3(job, root, a.id, a.execute, a.approval, a.resume)
            elif a.command == "assemble":result = assemble(job, root)
            elif a.command == "captions":result = captions(job, root, a.words)
            else:result = comparison(job, root)
        print(json.dumps(result, ensure_ascii=False, indent=2));return 0
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration) as e:
        print(json.dumps({"error": str(e) or type(e).__name__, "success": False}, ensure_ascii=False), file=sys.stderr);return 1


if __name__ == "__main__":
    raise SystemExit(main())
