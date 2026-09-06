"""Review narration style profiles for AutoRecapPro.

The pipeline keeps story grounding separate from voice style.  These profiles
only shape how the recap is narrated; they must never override SRT/visual facts.
"""

from __future__ import annotations

import os
from typing import Dict


DEFAULT_STYLE = "professional_youtube_movie_recap"


STYLE_PROFILES: Dict[str, Dict[str, str]] = {
    "professional_youtube_movie_recap": {
        "name": "Review YouTube chuyên nghiệp",
        "voice": "Tự tin, rõ ý, có nhịp giữ chân người xem, nhưng không phóng đại sai sự thật.",
        "rhythm": "Câu vừa phải, nối mạch liên tục; mỗi block có mở ý, giải thích, rồi kéo sang cảnh sau.",
        "focus": "Hành động đang diễn ra, ý nghĩa của manh mối, động cơ nhân vật, hậu quả kéo sang đoạn tiếp theo.",
        "formula": "cảnh/sự kiện nhìn thấy -> ý nghĩa/drama -> hậu quả/câu nối.",
        "avoid": "Không viết chung chung kiểu 'ở cảnh này', 'chi tiết này', 'mạch phim tiếp tục'; không lặp cùng một công thức.",
    },
    "cinematic_suspense": {
        "name": "Điện ảnh căng thẳng",
        "voice": "Trầm, căng, có cảm giác bí mật đang dần lộ ra, phù hợp phim phá án/âm mưu.",
        "rhythm": "Câu giàu nhịp suspense, nhấn vào sự im lặng, ánh mắt, dấu hiệu bất thường và nguy cơ kế tiếp.",
        "focus": "Bầu không khí, nguy cơ, manh mối nhỏ, phản ứng bất thường và cú chuyển tình thế.",
        "formula": "dấu hiệu bất thường -> áp lực tăng lên -> câu hỏi/nguy cơ mở sang block sau.",
        "avoid": "Không làm quá như trailer kinh dị; không thêm bí mật nếu SRT/visual không chứng minh.",
    },
    "detective_case_analysis": {
        "name": "Phân tích vụ án",
        "voice": "Sắc bén, logic, như người dẫn đang bóc từng lớp chứng cứ cho khán giả.",
        "rhythm": "Câu gọn và chắc; ưu tiên nguyên nhân, bằng chứng, suy luận và điểm mâu thuẫn.",
        "focus": "Ai liên quan, chứng cứ nào xuất hiện, lời nói nào đáng nghi, vì sao chi tiết đó đổi hướng vụ án.",
        "formula": "chứng cứ/lời khai -> suy luận -> tác động tới nghi phạm hoặc hướng điều tra.",
        "avoid": "Không kết luận hung thủ khi chưa có căn cứ; không biến recap thành danh sách bằng chứng khô cứng.",
    },
    "historical_palace_intrigue": {
        "name": "Cổ trang quyền mưu",
        "voice": "Trang trọng vừa đủ, giàu cảm giác triều chính, lễ nghi và thế lực ngầm.",
        "rhythm": "Câu mượt, có trọng lượng; nhấn vào thân phận, phép tắc, quyền lực, lời nói hai tầng nghĩa.",
        "focus": "Quan hệ thân phận, mệnh lệnh, nghi lễ, nghi án, quyền lực sau lưng nhân vật và cái giá nếu sai một bước.",
        "formula": "lời nói/hành động -> tầng nghĩa quyền mưu -> rủi ro hoặc thế cờ tiếp theo.",
        "avoid": "Không dùng từ hiện đại quá lố; không thêm chức tước hoặc âm mưu ngoài dữ liệu nguồn.",
    },
    "emotional_drama": {
        "name": "Tâm lý cảm xúc",
        "voice": "Ấm hơn, giàu cảm xúc, tập trung vào tổn thương, lựa chọn và quan hệ giữa nhân vật.",
        "rhythm": "Câu mềm, có nhịp lắng; vẫn giữ đủ thông tin để người xem theo kịp câu chuyện.",
        "focus": "Ánh mắt, sự do dự, nỗi sợ, hiểu lầm, tình cảm bị che giấu và quyết định khó nói.",
        "formula": "hành động nhỏ -> cảm xúc/ẩn ý -> hậu quả với mối quan hệ.",
        "avoid": "Không sến, không diễn giải tâm lý quá đà nếu cảnh không có bằng chứng.",
    },
    "romantic_emotional_recap": {
        "name": "Tình cảm lãng mạn",
        "voice": "Ấm, mềm và có chiều sâu cảm xúc, tập trung vào tình cảm, sự rung động, hiểu lầm và lựa chọn của nhân vật.",
        "rhythm": "Câu mượt, giàu nhịp dẫn chuyện; có khoảng lắng ở các khoảnh khắc tình cảm nhưng vẫn giữ mạch recap rõ ràng.",
        "focus": "Ánh mắt, lời chưa nói, khoảng cách giữa hai nhân vật, sự quan tâm bị che giấu, hiểu lầm và biến chuyển trong mối quan hệ.",
        "formula": "hành động/cử chỉ -> cảm xúc hoặc ẩn ý tình cảm -> tác động tới quan hệ và cầu nối sang cảnh sau.",
        "avoid": "Không sến quá mức, không tự gán tình yêu nếu cảnh chưa chứng minh; không làm mất thông tin cốt truyện vì quá tập trung cảm xúc.",
    },
    "fast_hook_shorts": {
        "name": "Hook nhanh giữ chân",
        "voice": "Nhanh, sắc, nhiều lực kéo, phù hợp video ngắn hoặc đoạn cần giữ retention cao.",
        "rhythm": "Câu ngắn hơn, ít mệnh đề, vào thẳng xung đột và kết thúc bằng lực kéo rõ.",
        "focus": "Cú lật, nguy cơ, bí mật, hành động quyết định và câu hỏi khiến người xem muốn xem tiếp.",
        "formula": "điểm sốc -> vì sao nguy hiểm -> chuyện gì sắp nổ ra.",
        "avoid": "Không clickbait sai sự thật; không bỏ mất bối cảnh quan trọng chỉ để câu ngắn.",
    },
    "calm_explainer": {
        "name": "Giải thích mạch lạc",
        "voice": "Điềm tĩnh, rõ ràng, dễ nghe, phù hợp recap dài cần ít ồn nhưng vẫn cuốn.",
        "rhythm": "Câu sạch, ít giật gân; ưu tiên logic nguyên nhân-kết quả và chuyển đoạn mượt.",
        "focus": "Diễn biến chính, động cơ, bối cảnh, quan hệ nhân vật và sự thay đổi trong từng cảnh.",
        "formula": "bối cảnh -> hành động chính -> ý nghĩa trong mạch truyện.",
        "avoid": "Không quá đều giọng; mỗi block vẫn cần có điểm nhấn để không bị giống phụ đề đọc lại.",
    },
    "horror_suspense_recap": {
        "name": "Kinh dị căng thẳng",
        "voice": "Trầm, hồi hộp, tạo cảm giác nguy hiểm đang áp sát, nhưng không bịa thêm ma quỷ hay cú sốc ngoài hình ảnh/SRT.",
        "rhythm": "Câu có nhịp nén, nhấn vào im lặng, ánh mắt, tiếng động, dấu hiệu lạ và sự bất an tăng dần.",
        "focus": "Nỗi sợ, manh mối bất thường, phản ứng né tránh, nguy cơ phía sau hành động nhỏ và câu hỏi khiến người xem muốn biết chuyện gì tiếp theo.",
        "formula": "dấu hiệu đáng ngờ -> cảm giác bất an/nguy cơ -> hệ quả hoặc câu hỏi kéo sang block sau.",
        "avoid": "Không hét lên kiểu trailer, không lạm dụng từ 'kinh hoàng', không biến cảnh bình thường thành siêu nhiên nếu nguồn không có.",
    },
    "comedy_witty_recap": {
        "name": "Hài hước duyên dáng",
        "voice": "Dí dỏm, có bình luận thông minh và nhẹ nhàng, nhưng vẫn giữ mạch recap rõ và không làm sai tính cách nhân vật.",
        "rhythm": "Câu linh hoạt, thỉnh thoảng có một cú nhấn hài ngắn sau thông tin chính; không biến toàn bộ block thành pha tấu hài.",
        "focus": "Tình huống trớ trêu, phản ứng ngượng ngùng, hiểu lầm, câu nói gây cười và sự đối lập giữa điều nhân vật muốn với điều đang xảy ra.",
        "formula": "sự kiện chính -> điểm trớ trêu/hài nhẹ -> hậu quả hoặc cú chuyển sang cảnh sau.",
        "avoid": "Không chế lời quá đà, không mỉa mai độc ác, không dùng meme hiện đại nếu phim/cảnh không hợp.",
    },
    "action_high_energy_recap": {
        "name": "Hành động dồn dập",
        "voice": "Mạnh, gọn, giàu lực đẩy, ưu tiên hành động, quyết định nhanh và nguy cơ trực tiếp.",
        "rhythm": "Câu chắc, ít vòng vo; nhấn vào ai ra tay, ai bị ép lựa chọn, thời gian đang cạn và đòn đáp trả tiếp theo.",
        "focus": "Rượt đuổi, giao tranh, phục kích, kế hoạch, phản công, nhịp nguy hiểm và lợi thế đổi bên trong từng cảnh.",
        "formula": "hành động/đòn quyết định -> lợi thế hoặc rủi ro đổi chiều -> cú nối sang biến cố kế tiếp.",
        "avoid": "Không thêm cảnh đánh nhau nếu không có; không dùng quá nhiều từ hô hào làm mất tính review.",
    },
    "horror_comedy_action_mix": {
        "name": "Kinh dị + hài + hành động",
        "voice": "Giữ nền review chuyên nghiệp, pha căng thẳng khi có nguy cơ, chèn hài duyên khi tình huống cho phép, và tăng lực kể ở đoạn hành động.",
        "rhythm": "Linh hoạt theo cảnh: cảnh bí ẩn thì nén giọng, cảnh trớ trêu thì có một cú bình luận nhẹ, cảnh hành động thì câu ngắn và dứt khoát.",
        "focus": "Nguy cơ, phản ứng nhân vật, cú trớ trêu, hành động đẩy mạch phim và hậu quả rõ ràng sau mỗi lựa chọn.",
        "formula": "sự kiện nhìn thấy -> sắc thái phù hợp cảnh (sợ/hài/hành động) -> ý nghĩa và cầu nối sang block sau.",
        "avoid": "Không trộn cả ba sắc thái trong mọi câu; chỉ dùng sắc thái đúng với bằng chứng trong block để không bị lố.",
    },
}


