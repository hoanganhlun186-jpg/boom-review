from pathlib import Path

p = Path(__file__).with_name('translate_tab.py')
s = p.read_text(encoding='utf-8')
s = s.replace('from full_context import packets, fingerprint, decode_rules, save_rules, SECTIONS', 'from full_context import packets, fingerprint, save_rules')
s = s.replace('signature = fingerprint(parts, self.address_notes)', 'signature = fingerprint(parts, "plain-text-conventions-v1\\n" + self.address_notes)')
s = s.replace('if all(rules.get(k) for k in SECTIONS):', 'if isinstance(rules, str) and rules.strip():')
s = s.replace('json.dumps(rules,ensure_ascii=False)+"\\nQuy ước người dùng: "+self.address_notes', 'rules+"\\nQuy ước người dùng: "+self.address_notes')
s = s.replace('Chưa rõ thì ghi chưa rõ, không đoán người nói từ thứ tự dòng. Nếu mất nội dung trước hãy báo LOI_THIEU_DU_LIEU. ', 'Chưa rõ thì ghi chưa rõ, không đoán người nói từ thứ tự dòng. ')
s = s.replace('Mỗi lượt chỉ xác nhận đúng mã nhận yêu cầu. Bây giờ chỉ trả SAN_SANG.', 'Mỗi lượt xác nhận ngắn gọn đã nhận. Bây giờ xác nhận đã sẵn sàng.')
s = s.replace('if response.strip() != "SAN_SANG": raise RuntimeError("Gemini chưa xác nhận sẵn sàng tiếp nhận toàn bộ SRT")', 'if not response.strip() or response.lstrip().startswith("ERROR"):\n            raise RuntimeError(response or "Gemini chưa phản hồi")')
s = s.replace('            token=f"DA_NHAN_{number}_{len(parts)}"\n', '')
s = s.replace('Chỉ trả {token} sau khi nhận, chưa phân tích.', 'Xác nhận ngắn gọn đã nhận, chưa phân tích.')
s = s.replace('{part}</KET_THUC_DU_LIEU>', '{part}\\n</KET_THUC_DU_LIEU>')
s = s.replace('if response.strip()!=token: raise RuntimeError(f"Không xác nhận được dữ liệu {number}/{len(parts)}; dừng trước khi dịch")', 'if not response.strip() or response.lstrip().startswith("ERROR"):\n                raise RuntimeError(response or f"Gemini chưa phản hồi lượt {number}/{len(parts)}")')
s = s.replace('            "Không dịch phụ đề. Nếu không còn đọc đủ các lượt, trả LOI_THIEU_DU_LIEU. "\n            "Chỉ trả một đối tượng JSON hợp lệ, dùng dấu ngoặc kép. received_parts liệt kê đủ các lượt thực sự nhận. "\n            "rules phải có đúng 7 mục sau:\\n"', '            "Không dịch phụ đề. Viết bộ quy ước chung bằng văn bản tiếng Việt dễ đọc, "\n            "chia thành 7 mục có tiêu đề sau:\\n"')
labels = {'boi_canh': 'Bối cảnh', 'van_phong': 'Văn phong', 'nhan_vat': 'Nhân vật', 'xung_ho_hai_chieu': 'Xưng hô hai chiều', 'thay_doi_quan_he': 'Thay đổi quan hệ', 'thuat_ngu': 'Thuật ngữ', 'chua_ro': 'Chưa rõ'}
for key, label in labels.items():
    s = s.replace('• ' + key + ':', '• ' + label + ':')
s = s.replace('để ở chua_ro.', "để ở mục Chưa rõ.")
start = s.index('        schema = json.dumps(', s.index('    def _extract_full_series_context_impl'))
end = s.index('        if cache_path:\n            save_rules', start)
s = s[:start] + '''        if self._cancel: raise RuntimeError("Đã hủy phân tích")
        response = self._send_and_wait(page, "Quy-uoc-toan-bo", prompt, continue_chat=True)
        rules = (response or "").strip()
        if not rules or rules.startswith("ERROR"):
            raise RuntimeError(rules or "Gemini trả bộ quy ước trống")
''' + s[end:]
s = s.replace('from gemini_response import generation_active, response_complete\n            structured = bot_name in ("Quy-uoc-toan-bo", "Kiem-tra-luot-thieu")', 'from gemini_response import generation_active\n            is_conventions = bot_name == "Quy-uoc-toan-bo"')
s = s.replace('24 if structured else 12', '24 if is_conventions else 12')
s = s.replace('if stable >= required_stable and (not structured or response_complete(cur)):', 'if stable >= required_stable:')
# The selected model remains a user choice; remove the broad Pro fallback,
# which could select a different model merely because its name contains Pro.
s = s.replace('                if (tgt.includes("pro") && txt.includes("pro")) match = true;\n', '')
s = s.replace('# BỎ CÔNG TẮC: Ép sử dụng duy nhất model khách chọn UI', '# Dùng model người dùng chọn; Auto giữ lựa chọn trên web')
p.write_text(s, encoding='utf-8')
