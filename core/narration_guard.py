"""Reject assistant refusals and meta responses as spoken narration."""
import re
import unicodedata

def invalid_narration(text):
    folded = ''.join(c for c in unicodedata.normalize('NFD', str(text or '').lower())
                     if unicodedata.category(c) != 'Mn').replace('đ', 'd')
    folded = re.sub(r'\s+', ' ', folded).strip()
    return not folded or any(pattern in folded for pattern in (
        'toi khong the tro giup', 'toi khong the ho tro', 'toi khong the giup',
        'toi chi la mot mo hinh', 'la mot mo hinh ngon ngu', 'as a language model',
        'as an ai', "i cannot assist", "i can't assist", "i cannot help", "i can't help",
        'khong the dap ung yeu cau', 'xin loi, toi khong the'))
