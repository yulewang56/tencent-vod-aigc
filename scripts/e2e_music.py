#!/usr/bin/env python3
"""MPS AI 音乐生成端到端验证脚本（真实调用，会产生费用，谨慎运行）。

复用 vod_aigc_core.py 的签名 / 轮询 / 下载实现（纯标准库，无需 ComfyUI /
numpy / PIL 桩），在 ComfyUI 之外跑通
CreateAigcAudioTask → DescribeAigcAudioTask → 下载 全链路。

用法：
    # 前提：仓库根目录 tencent-vod-config.json 已配置 secret_id / secret_key
    python3 e2e_music.py --prompt "轻快的钢琴曲，温暖治愈"
    python3 e2e_music.py --prompt "流行情歌" --lyrics "你说你有点难追，想让我知难而退" --format mp3
    python3 e2e_music.py --prompt "纯音乐" --instrumental --model "MiniMaxMusic 2.6"

产物下载到 output/vod_aigc/，脚本打印 task_id / audio_url / audio_path。
"""
import argparse
import json
import os
import sys

# scripts/ 子目录 → 上跳一级得到仓库根
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _repo_root)
import vod_aigc_core as core  # noqa: E402

_OUT_DIR = os.path.join(_repo_root, "output", "vod_aigc")


def main():
    ap = argparse.ArgumentParser(description="MPS 音乐生成端到端验证")
    ap.add_argument("--prompt", default="轻快的钢琴曲，温暖治愈",
                    help="音乐风格 / 演唱要求描述（≤2000 字符）")
    ap.add_argument("--model", default="MiniMaxMusic 2.6", choices=core.MUSIC_MODELS,
                    help="音乐模型")
    ap.add_argument("--lyrics", default="", help="歌词（与 --instrumental 互斥），多行用 \\n 分段")
    ap.add_argument("--instrumental", action="store_true", help="纯音乐模式（无歌词）")
    ap.add_argument("--format", default="", choices=["", "mp3", "wav"], help="输出格式，留空跟随模型默认")
    ap.add_argument("--filename", default="", help="本地保存文件名（不含扩展名）")
    ap.add_argument("--region", default=core.DEFAULT_REGION)
    ap.add_argument("--endpoint", default=core.MPS_ENDPOINT)
    ap.add_argument("--poll-interval", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    cfg = core.load_config(os.path.join(_repo_root, "tencent-vod-config.json"))
    sid, skey = cfg.get("secret_id"), cfg.get("secret_key")
    if not sid or not skey:
        sys.exit(f"缺少凭据：请先在 {_repo_root}/tencent-vod-config.json 配置 secret_id / secret_key"
                 "（模板见 tencent-vod-config.example.json）")
    if args.lyrics and args.instrumental:
        sys.exit("--lyrics 与 --instrumental 互斥，请只保留一种")
    if len(args.prompt) > 2000:
        sys.exit(f"Prompt 超过 2000 字符上限（当前 {len(args.prompt)} 字符）")

    additional = {}
    if args.lyrics:
        additional["lyric"] = args.lyrics
    if args.instrumental:
        additional["is_instrumental"] = True
    payload = core.build_music_payload(
        args.prompt, args.model,
        {"additional_parameters": json.dumps(additional, ensure_ascii=False) if additional else "",
         "output_format": args.format})

    print(f"提交音乐生成任务: model={args.model} prompt={args.prompt[:60]!r}")
    response = core.call_api(sid, skey, args.region, args.endpoint, "CreateAigcAudioTask", payload,
                             version=core.MPS_API_VERSION, service=core.MPS_SERVICE)
    task_id = response.get("TaskId")
    if not task_id:
        sys.exit(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
    print(f"task_id: {task_id}")

    result = core.wait_for_task(sid, skey, args.region, args.endpoint, None, task_id,
                                args.poll_interval, args.timeout,
                                on_progress=lambda t: print("  " + t),
                                task_label="音乐生成中", action="DescribeAigcAudioTask",
                                err_label="音乐", version=core.MPS_API_VERSION, service=core.MPS_SERVICE)
    url = result["urls"][0]
    print(f"audio_url: {url}")

    path = core.download_file(url, task_id, _OUT_DIR, name_hint=args.filename or "",
                              on_progress=lambda t: print("  " + t))
    print(f"audio_path: {path}")
    print("完成。注意：音频 URL 约 12 小时有效期，需在有效期内下载。")


if __name__ == "__main__":
    main()
