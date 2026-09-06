import subprocess
import os
import re
import tempfile
from pathlib import Path
from utils.helpers import FFmpegUtils

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None


class VideoCutter:
    """
    Cắt video dựa trên mẫu keep/skip (ví dụ: giữ 3s, bỏ 10s)
    và tính toán thời lượng video thực tế được cắt
    """

    @staticmethod
    def _ffmpeg_bin():
        return FFmpegUtils.ffmpeg_executable()

    @staticmethod
    def _ffprobe_bin():
        return FFmpegUtils.ffprobe_executable()
    
    @staticmethod
    def get_video_duration(video_path):
        """Lấy thời lượng video (giây)"""
        try:
            if not video_path or not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")
            ffprobe_bin = VideoCutter._ffprobe_bin()
            cmd = [
                ffprobe_bin, '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1:nokey=1',
                video_path
            ]
            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True, text=True, check=True
                ),
            )
            return float(result.stdout.strip())
        except Exception as e:
            raise Exception(f"Không thể lấy thời lượng video: {str(e)}")
    
    @staticmethod
    def calculate_cut_duration(video_duration, keep_seconds=3, skip_seconds=10):
        """
        Tính toán thời lượng video sau khi cắt
        
        Args:
            video_duration: Thời lượng video gốc (giây)
            keep_seconds: Số giây giữ lại trong mỗi chu kỳ
            skip_seconds: Số giây bỏ đi trong mỗi chu kỳ
        
        Returns:
            Thời lượng video sau khi cắt (giây)
        """
        if keep_seconds <= 0 or skip_seconds <= 0:
            return video_duration
        
        cycle = keep_seconds + skip_seconds
        num_cycles = video_duration / cycle
        kept_duration = num_cycles * keep_seconds
        return kept_duration

    @staticmethod
    def get_keep_segments(video_duration, keep_seconds=3, skip_seconds=10):
        """
        Trả về danh sách các đoạn video sẽ được giữ lại theo công thức keep/skip.

        Args:
            video_duration: Tổng thời lượng video gốc (giây)
            keep_seconds: Số giây giữ trong mỗi chu kỳ
            skip_seconds: Số giây bỏ trong mỗi chu kỳ

        Returns:
            List[dict] với keys: start, end
        """
        try:
            video_duration = float(video_duration or 0)
            keep_seconds = float(keep_seconds or 0)
            skip_seconds = float(skip_seconds or 0)
        except Exception:
            return []

        if video_duration <= 0:
            return []

        if keep_seconds <= 0 or skip_seconds <= 0:
            return [{"start": 0.0, "end": round(video_duration, 2)}]

        cycle = keep_seconds + skip_seconds
        if cycle <= 0:
            return [{"start": 0.0, "end": round(video_duration, 2)}]

        segments = []
        current_time = 0.0
        while current_time < video_duration:
            start = current_time
            end = min(start + keep_seconds, video_duration)
            if end > start:
                segments.append({
                    "start": round(start, 2),
                    "end": round(end, 2),
                })
            current_time += cycle
        return segments

    @staticmethod
    def _limit_segments_to_duration(segments, max_duration_seconds=None):
        if not max_duration_seconds or max_duration_seconds <= 0:
            return segments
        limited = []
        total = 0.0
        for seg in segments:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            duration = max(0.0, end - start)
            if duration <= 0:
                continue
            remaining = float(max_duration_seconds) - total
            if remaining <= 0:
                break
            if duration > remaining:
                seg = dict(seg)
                seg["end"] = round(start + remaining, 2)
                limited.append(seg)
                break
            limited.append(seg)
            total += duration
        return limited

    @staticmethod
    def _merge_segments(segments, source_duration=None, gap_tolerance=0.15):
        cleaned = []
        for seg in segments or []:
            try:
                start = max(0.0, float(seg.get("start", 0.0)))
                end = float(seg.get("end", 0.0))
                if source_duration:
                    end = min(float(source_duration), end)
                if end - start < 0.25:
                    continue
                item = dict(seg)
                item["start"] = round(start, 2)
                item["end"] = round(end, 2)
                cleaned.append(item)
            except Exception:
                continue

        cleaned.sort(key=lambda item: item["start"])
        merged = []
        for seg in cleaned:
            if not merged or seg["start"] > merged[-1]["end"] + gap_tolerance:
                merged.append(seg)
                continue
            merged[-1]["end"] = round(max(merged[-1]["end"], seg["end"]), 2)
            merged[-1]["score"] = max(float(merged[-1].get("score", 0.0)), float(seg.get("score", 0.0)))
            reasons = {r for r in [merged[-1].get("reason"), seg.get("reason")] if r}
            if reasons:
                merged[-1]["reason"] = "+".join(sorted(reasons))
            scene_ids = list(merged[-1].get("scene_ids") or [])
            for sid in seg.get("scene_ids") or ([] if seg.get("scene_id") is None else [seg.get("scene_id")]):
                if sid not in scene_ids:
                    scene_ids.append(sid)
            if scene_ids:
                merged[-1]["scene_ids"] = scene_ids
            for key in ("dialogue_text", "visual_anchor"):
                left = str(merged[-1].get(key, "") or "").strip()
                right = str(seg.get(key, "") or "").strip()
                if right and right not in left:
                    merged[-1][key] = (left + " " + right).strip()[:800]
            for key in ("subtitle_count",):
                try:
                    merged[-1][key] = int(merged[-1].get(key, 0) or 0) + int(seg.get(key, 0) or 0)
                except Exception:
                    pass
            for key in ("source", "importance_score"):
                if key not in merged[-1] and key in seg:
                    merged[-1][key] = seg.get(key)
        return merged

    @staticmethod
    def _face_detector():
        if cv2 is None:
            return None
        try:
            cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            detector = cv2.CascadeClassifier(cascade_path)
            return detector if not detector.empty() else None
        except Exception:
            return None

    @staticmethod
    def get_smart_keep_segments(
        input_path,
        keep_seconds=4,
        skip_seconds=8,
        max_duration_seconds=None,
        progress_callback=None,
    ):
        """Select interesting kept segments using scene changes, motion, and prominent recurring faces.

        This is a lightweight local heuristic. It does not identify a named actor, but it
        strongly prioritizes shots with large/centered/repeated faces as a proxy for the
        main character, plus high motion and scene transitions.
        """
        source_duration = VideoCutter.get_video_duration(input_path)
        if cv2 is None or np is None or source_duration <= 0:
            fallback = VideoCutter.get_keep_segments(source_duration, keep_seconds, skip_seconds)
            return VideoCutter._limit_segments_to_duration(fallback, max_duration_seconds)

        target_duration = max_duration_seconds if max_duration_seconds and max_duration_seconds > 0 else None
        if not target_duration:
            target_duration = VideoCutter.calculate_cut_duration(source_duration, keep_seconds, skip_seconds)
        target_duration = max(1.0, min(float(target_duration), source_duration))
        if target_duration >= source_duration * 0.96:
            return [{"start": 0.0, "end": round(source_duration, 2), "score": 1.0, "reason": "full_video"}]

        segment_len = max(2.0, min(8.0, float(keep_seconds or 4)))
        sample_interval = max(0.75, min(3.0, source_duration / 1200.0))
        detector = VideoCutter._face_detector()
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            fallback = VideoCutter.get_keep_segments(source_duration, keep_seconds, skip_seconds)
            return VideoCutter._limit_segments_to_duration(fallback, max_duration_seconds)

        samples = []
        prev_gray = None
        previous_bucket = None
        try:
            t = 0.0
            sample_index = 0
            while t < source_duration:
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, frame = cap.read()
                if not ok or frame is None:
                    t += sample_interval
                    continue

                small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                brightness = float(np.mean(hsv[:, :, 2]))
                saturation = float(np.mean(hsv[:, :, 1]))
                contrast = float(np.std(gray))

                diff = 0.0
                if prev_gray is not None:
                    diff = float(np.mean(cv2.absdiff(prev_gray, gray)))
                prev_gray = gray

                face_score = 0.0
                face_count = 0
                if detector is not None:
                    faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=4, minSize=(18, 18))
                    face_count = len(faces)
                    if face_count:
                        areas = []
                        center_scores = []
                        frame_area = float(gray.shape[0] * gray.shape[1])
                        for (x, y, w, h) in faces:
                            areas.append((w * h) / frame_area)
                            cx = (x + w / 2.0) / gray.shape[1]
                            cy = (y + h / 2.0) / gray.shape[0]
                            center_scores.append(max(0.0, 1.0 - (abs(cx - 0.5) + abs(cy - 0.45))))
                        face_score = min(1.0, max(areas) * 8.0 + max(center_scores) * 0.35 + min(face_count, 3) * 0.08)

                bucket = int(t // max(segment_len, 1.0))
                recurring_bonus = 0.0
                if face_count and previous_bucket is not None and bucket != previous_bucket:
                    recurring_bonus = 0.08
                if face_count:
                    previous_bucket = bucket

                samples.append({
                    "time": t,
                    "diff": diff,
                    "brightness": brightness,
                    "saturation": saturation,
                    "contrast": contrast,
                    "face_score": face_score,
                    "face_count": face_count,
                    "recurring_bonus": recurring_bonus,
                })

                sample_index += 1
                if progress_callback and sample_index % 80 == 0:
                    progress_callback(f"Đang phân tích cảnh thông minh: {min(100, int(t / source_duration * 100))}%")
                t += sample_interval
        finally:
            cap.release()

        if not samples:
            fallback = VideoCutter.get_keep_segments(source_duration, keep_seconds, skip_seconds)
            return VideoCutter._limit_segments_to_duration(fallback, max_duration_seconds)

        diff_values = [s["diff"] for s in samples]
        contrast_values = [s["contrast"] for s in samples]
        diff_ref = max(1.0, float(np.percentile(diff_values, 90)))
        contrast_ref = max(1.0, float(np.percentile(contrast_values, 90)))

        for sample in samples:
            motion_score = min(1.0, sample["diff"] / diff_ref)
            scene_score = 1.0 if sample["diff"] >= diff_ref * 0.85 else motion_score * 0.55
            color_score = min(1.0, (sample["saturation"] / 120.0) * 0.6 + (sample["contrast"] / contrast_ref) * 0.4)
            sample["score"] = (
                scene_score * 0.32
                + motion_score * 0.28
                + sample["face_score"] * 0.28
                + color_score * 0.12
                + sample["recurring_bonus"]
            )
            reasons = []
            if scene_score > 0.75:
                reasons.append("phan_canh")
            if motion_score > 0.7:
                reasons.append("hanh_dong")
            if sample["face_score"] > 0.35:
                reasons.append("nhan_vat_chinh")
            sample["reason"] = "+".join(reasons) if reasons else "canh_hay"

        ranked = sorted(samples, key=lambda item: item["score"], reverse=True)
        selected = []
        selected_total = 0.0
        min_gap = max(0.6, segment_len * 0.55)

        # Keep a short opening hook when possible.
        opening = {
            "start": 0.0,
            "end": round(min(segment_len, source_duration), 2),
            "score": 0.7,
            "reason": "mo_dau",
        }
        selected.append(opening)
        selected_total += opening["end"] - opening["start"]

        for sample in ranked:
            if selected_total >= target_duration:
                break
            start = max(0.0, sample["time"] - segment_len * 0.35)
            end = min(source_duration, start + segment_len)
            if end - start < 0.5:
                continue
            overlaps = any(not (end < seg["start"] - min_gap or start > seg["end"] + min_gap) for seg in selected)
            if overlaps:
                continue
            selected.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "score": round(float(sample["score"]), 3),
                "reason": sample["reason"],
            })
            selected_total += end - start

        merged = VideoCutter._merge_segments(selected, source_duration)
        merged = VideoCutter._limit_segments_to_duration(merged, target_duration)

        if sum(max(0.0, seg["end"] - seg["start"]) for seg in merged) < min(target_duration * 0.5, source_duration * 0.2):
            fallback = VideoCutter.get_keep_segments(source_duration, keep_seconds, skip_seconds)
            return VideoCutter._limit_segments_to_duration(fallback, target_duration)
        return merged

    @staticmethod
    def cut_video_with_segments(input_path, output_path, segments):
        try:
            source_duration = VideoCutter.get_video_duration(input_path)
            segments = VideoCutter._merge_segments(segments, source_duration)
            if not segments:
                return False, 0, "Không có segment hợp lệ để cắt"

            output_dir = os.path.dirname(os.path.abspath(output_path))
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            ffmpeg_bin = VideoCutter._ffmpeg_bin()
            with tempfile.TemporaryDirectory() as temp_dir:
                concat_path = os.path.join(temp_dir, "concat.txt")
                segment_paths = []
                for idx, seg in enumerate(segments, 1):
                    start = float(seg["start"])
                    duration = max(0.01, float(seg["end"]) - start)
                    segment_path = os.path.join(temp_dir, f"segment_{idx:04d}.mp4")
                    cmd = [
                        ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                        '-ss', str(start),
                        '-i', input_path,
                        '-t', str(duration),
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22',
                        '-c:a', 'aac', '-b:a', '128k',
                        segment_path,
                    ]
                    result = subprocess.run(
                        cmd,
                        **FFmpegUtils.subprocess_kwargs(
                            capture_output=True, text=True, check=False
                        ),
                    )
                    if result.returncode != 0:
                        return False, 0, result.stderr or f"Lỗi cắt segment {idx}"
                    segment_paths.append(segment_path)

                with open(concat_path, "w", encoding="utf-8") as f:
                    for segment_path in segment_paths:
                        safe_path = segment_path.replace("\\", "/").replace("'", "'\\''")
                        f.write(f"file '{safe_path}'\n")

                cmd = [
                    ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'concat', '-safe', '0',
                    '-i', concat_path,
                    '-c', 'copy',
                    output_path,
                ]
                result = subprocess.run(
                    cmd,
                    **FFmpegUtils.subprocess_kwargs(
                        capture_output=True, text=True, check=False
                    ),
                )
                if result.returncode != 0:
                    return False, 0, result.stderr or "Lỗi ghép segment thông minh"

            try:
                actual_duration = VideoCutter.get_video_duration(output_path)
            except Exception:
                actual_duration = sum(max(0.0, float(seg["end"]) - float(seg["start"])) for seg in segments)
            return True, actual_duration, ""
        except Exception as e:
            return False, 0, str(e)

    @staticmethod
    def cut_video_smart(
        input_path,
        output_path,
        keep_seconds=4,
        skip_seconds=8,
        max_duration_seconds=None,
        progress_callback=None,
        return_segments=False,
    ):
        try:
            ffmpeg_bin = VideoCutter._ffmpeg_bin()
            segments = VideoCutter.get_smart_keep_segments(
                input_path,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                max_duration_seconds=max_duration_seconds,
                progress_callback=progress_callback,
            )
            success, duration, error = VideoCutter.cut_video_with_segments(input_path, output_path, segments)
            if return_segments:
                return success, duration, error, segments if success else []
            return success, duration, error
        except Exception as e:
            if return_segments:
                return False, 0, str(e), []
            return False, 0, str(e)
    
    @staticmethod
    def cut_video_with_pattern(
        input_path,
        output_path,
        keep_seconds=3,
        skip_seconds=10,
        max_duration_seconds=None
    ):
        """
        Cắt video dựa trên mẫu keep/skip
        
        Args:
            input_path: Đường dẫn video gốc
            output_path: Đường dẫn video cắt
            keep_seconds: Số giây giữ lại
            skip_seconds: Số giây bỏ đi
            max_duration_seconds: Giới hạn thời lượng output (giây)
        
        Returns:
            (success: bool, duration_seconds: float, error_msg: str)
        """
        try:
            # Lấy thời lượng video gốc
            source_duration = VideoCutter.get_video_duration(input_path)
            
            # Tính toán thời lượng video sẽ được cắt
            estimated_duration = VideoCutter.calculate_cut_duration(
                source_duration, keep_seconds, skip_seconds
            )
            
            # Nếu có giới hạn, lấy giá trị nhỏ hơn
            if max_duration_seconds and max_duration_seconds > 0:
                estimated_duration = min(estimated_duration, max_duration_seconds)
            
            # Build ffmpeg filter
            cycle = keep_seconds + skip_seconds
            filter_str = f"select='lt(mod(t,{cycle}),{keep_seconds})',setpts=N/FRAME_RATE/TB"
            
            cmd = [
                ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                '-i', input_path,
                '-vf', filter_str,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22',
                '-c:a', 'aac', '-b:a', '128k',
            ]
            
            if max_duration_seconds and max_duration_seconds > 0:
                cmd.extend(['-t', str(max_duration_seconds)])
            
            cmd.append(output_path)
            
            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True, text=True, check=False
                ),
            )
            
            if result.returncode == 0:
                # Lấy thời lượng output thực tế
                try:
                    actual_duration = VideoCutter.get_video_duration(output_path)
                except:
                    actual_duration = estimated_duration
                
                return True, actual_duration, ""
            else:
                return False, 0, result.stderr or "Lỗi không xác định"
                
        except Exception as e:
            return False, 0, str(e)
    
    @staticmethod
    def extract_video_segments(input_path, segments):
        """
        Trích xuất các đoạn video cụ thể
        
        Args:
            input_path: Đường dẫn video gốc
            segments: List của (start_sec, end_sec) tuples
        
        Returns:
            List đường dẫn file tạm thời cho mỗi segment
        """
        segment_files = []
        try:
            ffmpeg_bin = VideoCutter._ffmpeg_bin()
            for idx, (start, end) in enumerate(segments):
                segment_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False).name
                
                cmd = [
                    ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                    '-i', input_path,
                    '-ss', str(start),
                    '-to', str(end),
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22',
                    '-c:a', 'copy',
                    segment_file
                ]
                
                result = subprocess.run(
                    cmd,
                    **FFmpegUtils.subprocess_kwargs(
                        capture_output=True, text=True, check=False
                    ),
                )
                if result.returncode != 0:
                    raise Exception(f"Không thể trích xuất segment {idx}")
                
                segment_files.append(segment_file)
            
            return segment_files
        except Exception as e:
            # Cleanup
            for f in segment_files:
                try:
                    if os.path.exists(f):
                        os.unlink(f)
                except:
                    pass
            raise e
    
    @staticmethod
    def get_video_fps(video_path):
        """Lấy FPS của video"""
        try:
            if not video_path or not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")
            ffprobe_bin = VideoCutter._ffprobe_bin()
            cmd = [
                ffprobe_bin, '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=r_frame_rate',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]
            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True, text=True, check=True
                ),
            )
            fps_str = result.stdout.strip()
            # fps_str might be "25/1" or "30000/1001"
            if '/' in fps_str:
                num, den = map(float, fps_str.split('/'))
                return num / den
            else:
                return float(fps_str)
        except Exception as e:
            return 25.0  # Default
    
    @staticmethod
    def get_video_resolution(video_path):
        """Lấy độ phân giải video (width, height)"""
        try:
            if not video_path or not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")
            ffprobe_bin = VideoCutter._ffprobe_bin()
            cmd = [
                ffprobe_bin, '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]
            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True, text=True, check=True
                ),
            )
            lines = result.stdout.strip().split('\n')
            width = int(lines[0]) if len(lines) > 0 else 1920
            height = int(lines[1]) if len(lines) > 1 else 1080
            return width, height
        except Exception as e:
            return 1920, 1080  # Default
