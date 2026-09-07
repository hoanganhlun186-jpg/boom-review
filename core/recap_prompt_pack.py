"""Shared RECAP prompt pack used by AI_FULL and Script Editor.

The rules are intentionally ASCII-safe to avoid Windows console encoding issues.
"""

RECAP_PROMPT_PACK = {
    "analyze_scene": {
        "title": "Phan tich phan canh",
        "task": "Phan tich phan canh phim: tom tat, nhan vat, boi canh, cam xuc, muc do quan trong de dung kich ban recap.",
        "rules": [
            "Return JSON only.",
            "Do not invent unsupported plot details.",
            "Use provided subtitles, keyframe descriptions, visual notes, and keyframe paths.",
            "Filter out ads, betting/casino logos, promotional audio, spam subtitles: set recap_use=false and low importance.",
            "Filter out studio logos, previous-episode recap, opening trailer montage, ending credits: set recap_use=false and low importance.",
            "recap_use=true only for scenes that belong to the real movie storyline.",
        ],
    },
    "build_timeline": {
        "title": "Xay dung dong thoi gian",
        "task": "Sap xep phan canh thanh timeline su kien chinh theo dung thu tu cot truyen, giu causal logic.",
        "rules": [
            "Return JSON array only.",
            "Avoid rough merging of scenes with distinct physical actions or different dialogue.",
            "Keep enough granular events so narration can track visual actions precisely.",
            "Preserve causal logic: event A should naturally lead to event B.",
        ],
    },
    "write_recap_script": {
        "title": "Viet kich ban thuyet minh",
        "task": "Write detailed Vietnamese movie recap narration that is natural, engaging, scene-grounded, and follows the timeline.",
        "rules": [
            "Return JSON only.",
            "Return JSON shape: {title:string, segments:[{segment_id:int, scene_ids:[int], narration:string, start:float, end:float, mapping_reason:string, evidence:{visual_actions:string, keyframe_refs:[string], confidence:float}}]}",
            "evidence.visual_actions must describe the physical actions seen in keyframes/visual_notes that match the narration.",
            "evidence.keyframe_refs must include relative keyframe image paths from the matched scenes when available.",
            "MANDATORY: write narration in the selected review tone/style.",
            "Focus on visual actions and dialogue meaning, not raw subtitle translation.",
            "Each segment narration must fit the target duration reasonably.",
            "Every segment must reference scene_ids and strictly align with those visual events.",
            "Do not mix unrelated events into one segment.",
            "If a segment spans multiple scenes, reference all scene_ids in chronological order.",
            "Write 2-3 smooth connected sentences per segment when duration allows.",
            "Prefer 1-2 scene_ids per segment; avoid grouping 3+ unrelated scenes.",
        ],
    },
    "harmonize_recap_scripts": {
        "title": "Chuan hoa chunks",
        "task": "Combine chunk scripts into one coherent master recap, remove repetition, keep flow and tone consistent.",
        "rules": [
            "Return JSON only.",
            "Maintain smooth narrative flow and consistent tone.",
            "Remove repetitive intros/outros from individual chunks.",
            "Each block must advance the story; no repeated generic framing.",
        ],
    },
}



NARRATIVE_ROLE_RULES = """NARRATIVE ROLE MAP FOR A COMPLETE RECAP:
- hook_intro: only the first block/segment of the whole video. Open with the MOST DRAMATIC or TENSE moment available in the evidence — a confrontation, a revelation, a danger — not the earliest chronological event. Drop the viewer into the middle of the conflict first, then provide context. Never open with "today we review", "let's begin", or a generic character introduction. The first sentence must create immediate curiosity or tension.
- setup_context: early blocks. Explain who is involved, where the pressure comes from, and why the event matters.
- rising_action: middle blocks. Move the investigation/conflict forward with cause -> reaction -> consequence.
- escalation_twist: later-middle blocks. Emphasize discoveries, reversals, danger, suspicion, or emotional stakes.
- climax_payoff: near the end. Resolve the main beat shown in the cut video, but do not invent an ending outside evidence.
- ending_aftertaste: final block/segment only. Close the recap with consequence/aftertaste, then add ONE short Vietnamese channel CTA such as asking viewers to follow the channel and wait for the newest/next episode.
- Only ONE hook_intro and ONE ending_aftertaste are allowed in a full script.
- Only the final block may contain follow/subscribe/next-episode CTA. Body blocks must not contain channel CTA.
- Body blocks must not restart the story or use repeated intros/outros.
""".strip()