ALIASES = {
    "youtube": "professional_youtube_movie_recap",
    "professional": "professional_youtube_movie_recap",
    "chuyen_nghiep": "professional_youtube_movie_recap",
    "review": "professional_youtube_movie_recap",
    "suspense": "cinematic_suspense",
    "cang_thang": "cinematic_suspense",
    "pha_an": "detective_case_analysis",
    "trinh_tham": "detective_case_analysis",
    "detective": "detective_case_analysis",
    "co_trang": "historical_palace_intrigue",
    "quyen_muu": "historical_palace_intrigue",
    "cung_dau": "historical_palace_intrigue",
    "cam_xuc": "emotional_drama",
    "tam_ly": "emotional_drama",
    "tinh_cam": "romantic_emotional_recap",
    "lang_man": "romantic_emotional_recap",
    "romance": "romantic_emotional_recap",
    "romantic": "romantic_emotional_recap",
    "shorts": "fast_hook_shorts",
    "hook": "fast_hook_shorts",
    "nhanh": "fast_hook_shorts",
    "giai_thich": "calm_explainer",
    "calm": "calm_explainer",
    "kinh_di": "horror_suspense_recap",
    "horror": "horror_suspense_recap",
    "hai_huoc": "comedy_witty_recap",
    "comedy": "comedy_witty_recap",
    "hanh_dong": "action_high_energy_recap",
    "action": "action_high_energy_recap",
    "kinh_di_hai_hanh_dong": "horror_comedy_action_mix",
    "horror_comedy_action": "horror_comedy_action_mix",
}


