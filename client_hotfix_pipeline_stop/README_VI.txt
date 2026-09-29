HOTFIX RIÊNG: PIPELINE DỪNG Ở SCRIPT REVIEW / VOICE_SEGMENTS

Chỉ sửa file:
  core/full_pipeline.py

Không bao gồm các thay đổi voice-fit, voice-speed hoặc source-map khác.

Cách áp dụng trên máy khách (tại thư mục gốc AutoRecapPro_V2):

  git apply --check client_hotfix_pipeline_stop/HOTFIX_PIPELINE_STOP_ONLY.patch
  git apply client_hotfix_pipeline_stop/HOTFIX_PIPELINE_STOP_ONLY.patch

Nếu máy khách không dùng Git, mở file PATCH và áp dụng 5 hunk vào
core/full_pipeline.py. Mỗi dòng bắt đầu bằng dấu + là phần thêm mới;
dòng bắt đầu bằng dấu - là phần cần thay thế.

Hotfix gồm đúng 5 thay đổi:
1. Nhận diện package chapter_budget_story_segment là package mới hợp lệ.
2. Không loại cache mới chỉ vì có hơn 160 story segments.
3. Không chặn VOICE_SEGMENTS của package mới có 264 segments.
4. Cho lượt khử câu lặp cuối chạy thay vì bị market gate chặn trước.
5. Sau 3 lượt tự sửa, câu lặp/lỗi bám cảnh chỉ còn là cảnh báo;
   block rỗng hoặc lời kể không hợp lệ vẫn dừng để bảo vệ phí TTS.

Sau khi áp dụng: khởi động lại ứng dụng và Resume job đã lưu.