def narrative_role_for_position(index: int, total: int) -> dict:
    """Return the story role for a 1-based block/segment position."""
    try:
        index = int(index or 1)
        total = int(total or 1)
    except Exception:
        index, total = 1, 1
    total = max(1, total)
    index = max(1, min(index, total))
    if total == 1:
        role = "complete_mini_recap"
        label = "Mini recap: open with conflict, explain the turn, and close with payoff in one smooth paragraph."
    elif index == 1:
        role = "hook_intro"
        label = "Opening: hook the earliest real conflict and introduce the main situation/character."
    elif index == total:
        role = "ending_aftertaste"
        label = "Ending: close the shown story beat with consequence/aftertaste; do not restart or add a new intro."
    else:
        ratio = (index - 1) / max(1, total - 1)
        if ratio < 0.20:
            role = "setup_context"
            label = "Setup: clarify context, character pressure, and why the event matters."
        elif ratio < 0.68:
            role = "rising_action"
            label = "Body: advance cause -> reaction -> consequence while staying on this block evidence."
        elif ratio < 0.88:
            role = "escalation_twist"
            label = "Escalation: highlight discovery, reversal, danger, suspicion, or emotional stakes."
        else:
            role = "climax_payoff"
            label = "Payoff: resolve or tighten the current beat and bridge toward the ending."
    return {"scene_role": role, "scene_role_label": label}


def narrative_role_rules_text() -> str:
    return NARRATIVE_ROLE_RULES

def recap_prompt_pack_text(section: str | None = None) -> str:
    sections = [section] if section else list(RECAP_PROMPT_PACK.keys())
    out = []
    for key in sections:
        item = RECAP_PROMPT_PACK.get(key)
        if not item:
            continue
        out.append(f"[{key.upper()}] {item['title']}")
        out.append(f"Task: {item['task']}")
        for rule in item.get("rules", []):
            out.append(f"- {rule}")
    return "\n".join(out).strip()


def recap_block_prompt_rules() -> str:
    return """BLOCK WRITING RULES FROM RECAP PROMPT PACK:
- Treat every block as a mapped recap segment, not a translated subtitle line.
- The narration must match the block scene_ids/source_block_ids, visual actions, SRT evidence, and keyframe/visual notes.
- Write like a professional Vietnamese YouTube movie recap: visible action -> plot/emotional meaning -> consequence/bridge.`r`n- Respect scene_role/narrative_role: hook_intro opens, body advances, ending_aftertaste closes with one short follow-channel / next-episode CTA only in the final block.
- Do not invent unsupported plot details, suspects, motives, locations, or relationships.
- Do not mix unrelated events. If multiple source scenes are used, keep them chronological and connected.
- Avoid repeated generic phrases and repeated sentence structures across blocks.
- If evidence shows ad/logo/betting/trailer/credits/spam, do not create fake story narration for it.
- When keyframe_refs or visual notes exist, use them as hard visual grounding.
- NEVER copy system-announcement subtitles verbatim into narration. Subtitles like "[creature/character name] chien dau he cap [rank] sao", "[skill] kich hoat", "[title] [name]" are game/system UI text — convert them into natural narration instead (e.g. "mot con di thu cap C xuat hien" not the raw subtitle text). If the same announcement pattern appeared in a previous block, do not repeat the same sentence structure.
""".strip()

