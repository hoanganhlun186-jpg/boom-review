"""
Đặt file này vào cùng thư mục với capcut_bridge.py hoặc import đầu capcut_bridge.py.
Nó đọc capcut_device.json và inject device_id/iid vào headers mặc định.
"""
import json, os, uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEVICE_FILE = os.path.join(_HERE, "capcut_device.json")

def get_device() -> dict:
    if os.path.exists(_DEVICE_FILE):
        try:
            with open(_DEVICE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("device_id") and d.get("iid"):
                return d
        except Exception:
            pass
    did = str(uuid.uuid4().int)[:19]
    iid = str(uuid.uuid4().int)[:19]
    d = {"device_id": did, "iid": iid, "tdid": did, "appvr": "4.1.0", "region": "us", "lan": "en"}
    with open(_DEVICE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    return d
