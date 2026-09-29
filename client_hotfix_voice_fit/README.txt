HOTFIX VOICE FIT - HUONG DAN COPY SANG MAY KHACH
=================================================

1. Tat AutoRecapPro tren may khach.

2. Sao luu 3 file cu tren may khach:
   core\voice_fit.py
   core\voice_speed.py
   core\full_pipeline.py

3. Copy de 2 file trong thu muc core cua goi nay vao thu muc core tren may khach:
   core\voice_fit.py
   core\voice_speed.py

4. Mo core\full_pipeline.py tren may khach va sua 3 cho sau.

CHO 1 - Khong retry lai VOICE_CONCAT khi da cham gioi han tang toc
-----------------------------------------------------------------
Trong ham _with_retry, tim:

            result = step_fn()
            if result:
                return True

Chen ngay ben duoi:

            current_step = self.steps.get(step_name)
            current_error = str(current_step.error or '') if current_step else ''
            if '[VOICE_FIT_LIMIT]' in current_error:
                self._log('   ❌ Voice block đã chạm giới hạn tăng tốc; không lặp lại cùng lỗi.')
                return False


CHO 2 - Tang version cache cua VOICE_CONCAT
-------------------------------------------
Trong ham step_voice_concat, tim:

        voice_concat_policy = {
            "version": 7,

Sua thanh:

        voice_concat_policy = {
            "version": 8,


CHO 3 - Sua prompt rut gon theo khoang dung sai +/-10 tu
--------------------------------------------------------
Trong ham rewrite_fit, thay phan prompt cu bang:

                    prompt = (
                        f'Rút gọn lời kể phim sau về khoảng {words} từ để khớp thời gian cảnh; '
                        f'chấp nhận chênh lệch tối đa 10 từ ({max(1, words - 10)}-{words + 10} từ). '
                        'Giữ đúng nhân vật, hành động chính, quan hệ nhân quả và thứ tự sự kiện. '
                        'Không thêm tình tiết. Ưu tiên sự kiện chính; bỏ tính từ, câu nhắc lại và CTA quảng bá nếu không đủ chỗ. '
                        'Số từ tính theo khoảng trắng, mỗi tiếng Việt cách nhau được tính một từ. '
                        'Giữ ngôn ngữ gốc, câu trọn nghĩa. Chỉ trả văn bản lời kể, không giải thích.\n'
                        + text)


KET QUA
-------
- Muc tieu 29 tu chap nhan 19-39 tu.
- Cau cu 49 tu, AI tra 39 tu: chap nhan ngay, khong goi lai 3 lan.
- Neu voice van dai hon hinh: tang toc rieng audio cua block do.
- Cac block khac van giu toc do mac dinh 1.2x.
- Toc do fallback toi da mac dinh 1.6x.
- Co the doi gioi han bang bien moi truong AUTORECAP_MAX_BLOCK_VOICE_SPEED.

KIEM TRA NHANH SAU KHI COPY
---------------------------
Chay tai thu muc du an:

python -m py_compile core/voice_fit.py core/voice_speed.py core/full_pipeline.py

