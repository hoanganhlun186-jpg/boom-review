# Voice Selection Guide - AutoRecapPro V2

## Vấn đề: Giọng nói không phải tiếng Việt

Nếu voice TTS không phải tiếng Việt, hệ thống sẽ log ra cảnh báo:
```
⚠️ Không phải tiếng Việt, hệ thống dùng: en-US-AriaNeural
```

## Nguyên nhân

Edge TTS (dịch vụ text-to-speech) có thể không có voice tiếng Việt sẵn có tùy thuộc vào:
1. **Mạng internet** - Nếu không kết nối tốt, voice list có thể không cập nhật
2. **Khu vực địa lý** - Một số region không có Vietnamese voices
3. **Phiên bản Edge TTS** - Phiên bản cũ có thể không hỗ trợ

## Giải pháp

### 1️⃣ **Cách tốt nhất: Đảm bảo edge-tts mới nhất**
```bash
pip install --upgrade edge-tts
```

### 2️⃣ **Kiểm tra voice có sẵn**
Chạy script sau để xem voice nào available:
```python
import asyncio
import edge_tts

async def check_voices():
    voices = await edge_tts.list_voices()
    
    # Tìm Vietnamese voices
    vi_voices = [v for v in voices if v.get('Locale', '').startswith('vi-')]
    print(f"Vietnamese voices: {len(vi_voices)}")
    for v in vi_voices:
        print(f"  ✓ {v.get('ShortName')}: {v.get('LocalName')}")
    
    # Tìm các voices khác
    print(f"\nOther languages: {len(voices) - len(vi_voices)}")

asyncio.run(check_voices())
```

### 3️⃣ **Nếu không có Vietnamese voice**
Hệ thống sẽ tự động fallback sang bất kỳ voice nào available. Có 2 lựa chọn:

**A. Chỉnh sửa code để yêu cầu voice cụ thể:**

Edit `engine/ai_engine.py`, tìm hàm `text_to_speech()` và thay đổi:

```python
# Tìm dòng này (trong main.py):
chosen_voice = asyncio.run(ai.text_to_speech(script))

# Thay thành (ví dụ: dùng voice tiếng Anh có accent):
chosen_voice = asyncio.run(ai.text_to_speech(script, voice="en-US-AriaNeural"))
```

**B. Sử dụng voice option UI (cần thêm vào GUI - không có sẵn)**

## Voice Priority Logic

Hệ thống sẽ thử voices theo thứ tự này:

1. **Nếu user chỉ định** → Dùng voice đó
2. **Tất cả Vietnamese voices (vi-*)** → Thử lần lượt
3. **Bất kỳ voice nào available** → Fallback cuối cùng

## Ví dụ Log Output

### ✓ Nếu có Vietnamese voice:
```
🎙️ Đang tạo giọng nói (ưu tiên tiếng Việt)...
✓ Giọng nói hoàn thành: vi-VN-HoaiMyNeural
  → Tiếng Việt ✓
```

### ⚠️ Nếu không có Vietnamese:
```
🎙️ Đang tạo giọng nói (ưu tiên tiếng Việt)...
✓ Giọng nối hoàn thành: en-US-AriaNeural
  ⚠️ Không phải tiếng Việt, hệ thống dùng: en-US-AriaNeural
```

## Khuyến cáo

1. **Cập nhật edge-tts**: `pip install --upgrade edge-tts`
2. **Kiểm tra mạng**: Vietnamese voices có thể mất tài liệu nếu mạng chậm
3. **Báo cáo lỗi**: Nếu vẫn không có voice Việt sau khi cập nhật, có thể là vấn đề của Edge TTS service

## FAQ

**Q: Voice tiếng Việt nào được hỗ trợ?**
A: Thường là `vi-VN-HoaiMyNeural` (nữ) hoặc `vi-VN-NamMinh-X` (nam), tùy phiên bản.

**Q: Tôi có thể chọn voice cụ thể không?**
A: Có, sửa code như mô tả ở **Giải pháp 3B** hoặc chờ bản nâng cấp có UI.

**Q: Nếu dùng voice tiếng Anh thì sao?**
A: Video sẽ có narration tiếng Anh thay vì tiếng Việt, nhưng vẫn hoạt động bình thường.

**Q: Có cách nào tải voice offline?**
A: Không, Edge TTS cần kết nối internet để streaming voice.
