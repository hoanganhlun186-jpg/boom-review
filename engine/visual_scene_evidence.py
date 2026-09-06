"""Build visual evidence for recap books from keyframes."""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from engine.ai_engine import AIEngine
from engine.premium_pipeline import PremiumReviewPipeline


class VisualSceneEvidence:
    """Optional Gemini Vision pass that describes book keyframes for grounding."""

    TECHNICAL_ANCHORS = {
        "semantic_story",
        "action_lookahead",
        "scene_detect",
        "scene_cut",
        "cut_score",
        "intro",
        "unknown",
    }
    CLUE_WORDS = (
        "đầu lâu", "dau lau", "xương", "xuong", "xác", "xac", "máu", "mau",
        "ao", "hồ", "ho", "nước", "nuoc", "vật chứng", "vat chung", "pháp y",
        "phap y", "hung thủ", "hung thu", "nghi phạm", "nghi pham", "giết",
        "giet", "chết", "chet", "bí mật", "bi mat",
    )

    @staticmethod
    def _run_with_progress_timeout(
        call: Callable[[], Any],
        timeout: float,
        progress_callback: Optional[Callable[[str], None]] = None,
        label: str = "Vision",
    ) -> Any:
        """Run a network-heavy vision call with a real wall-clock deadline.

        urllib timeouts apply to each provider/model attempt, so a fallback
        chain can otherwise take many minutes. The daemon worker lets the
        pipeline keep partial evidence and continue when that chain stalls.
        """
        result_queue: queue.Queue = queue.Queue(maxsize=1)

        def _worker():
            try:
                result_queue.put((True, call()))
            except BaseException as exc:
                result_queue.put((False, exc))

        worker = threading.Thread(target=_worker, daemon=True, name="vision-request")
        worker.start()
        deadline = time.time() + max(5.0, float(timeout or 5.0))
        next_notice = time.time() + 10.0

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"{label} quá {int(max(5.0, float(timeout or 5.0)))}s")
            try:
                ok, value = result_queue.get(timeout=min(1.0, remaining))
                if ok:
                    return value
                raise value
            except queue.Empty:
                now = time.time()
                if progress_callback and now >= next_notice:
                    progress_callback(
                        f"{label}: đang chờ AI phản hồi, còn tối đa {max(0, int(deadline - now))}s"
                    )
                    next_notice = now + 10.0

    @staticmethod
    def _clean_text(value: Any, limit: Optional[int] = None) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if limit and len(text) > limit:
            text = text[: max(0, limit - 3)].rstrip() + "..."
        return text

    @staticmethod
    def _scene_ids(block: Dict[str, Any]) -> List[int]:
        raw = block.get("scene_ids") or ([] if block.get("scene_id") is None else [block.get("scene_id")])
        result = []
        for item in raw:
            try:
                scene_id = int(item)
            except Exception:
                continue
            if scene_id not in result:
                result.append(scene_id)
        return result

    @staticmethod
    def _float_value(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @classmethod
    def _has_real_text(cls, value: Any) -> bool:
        text = cls._clean_text(value).lower()
        if len(text) < 4:
            return False
        if text in cls.TECHNICAL_ANCHORS:
            return False
        return not any(text == marker or text.startswith(marker + "+") for marker in cls.TECHNICAL_ANCHORS)

    @classmethod
    def _target_priority(cls, block: Dict[str, Any], card: Dict[str, Any], index: int, total: int) -> float:
        visual = block.get("visual_anchor") or block.get("visual_hint")
        dialogue = block.get("dialogue_text") or block.get("srt_anchor") or block.get("source_srt_anchor")
        score = 0.0
        if not cls._has_real_text(visual):
            score += 70.0
        if not cls._has_real_text(dialogue):
            score += 30.0
        score += min(35.0, cls._float_value(card.get("importance_score")) * 35.0)
        score += min(20.0, cls._float_value(card.get("cut_score")) / 5.0)
        duration = cls._float_value(block.get("duration"))
        if duration <= 0:
            start = cls._float_value(block.get("start_in_final_video"))
            end = cls._float_value(block.get("end_in_final_video"))
            duration = max(0.0, end - start)
        score += min(12.0, duration / 2.0)
        role_text = cls._clean_text(
            " ".join(
                str(block.get(key) or "")
                for key in ("scene_role", "scene_role_label", "beat", "book_title", "cut_reason", "reason")
            )
        ).lower()
        if any(word in role_text for word in ("climax", "cao trào", "cao trao", "turning", "twist", "hook")):
            score += 18.0
        evidence_text = cls._clean_text(
            " ".join(
                str(value or "")
                for value in (
                    visual,
                    dialogue,
                    block.get("cut_reason"),
                    block.get("reason"),
                    card.get("dialogue_text"),
                    card.get("keywords"),
                )
            )
        ).lower()
        if any(word in evidence_text for word in cls.CLUE_WORDS):
            score += 25.0
        if index in (1, total):
            score += 8.0
        return score

    @classmethod
    def select_keyframes(
        cls,
        render_blocks: Iterable[Dict[str, Any]],
        scene_cards: Iterable[Dict[str, Any]],
        max_blocks: int = 90,
    ) -> List[Dict[str, Any]]:
        cards_by_id = {}
        for card in scene_cards or []:
            if not isinstance(card, dict):
                continue
            try:
                cards_by_id[int(card.get("scene_id"))] = card
            except Exception:
                continue

        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        total = len(blocks)
        candidates = []
        for index, block in enumerate(blocks, 1):
            if not isinstance(block, dict):
                continue
            block_id = int(block.get("block_id") or block.get("book_id") or index)
            best_card = None
            for scene_id in cls._scene_ids(block):
                card = cards_by_id.get(scene_id)
                if not card:
                    continue
                keyframe = str(card.get("keyframe") or "").strip()
                if keyframe and os.path.exists(keyframe):
                    best_card = card
                    break
            if not best_card:
                continue
            candidates.append(
                {
                    "block_id": block_id,
                    "scene_id": best_card.get("scene_id"),
                    "image_path": str(best_card.get("keyframe") or ""),
                    "start_s": best_card.get("start_s"),
                    "end_s": best_card.get("end_s"),
                    "book_time": [
                        block.get("start_in_final_video"),
                        block.get("end_in_final_video"),
                    ],
                    "source_time": [
                        block.get("original_start"),
                        block.get("original_end"),
                    ],
                    "_order": index,
                    "_priority": cls._target_priority(block, best_card, index, total),
                }
            )
        if max_blocks and len(candidates) > max_blocks:
            candidates = sorted(candidates, key=lambda item: (-item.get("_priority", 0.0), item.get("_order", 0)))[:max_blocks]
        candidates.sort(key=lambda item: item.get("_order", 0))
        for item in candidates:
            item.pop("_order", None)
            item.pop("_priority", None)
        return candidates

    @staticmethod
    def _cache_valid(output_path: str, expected_count: int) -> bool:
        if not output_path or not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
            return False
        try:
            with open(output_path, encoding="utf-8") as handle:
                data = json.load(handle)
            items = data.get("items") if isinstance(data, dict) else []
            return len(items or []) >= max(1, int(expected_count))
        except Exception:
            return False

    @staticmethod
    def _extract_json(raw: Any) -> Dict[str, Any]:
        data = AIEngine._extract_json_payload(str(raw or ""))
        if isinstance(data, dict):
            return data
        return {}

    @classmethod
    def _normalize_item(cls, target: Dict[str, Any], desc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(desc, dict):
            return None
        visual_anchor = cls._clean_text(desc.get("visual_anchor"), 220)
        actions = [cls._clean_text(item, 80) for item in (desc.get("actions") or []) if cls._clean_text(item)]
        objects = [cls._clean_text(item, 80) for item in (desc.get("objects") or []) if cls._clean_text(item)]
        setting = cls._clean_text(desc.get("setting"), 80)
        emotion = cls._clean_text(desc.get("emotion"), 80)
        if not PremiumReviewPipeline._anchor_phrase(visual_anchor, "; ".join(actions), "; ".join(objects), setting, limit=40):
            return None
        item = dict(target)
        item.update(
            {
                "visual_anchor": visual_anchor,
                "actions": actions[:6],
                "objects": objects[:8],
                "setting": setting,
                "emotion": emotion,
            }
        )
        return item

    @classmethod
    def _describe_images_batch_with_gemini(
        cls,
        ai: AIEngine,
        targets: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        prompt = (
            "Bạn là trợ lý phân tích cảnh phim để viết recap review chuyên nghiệp.\n"
            "Bạn sẽ nhận nhiều keyframe. Mỗi keyframe có dòng BLOCK_META ngay trước ảnh.\n"
            "Hãy mô tả từng keyframe bằng tiếng Việt có dấu, chỉ ghi chi tiết CỤ THỂ nhìn thấy: "
            "nhân vật, hành động, vật thể/manh mối, bối cảnh, cảm xúc.\n"
            "Nếu thấy đầu lâu, xương, xác, máu, ao/hồ/nước, vật chứng, pháp y hoặc nhân vật cầm/nâng vật gì, PHẢI ghi rõ.\n"
            "Không bịa tên nhân vật nếu không chắc; nếu biết Sở Sở thì có thể ghi Sở Sở.\n"
            "Trả về DUY NHẤT JSON hợp lệ theo mẫu:\n"
            "{\"items\":["
            "{\"block_id\":1,\"visual_anchor\":\"một câu 12-28 từ nêu cảnh chính\","
            "\"actions\":[\"hành động\"],\"objects\":[\"vật thể/manh mối\"],"
            "\"setting\":\"bối cảnh ngắn\",\"emotion\":\"cảm xúc/không khí\"}"
            "]}\n"
            "Phải có đúng block_id theo BLOCK_META; không thêm markdown."
        )

        content_parts: List[Any] = [{"type": "text", "text": prompt}]
        for target in targets:
            content_parts.append({
                "type": "text",
                "text": (
                    "BLOCK_META "
                    f"block_id={target.get('block_id')} "
                    f"scene_id={target.get('scene_id')} "
                    f"book_time={target.get('book_time')} "
                    f"source_time={target.get('source_time')}"
                ),
            })
            content_parts.append({"type": "image_path", "path": target.get("image_path", "")})

        keys_to_try = ai.api_keys or ([ai.api_key] if ai.api_key else [])
        last_exc = None
        for api_key in keys_to_try:
            if ai._is_openrouter_key(api_key):
                try:
                    raw = ai._try_openrouter_vision_content(content_parts, api_key=api_key)
                    data = cls._extract_json(raw)
                    raw_items = data.get("items") if isinstance(data, dict) else []
                    if not isinstance(raw_items, list):
                        raw_items = []
                    by_block = {}
                    ordered = []
                    for entry in raw_items:
                        if not isinstance(entry, dict):
                            continue
                        try:
                            by_block[int(entry.get("block_id"))] = entry
                        except Exception:
                            ordered.append(entry)
                    normalized = []
                    for idx, target in enumerate(targets):
                        desc = None
                        try:
                            desc = by_block.get(int(target.get("block_id")))
                        except Exception:
                            desc = None
                        if desc is None and idx < len(ordered):
                            desc = ordered[idx]
                        item = cls._normalize_item(target, desc or {})
                        if item:
                            item["visual_evidence_source"] = "openrouter_keyframe"
                            normalized.append(item)
                    if normalized:
                        return normalized
                except Exception as exc:
                    last_exc = exc
                    continue
            try:
                raw = ai._try_gemini_vision_content(content_parts, api_key=api_key)
                data = cls._extract_json(raw)
                raw_items = data.get("items") if isinstance(data, dict) else []
                if not isinstance(raw_items, list):
                    raw_items = []
                by_block = {}
                ordered = []
                for entry in raw_items:
                    if not isinstance(entry, dict):
                        continue
                    try:
                        by_block[int(entry.get("block_id"))] = entry
                    except Exception:
                        ordered.append(entry)
                normalized = []
                for idx, target in enumerate(targets):
                    desc = by_block.get(int(target.get("block_id"))) if target.get("block_id") is not None else None
                    if desc is None and idx < len(ordered):
                        desc = ordered[idx]
                    item = cls._normalize_item(target, desc or {})
                    if item:
                        item["visual_evidence_source"] = "gemini_rest_keyframe"
                        normalized.append(item)
                if normalized:
                    return normalized
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"Không mô tả được batch keyframe bằng Gemini vision: {last_exc}")

    @classmethod
    def _describe_image_with_gemini(cls, ai: AIEngine, image_path: str, block: Dict[str, Any]) -> Dict[str, Any]:
        prompt = (
            "Bạn là trợ lý phân tích cảnh phim để viết recap review chuyên nghiệp.\n"
            "Hãy mô tả frame này bằng tiếng Việt có dấu, tập trung vào tình tiết CỤ THỂ nhìn thấy:\n"
            "- nhân vật nào xuất hiện, đang làm gì\n"
            "- vật thể/manh mối nổi bật\n"
            "- bối cảnh như ao, nước, phòng xử án, yến tiệc, cung đình\n"
            "- cảm xúc/hành động có thể quan sát\n"
            "Nếu thấy đầu lâu, xương, xác, máu, ao/hồ/nước, vật chứng, pháp y hoặc nhân vật cầm/nâng vật gì, PHẢI ghi rõ.\n"
            "Không bịa tên nhân vật nếu không chắc; nếu biết Sở Sở thì có thể ghi Sở Sở.\n"
            "Trả về DUY NHẤT JSON:\n"
            '{"visual_anchor":"một câu 12-28 từ nêu cảnh chính",'
            '"actions":["hành động 1","hành động 2"],'
            '"objects":["vật thể/manh mối"],'
            '"setting":"bối cảnh ngắn",'
            '"emotion":"cảm xúc/không khí"}\n'
            f"Book/block id: {block.get('block_id')} | time: {block.get('book_time')} | source: {block.get('source_time')}"
        )

        keys_to_try = ai.api_keys or ([ai.api_key] if ai.api_key else [])
        last_exc = None
        for api_key in keys_to_try:
            if ai._is_openrouter_key(api_key):
                try:
                    raw = ai._try_openrouter_vision_content(
                        [
                            {"type": "text", "text": prompt},
                            {"type": "image_path", "path": image_path},
                        ],
                        api_key=api_key,
                    )
                    data = cls._extract_json(raw)
                    if data:
                        return data
                    text = cls._clean_text(raw, 220)
                    if text:
                        return {"visual_anchor": text, "actions": [], "objects": [], "setting": "", "emotion": ""}
                except Exception as exc:
                    last_exc = exc
                    continue
            try:
                raw = ai._try_gemini_vision_content(
                    [
                        {"type": "text", "text": prompt},
                        {"type": "image_path", "path": image_path},
                    ],
                    api_key=api_key,
                )
                data = cls._extract_json(raw)
                if data:
                    return data
                text = cls._clean_text(raw, 220)
                if text:
                    return {"visual_anchor": text, "actions": [], "objects": [], "setting": "", "emotion": ""}
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"Không mô tả được keyframe bằng Gemini vision: {last_exc}")

    @classmethod
    def analyze_render_blocks(
        cls,
        render_blocks: Iterable[Dict[str, Any]],
        scene_cards: Iterable[Dict[str, Any]],
        ai_keys: Any,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        force: bool = False,
        max_blocks: int = 36,
        batch_size: int = 6,
        max_seconds: float = 480.0,
    ) -> Dict[str, Any]:
        render_blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        if not render_blocks:
            return {"items": [], "ok": False, "error": "missing_render_blocks"}

        targets = cls.select_keyframes(render_blocks, scene_cards, max_blocks=max_blocks)
        if not targets:
            return {"items": [], "ok": False, "error": "missing_keyframes"}

        def _block_id(item: Dict[str, Any]) -> int:
            try:
                return int(item.get("block_id", 0) or 0)
            except (TypeError, ValueError):
                return 0

        all_targets = list(targets)
        total_target_count = len(all_targets)
        target_ids = {_block_id(item) for item in all_targets}
        target_ids.discard(0)
        cached_payload: Dict[str, Any] = {}
        cached_by_id: Dict[int, Dict[str, Any]] = {}
        if not force and output_path and os.path.exists(output_path):
            try:
                with open(output_path, encoding="utf-8-sig") as handle:
                    cached_payload = json.load(handle)
                for item in cached_payload.get("items", []) or []:
                    if not isinstance(item, dict):
                        continue
                    block_id = _block_id(item)
                    if block_id in target_ids and block_id not in cached_by_id:
                        cached_by_id[block_id] = item
            except Exception:
                cached_payload = {}
                cached_by_id = {}

        cached_items = list(cached_by_id.values())
        if cached_items:
            targets = [item for item in all_targets if _block_id(item) not in cached_by_id]
            if not targets:
                cached_payload.update(
                    {
                        "ok": True,
                        "item_count": len(cached_items),
                        "target_count": total_target_count,
                        "items": cached_items,
                    }
                )
                if progress_callback:
                    progress_callback(
                        f"Vision cache hoàn chỉnh: {len(cached_items)}/{total_target_count} block"
                    )
                return cached_payload
            if progress_callback:
                progress_callback(
                    f"Vision cache: {len(cached_items)}/{total_target_count} block; "
                    f"bổ sung {len(targets)} block thiếu"
                )

        ai = AIEngine(ai_keys)
        items = list(cached_items)
        errors = []
        started_at = time.time()
        timed_out = False
        batch_size = max(1, int(batch_size or 1))

        def _save_partial():
            if not output_path:
                return
            result = {
                "ok": bool(items),
                "item_count": len(items),
                "target_count": total_target_count,
                "error_count": len(errors),
                "errors": errors[:20],
                "items": items,
                "mode": "fast_batch",
                "batch_size": batch_size,
                "max_seconds": max_seconds,
                "duration_seconds": round(time.time() - started_at, 2),
                "timed_out": timed_out,
            }
            Path(os.path.dirname(output_path) or ".").mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)

        for start_index in range(0, len(targets), batch_size):
            elapsed = time.time() - started_at
            if max_seconds and elapsed >= max_seconds:
                timed_out = True
                errors.append({"block_id": "timeout", "error": f"visual_evidence_time_limit_{int(max_seconds)}s"})
                break
            batch = targets[start_index:start_index + batch_size]
            batch_number = start_index // batch_size + 1
            batch_total = (len(targets) + batch_size - 1) // batch_size
            try:
                if progress_callback:
                    progress_callback(
                        f"Vision batch {batch_number}/{batch_total}: "
                        f"{len(batch)} frame"
                    )
                configured_batch_timeout = float(
                    os.environ.get("AUTORECAP_VISION_BATCH_TIMEOUT", "60") or "60"
                )
                remaining_total = (
                    max(5.0, max_seconds - elapsed) if max_seconds else configured_batch_timeout
                )
                batch_timeout = min(max(15.0, configured_batch_timeout), remaining_total)
                batch_items = cls._run_with_progress_timeout(
                    lambda: cls._describe_images_batch_with_gemini(ai, batch),
                    timeout=batch_timeout,
                    progress_callback=progress_callback,
                    label=f"Vision batch {batch_number}/{batch_total}",
                )
                items.extend(batch_items)
                _save_partial()
                if progress_callback:
                    progress_callback(
                        f"Vision batch {batch_number}/{batch_total} xong: "
                        f"nhận {len(batch_items)}/{len(batch)} frame"
                    )
            except TimeoutError as batch_exc:
                timed_out = True
                errors.append(
                    {
                        "block_id": ",".join(str(t.get("block_id")) for t in batch),
                        "error": str(batch_exc)[:300],
                    }
                )
                if progress_callback:
                    progress_callback(
                        f"Vision batch {batch_number}/{batch_total} timeout -> "
                        f"dùng {len(items)} kết quả đã có và tiếp tục pipeline"
                    )
                _save_partial()
                break
            except Exception as batch_exc:
                errors.append(
                    {
                        "block_id": ",".join(str(t.get("block_id")) for t in batch),
                        "error": str(batch_exc)[:300],
                    }
                )
                for target in batch:
                    if max_seconds and time.time() - started_at >= max_seconds:
                        timed_out = True
                        break
                    try:
                        if progress_callback:
                            progress_callback(f"Vision fallback: block {target.get('block_id')}")
                        remaining_total = (
                            max(5.0, max_seconds - (time.time() - started_at))
                            if max_seconds else 35.0
                        )
                        desc = cls._run_with_progress_timeout(
                            lambda target=target: cls._describe_image_with_gemini(
                                ai, target.get("image_path", ""), target
                            ),
                            timeout=min(35.0, remaining_total),
                            progress_callback=progress_callback,
                            label=f"Vision fallback block {target.get('block_id')}",
                        )
                        item = cls._normalize_item(target, desc)
                        if item:
                            items.append(item)
                            _save_partial()
                    except Exception as exc:
                        errors.append({"block_id": target.get("block_id"), "error": str(exc)[:300]})
                        if len(errors) >= 5 and not items:
                            break
                if timed_out or (len(errors) >= 5 and not items):
                    break

        result = {
            "ok": bool(items),
            "item_count": len(items),
            "target_count": total_target_count,
            "error_count": len(errors),
            "errors": errors[:20],
            "items": items,
            "mode": "fast_batch",
            "batch_size": batch_size,
            "max_seconds": max_seconds,
            "duration_seconds": round(time.time() - started_at, 2),
            "timed_out": timed_out,
        }
        if output_path:
            Path(os.path.dirname(output_path) or ".").mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback(
                f"Vision hoàn tất: {len(items)}/{total_target_count} frame | "
                f"lỗi {len(errors)} | {result['duration_seconds']}s"
            )
        return result

    @classmethod
    def merge_into_render_blocks(
        cls,
        render_blocks: Iterable[Dict[str, Any]],
        evidence: Any,
    ) -> List[Dict[str, Any]]:
        if isinstance(evidence, dict):
            items = evidence.get("items") or []
        else:
            items = evidence or []
        by_block = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                by_block[int(item.get("block_id"))] = item
            except Exception:
                continue

        merged = []
        for index, block in enumerate(render_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            item = dict(block)
            try:
                block_id = int(item.get("block_id") or item.get("book_id") or index)
            except Exception:
                block_id = index
            evidence_item = by_block.get(block_id)
            if evidence_item:
                parts = [
                    evidence_item.get("visual_anchor"),
                    "; ".join(evidence_item.get("actions") or []),
                    "; ".join(evidence_item.get("objects") or []),
                    evidence_item.get("setting"),
                ]
                visual = PremiumReviewPipeline._anchor_phrase(*parts, limit=260)
                if visual:
                    item["visual_anchor"] = visual
                    item["visual_hint"] = visual
                    item["visual_notes"] = visual
                    item["must_mention"] = item.get("must_mention") or visual
                    item["visual_evidence_source"] = "gemini_keyframe"
                    item["visual_evidence_scene_id"] = evidence_item.get("scene_id")
            merged.append(item)
        return merged
