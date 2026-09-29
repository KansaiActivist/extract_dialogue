#!/usr/bin/env python3
"""
extract_dialogue.py — 動画からセリフ(発話区間)を自動で切り出して音声ファイルにする

モード:
  silence : ffmpeg の無音検出だけで区間を切る(軽量・追加インストール不要)
  whisper : faster-whisper で発話区間を検出し、文字起こしも出力(精度が高い)

使い方:
  python extract_dialogue.py movie.mp4
  python extract_dialogue.py movie.mp4 --mode whisper --lang ja -f mp3
  python extract_dialogue.py *.mp4 -o out --noise -30 --min-silence 0.8

必要なもの:
  ffmpeg / ffprobe（PATH に通っていること）
  whisper モードのみ: pip install faster-whisper
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

CODECS = {
    "wav": ["-c:a", "pcm_s16le"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "flac": ["-c:a", "flac"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
}


@dataclass
class Segment:
    start: float
    end: float
    text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def get_duration(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        sys.exit(f"動画の長さを取得できませんでした: {path}\n{r.stderr}")


def extract_analysis_wav(video: Path, wav: Path) -> None:
    """解析用に 16kHz モノラル WAV を作る。"""
    r = run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(wav)])
    if r.returncode != 0:
        sys.exit(f"音声の抽出に失敗しました（音声トラックがない可能性があります）:\n{r.stderr[-600:]}")


# ---------------------------------------------------------------- 検出
def detect_silence_mode(wav: Path, duration: float, noise_db: float,
                        min_silence: float) -> list[Segment]:
    r = run(["ffmpeg", "-i", str(wav), "-af",
             f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", r.stderr)]

    segments: list[Segment] = []
    cursor = 0.0
    for i, s in enumerate(starts):
        s = max(s, 0.0)
        if s - cursor > 0:
            segments.append(Segment(cursor, s))
        if i < len(ends):
            cursor = ends[i]
        else:  # 末尾まで無音
            cursor = duration
    if cursor < duration:
        segments.append(Segment(cursor, duration))
    return segments


def detect_whisper_mode(wav: Path, model_name: str, lang: str | None) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("whisper モードには faster-whisper が必要です:\n  pip install faster-whisper")
    print(f"  モデル読み込み中: {model_name}（初回はダウンロードされます）")
    model = WhisperModel(model_name, device="auto", compute_type="auto")
    segs, info = model.transcribe(str(wav), language=lang or None, vad_filter=True,
                                  vad_parameters={"min_silence_duration_ms": 500})
    print(f"  検出言語: {info.language}")
    return [Segment(s.start, s.end, s.text.strip()) for s in segs]


# ---------------------------------------------------------------- 整形
def postprocess(segs: list[Segment], duration: float, pad: float,
                min_dur: float, merge_gap: float) -> list[Segment]:
    segs = sorted(segs, key=lambda s: s.start)
    # 近すぎる区間はひとまとめにする
    merged: list[Segment] = []
    for s in segs:
        if merged and s.start - merged[-1].end <= merge_gap:
            merged[-1].end = s.end
            merged[-1].text = (merged[-1].text + " " + s.text).strip()
        else:
            merged.append(Segment(s.start, s.end, s.text))
    # 短すぎる区間（咳・効果音など）を除外 → 前後に余白
    out = [s for s in merged if s.duration >= min_dur]
    for s in out:
        s.start = max(0.0, s.start - pad)
        s.end = min(duration, s.end + pad)
    # 余白で重なった分を調整
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            mid = (a.end + b.start) / 2
            a.end = b.start = mid
    return out


# ---------------------------------------------------------------- 出力
def safe_name(text: str, limit: int = 20) -> str:
    text = re.sub(r'[\\/:*?"<>|\s\r\n\t]+', "_", text).strip("_")
    return text[:limit]


def fmt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cut_segment(video: Path, seg: Segment, dest: Path, fmt: str) -> bool:
    cmd = ["ffmpeg", "-y", "-ss", f"{seg.start:.3f}", "-t", f"{seg.duration:.3f}",
           "-i", str(video), "-vn", *CODECS[fmt], str(dest)]
    return run(cmd).returncode == 0


def process(video: Path, args: argparse.Namespace) -> None:
    print(f"\n▶ {video.name}")
    duration = get_duration(video)
    outdir = Path(args.output) / video.stem
    outdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "analysis.wav"
        extract_analysis_wav(video, wav)
        if args.mode == "whisper":
            raw = detect_whisper_mode(wav, args.model, args.lang)
        else:
            raw = detect_silence_mode(wav, duration, args.noise, args.min_silence)

    segs = postprocess(raw, duration, args.pad, args.min_duration, args.merge_gap)
    if not segs:
        print("  セリフ区間が見つかりませんでした。--noise を大きく（例: -30）、"
              "--min-duration を小さくして試してください。")
        return

    rows = []
    for i, seg in enumerate(segs, 1):
        name = f"{video.stem}_{i:03d}"
        if seg.text and args.name_with_text:
            name += "_" + safe_name(seg.text)
        dest = outdir / f"{name}.{args.format}"
        ok = cut_segment(video, seg, dest, args.format)
        mark = "✓" if ok else "✗"
        print(f"  {mark} {i:03d}  {seg.start:8.2f}s – {seg.end:8.2f}s  {seg.text[:30]}")
        rows.append([i, dest.name, f"{seg.start:.3f}", f"{seg.end:.3f}",
                     f"{seg.duration:.3f}", seg.text])

    with open(outdir / "segments.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["no", "file", "start_sec", "end_sec", "duration_sec", "text"])
        w.writerows(rows)

    if args.mode == "whisper":
        with open(outdir / f"{video.stem}.srt", "w", encoding="utf-8") as f:
            for i, seg in enumerate(segs, 1):
                f.write(f"{i}\n{fmt_ts(seg.start)} --> {fmt_ts(seg.end)}\n{seg.text}\n\n")

    print(f"  → {len(segs)} 件を {outdir} に保存しました")


def main() -> None:
    p = argparse.ArgumentParser(description="動画からセリフを自動で切り出して音声ファイルにする")
    p.add_argument("videos", nargs="+", type=Path, help="動画ファイル（複数可）")
    p.add_argument("-o", "--output", default="dialogue_out", help="出力フォルダ")
    p.add_argument("-f", "--format", choices=CODECS, default="wav", help="出力形式")
    p.add_argument("--mode", choices=["silence", "whisper"], default="silence")
    # silence モード
    p.add_argument("--noise", type=float, default=-35, help="無音とみなす音量(dB)。BGMが大きい時は大きめに")
    p.add_argument("--min-silence", type=float, default=0.6, help="この秒数以上の無音で区切る")
    # 共通
    p.add_argument("--min-duration", type=float, default=0.5, help="これより短い区間は捨てる(秒)")
    p.add_argument("--pad", type=float, default=0.15, help="前後に付ける余白(秒)")
    p.add_argument("--merge-gap", type=float, default=0.3, help="これ以下の間隔の区間は結合(秒)")
    # whisper モード
    p.add_argument("--model", default="small", help="whisper モデル: tiny/base/small/medium/large-v3")
    p.add_argument("--lang", default=None, help="言語コード（例: ja, en）。省略で自動判定")
    p.add_argument("--name-with-text", action="store_true", help="ファイル名にセリフの冒頭を入れる")
    args = p.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if run([tool, "-version"]).returncode != 0:
            sys.exit(f"{tool} が見つかりません。ffmpeg をインストールして PATH を通してください。")

    for v in args.videos:
        if not v.exists():
            print(f"見つかりません: {v}")
            continue
        process(v, args)


if __name__ == "__main__":
    main()