def normalize_review_style(style: str | None = None) -> str:
    key = str(style or os.environ.get("AUTORECAP_REVIEW_STYLE") or DEFAULT_STYLE).strip().lower()
    key = key.replace("-", "_").replace(" ", "_")
    return ALIASES.get(key, key if key in STYLE_PROFILES else DEFAULT_STYLE)


def review_style_metadata(style: str | None = None) -> Dict[str, str]:
    key = normalize_review_style(style)
    data = dict(STYLE_PROFILES[key])
    data["id"] = key
    return data


def review_style_instruction(style: str | None = None) -> str:
    data = review_style_metadata(style)
    return (
        f"STYLE_ID: {data['id']}\n"
        f"STYLE_NAME: {data['name']}\n"
        f"VOICE: {data['voice']}\n"
        f"RHYTHM: {data['rhythm']}\n"
        f"FOCUS: {data['focus']}\n"
        f"BLOCK_FORMULA: {data['formula']}\n"
        f"AVOID: {data['avoid']}\n"
        "GROUNDING: Phong cách chỉ điều chỉnh giọng kể. Luôn ưu tiên SRT, visual_anchor, character_focus và timeline."
    )


def story_writer_instruction() -> str:
    return (
        "STORY_WRITER_LAYER:\n"
        "- Treat each block as one story beat, not one translated subtitle.\n"
        "- Every block must answer 3 questions: What changes on screen? Why does it matter? What pressure/question carries into the next beat?\n"
        "- Use this beat ladder across the video: hook -> setup -> discovery -> suspicion -> escalation -> twist/reveal -> consequence -> cliff/aftertaste.\n"
        "- For each block, write a natural recap voiceover with: visible action/clue -> meaning for character/case -> consequence/bridge.\n"
        "- Dialogue is evidence only. Do not paste long dialogue back into the review; convert it into narration and mention only the key phrase when needed.\n"
        "- Prefer specific nouns over generic labels: character name, object, order, clue, risk, relationship, location, motive.\n"
        "- Avoid generic filler such as 'mạch phim tiếp tục', 'ở cảnh này', 'chi tiết này', 'áp lực tăng lên', unless followed by a concrete fact from the block.\n"
        "- Return/keep metadata when JSON allows it: story_beat, narrative_goal, bridge_line, character_focus.\n"
        "- The final result must feel like a continuous professional movie recap, not independent subtitles."
    )


def available_review_styles() -> Dict[str, str]:
    return {key: value["name"] for key, value in STYLE_PROFILES.items()}
