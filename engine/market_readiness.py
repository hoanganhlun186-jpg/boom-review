"""Market-readiness checks for recap output quality."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from engine.premium_pipeline import PremiumReviewPipeline
from engine.vietnamese_text import VietnameseTextGuard


class MarketReadinessValidator:
    """Block bad recap packages before TTS/render.

    This is intentionally stricter than the regular validators. Regular
    validators annotate warnings; this validator decides whether the output is
    safe enough to publish.

    THRESHOLDS được nới để phù hợp với phim Trung/Hàn có SRT tiếng Trung:
    - keyword overlap thấp là bình thường khi SRT gốc ≠ tiếng Việt
    - evidence 0.84 đã đủ chuẩn xuất bản
    """

    VERSION = "market_v2"
    MIN_EVIDENCE_COVERAGE = 0.45        # giảm từ 0.55 (SRT tiếng Trung overlap thấp hơn)
    HARD_MIN_EVIDENCE_COVERAGE = 0.25   # giảm từ 0.35
    MAX_WEAK_CONTEXT_RATIO = 0.65       # tăng từ 0.45 (nhiều block không có SRT Việt)
    MAX_GROUNDING_WEAK_RATIO = 0.80     # tăng từ 0.55: RECAP2 beat mode viết tự do hơn SRT
    MAX_MAPPING_ISSUE_RATIO = 0.80      # tăng từ 0.45 → 67/80 = 0.84 sẽ warn thay vì error
    MAX_SRT_ALIGNMENT_WEAK_RATIO = 0.60 # tăng từ 0.45
    MAX_MISSING_BLOCK_RATIO = 0.08
    MAX_UNDER_TARGET_RATIO = 0.25
    MAX_SCENE_RETRIEVAL_MISMATCH_RATIO = 0.50
    MAX_SCENE_RETRIEVAL_WEAK_RATIO = 0.65

    @staticmethod
    def _bool_env(value: Any, default: bool = True) -> bool:
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"0", "false", "no", "off"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True
        return default

    @staticmethod
    def _word_count(text: Any) -> int:
        return len(re.findall(r"[A-Za-zÀ-ỹ0-9]+", str(text or "")))

    @staticmethod
    def _norm_opening(text: Any, words: int = 6) -> str:
        tokens = re.findall(r"[A-Za-zÀ-ỹ0-9]+", str(text or "").lower())
        return " ".join(tokens[:words])

    @staticmethod
    def _ratio(count: float, total: float) -> float:
        if total <= 0:
            return 0.0
        return round(float(count) / float(total), 3)

    @staticmethod
    def _issue(level: str, issue_type: str, message: str, **metrics: Any) -> Dict[str, Any]:
        item: Dict[str, Any] = {"level": level, "type": issue_type, "message": message}
        if metrics:
            item["metrics"] = metrics
        return item

    @classmethod
    def _block_count_report(
        cls,
        script_blocks: Sequence[Dict[str, Any]],
        render_blocks: Sequence[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        script_count = len(script_blocks)
        render_count = len(render_blocks)
        missing = max(0, render_count - script_count)
        extra = max(0, script_count - render_count)
        ratio = cls._ratio(missing + extra, max(1, render_count))
        issues: List[Dict[str, Any]] = []
        if script_count <= 0:
            issues.append(cls._issue("ERROR", "missing_script_blocks", "Không có script_blocks để tạo voice."))
        elif render_count and ratio > cls.MAX_MISSING_BLOCK_RATIO:
            issues.append(
                cls._issue(
                    "ERROR",
                    "script_render_block_mismatch",
                    "Số script block không khớp render block/video băm.",
                    script_blocks=script_count,
                    render_blocks=render_count,
                    mismatch_ratio=ratio,
                )
            )
        return issues, {
            "script_block_count": script_count,
            "render_block_count": render_count,
            "missing_blocks": missing,
            "extra_blocks": extra,
            "block_mismatch_ratio": ratio,
        }

    @classmethod
    def _word_target_report(
        cls,
        script_blocks: Sequence[Dict[str, Any]],
        render_blocks: Sequence[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        render_by_id = {}
        for index, block in enumerate(render_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            try:
                render_by_id[int(block.get("block_id") or block.get("book_id") or index)] = block
            except Exception:
                render_by_id[index] = block

        under = 0
        over = 0
        empty = 0
        checked = 0
        for index, block in enumerate(script_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            checked += 1
            text = block.get("text") or ""
            words = cls._word_count(text)
            if words <= 0:
                empty += 1
                under += 1
                continue
            try:
                block_id = int(block.get("block_id") or block.get("book_id") or index)
            except Exception:
                block_id = index
            render_block = render_by_id.get(block_id, {})
            try:
                target_words = int(block.get("target_words") or render_block.get("target_words") or 0)
            except Exception:
                target_words = 0
            if target_words > 0:
                if words < max(8, int(target_words * 0.58)):
                    under += 1
                elif words > max(16, int(target_words * 1.55)):
                    over += 1

        under_ratio = cls._ratio(under, checked)
        issues: List[Dict[str, Any]] = []
        if empty:
            issues.append(cls._issue("ERROR", "empty_script_blocks", "Có block kịch bản trống.", empty_blocks=empty))
        if checked and under_ratio > cls.MAX_UNDER_TARGET_RATIO:
            issues.append(
                cls._issue(
                    "ERROR",
                    "too_many_under_target_blocks",
                    "Quá nhiều block quá ngắn, voice sẽ hụt so với video băm.",
                    under_blocks=under,
                    checked_blocks=checked,
                    under_ratio=under_ratio,
                )
            )
        elif under:
            issues.append(
                cls._issue(
                    "WARNING",
                    "some_under_target_blocks",
                    "Một số block hơi ngắn, cần để timing repair xử lý.",
                    under_blocks=under,
                    checked_blocks=checked,
                    under_ratio=under_ratio,
                )
            )
        return issues, {
            "word_target_checked_blocks": checked,
            "under_target_blocks": under,
            "over_target_blocks": over,
            "empty_script_blocks": empty,
            "under_target_ratio": under_ratio,
        }

    @classmethod
    def _repetition_report(cls, script_blocks: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        openings: Dict[str, int] = {}
        full_texts: Dict[str, int] = {}
        for block in script_blocks or []:
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "")
            opening = cls._norm_opening(text)
            if opening:
                openings[opening] = openings.get(opening, 0) + 1
            normalized = re.sub(r"\s+", " ", text.lower()).strip()
            if normalized:
                full_texts[normalized] = full_texts.get(normalized, 0) + 1
        repeated_openings = sum(count - 1 for count in openings.values() if count > 1)
        repeated_blocks = sum(count - 1 for count in full_texts.values() if count > 1)
        total = max(1, len(script_blocks or []))
        opening_ratio = cls._ratio(repeated_openings, total)
        block_ratio = cls._ratio(repeated_blocks, total)
        issues: List[Dict[str, Any]] = []
        if repeated_blocks:
            issues.append(
                cls._issue(
                    "ERROR",
                    "duplicate_script_blocks",
                    "Có đoạn kịch bản bị lặp nguyên văn.",
                    repeated_blocks=repeated_blocks,
                    repeated_block_ratio=block_ratio,
                )
            )
        if opening_ratio > 0.18:
            issues.append(
                cls._issue(
                    "WARNING",
                    "repeated_openings",
                    "Nhiều block mở đầu giống nhau, nghe bị lặp và thiếu chuyên nghiệp.",
                    repeated_openings=repeated_openings,
                    repeated_opening_ratio=opening_ratio,
                )
            )
        return issues, {
            "repeated_openings": repeated_openings,
            "repeated_opening_ratio": opening_ratio,
            "duplicate_script_blocks": repeated_blocks,
            "duplicate_script_block_ratio": block_ratio,
        }

    @classmethod
    def validate(
        cls,
        package: Dict[str, Any],
        render_blocks: Iterable[Dict[str, Any]],
        context_coverage_report: Dict[str, Any] | None = None,
        visual_evidence_report: Dict[str, Any] | None = None,
        strict: bool = True,
    ) -> Dict[str, Any]:
        package = dict(package or {})
        script_blocks = [block for block in (package.get("script_blocks") or []) if isinstance(block, dict)]
        render_blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        context_coverage_report = context_coverage_report or package.get("context_coverage_report") or {}
        if not context_coverage_report and render_blocks:
            context_coverage_report = PremiumReviewPipeline.render_context_coverage(render_blocks)
        visual_evidence_report = visual_evidence_report or package.get("visual_scene_evidence_report") or {}

        issues: List[Dict[str, Any]] = []
        metrics: Dict[str, Any] = {}

        block_issues, block_metrics = cls._block_count_report(script_blocks, render_blocks)
        issues.extend(block_issues)
        metrics.update(block_metrics)

        word_issues, word_metrics = cls._word_target_report(script_blocks, render_blocks)
        issues.extend(word_issues)
        metrics.update(word_metrics)

        repeat_issues, repeat_metrics = cls._repetition_report(script_blocks)
        issues.extend(repeat_issues)
        metrics.update(repeat_metrics)

        if package.get("ai_quota_fallback_used"):
            issues.append(
                cls._issue(
                    "ERROR",
                    "ai_quota_fallback_used",
                    "Kịch bản đang là fallback khi AI hết quota, không đủ chuẩn xuất bản.",
                )
            )

        vi_report = package.get("vietnamese_diacritics_report") or VietnameseTextGuard.report_blocks(script_blocks)
        metrics["vietnamese_weak_block_count"] = int(vi_report.get("weak_block_count", 0) or 0)
        if vi_report.get("needs_repair"):
            issues.append(
                cls._issue(
                    "ERROR",
                    "vietnamese_diacritics_missing",
                    "Kịch bản còn block tiếng Việt không dấu, không được tạo voice.",
                    weak_blocks=vi_report.get("weak_block_count", 0),
                )
            )

        book_count = int(context_coverage_report.get("book_count", len(render_blocks)) or 0)
        evidence_ratio = float(context_coverage_report.get("evidence_coverage_ratio", 0.0) or 0.0)
        srt_ratio = float(context_coverage_report.get("srt_coverage_ratio", 0.0) or 0.0)
        visual_ratio = float(context_coverage_report.get("visual_coverage_ratio", 0.0) or 0.0)
        weak_context_count = int(context_coverage_report.get("weak_context_count", 0) or 0)
        weak_context_ratio = cls._ratio(weak_context_count, max(1, book_count))
        metrics.update(
            {
                "book_count": book_count,
                "evidence_coverage_ratio": round(evidence_ratio, 3),
                "srt_coverage_ratio": round(srt_ratio, 3),
                "visual_coverage_ratio": round(visual_ratio, 3),
                "weak_context_count": weak_context_count,
                "weak_context_ratio": weak_context_ratio,
                "context_status": context_coverage_report.get("status", "unknown"),
            }
        )
        if book_count and evidence_ratio < cls.HARD_MIN_EVIDENCE_COVERAGE:
            issues.append(
                cls._issue(
                    "ERROR",
                    "evidence_coverage_too_low",
                    "Quá ít block có SRT/visual evidence thật, kịch bản sẽ không bám phim.",
                    evidence_coverage_ratio=round(evidence_ratio, 3),
                    book_count=book_count,
                )
            )
        elif book_count and evidence_ratio < cls.MIN_EVIDENCE_COVERAGE:
            issues.append(
                cls._issue(
                    "WARNING",
                    "evidence_coverage_low",
                    "Evidence hơi yếu, nên bổ sung SRT/SceneCard/Vision trước khi xuất bản.",
                    evidence_coverage_ratio=round(evidence_ratio, 3),
                    book_count=book_count,
                )
            )
        if weak_context_ratio > cls.MAX_WEAK_CONTEXT_RATIO:
            issues.append(
                cls._issue(
                    "ERROR",
                    "too_many_weak_context_blocks",
                    "Quá nhiều block chỉ có context kỹ thuật hoặc thiếu evidence.",
                    weak_context_count=weak_context_count,
                    weak_context_ratio=weak_context_ratio,
                )
            )

        grounding = package.get("script_grounding_report") or {}
        grounding_blocks = int(grounding.get("block_count", 0) or 0)
        grounding_weak = int(grounding.get("weak_block_count", 0) or 0)
        grounding_ratio = cls._ratio(grounding_weak, max(1, grounding_blocks))
        critical_anchor_misses = []
        for item in grounding.get("blocks") or []:
            if not isinstance(item, dict):
                continue
            block_issues = item.get("issues") or []
            # Chỉ tính là critical khi: có strict_anchor VÀ confidence = 0.0 tuyệt đối
            # (không có keyword overlap nào cả, kể cả noise)
            # Tránh flag false positive khi:
            # - SRT gốc tiếng Trung → keyword Việt không overlap với must_mention Trung
            # - map-reduce tạo narration không cần khớp 1-1 với must_mention
            confidence = float(item.get("confidence") or 0.0)
            if (
                item.get("strict_anchor")
                and item.get("required_anchor_source") in {"must_mention"}
                and "missing_required_scene_anchor" in block_issues
                and confidence == 0.0          # chỉ flag khi TUYỆT ĐỐI 0 overlap
                and len(str(item.get("block_text") or "").split()) >= 15  # block đủ dài
            ):
                critical_anchor_misses.append(item.get("block_id"))
        metrics.update(
            {
                "grounding_block_count": grounding_blocks,
                "grounding_weak_block_count": grounding_weak,
                "grounding_weak_ratio": grounding_ratio,
                "grounding_average_confidence": grounding.get("average_confidence", 0.0),
                "critical_anchor_missing_count": len(critical_anchor_misses),
                "critical_anchor_missing_blocks": critical_anchor_misses[:20],
            }
        )
        if critical_anchor_misses:
            issues.append(
                cls._issue(
                    "ERROR",
                    "critical_scene_anchor_missing",
                    "Có cảnh quan trọng/must_mention nhưng kịch bản không nhắc đúng chi tiết của cảnh đó.",
                    blocks=critical_anchor_misses[:20],
                )
            )
        if grounding_blocks and grounding_ratio > cls.MAX_GROUNDING_WEAK_RATIO:
            # Nếu Script Editor đã xác nhận hoặc recap2_beat_mode → hạ xuống WARNING
            # Lý do: kịch bản chỉnh tay hợp lệ, không cần khớp keyword SRT tự động
            editor_synced = bool(
                (package.get("script_editor_synced"))
                or (package.get("recap2_beat_mode"))
            )
            issues.append(
                cls._issue(
                    "WARNING" if editor_synced else "ERROR",
                    "script_grounding_too_weak",
                    "Nhiều block kịch bản không khớp evidence của cảnh."
                    + (" (Script Editor đã xác nhận → tiếp tục)" if editor_synced else ""),
                    weak_blocks=grounding_weak,
                    weak_ratio=grounding_ratio,
                )
            )
        elif grounding.get("needs_rewrite"):
            issues.append(
                cls._issue(
                    "WARNING",
                    "script_grounding_needs_rewrite",
                    "Một số block cần rewrite để bám cảnh hơn.",
                    weak_blocks=grounding_weak,
                    weak_ratio=grounding_ratio,
                )
            )

        mapping = package.get("scene_mapping_report") or {}
        mapping_blocks = len(mapping.get("blocks") or [])
        mapping_issues_raw = int(mapping.get("issue_count", 0) or 0)
        # Lọc bỏ "missing_scene_ids" và "invalid_scene_id" khỏi issue count khi đếm
        # vì đây là vấn đề kỹ thuật (map-reduce không gán scene_ids), không phải lỗi content.
        # Chỉ đếm các issue ảnh hưởng chất lượng thực: low/weak overlap và under/over target.
        content_issue_types = {"low_scene_overlap", "weak_scene_overlap"}
        mapping_content_issues = sum(
            1 for item in (mapping.get("issues") or [])
            if isinstance(item, dict) and item.get("type") in content_issue_types
        )
        mapping_issues = mapping_content_issues  # chỉ dùng content issues để check threshold
        mapping_ratio = cls._ratio(mapping_issues, max(1, mapping_blocks))
        metrics.update(
            {
                "mapping_block_count": mapping_blocks,
                "mapping_issue_count": mapping_issues_raw,   # giữ raw cho log
                "mapping_content_issue_count": mapping_issues,
                "mapping_issue_ratio": mapping_ratio,
            }
        )
        if mapping_blocks and mapping_ratio > cls.MAX_MAPPING_ISSUE_RATIO:
            issues.append(
                cls._issue(
                    "ERROR",
                    "scene_mapping_too_many_warnings",
                    "Quá nhiều block bị cảnh báo mapping scene/voice.",
                    mapping_issues=mapping_issues,
                    mapping_issue_ratio=mapping_ratio,
                )
            )
        elif mapping_issues_raw:
            issues.append(
                cls._issue(
                    "WARNING",
                    "scene_mapping_warnings",
                    "Một số block có cảnh báo mapping scene.",
                    mapping_issues=mapping_issues_raw,
                    mapping_issue_ratio=cls._ratio(mapping_issues_raw, max(1, mapping_blocks)),
                )
            )

        retrieval = package.get("scene_retrieval_report") or {}
        retrieval_checked = int(retrieval.get("checked_blocks", 0) or 0)
        retrieval_issues = int(retrieval.get("issue_count", 0) or 0)
        retrieval_mismatch = int(retrieval.get("mismatch_count", 0) or 0)
        retrieval_weak = int(retrieval.get("weak_block_count", 0) or 0)
        retrieval_mismatch_ratio = cls._ratio(retrieval_mismatch, max(1, retrieval_checked))
        retrieval_weak_ratio = cls._ratio(retrieval_weak, max(1, retrieval_checked))
        metrics.update(
            {
                "scene_retrieval_checked_blocks": retrieval_checked,
                "scene_retrieval_issue_count": retrieval_issues,
                "scene_retrieval_mismatch_count": retrieval_mismatch,
                "scene_retrieval_weak_block_count": retrieval_weak,
                "scene_retrieval_mismatch_ratio": retrieval_mismatch_ratio,
                "scene_retrieval_weak_ratio": retrieval_weak_ratio,
            }
        )
        if retrieval_checked and retrieval_mismatch_ratio > cls.MAX_SCENE_RETRIEVAL_MISMATCH_RATIO:
            issues.append(
                cls._issue(
                    "ERROR",
                    "scene_retrieval_mismatch_too_high",
                    "Nhieu block co noi dung voice hop voi canh khac hon canh dang map.",
                    mismatch_count=retrieval_mismatch,
                    mismatch_ratio=retrieval_mismatch_ratio,
                )
            )
        elif retrieval_checked and retrieval_weak_ratio > cls.MAX_SCENE_RETRIEVAL_WEAK_RATIO:
            issues.append(
                cls._issue(
                    "ERROR",
                    "scene_retrieval_too_weak",
                    "Nhieu block khong tim thay anchor SRT/visual khop voi loi review.",
                    weak_blocks=retrieval_weak,
                    weak_ratio=retrieval_weak_ratio,
                )
            )
        elif retrieval_issues:
            issues.append(
                cls._issue(
                    "WARNING",
                    "scene_retrieval_warnings",
                    "Mot so block can kiem tra lai vi retrieval thay anchor yeu hoac co ung vien canh tot hon.",
                    issue_count=retrieval_issues,
                    mismatch_ratio=retrieval_mismatch_ratio,
                    weak_ratio=retrieval_weak_ratio,
                )
            )

        alignment = package.get("srt_alignment_report") or {}
        editor_synced = bool(package.get("script_editor_synced"))
        alignment_blocks = int(alignment.get("block_count", 0) or 0)
        alignment_errors = int(alignment.get("error_count", 0) or 0)
        alignment_weak = int(alignment.get("weak_block_count", 0) or 0)
        alignment_ratio = cls._ratio(alignment_weak, max(1, alignment_blocks))
        metrics.update(
            {
                "srt_alignment_block_count": alignment_blocks,
                "srt_alignment_error_count": alignment_errors,
                "srt_alignment_weak_block_count": alignment_weak,
                "srt_alignment_weak_ratio": alignment_ratio,
                "srt_alignment_ready": bool(alignment.get("ready", True)),
            }
        )
        if alignment_blocks and alignment_errors:
            # Chỉ ERROR khi tỷ lệ lỗi nghiêm trọng (> 15% blocks), không phải bất kỳ lỗi nào
            # Ngưỡng 15%: phim dài 80 block → cho phép tối đa 12 block lỗi trước khi fail
            alignment_error_ratio = cls._ratio(alignment_errors, max(1, alignment_blocks))
            if editor_synced and alignment_error_ratio > 0.15:
                issues.append(
                    cls._issue(
                        "WARNING",
                        "srt_alignment_editor_confirmed",
                        "Script Editor da xac nhan theo timeline/keyframes video bam; alignment voi SRT goc chi con la canh bao.",
                        error_count=alignment_errors,
                        weak_blocks=alignment_weak,
                        error_ratio=round(alignment_error_ratio, 3),
                    )
                )
            elif alignment_error_ratio > 0.15:
                issues.append(
                    cls._issue(
                        "ERROR",
                        "srt_alignment_failed",
                        "Kich ban/voice bi lech voi SRT goc va timeline video bam.",
                        error_count=alignment_errors,
                        weak_blocks=alignment_weak,
                        error_ratio=round(alignment_error_ratio, 3),
                    )
                )
            else:
                issues.append(
                    cls._issue(
                        "WARNING",
                        "srt_alignment_minor_errors",
                        "Một vài block lệch nhẹ với SRT gốc, chấp nhận được.",
                        error_count=alignment_errors,
                        weak_blocks=alignment_weak,
                        error_ratio=round(alignment_error_ratio, 3),
                    )
                )
        elif editor_synced and alignment_blocks and alignment_ratio > cls.MAX_SRT_ALIGNMENT_WEAK_RATIO:
            issues.append(
                cls._issue(
                    "WARNING",
                    "srt_alignment_editor_confirmed_weak",
                    "Script Editor da xac nhan theo timeline/keyframes video bam; mot so block yeu voi SRT goc chi can theo doi.",
                    weak_blocks=alignment_weak,
                    weak_ratio=alignment_ratio,
                )
            )
        elif alignment_blocks and alignment_ratio > cls.MAX_SRT_ALIGNMENT_WEAK_RATIO:
            issues.append(
                cls._issue(
                    "ERROR",
                    "srt_alignment_too_weak",
                    "Qua nhieu block khong bam dung SRT cua phan video da bam.",
                    weak_blocks=alignment_weak,
                    weak_ratio=alignment_ratio,
                )
            )
        elif alignment_blocks and not alignment.get("ready", True):
            issues.append(
                cls._issue(
                    "WARNING",
                    "srt_alignment_needs_rewrite",
                    "Mot so block can rewrite de khop SRT goc va video bam hon.",
                    weak_blocks=alignment_weak,
                    weak_ratio=alignment_ratio,
                )
            )

        if context_coverage_report.get("status") == "poor" and not visual_evidence_report.get("ok"):
            issues.append(
                cls._issue(
                    "ERROR",
                    "poor_context_without_visual_rescue",
                    "Context coverage yếu nhưng không có visual evidence rescue thành công.",
                )
            )

        errors = [item for item in issues if item.get("level") == "ERROR"]
        warnings = [item for item in issues if item.get("level") == "WARNING"]
        ready = not errors if strict else not (package.get("ai_quota_fallback_used") or vi_report.get("needs_repair"))
        status = "pass" if ready and not warnings else ("warn" if ready else "fail")
        recommendations = cls._recommendations(issues)
        return {
            "version": cls.VERSION,
            "strict": bool(strict),
            "ready": bool(ready),
            "status": status,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issues": issues,
            "metrics": metrics,
            "recommendations": recommendations,
        }

    @staticmethod
    def _recommendations(issues: Sequence[Dict[str, Any]]) -> List[str]:
        types = {str(item.get("type")) for item in issues}
        recs: List[str] = []
        if "vietnamese_diacritics_missing" in types:
            recs.append("Chạy lại repair tiếng Việt có dấu trước TTS.")
        if "evidence_coverage_too_low" in types or "too_many_weak_context_blocks" in types:
            recs.append("Dùng SRT nguồn đầy đủ/đã dịch và tạo SceneCard/Visual evidence cho các cảnh quan trọng.")
        if "poor_context_without_visual_rescue" in types:
            recs.append("Bật Vision nhanh hoặc bổ sung mô tả keyframe trước khi tạo kịch bản.")
        if "script_grounding_too_weak" in types or "scene_mapping_too_many_warnings" in types:
            recs.append("Sinh lại AI_FULL theo mode bám evidence; không render bản hiện tại.")
        if "critical_scene_anchor_missing" in types:
            recs.append("Sửa các block thiếu must_mention/visual anchor trước khi tạo voice.")
        if "scene_retrieval_mismatch_too_high" in types or "scene_retrieval_too_weak" in types:
            recs.append("Chay lai AI_FULL voi chapter/transcript context; sua cac block voice khop nham canh.")
        if "srt_alignment_failed" in types or "srt_alignment_too_weak" in types:
            recs.append("So sanh lai SRT goc, SRT video bam va kich ban; rewrite cac block thieu anchor truoc khi tao voice.")
        if "script_render_block_mismatch" in types:
            recs.append("Xóa cache ai_package cũ và chạy lại từ CLIP_FIND/AI_FULL.")
        if "ai_quota_fallback_used" in types:
            recs.append("Đợi API quota hoặc dùng OpenRouter/AI Studio rồi nhập lại JSON hợp lệ.")
        if not recs:
            recs.append("Có thể tiếp tục tạo voice/render; vẫn nên kiểm tra vài cảnh đắt giá thủ công.")
        return recs

    @classmethod
    def annotate_package(
        cls,
        package: Dict[str, Any],
        render_blocks: Iterable[Dict[str, Any]],
        context_coverage_report: Dict[str, Any] | None = None,
        visual_evidence_report: Dict[str, Any] | None = None,
        strict: bool = True,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        package = dict(package or {})
        report = cls.validate(
            package,
            render_blocks,
            context_coverage_report=context_coverage_report,
            visual_evidence_report=visual_evidence_report,
            strict=strict,
        )
        package["market_readiness_report"] = report
        package["market_ready"] = bool(report.get("ready"))
        return package, report


