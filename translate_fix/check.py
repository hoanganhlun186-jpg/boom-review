import ast
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import os

source = Path(__file__).with_name('translate_tab.py').read_text(encoding='utf-8')
tree = ast.parse(source)
compile(tree, 'translate_tab.py', 'exec')
method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_extract_full_series_context_impl')
sys.path.insert(0, r'C:\Users\anh\Desktop\update honggou tự động')
ns = {'os': os}
exec(compile(ast.Module(body=[method], type_ignores=[]), '<test>', 'exec'), ns)
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / '1.srt'
    path.write_text('1\n00:00:00,000 --> 00:00:01,000\n你好\n', encoding='utf-8')
    prompts = []
    replies = iter(['**Đã sẵn sàng.**', 'Đã nhận lượt 1.', 'Bối cảnh: Hiện đại.\nNhân vật: Chưa xác định.'])
    def send(page, name, prompt, **kwargs):
        prompts.append(prompt)
        return next(replies)
    fake = SimpleNamespace(context_items=[{'srt': str(path)}], address_notes='Giữ tên riêng',
        _parse_srt=lambda text: [{'stt': '1', 'text': '你好'}],
        _send_and_wait=send, _cancel=False, translate_workers=2,
        log=SimpleNamespace(emit=lambda text: None))
    result = ns[method.name](fake, None)
    assert 'Bối cảnh: Hiện đại.' in result
    assert len(prompts) == 3
    assert all('JSON' not in p and 'LOI_THIEU_DU_LIEU' not in p for p in prompts)
    assert ns[method.name](fake, None) == result
    assert len(prompts) == 3, 'Cache should avoid another request'
    fake.address_notes = 'Changed notes'
    fake._send_and_wait = lambda *a, **kw: 'ERROR: connection failed'
    try:
        ns[method.name](fake, None)
    except RuntimeError as exc:
        assert 'connection failed' in str(exc)
    else:
        raise AssertionError('Transport error must still stop the operation')
assert 'decode_rules' not in source
assert 'response_complete' not in source
assert 'LOI_THIEU_DU_LIEU' not in source
print('PASS: syntax, plain-text replies, natural acknowledgments, cache reuse/invalidation, transport errors')
