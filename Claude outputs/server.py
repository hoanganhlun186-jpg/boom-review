# -*- coding: utf-8 -*-
# ==========================================
# SERVER ANHSTUDIO — BẢN TỐI ƯU HÓA HẠ TẦNG & BẢO MẬT CAO
# ==========================================
from fastapi import FastAPI, Request, HTTPException, Query, Depends, BackgroundTasks, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import base64 as _base64
import sqlite3, datetime, time, re, json, threading, secrets, random, os, io, asyncio, ast
import mysql.connector
from mysql.connector import pooling, Error, IntegrityError
import uvicorn
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool
import httpx, requests, concurrent.futures
from collections import defaultdict
from googleapiclient.discovery import build
from PIL import Image
from pillow_heif import register_heif_opener
register_heif_opener()

# ==========================================
# WINDOWS: CHỐNG TREO LISTENER (WinError 64) & LỌC LOG RÁC
# ==========================================
import sys as _sys, logging as _logging

# Dùng Selector loop thay Proactor để listener không bị kẹt khi client ngắt
# kết nối đột ngột. An toàn: server này KHÔNG chạy Playwright/subprocess.
if _sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# Chặn spam log vô hại do socket chết đột ngột (không ảnh hưởng lỗi thật)
class _SuppressProactorErrors(_logging.Filter):
    _NOISE = ("WinError 64", "WinError 121", "WinError 1236",
              "specified network name is no longer available",
              "Accept failed on a socket", "Task exception was never retrieved")
    def filter(self, record):
        try:
            return not any(n in record.getMessage() for n in self._NOISE)
        except Exception:
            return True

for _n in ("asyncio", "uvicorn.error", "uvicorn.asgi"):
    _logging.getLogger(_n).addFilter(_SuppressProactorErrors())

# ==========================================
# CẤU HÌNH BẢO MẬT & TRẠNG THÁI SERVER
# ==========================================
MAINTENANCE_MODE = False
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456789")
JWT_SECRET = os.environ.get("JWT_SECRET", "anhstudio_jwt_secret_key_2026_super_safe")
ADMIN_SESSION_SECRET = os.environ.get("ADMIN_SESSION_SECRET", "anhstudio_admin_session_key_2026")
WORKER_SECRET = os.environ.get("WORKER_SECRET", "anhstudio_secret_key")
API_52_KEY = os.environ.get("API_52_KEY", "Vpc23ZYZ4vTbQ5tbW2MCYLsmxL")  # KHÔNG còn dùng: đã gỡ cào lịch phim + cào phim hot 52api. Giữ để tương thích cấu hình cũ.
JWT_EXPIRY_HOURS = 24

# ==========================================
# CẤU HÌNH API ĐỐI TÁC (HONGGUO/MOMIGO)
# ==========================================
PARTNER_ID = os.environ.get("PARTNER_ID", "hoanganh")
PARTNER_KEY = os.environ.get("PARTNER_KEY", "xkUucdwPCvuDfom6gPhnJsieCEXRMye6F7WNNsUU")
HONGGUO_API_URL = "https://rpa.momigo.ai/webhook/partner"

# ==========================================
# RATE LIMITER
# ==========================================
class RateLimiter:
    def __init__(self): self._requests = defaultdict(list)
    def is_allowed(self, key: str, max_req: int, window: int) -> bool:
        now = time.time(); self._requests[key] = [t for t in self._requests[key] if t > now - window]
        if len(self._requests[key]) >= max_req: return False
        self._requests[key].append(now); return True
    def cleanup(self):
        now = time.time(); stale = [k for k, v in self._requests.items() if not v or v[-1] < now - 600]
        for k in stale: del self._requests[k]
rate_limiter = RateLimiter()

import bcrypt as _bcrypt
def hash_password(plain: str) -> str: return _bcrypt.hashpw(plain.encode('utf-8'), _bcrypt.gensalt()).decode('utf-8')
def verify_password(plain: str, stored: str) -> tuple:
    if stored.startswith("$2b$") or stored.startswith("$2a$"): return _bcrypt.checkpw(plain.encode('utf-8'), stored.encode('utf-8')), False
    else: return (plain == stored), True

import jwt as _jwt
def create_client_token(username: str, platform: str) -> str:
    return _jwt.encode({"sub": username, "platform": platform, "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS), "iat": datetime.datetime.utcnow(), "type": "client"}, JWT_SECRET, algorithm="HS256")
def create_admin_token() -> str:
    return _jwt.encode({"role": "admin", "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=12), "iat": datetime.datetime.utcnow(), "type": "admin"}, ADMIN_SESSION_SECRET, algorithm="HS256")
def decode_client_token(token: str) -> dict: return _jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
def decode_admin_token(token: str) -> dict: return _jwt.decode(token, ADMIN_SESSION_SECRET, algorithms=["HS256"])

async def require_client(request: Request) -> dict:
    global MAINTENANCE_MODE
    if MAINTENANCE_MODE: raise HTTPException(status_code=503, detail="Hệ thống đang bảo trì để nâng cấp.")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "): raise HTTPException(status_code=401, detail="Thiếu token xác thực.")
    try:
        payload = decode_client_token(auth[7:])
        if payload.get("type") != "client": raise Exception()
        return payload
    except: raise HTTPException(status_code=401, detail="Token không hợp lệ hoặc hết hạn.")

async def require_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {ADMIN_PASSWORD}" or auth == f"Admin {ADMIN_PASSWORD}": return True
    if auth.startswith("Admin "):
        try:
            if decode_admin_token(auth[6:]).get("role") == "admin": return True
        except: pass
    raise HTTPException(status_code=401, detail="Bạn cần đăng nhập Admin.")

async def require_worker(request: Request):
    if request.headers.get("Authorization") != WORKER_SECRET: raise HTTPException(status_code=401, detail="Worker không được xác thực.")
    return True

def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

GENRE_DICT = {
    "现代": "Hiện đại", "都市日常": "Đô thị", "都市": "Đô thị", "古代": "Cổ đại", "乡村": "Nông thôn", "年代": "Niên đại", "架空": "Giả tưởng", "职场": "Công sở", "民国": "Dân quốc", "校园": "Vườn trường", "宫廷": "Cung đình", "荒岛": "Đảo hoang", "古风": "Cổ phong", "爽文": "Sảng văn", "成长": "Trưởng thành", "脑洞": "Độc lạ", "奇幻": "Kỳ ảo", "玄幻": "Huyền huyễn", "古言": "Cổ ngôn", "战神": "Chiến thần", "宫斗": "Cung đấu", "宅斗": "Trạch đấu", "仙侠": "Tiên hiệp", "权谋": "Quyền mưu", "种田": "Điền văn", "爱情": "Tình yêu", "悬疑": "Hồi hộp", "喜剧": "Hài hước", "青春": "Thanh xuân", "虐恋": "Ngược luyến", "灵异": "Linh dị", "家国情怀": "Tình quốc gia", "法律": "Pháp luật", "刑侦": "Hình sự", "抗战": "Kháng chiến", "武侠": "Võ hiệp", "传奇": "Truyền kỳ", "求生": "Sinh tồn", "动作": "Hành động", "科幻": "Viễn tưởng", "恐怖": "Kinh dị", "商战": "Thương chiến", "打脸": "Vả mặt", "虐渣": "Ngược tra", "反击": "Phản công", "大男主": "Nam chủ", "大女主": "Nữ chủ", "马甲文": "Giấu nghề", "马甲": "Giấu nghề", "重生逆袭": "Trọng sinh nghịch tập", "重生": "Trọng sinh", "穿越": "Xuyên không", "系统": "Hệ thống", "先婚后爱": "Cưới trước yêu sau", "闪婚": "Cưới chớp nhoáng", "家长里短": "Gia đình", "小人物": "Nhân vật nhỏ", "破镜重圆": "Gương vỡ lại lành", "神豪": "Thần hào", "豪门": "Hào门", "黑化归来": "Hắc hóa trở về", "回归": "Trở về", "异能": "Dị năng", "传承": "Truyền thừa", "觉醒": "Giác ngộ", "医生": "Bác sĩ", "强强": "Cường cường", "替身": "Thế thân", "逆袭": "Nghịch tập", "翻身": "Lật kèo", "甜宠": "Ngọt sủng", "宠妻": "Sủng thê", "护妻": "Bảo vệ vợ", "娱乐圈": "Giới giải trí", "神医": "Thần y", "青梅竹马": "Thanh mai trúc mã", "姐弟恋": "Tình chị em", "玄学": "Huyền học", "娇妻": "Kiều thê", "傲娇": "Ngạo kiều", "精英": "Tinh anh", "一见钟情": "Nhất kiến chung tình", "日久生情": "Lâu ngày sinh tình", "萌宝": "Manh bảo", "带娃": "Mang thai", "扮猪吃虎": "Giả heo ăn hổ", "反派": "Phản diện", "黑化": "Hắc hóa", "萌宠": "Manh sủng", "双向救赎": "Song hướng cứu rỗi", "白月光": "Bạch nguyệt quang", "灵魂互换": "Hoán đổi linh hồn", "病娇": "Bệnh kiều", "暴富": "Đổi đời", "黑道": "Hắc đạo", "丧尸": "Zombie", "特种兵": "Đặc chủng binh", "婆媳": "Mẹ chồng nàng dâu", "反转": "Cú lừa", "复仇": "Báo thù", "赘婿": "Ở rể", "真相大白": "Sự thật", "男频": "Nam tần", "女频": "Nữ tần", "短剧": "Phim ngắn"
}

def extract_and_translate_genres(html):
    genres = []
    json_match = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*;?\s*</script>', html, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            tags = data.get("loaderData", {}).get("detail_page", {}).get("seriesDetail", {}).get("tags", [])
            for tag in tags:
                name = tag.get("name", "") if isinstance(tag, dict) else str(tag)
                if name: genres.append(name)
        except: pass
    if not genres:
        matches = re.findall(r'<span[^>]*class="[^"]*pc-tag-text[^"]*"[^>]*>([^<]+)</span>', html)
        if matches: genres = matches
    translated = []
    for g in genres:
        g = re.sub(r'<[^>]+>', '', g.strip()).replace('>', '')
        if g in GENRE_DICT: translated.append(GENRE_DICT[g])
        else:
            for cn_key, vn_val in GENRE_DICT.items():
                if cn_key in g:
                    translated.append(vn_val)
                    break
    return ", ".join(list(dict.fromkeys(translated)))

APP_VERSION = "1.0.31"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "database_users.db")
DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive']
DRIVE_PARENT_FOLDER_ID = '1QVP3Mh86LGLsEIQSyojZ6DBkjGUdWv3c'
_drive_checked = False
MAX_HOT_MOVIES = 30
WORKER_HEARTBEATS = {}
WATCHDOG_COMMANDS = {}
WORKER_TIMEOUT = 120
CLIENT_HEARTBEATS = {}
CLIENT_TIMEOUT = 60
UPDATE_INFO_FILE = os.path.join(BASE_DIR, "update_info.json")

# R2 CONFIG
R2_ACCOUNT_ID        = os.environ.get("R2_ACCOUNT_ID", "988d56c97ff33a11d0b7e3bbebd56cbb")
R2_ACCESS_KEY_ID     = os.environ.get("R2_ACCESS_KEY_ID", "4a368f6ee495cc06f41fc8562d006292")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "9d3cbedb97793c3a07db8b1891d4aa443c64b2003610404dee2b200b472ddf4b")
R2_BUCKET            = os.environ.get("R2_BUCKET", "hongguo-phim")
R2_LINK_EXPIRE_SECONDS = 3 * 3600  

def r2_enabled(): return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET)
_r2_client = None
def get_r2_client():
    global _r2_client
    if _r2_client is None:
        import boto3
        from botocore.config import Config as _BotoConfig
        _r2_client = boto3.client("s3", endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com", aws_access_key_id=R2_ACCESS_KEY_ID, aws_secret_access_key=R2_SECRET_ACCESS_KEY, config=_BotoConfig(signature_version="s3v4", region_name="auto"))
    return _r2_client

def r2_presign_get(key: str, expires: int = None) -> str: return get_r2_client().generate_presigned_url("get_object", Params={"Bucket": R2_BUCKET, "Key": key}, ExpiresIn=expires or R2_LINK_EXPIRE_SECONDS)
def r2_presign_put(key: str, expires: int = 3600) -> str: return get_r2_client().generate_presigned_url("put_object", Params={"Bucket": R2_BUCKET, "Key": key, "ContentType": "video/mp4"}, ExpiresIn=expires)

def _sign_episodes(episodes, series_id):
    out = []
    for ep in episodes or []:
        ep = dict(ep); link = ep.get("drive_link") or ""
        if link.startswith("r2://"):
            try: ep["drive_link"] = r2_presign_get(link[5:])
            except: ep["drive_link"] = ""
        out.append(ep)
    return out

def verify_r2_episodes(series_id):
    real_eps = set()
    try:
        paginator = get_r2_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=f"{series_id}/"):
            for obj in page.get("Contents", []):
                ep = parse_episode_from_name(obj["Key"].rsplit("/", 1)[-1])
                if ep > 0: real_eps.add(ep)
    except: pass
    return real_eps

def verify_drive_episodes(series_id):
    real_eps = set()
    try:
        service = get_drive_service()
        if not service: return real_eps
        result = service.files().list(q=f"'{DRIVE_PARENT_FOLDER_ID}' in parents and name = '{series_id}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false", fields='files(id)').execute()
        folders = result.get('files', [])
        if not folders: return real_eps
        files = service.files().list(q=f"'{folders[0]['id']}' in parents and trashed = false and mimeType = 'video/mp4'", fields='files(name)', pageSize=500).execute().get('files', [])
        for f in files:
            ep = parse_episode_from_name(f['name'])
            if ep > 0: real_eps.add(ep)
    except: pass
    return real_eps

def verify_storage_episodes(series_id): return verify_r2_episodes(series_id) if r2_enabled() else verify_drive_episodes(series_id)

def _load_update_info():
    try:
        if os.path.exists(UPDATE_INFO_FILE):
            with open(UPDATE_INFO_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: pass
    return {"latest_version": APP_VERSION, "download_url": "", "changelog": "", "force_update": False}
def _save_update_info(info):
    try:
        with open(UPDATE_INFO_FILE, "w", encoding="utf-8") as f: json.dump(info, f, ensure_ascii=False, indent=2)
    except: pass

def get_drive_service():
    global _drive_checked
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    token_path = os.path.join(BASE_DIR, 'token.json')
    creds = Credentials.from_authorized_user_file(token_path, DRIVE_SCOPES) if os.path.exists(token_path) else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f: f.write(creds.to_json())
    if not creds or not creds.valid:
        _drive_checked = True; return None
    return build('drive', 'v3', credentials=creds)

def get_real_web_total(html):
    try:
        json_match = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*;?\s*</script>', html, re.DOTALL)
        if json_match:
            detail = json.loads(json_match.group(1)).get("loaderData", {}).get("detail_page", {}).get("seriesDetail", {})
            rt = detail.get("episode_right_text", "")
            if rt and re.search(r'(\d+)', rt): return int(re.search(r'(\d+)', rt).group(1))
            vl = detail.get("vid_list", [])
            if vl: return len(vl)
        ep_match = re.search(r'"episode_cnt"\s*:\s*(\d+)', html)
        if ep_match: return int(ep_match.group(1))
    except: pass
    return 0

def _extract_balanced_json(html, marker):
    idx = html.find(marker)
    if idx == -1: return None
    start = html.find('{', idx)
    if start == -1: return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
        return obj
    except Exception:
        return None

_HG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7,zh-CN;q=0.6",
    "Referer": "https://hongguoduanju.com/",
    "Accept-Encoding": "gzip, deflate",
}
_hg_session = None
def _get_hg_session():
    global _hg_session
    if _hg_session is None:
        _hg_session = requests.Session()
        _hg_session.headers.update(_HG_HEADERS)
    return _hg_session

def fetch_series_metadata(series_id, original_url=None):
    res = {"title": "", "cover_url": "", "total_episodes": 0, "genres": ""}
    url = original_url or f"https://hongguoduanju.com/detail?series_id={series_id}"
    html = ""
    sess = _get_hg_session()
    for attempt in range(3):
        try:
            r = sess.get(url, timeout=20, allow_redirects=True)
            if r.status_code == 200 and r.text:
                html = r.text
                break
        except Exception:
            pass
        time.sleep(0.8 * (attempt + 1))
    if not html: return res
    try:
        data = _extract_balanced_json(html, "window._ROUTER_DATA")
        if data:
            detail = ((data.get("loaderData", {}) or {}).get("detail_page", {}) or {}).get("seriesDetail", {}) or {}
            res["title"] = detail.get("series_name", "") or ""
            res["cover_url"] = detail.get("series_cover", "") or detail.get("cover_url", "") or ""

        if not res["cover_url"]:
            cm = re.search(r'<img[^>]*class="[^"]*arco-image-img[^"]*"[^>]*src="([^"]+)"', html)
            if cm: res["cover_url"] = cm.group(1)

        if not res["cover_url"]:
            om = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not om: om = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.IGNORECASE)
            if om: res["cover_url"] = om.group(1)

        if not res["cover_url"]:
            km = re.search(r'"series_cover"\s*:\s*"([^"]+)"', html) or re.search(r'"cover_url"\s*:\s*"([^"]+)"', html) or re.search(r'"cover"\s*:\s*"([^"]+)"', html)
            if km: res["cover_url"] = km.group(1).replace('\\/', '/').replace('\\u002F', '/')

        if res["cover_url"]:
            res["cover_url"] = res["cover_url"].replace('\\/', '/').replace('\\u002F', '/')

        if not res["title"]:
            tm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            if tm: res["title"] = tm.group(1).strip()
            if not res["title"]:
                tm2 = re.search(r'<title>([^<]+)</title>', html)
                if tm2: res["title"] = tm2.group(1).split('|')[0].strip()

        res["total_episodes"] = get_real_web_total(html)
        res["genres"] = extract_and_translate_genres(html)
    except: pass
    return res

async def _fetch_cover_b64(client, sid, cover_url, original_url=None, db_b64=None):
    if db_b64: return (db_b64, False)
    sid = str(sid or '')
    cp = os.path.join(COVERS_DIR, f"{sid}.jpg") if sid else None
    if cp and os.path.exists(cp):
        try:
            with open(cp, 'rb') as f: return (_base64.b64encode(f.read()).decode('ascii'), True)
        except: pass
    async def _tai(u):
        if not u: return None
        if u.startswith('//'): u = 'https:' + u
        for _ in range(3):
            try:
                # FIX 1: User-Agent chống chặn
                r = await client.get(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Referer": "https://hongguoduanju.com/"})
                if r.status_code == 200 and r.content:
                    if b'ftyp' in r.content[:32] or b'heic' in r.headers.get('Content-Type', '').lower():
                        try:
                            img = Image.open(io.BytesIO(r.content)).convert('RGB')
                            out = io.BytesIO(); img.save(out, format='JPEG', quality=90); return out.getvalue()
                        except: pass
                    return r.content
            except: pass
        return None
    content = await _tai(cover_url)
    if not content and sid:
        try:
            meta = await asyncio.get_event_loop().run_in_executor(None, fetch_series_metadata, sid, original_url)
            if meta.get('cover_url') and meta['cover_url'] != cover_url: content = await _tai(meta['cover_url'])
        except: pass
    if content:
        if cp:
            try:
                with open(cp, 'wb') as f: f.write(content)
            except: pass
        return (_base64.b64encode(content).decode('ascii'), True)
    return (None, False)

def parse_episode_from_name(name):
    m = re.search(r'(?:Tập|Tap|Episode|EP)\s*[_\-\.]?\s*(\d+)', name.rsplit(".", 1)[0] if "." in name else name, re.IGNORECASE)
    return int(m.group(1)) if m else 0

_meta_targets = set()
_meta_lock = threading.Lock()
def _queue_meta_target(job_id, series_id, original_url):
    with _meta_lock: _meta_targets.add((job_id, str(series_id), original_url or ''))

def _do_flush_meta_targets(targets):
    """Chay trong thread rieng - KHONG block loop hay threadpool caller."""
    for job_id, series_id, original_url in targets:
        try:
            meta = fetch_series_metadata(series_id, original_url or None)
            mconn = get_mysql_connection()
            if not mconn: continue
            mcur = mconn.cursor(dictionary=True)
            mcur.execute("SELECT total_episodes, cover_url, title, genres FROM jobs WHERE job_id = %s", (job_id,))
            row = mcur.fetchone() or {}
            nt = meta['total_episodes'] if meta['total_episodes'] > 0 else (row.get('total_episodes') or 0)
            nc = meta['cover_url'] or row.get('cover_url') or ''
            nti = meta['title'] or row.get('title') or ''
            og = str(row.get('genres') or "")
            fg = meta['genres'] or ""
            keep_tags = [t for t in ["BXH De Cu", "BXH Luot Xem", "BXH Phim Moi", "BXH Hoat Hinh", "Lich Phim"] if t in og]
            if keep_tags:
                for t in keep_tags: fg = fg.replace(t, "").replace(", ,", ",").strip(", ")
                fg = ", ".join(keep_tags) + ((", " + fg) if fg else "")
            mcur.execute("UPDATE jobs SET total_episodes = %s, cover_url = %s, title = %s, genres = %s, updated_at = NOW() WHERE job_id = %s", (nt, nc, nti, fg or og, job_id))
            mconn.commit(); mcur.close(); mconn.close()
        except: pass

def _flush_meta_targets():
    """Lay targets ra roi day vao daemon thread - tra ve ngay, khong cho."""
    global _meta_targets
    with _meta_lock:
        targets = list(_meta_targets); _meta_targets = set()
    if not targets: return
    t = threading.Thread(target=_do_flush_meta_targets, args=(targets,), daemon=True)
    t.start()

_hot_movies_cache = {}
_cache_lock = asyncio.Lock()
COVERS_DIR = os.path.join(BASE_DIR, "covers")
os.makedirs(COVERS_DIR, exist_ok=True)
_hot_refresh_event = asyncio.Event()
_main_loop = None  

_placeholder_cover_b64_cache = None
def get_placeholder_cover_b64():
    global _placeholder_cover_b64_cache
    if _placeholder_cover_b64_cache: return _placeholder_cover_b64_cache
    try:
        img = Image.new('RGB', (300, 420), color=(40, 44, 58))
        try:
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            draw.rectangle([20, 20, 280, 400], outline=(90, 96, 120), width=3)
            draw.line([90, 180, 210, 240], fill=(90, 96, 120), width=4)
            draw.line([210, 180, 90, 240], fill=(90, 96, 120), width=4)
        except Exception: pass
        out = io.BytesIO(); img.save(out, format='JPEG', quality=85)
        _placeholder_cover_b64_cache = _base64.b64encode(out.getvalue()).decode('ascii')
    except Exception:
        _placeholder_cover_b64_cache = ""
    return _placeholder_cover_b64_cache

def trigger_hot_refresh():
    global _main_loop
    try:
        if _main_loop and _main_loop.is_running(): _main_loop.call_soon_threadsafe(_hot_refresh_event.set)
        else: _hot_refresh_event.set()
    except: pass

async def _build_hot_movies_cache():
    while True:
        try:
            # --- DB fetch (off loop) ---
            def _fetch_from_db():
                c = get_mysql_connection()
                if not c: return [], []
                cur = c.cursor(dictionary=True)
                cur.execute("SELECT series_id, original_url, title, cover_url, cover_b64, total_episodes, genres FROM jobs WHERE status = 'completed' AND total_episodes > 0 AND title IS NOT NULL AND title != '' AND cover_url IS NOT NULL AND cover_url != '' ORDER BY updated_at DESC LIMIT 30")
                all_jobs = cur.fetchall()
                cur.execute("SELECT series_id, original_url, title, cover_url, cover_b64, total_episodes, genres, air_time, status FROM jobs WHERE genres LIKE %s AND title IS NOT NULL AND title != '' ORDER BY COALESCE(air_time, updated_at) DESC LIMIT 30", ("%Lịch Phim%",))
                sched_jobs = cur.fetchall()
                cur.close(); c.close()
                return all_jobs, sched_jobs

            all_jobs, sched_jobs = await asyncio.to_thread(_fetch_from_db)
            if all_jobs is not None:
                seen = set(); clean = []
                for job in all_jobs:
                    t = (job.get('title') or '').strip()
                    if t and t in seen: continue
                    if t: seen.add(t)
                    clean.append(job)

                have_sids = {str(j.get('series_id')) for j in clean}
                sched_extra = [j for j in sched_jobs if str(j.get('series_id')) not in have_sids]
                fetch_list = clean + sched_extra

                sem = asyncio.Semaphore(4)
                async with httpx.AsyncClient(timeout=15.0) as client:
                    async def _fetch_cover(job):
                        async with sem:
                            return await _fetch_cover_b64(client, job.get('series_id'), job.get('cover_url', ''), job.get('original_url'), job.get('cover_b64'))

                    results = await asyncio.gather(*[_fetch_cover(j) for j in fetch_list])
                    covers = [r[0] for r in results]
                    cover_by_sid = {str(fetch_list[i].get('series_id')): covers[i] for i in range(len(fetch_list)) if covers[i]}
                    to_persist = [(covers[i], str(fetch_list[i].get('series_id'))) for i in range(len(fetch_list)) if results[i][1] and covers[i]]

                # --- DB persist covers (off loop) ---
                if to_persist:
                    def _persist(data):
                        try:
                            pconn = get_mysql_connection()
                            if pconn:
                                pcur = pconn.cursor()
                                pcur.executemany("UPDATE jobs SET cover_b64 = %s WHERE series_id = %s", data)
                                pconn.commit(); pcur.close(); pconn.close()
                        except: pass
                    await asyncio.to_thread(_persist, to_persist)

                entries = []
                for j in clean:
                    c = cover_by_sid.get(str(j.get('series_id')))
                    if not c: continue
                    entries.append({"url": j.get('original_url') or f"https://hongguoduanju.com/detail?series_id={j['series_id']}", "series_id": j['series_id'], "title": j.get('title'), "cover_url": j.get('cover_url'), "total_episodes": j['total_episodes'], "genres": j.get('genres', ''), "cover_base64": c})

                sched_entries = []
                for j in sched_jobs:
                    c = j.get('cover_b64') or cover_by_sid.get(str(j.get('series_id'))) or get_placeholder_cover_b64()
                    raw_t = j.get('title') or ''
                    at = j.get('air_time')
                    disp_title = f"[{at.strftime('%d/%m - %H:%M')}] {raw_t}" if at else raw_t
                    sched_entries.append({"url": j.get('original_url') or f"https://hongguoduanju.com/detail?series_id={j['series_id']}", "series_id": j['series_id'], "title": disp_title, "cover_url": j.get('cover_url'), "total_episodes": j['total_episodes'], "genres": j.get('genres', ''), "cover_base64": c, "status": j.get('status')})

                async with _cache_lock:
                    _hot_movies_cache["__all__"] = entries
                    genre_map = {}
                    for e in entries:
                        for g in (e.get('genres', '')).split(','):
                            g = g.strip()
                            if g and g != "Lịch Phim": genre_map.setdefault(g, []).append(e)
                    for g, items in genre_map.items(): _hot_movies_cache[g] = items
                    _hot_movies_cache["Lịch Phim"] = sched_entries
        except: pass
        try: await asyncio.wait_for(_hot_refresh_event.wait(), timeout=900)
        except: pass
        finally: _hot_refresh_event.clear()
        await asyncio.sleep(2)

async def cleanup_zombie_tasks():
    while True:
        def _do_cleanup():
            try:
                conn = get_mysql_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE jobs SET status = 'pending', worker_id = NULL WHERE status = 'processing' AND updated_at < (NOW() - INTERVAL 2 MINUTE)")
                    cursor.execute("UPDATE job_episodes SET worker_id = NULL WHERE worker_id IS NOT NULL AND raw_video_url IS NOT NULL AND updated_at < (NOW() - INTERVAL 5 MINUTE)")
                    cursor.execute("UPDATE jobs SET worker_id = NULL WHERE worker_id IS NOT NULL AND status NOT IN ('processing', 'completed') AND updated_at < (NOW() - INTERVAL 5 MINUTE)")
                    conn.commit(); cursor.close(); conn.close()
            except: pass
        await asyncio.to_thread(_do_cleanup)
        rate_limiter.cleanup()
        stale = [k for k, v in list(CLIENT_HEARTBEATS.items()) if time.time() - v["time"] > 300]
        for k in stale: del CLIENT_HEARTBEATS[k]
        await asyncio.sleep(60)

_cover_scan_on = False          
_cover_scan_status = {"running": False, "done": 0, "fail": 0, "last": ""}
async def _cover_scan_worker():
    while True:
        if not _cover_scan_on: _cover_scan_status["running"] = False; await asyncio.sleep(3); continue
        _cover_scan_status["running"] = True
        try:
            def _fetch_rows():
                c = get_mysql_connection()
                if not c: return []
                try:
                    cur = c.cursor(dictionary=True)
                    cur.execute("SELECT series_id, original_url, cover_url FROM jobs WHERE (cover_b64 IS NULL OR cover_b64 = '') AND series_id IS NOT NULL AND series_id <> '' ORDER BY updated_at DESC LIMIT 5")
                    rows = cur.fetchall(); cur.close()
                    return rows
                finally:
                    try: c.close()
                    except: pass
            rows = await asyncio.to_thread(_fetch_rows)
            if not rows: _cover_scan_status["last"] = "Da quet het."; _cover_scan_on_off(False); await asyncio.sleep(3); continue

            async with httpx.AsyncClient(timeout=15.0) as client:
                for job in rows:
                    if not _cover_scan_on: break
                    sid = str(job.get('series_id') or ''); content = None
                    async def _tai(u):
                        if not u: return None
                        if u.startswith('//'): u = 'https:' + u
                        for _ in range(3):
                            try:
                                r = await client.get(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Referer": "https://hongguoduanju.com/"})
                                if r.status_code == 200 and r.content:
                                    if b'ftyp' in r.content[:32] or b'heic' in r.headers.get('Content-Type', '').lower():
                                        try: img = Image.open(io.BytesIO(r.content)).convert('RGB'); out = io.BytesIO(); img.save(out, format='JPEG', quality=90); return out.getvalue()
                                        except: pass
                                    return r.content
                            except: pass
                        return None
                    content = await _tai(job.get('cover_url'))
                    if not content:
                        try:
                            meta = await asyncio.to_thread(fetch_series_metadata, sid, job.get('original_url'))
                            if meta.get('cover_url'):
                                content = await _tai(meta['cover_url'])
                                def _update_cover_url(cover_url, series_id):
                                    c2 = None
                                    try:
                                        c2 = get_mysql_connection()
                                        if c2: cc = c2.cursor(); cc.execute("UPDATE jobs SET cover_url = %s WHERE series_id = %s", (cover_url, series_id)); c2.commit(); cc.close()
                                    except: pass
                                    finally:
                                        try:
                                            if c2: c2.close()
                                        except: pass
                                await asyncio.to_thread(_update_cover_url, meta['cover_url'], sid)
                        except: pass
                    if content:
                        try:
                            with open(os.path.join(COVERS_DIR, f"{sid}.jpg"), 'wb') as f: f.write(content)
                        except: pass
                        def _save_cover_b64(b64_str, series_id):
                            c3 = None
                            try:
                                c3 = get_mysql_connection()
                                if c3: cc = c3.cursor(); cc.execute("UPDATE jobs SET cover_b64 = %s WHERE series_id = %s", (b64_str, series_id)); c3.commit(); cc.close()
                            except: pass
                            finally:
                                try:
                                    if c3: c3.close()
                                except: pass
                        try:
                            await asyncio.to_thread(_save_cover_b64, _base64.b64encode(content).decode('ascii'), sid)
                            _cover_scan_status["done"] += 1
                        except: _cover_scan_status["fail"] += 1
                    else: _cover_scan_status["fail"] += 1
                    _cover_scan_status["last"] = f"OK {_cover_scan_status['done']} | Loi {_cover_scan_status['fail']}"
                    await asyncio.sleep(1)
        except Exception as e: _cover_scan_status["last"] = f"Loi: {e}"
        await asyncio.sleep(2)
def _cover_scan_on_off(state): global _cover_scan_on; _cover_scan_on = state

# ĐÃ GỠ BỎ: _auto_fetch_schedule_worker() + _write_schedule_rows()
# Trước đây 2 hàm này định kỳ (mỗi 2 giờ) gọi API 52api (hg_new_play) để cào
# "Lịch Phim" rồi ghi vào bảng jobs với genres = "Lịch Phim". Không còn dùng
# nữa nên đã xóa hoàn toàn cùng với task nền khởi tạo trong lifespan.

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop; _main_loop = asyncio.get_running_loop()
    # === TANG THREADPOOL ===
    # FastAPI chay cac ham `def` (dong bo, blocking) trong threadpool cua AnyIO,
    # MAC DINH chi 40 luong. Rat nhieu client heartbeat + worker get_job/cover_jobs
    # goi cung luc -> 40 luong day -> request xep hang -> giu connection lau ->
    # can pool -> 503. Nang len 120 de xu ly nhieu request blocking song song hon.
    # Con so nay nen <= tong pool connection (hien 128) de moi luong deu co connection.
    try:
        import anyio.to_thread as _a2t
        _limiter = _a2t.current_default_thread_limiter()
        _limiter.total_tokens = 120
        print(f"[BOOT] Threadpool AnyIO nang len {_limiter.total_tokens} luong.", flush=True)
    except Exception as _tp_err:
        print(f"[BOOT][WARN] Khong nang duoc threadpool: {_tp_err}", flush=True)
    task2 = asyncio.create_task(cleanup_zombie_tasks())
    task3 = asyncio.create_task(_build_hot_movies_cache())
    # ĐÃ TẮT _cover_scan_worker: việc cào ảnh bìa giờ do bot NGOÀI (cover_bot.py)
    # lo hoàn toàn - nó tự xin bộ thiếu bìa qua /api/worker/cover_jobs và nộp về
    # /api/worker/submit_cover. Chạy task này trong server nữa là TRÙNG việc và
    # tốn connection MySQL (mỗi lần lưu ảnh mở connection mới). Code hàm
    # _cover_scan_worker vẫn giữ nguyên bên trên phòng khi cần bật lại sau.
    # ĐÃ GỠ: task5 = _auto_fetch_schedule_worker() (cào Lịch Phim 52api) - không dùng nữa.
    print("[BOOT] Lifespan xong - background task da chay nen. Server SAN SANG nhan request.", flush=True)
    yield
    task2.cancel(); task3.cancel()

app = FastAPI(title="AnhStudio SaaS Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ==========================================
# MIDDLEWARE LOG REQUEST CHẬM - tự động chỉ ra endpoint gây treo.
# Bất kỳ request nào chạy > SLOW_REQ_SEC giây sẽ bị ghi vào file slow_requests.log
# kèm đường dẫn + thời gian. Lần sau server treo, mở file này ra là thấy NGAY
# request nào đang treo lâu bất thường -> biết endpoint thủ phạm, không cần canh.
# ==========================================
import time as _t_slow
SLOW_REQ_SEC = float(os.environ.get("SLOW_REQ_SEC", "5.0"))  # ngưỡng coi là chậm
_SLOW_LOG = _logging.getLogger("slow_requests")
try:
    _slow_h = _logging.FileHandler("slow_requests.log", encoding="utf-8")
    _slow_h.setFormatter(_logging.Formatter("%(asctime)s %(message)s"))
    _SLOW_LOG.addHandler(_slow_h); _SLOW_LOG.setLevel(_logging.INFO)
except Exception:
    pass

@app.middleware("http")
async def _log_slow_requests(request: Request, call_next):
    _start = _t_slow.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        dur = _t_slow.perf_counter() - _start
        _SLOW_LOG.info(f"[EXC {dur:.1f}s] {request.method} {request.url.path}")
        raise
    dur = _t_slow.perf_counter() - _start
    if dur >= SLOW_REQ_SEC:
        _SLOW_LOG.info(f"[SLOW {dur:.1f}s] {request.method} {request.url.path}")
    return response

def get_db():
    conn = sqlite3.connect(SQLITE_PATH, timeout=15); conn.row_factory = sqlite3.Row
    try: conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA busy_timeout=15000"); conn.execute("PRAGMA synchronous=NORMAL")
    except: pass
    return conn

def init_db():
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, zalo TEXT, hwid TEXT, expiry_date TIMESTAMP, balance_hongguo INTEGER DEFAULT 0, platform TEXT DEFAULT 'honggou', vip_unlocked INTEGER DEFAULT 0, created_at TIMESTAMP, vip_unlocked_at TIMESTAMP)")
    for c, ct, cd in [("balance_hongguo", "INTEGER", "0"), ("platform", "TEXT", "'honggou'"), ("vip_unlocked", "INTEGER", "0"), ("created_at", "TIMESTAMP", "NULL"), ("vip_unlocked_at", "TIMESTAMP", "NULL")]:
        try: cursor.execute(f"ALTER TABLE users ADD COLUMN {c} {ct} DEFAULT {cd}")
        except: pass
    # Tài khoản BOOM Story tách hoàn toàn khỏi bảng users đang hoạt động.
    cursor.execute("""CREATE TABLE IF NOT EXISTS boom_users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        note TEXT DEFAULT '',
        hwid TEXT DEFAULT '',
        expiry_date TIMESTAMP,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP,
        last_login TIMESTAMP
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_boom_users_expiry ON boom_users(expiry_date)")
    # Tài khoản BOOM Review tách hoàn toàn khỏi boom_users của BOOM Story.
    cursor.execute("""CREATE TABLE IF NOT EXISTS boom_review_users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        note TEXT DEFAULT '',
        expiry_date TIMESTAMP,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP,
        last_login TIMESTAMP
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_boom_review_users_expiry ON boom_review_users(expiry_date)")
    conn.commit(); conn.close()
init_db()

def _get_vip_unlocked(username: str, platform: str = "honggou") -> bool:
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT vip_unlocked FROM users WHERE username = ? AND platform = ?", (username, platform))
        row = cursor.fetchone(); conn.close()
        if not row: return False
        return bool(row["vip_unlocked"])
    except Exception:
        return False

DB_HOST, DB_USER, DB_PASSWORD, DB_NAME = "localhost", "root", os.environ.get("MYSQL_PASSWORD", "AnhStudio123!"), "phim_database"
mysql_pool = None          # giu lai de code cu tham chieu "if not mysql_pool" van chay
mysql_pools = []           # DANH SACH nhieu pool (moi pool toi da 32) - gop lai de vuot gioi han 32
_pool_rr = 0               # con tro round-robin: xoay vong lay connection tu cac pool
_pool_rr_lock = threading.Lock()

# So pool va kich thuoc moi pool. mysql.connector CHAN CUNG moi pool <= 32,
# nen muon 64 connection thi tao 2 pool x 32. Muon 96 thi doi NUM_POOLS=3, v.v.
NUM_POOLS = 4              # 4 x 32 = 128 connection (truoc la 2 x 32 = 64, hay bi 503 vi can pool)
POOL_SIZE_EACH = 32        # TOI DA 32 - dung tang qua, mysql.connector se loi.

def init_mysql_db():
    global mysql_pool, mysql_pools
    try:
        temp_conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, use_pure=True, auth_plugin='mysql_native_password')
        cursor = temp_conn.cursor(); cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}"); cursor.close(); temp_conn.close()

        # Tao NUM_POOLS pool, moi pool POOL_SIZE_EACH connection.
        mysql_pools = []
        for i in range(NUM_POOLS):
            p = pooling.MySQLConnectionPool(
                pool_name=f"anhstudio_pool_{i}", pool_size=POOL_SIZE_EACH,
                pool_reset_session=True, host=DB_HOST, user=DB_USER,
                password=DB_PASSWORD, database=DB_NAME, use_pure=True,
                auth_plugin='mysql_native_password',
                # connection_timeout: neu 1 query bi treo qua 30s -> connection tu
                # bao loi & duoc thu hoi, KHONG kẹt trong pool vinh vien. Ket hop
                # voi index (query nhanh) -> gan nhu het ro ri connection.
                connection_timeout=30)
            mysql_pools.append(p)
        mysql_pool = mysql_pools[0]  # de "if not mysql_pool" o cho khac van dung

        conn = get_mysql_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS jobs (job_id VARCHAR(100) PRIMARY KEY, series_id VARCHAR(100) UNIQUE, total_episodes INT DEFAULT 0, status VARCHAR(50) DEFAULT 'pending', original_url TEXT NULL, worker_id VARCHAR(50) NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS job_episodes (id INT AUTO_INCREMENT PRIMARY KEY, job_id VARCHAR(100), episode_number INT NOT NULL, drive_link TEXT NOT NULL, FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE)")
            cursor.execute("CREATE TABLE IF NOT EXISTS worker_blacklist (worker_id VARCHAR(50) PRIMARY KEY, blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            for q in ["ALTER TABLE job_episodes ADD COLUMN file_name TEXT", "ALTER TABLE job_episodes ADD COLUMN file_size BIGINT DEFAULT 0", "ALTER TABLE job_episodes ADD COLUMN raw_video_url TEXT NULL", "ALTER TABLE job_episodes ADD COLUMN worker_id VARCHAR(50) NULL", "ALTER TABLE job_episodes ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP", "ALTER TABLE jobs ADD COLUMN original_url TEXT", "ALTER TABLE jobs ADD COLUMN title TEXT NULL", "ALTER TABLE jobs ADD COLUMN cover_url TEXT NULL", "ALTER TABLE jobs ADD COLUMN worker_id VARCHAR(50) NULL", "ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP", "ALTER TABLE jobs ADD COLUMN genres TEXT NULL", "ALTER TABLE jobs ADD COLUMN cover_b64 LONGTEXT NULL", "ALTER TABLE jobs ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP", "ALTER TABLE jobs ADD COLUMN completed_at TIMESTAMP NULL", "ALTER TABLE jobs ADD COLUMN air_time DATETIME NULL"]:
                try: cursor.execute(q)
                except: pass
            # ===== THEM INDEX: tang toc query, KHONG quet toan bang 5260 dong =====
            # Truoc day cac query loc theo worker_id / cover_b64 / status / updated_at
            # phai QUET TOAN BANG (5s/cau) -> giu connection lau -> can pool. Them
            # index -> query con vai ms -> connection tra ve pool ngay -> het can pool.
            # Chay 1 lan luc khoi dong; lan sau da co index thi bao loi -> bo qua.
            for idx_q in [
                "CREATE INDEX idx_jobs_worker ON jobs (worker_id)",
                "CREATE INDEX idx_jobs_status ON jobs (status)",
                "CREATE INDEX idx_jobs_updated ON jobs (updated_at)",
                "CREATE INDEX idx_jobs_coverb64 ON jobs (cover_b64(1))",
                "CREATE INDEX idx_ep_job ON job_episodes (job_id)",
                "CREATE INDEX idx_ep_worker ON job_episodes (worker_id)",
                "CREATE INDEX idx_ep_dl ON job_episodes (drive_link(1))",
                # Composite index cho query cap job (get_job): loc theo tap tho da co
                # raw_video_url, chua co drive_link, chua co worker -> cap cho worker.
                # Giup query chi cham vao dung hang can, tra connection ve pool nhanh.
                "CREATE INDEX idx_ep_dispatch ON job_episodes (job_id, worker_id, drive_link(1), episode_number)",
            ]:
                try: cursor.execute(idx_q); print(f"[MYSQL] Tao index: {idx_q.split('ON')[1].strip()}", flush=True)
                except: pass  # index da ton tai -> bo qua
            cursor.execute("UPDATE jobs SET completed_at = updated_at WHERE status = 'completed' AND completed_at IS NULL")
            conn.commit(); cursor.close(); conn.close()
        print(f"[MYSQL] Pool tao THANH CONG: {NUM_POOLS} pool x {POOL_SIZE_EACH} = {NUM_POOLS * POOL_SIZE_EACH} connection.", flush=True)
    except Exception as _mysql_err:
        import traceback as _tb_mysql
        print("=" * 60, flush=True)
        print(f"[MYSQL][LOI] KHONG KET NOI DUOC MYSQL -> pool = None -> MOI request MySQL se bi 503!", flush=True)
        print(f"[MYSQL][LOI] Chi tiet: {_mysql_err}", flush=True)
        print(f"[MYSQL][LOI] Kiem tra: (1) MySQL service da chay chua? (2) Mat khau '{DB_PASSWORD}' dung chua? (3) user '{DB_USER}' @ '{DB_HOST}' co quyen khong?", flush=True)
        _tb_mysql.print_exc()
        print("=" * 60, flush=True)

def get_mysql_connection():
    """Lay 1 connection tu CAC pool (round-robin). Tong suc chua =
    NUM_POOLS * POOL_SIZE_EACH (hien tai 2 x 32 = 64).

    Cach hoat dong:
      - Xoay vong (round-robin) chon pool bat dau, de tai trai deu 2 pool.
      - Neu pool dang chon HET connection -> tu dong THU pool con lai (tran sang).
        Nho vay chi khi CA 64 connection deu ban moi bao 503.
      - Retry ngan vai lan phong truong hop qua tai tuc thoi.

    Connection tra ve la mysql.connector connection binh thuong -> moi cho goi
    .cursor()/.commit()/.close() nhu cu, KHONG phai sua gi."""
    global _pool_rr
    if not mysql_pools:
        raise HTTPException(status_code=503, detail="Database chua san sang, vui long thu lai sau.")
    import time as _time
    last_err = None
    n = len(mysql_pools)
    for attempt in range(3):
        # chon diem bat dau xoay vong
        with _pool_rr_lock:
            start = _pool_rr % n
            _pool_rr = (_pool_rr + 1) % n
        # thu lan luot tat ca pool ke tu 'start' (tran sang pool khac neu 1 pool cạn)
        for k in range(n):
            pool = mysql_pools[(start + k) % n]
            try:
                return pool.get_connection()
            except Exception as e:
                last_err = e
                continue
        _time.sleep(0.05 * (attempt + 1))  # 0.05s, 0.10s, 0.15s - ngan, tranh chan
    raise HTTPException(
        status_code=503,
        detail=f"Server dang qua tai (het connection database), vui long thu lai sau vai giay. ({last_err})"
    )

import time as _t_boot
_boot_t0 = _t_boot.perf_counter()
print("[BOOT] Bat dau init_mysql_db()...", flush=True)
init_mysql_db()
print(f"[BOOT] init_mysql_db() xong sau {_t_boot.perf_counter()-_boot_t0:.1f}s", flush=True)

class RegisterReq(BaseModel): username: str; password: str; zalo: str = ""; platform: str = "honggou"
class LoginReq(BaseModel): username: str; password: str; hwid: str = ""; platform: str = "honggou"
class AddJobReq(BaseModel): url: str; series_id: str; expected_total: int = 0; title: str = ""; cover_url: str = ""
class PayDownloadReq(BaseModel): username: str; num_episodes: int; series_id: str = ""; episodes: list = []; job_id: str = ""
class ScanUpdate(BaseModel): job_id: str; total_episodes: int; action: str = ""; title: str = ""; cover_url: str = ""; cover_b64: str = ""
class EpisodeUpdate(BaseModel): job_id: str; episode_number: int; drive_link: str; file_name: str; series_id: str = None
class PiggybackFailReq(BaseModel): job_id: str; episode_number: int
class CompleteJobReq(BaseModel): job_id: str; total_uploaded: int = 0; expected_total: int = 0
class VerifyTotalReq(BaseModel): job_id: str; series_id: str; current_count: int
class TopupReq(BaseModel): amount: int
class SubmitCoverReq(BaseModel): series_id: str; cover_b64: str; cover_url: str = ""
class AddVipReq(BaseModel): days: int
class ManualSeriesReq(BaseModel): series_id: str; title: str = ""; cover_url: str = ""; total_episodes: int = 0
class AdminCreateUserReq(BaseModel): username: str; password: str = "123456"; zalo: str = ""; days: int = 30; balance: int = 0
class AdminLoginReq(BaseModel): password: str
class BoomCreateAccountReq(BaseModel): username: str; password: str = "123456"; days: int = 30; note: str = ""
class BoomChangePasswordReq(BaseModel): password: str = "123456"
class BoomAddDaysReq(BaseModel): days: int = 30
class PublishUpdateReq(BaseModel): latest_version: str; download_url: str; changelog: str = ""; force_update: bool = False
class ClientHeartbeatReq(BaseModel): current_job_id: str = ""; series_id: str = ""; action: str = ""
class MaintenanceReq(BaseModel): enabled: bool
class RetryDeadLinkReq(BaseModel): username: str; job_id: str; series_id: str; episode_number: int
class BulkRandomReq(BaseModel): username: str; num_series: int; exclude_series_ids: list = []
class BulkPickReq(BaseModel): username: str; series_ids: list
class BulkConfirmReq(BaseModel): username: str; token: str

def process_register(req: RegisterReq, platform: str):
    return {"status": "error", "message": "Hệ thống không mở đăng ký. Vui lòng liên hệ Admin để được cấp tài khoản."}

def process_login(req: LoginReq, platform: str):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND platform = ?", (req.username, platform))
    user = cursor.fetchone()
    if not user: conn.close(); return {"status": "error", "message": "Tài khoản không tồn tại!"}
    pwd_match, needs_migration = verify_password(req.password, user["password"])
    if not pwd_match: conn.close(); return {"status": "error", "message": "Sai mật khẩu!"}
    if needs_migration: cursor.execute("UPDATE users SET password = ? WHERE username = ? AND platform = ?", (hash_password(req.password), req.username, platform)); conn.commit()
    # HWID check disabled — cho phép đăng nhập nhiều máy cùng lúc
    if user["expiry_date"]:
        try:
            if datetime.datetime.now() > datetime.datetime.strptime(user["expiry_date"], "%Y-%m-%d %H:%M:%S"): conn.close(); return {"status": "expired", "message": "Tài khoản hết hạn!", "expiry": user["expiry_date"]}
        except: pass
    try: vip_unlocked = bool(user["vip_unlocked"])
    except: vip_unlocked = False
    conn.close(); return {"status": "success", "expiry": user["expiry_date"], "vip_unlocked": vip_unlocked, "token": create_client_token(req.username, platform)}

def process_boomstory_login(req: LoginReq):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM boom_users WHERE username = ?", ((req.username or "").strip(),))
    user = cursor.fetchone()
    if not user:
        conn.close(); return {"status": "error", "message": "Tài khoản BOOM Story không tồn tại!"}
    if not bool(user["active"]):
        conn.close(); return {"status": "error", "message": "Tài khoản BOOM Story đã bị khóa!"}
    pwd_match, needs_migration = verify_password(req.password, user["password"])
    if not pwd_match:
        conn.close(); return {"status": "error", "message": "Sai mật khẩu!"}
    expiry = user["expiry_date"] or ""
    if expiry:
        try:
            if datetime.datetime.now() > datetime.datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S"):
                conn.close(); return {"status": "expired", "message": "Tài khoản BOOM Story đã hết hạn!", "expiry": expiry}
        except Exception:
            pass
    if needs_migration:
        cursor.execute("UPDATE boom_users SET password = ? WHERE username = ?", (hash_password(req.password), user["username"]))
    cursor.execute("UPDATE boom_users SET last_login = ? WHERE username = ?", (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["username"]))
    conn.commit(); conn.close()
    return {"status": "success", "username": user["username"], "expiry": expiry, "token": create_client_token(user["username"], "boomstory")}

def process_boomreview_login(req: LoginReq):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM boom_review_users WHERE username = ?", ((req.username or "").strip(),))
    user = cursor.fetchone()
    if not user:
        conn.close(); return {"status": "error", "message": "Tài khoản BOOM Review không tồn tại!"}
    if not bool(user["active"]):
        conn.close(); return {"status": "error", "message": "Tài khoản BOOM Review đã bị khóa!"}
    pwd_match, needs_migration = verify_password(req.password, user["password"])
    if not pwd_match:
        conn.close(); return {"status": "error", "message": "Sai mật khẩu!"}
    expiry = user["expiry_date"] or ""
    if expiry:
        try:
            if datetime.datetime.now() > datetime.datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S"):
                conn.close(); return {"status": "expired", "message": "Tài khoản BOOM Review đã hết hạn!", "expiry": expiry}
        except Exception:
            pass
    if needs_migration:
        cursor.execute("UPDATE boom_review_users SET password = ? WHERE username = ?", (hash_password(req.password), user["username"]))
    cursor.execute("UPDATE boom_review_users SET last_login = ? WHERE username = ?", (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["username"]))
    conn.commit(); conn.close()
    return {"status": "success", "username": user["username"], "expiry": expiry, "token": create_client_token(user["username"], "boomreview")}

async def require_boomreview(request: Request) -> dict:
    user = await require_client(request)
    if user.get("platform") != "boomreview":
        raise HTTPException(status_code=403, detail="Token không thuộc BOOM Review.")
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT username, expiry_date, active FROM boom_review_users WHERE username = ?", (user.get("sub", ""),))
    row = cursor.fetchone(); conn.close()
    if not row or not bool(row["active"]):
        raise HTTPException(status_code=401, detail="Tài khoản BOOM Review không còn hiệu lực.")
    if row["expiry_date"]:
        try:
            if datetime.datetime.now() > datetime.datetime.strptime(row["expiry_date"], "%Y-%m-%d %H:%M:%S"):
                raise HTTPException(status_code=403, detail="Tài khoản BOOM Review đã hết hạn.")
        except HTTPException:
            raise
        except Exception:
            pass
    return {**user, "expiry": row["expiry_date"]}

async def require_boomstory(request: Request) -> dict:
    user = await require_client(request)
    if user.get("platform") != "boomstory":
        raise HTTPException(status_code=403, detail="Token không thuộc BOOM Story.")
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT username, expiry_date, active FROM boom_users WHERE username = ?", (user.get("sub", ""),))
    row = cursor.fetchone(); conn.close()
    if not row or not bool(row["active"]):
        raise HTTPException(status_code=401, detail="Tài khoản BOOM Story không còn hiệu lực.")
    if row["expiry_date"]:
        try:
            if datetime.datetime.now() > datetime.datetime.strptime(row["expiry_date"], "%Y-%m-%d %H:%M:%S"):
                raise HTTPException(status_code=403, detail="Tài khoản BOOM Story đã hết hạn.")
        except HTTPException:
            raise
        except Exception:
            pass
    return {**user, "expiry": row["expiry_date"] or ""}

@app.get("/")
def health_check(): return {"status": "ok"}

@app.get("/health")
def health_light():
    """Endpoint SIÊU NHẸ cho watchdog canh treo. KHÔNG đụng DB/mạng - chỉ trả về
    ngay. Nếu endpoint này không phản hồi trong vài giây nghĩa là EVENT LOOP đã
    bị chặn cứng (treo) -> watchdog sẽ kill + restart. Cố tình không query MySQL
    để phân biệt 'server treo' với 'MySQL chậm' (MySQL chậm thì endpoint này VẪN
    phải trả lời được)."""
    return {"status": "ok", "ts": int(time.time())}

@app.get("/health/loop")
async def health_loop():
    """Endpoint ASYNC chạy TRÊN event loop — cho _healthcheck.bat / watchdog ping.
    Khác /health (def thường -> chạy threadpool, vẫn trả lời được kể cả khi loop
    treo): endpoint NÀY chạy trực tiếp trên event loop, nên khi loop bị chặn cứng
    (treo) nó sẽ TIMEOUT -> healthcheck phát hiện đúng 'treo thật' để kill + bật
    lại. Cố tình không đụng DB/mạng."""
    return {"status": "ok", "loop": True, "ts": int(time.time())}
# ==========================================
# CÔNG CỤ CHẨN ĐOÁN TREO/CRASH (chạy trên THREADPOOL nên vẫn trả lời được
# NGAY CẢ KHI event loop treo cứng). Bảo vệ bằng DEBUG_KEY để người ngoài
# không gọi được. Đặt biến môi trường DEBUG_KEY hoặc dùng mặc định bên dưới.
# ==========================================
DEBUG_KEY = os.environ.get("DEBUG_KEY", "anhstudio_debug_2026")

@app.get("/debug/stacks")
def debug_stacks(key: str = ""):
    """In stack của TẤT CẢ thread ngay lúc gọi. Khi server treo, gọi endpoint
    này từ máy khác: nó chạy trên threadpool (def thường) nên vẫn trả lời được
    dù event loop đã chết. Thread nào đang kẹt ở dòng nào sẽ lộ ra ngay -> biết
    CHÍNH XÁC chỗ gây treo, không phải đoán.

    Cách đọc: tìm thread có stack dừng ở get_mysql_connection / cursor.execute /
    requests.get / .recv / .acquire ... -> đó là chỗ đang chặn."""
    if key != DEBUG_KEY:
        raise HTTPException(403, "Sai key.")
    import sys as __sys, traceback as __tb, threading as __th
    frames = __sys._current_frames()
    id2name = {t.ident: t.name for t in __th.enumerate()}
    out = []
    for tid, frame in frames.items():
        out.append(f"===== THREAD {id2name.get(tid, '?')} (id={tid}) =====")
        out.append("".join(__tb.format_stack(frame)))
    return PlainTextResponse("\n".join(out))

@app.get("/debug/loop")
async def debug_loop(key: str = ""):
    """Cho biet dang chay SELECTOR hay PROACTOR loop. Sau khi doi sang run_server.py,
    goi endpoint nay phai thay 'Selector' -> da fix dung. Neu van thay 'Proactor'
    -> policy chua an, server con nguy co treo WinError 64.

    LUU Y: endpoint nay la ASYNC de chay TREN event loop chinh -> get_running_loop()
    tra ve dung loop that. (Neu de 'def' thuong -> chay o thread phu -> khong doc
    duoc loop chinh, bao nham.)"""
    if key != DEBUG_KEY:
        raise HTTPException(403, "Sai key.")
    import asyncio as __a
    try:
        loop = __a.get_running_loop()
        name = type(loop).__name__
    except Exception as e:
        name = f"?({e})"
    is_selector = "Selector" in name
    return {
        "loop_class": name,
        "is_selector": is_selector,
        "verdict": "OK - da fix treo Proactor" if is_selector else "CANH BAO - van la Proactor, con nguy co treo",
    }

@app.get("/debug/pool")
def debug_pool(key: str = ""):
    """Xem CAC pool MySQL con bao nhieu connection ranh (cong don tat ca pool).
    Neu 'in_use' cham 'total_size' thuong xuyen -> can tang NUM_POOLS.
    Neu 'pool_configured' = false -> MySQL khong ket noi duoc -> moi request 503."""
    if key != DEBUG_KEY:
        raise HTTPException(403, "Sai key.")
    info = {"pool_configured": bool(mysql_pools), "num_pools": len(mysql_pools)}
    if mysql_pools:
        try:
            total_size = 0; total_free = 0; per_pool = []
            for i, p in enumerate(mysql_pools):
                q = getattr(p, "_cnx_queue", None)
                size = getattr(p, "pool_size", 0)
                free = q.qsize() if q is not None else 0
                total_size += size; total_free += free
                per_pool.append({"pool": i, "size": size, "free": free, "in_use": size - free})
            info.update({
                "total_size": total_size,
                "total_free": total_free,
                "total_in_use": total_size - total_free,
                "per_pool": per_pool,
            })
        except Exception as e:
            info["error"] = str(e)
    return info

@app.post("/api/honggou/login")
def login_honggou(req: LoginReq, request: Request):
    if not rate_limiter.is_allowed(f"login:{get_client_ip(request)}", 10, 60): raise HTTPException(429, "Quá nhiều lần.")
    return process_login(req, "honggou")
@app.post("/api/login")
def login_launcher(req: LoginReq, request: Request): return login_honggou(req, request)
@app.post("/api/honggou/register")
def register_honggou(req: RegisterReq, request: Request):
    if not rate_limiter.is_allowed(f"register:{get_client_ip(request)}", 5, 60): raise HTTPException(429, "Quá nhiều lần.")
    return process_register(req, "honggou")
@app.post("/api/register")
def register_launcher(req: RegisterReq, request: Request): return register_honggou(req, request)
# BOOM Story dùng bảng boom_users và luồng đăng nhập riêng.
@app.post("/api/boomstory/login")
def login_boomstory(req: LoginReq, request: Request):
    if not rate_limiter.is_allowed(f"login_boomstory:{get_client_ip(request)}", 10, 60):
        raise HTTPException(429, "Quá nhiều lần.")
    return process_boomstory_login(req)

@app.get("/api/boomstory/me")
def boomstory_me(user: dict = Depends(require_boomstory)):
    return {"status": "success", "username": user.get("sub", ""), "expiry": user.get("expiry", "")}

# BOOM Review — luồng riêng, bảng boom_review_users.
@app.post("/api/boomreview/login")
def login_boomreview(req: LoginReq, request: Request):
    if not rate_limiter.is_allowed(f"login_boomreview:{get_client_ip(request)}", 10, 60):
        raise HTTPException(429, "Quá nhiều lần.")
    return process_boomreview_login(req)

@app.get("/api/boomreview/me")
def boomreview_me(user: dict = Depends(require_boomreview)):
    return {"status": "success", "username": user.get("sub", ""), "expiry": user.get("expiry", "")}

@app.get("/api/client/check_update")
def check_update(current_version: str = ""): return _load_update_info()

@app.get("/api/client/hot_movies")
async def get_hot_movies(genre: str = None):
    cached = _hot_movies_cache.get(genre or "__all__", [])
    if genre and genre != "Lịch Phim" and cached == _hot_movies_cache.get("__all__"):
        cached = [m for m in cached if genre in (m.get('genres') or '')]
    if cached: return cached
    ready = []
    if genre == "Lịch Phim":
        # --- DB call 1: fetch schedule rows (off loop) ---
        def _fetch_schedule():
            c = get_mysql_connection()
            if not c: return []
            cur = c.cursor(dictionary=True)
            cur.execute("SELECT series_id, original_url, title, cover_url, total_episodes, genres, cover_b64, air_time FROM jobs WHERE genres LIKE %s AND title IS NOT NULL AND title != '' ORDER BY COALESCE(air_time, updated_at) DESC LIMIT 30", (f"%{genre}%",))
            seen = set(); rows = []
            for o in cur.fetchall():
                t = (o.get('title') or '').strip()
                if t and t in seen: continue
                if t: seen.add(t)
                rows.append(o)
            cur.close(); c.close()
            return rows
        rows = await asyncio.to_thread(_fetch_schedule)

        missing = [r for r in rows if not r.get('cover_b64')]
        fetched_map = {}
        if missing:
            sem = asyncio.Semaphore(4)
            async with httpx.AsyncClient(timeout=15.0) as client:
                async def _get(r):
                    async with sem:
                        return await _fetch_cover_b64(client, r.get('series_id'), r.get('cover_url', ''), r.get('original_url'))
                results = await asyncio.gather(*[_get(r) for r in missing])
            to_persist = []
            for r, res in zip(missing, results):
                if res[0]:
                    fetched_map[str(r.get('series_id'))] = res[0]
                    if res[1]: to_persist.append((res[0], str(r.get('series_id'))))
            # --- DB call 2: persist covers (off loop) ---
            if to_persist:
                def _persist_covers(data):
                    try:
                        pconn = get_mysql_connection()
                        if pconn:
                            pcur = pconn.cursor()
                            pcur.executemany("UPDATE jobs SET cover_b64 = %s WHERE series_id = %s", data)
                            pconn.commit(); pcur.close(); pconn.close()
                    except: pass
                await asyncio.to_thread(_persist_covers, to_persist)

        for o in rows:
            at = o.get('air_time')
            t = (o.get('title') or '').strip()
            disp_title = f"[{at.strftime('%d/%m - %H:%M')}] {t}" if at else t
            c = o.get('cover_b64') or fetched_map.get(str(o.get('series_id'))) or get_placeholder_cover_b64()
            ready.append({"url": o.get('original_url') or f"https://hongguoduanju.com/detail?series_id={o['series_id']}", "series_id": o['series_id'], "title": disp_title, "cover_url": o.get('cover_url'), "total_episodes": o['total_episodes'], "genres": o.get('genres', ''), "cover_base64": c})
        return ready

    # --- DB call 3: fetch normal genre rows (off loop) ---
    def _fetch_normal():
        c = get_mysql_connection()
        if not c: return []
        cur = c.cursor(dictionary=True)
        q = "SELECT series_id, original_url, title, cover_url, total_episodes, genres, cover_b64 FROM jobs WHERE status = 'completed' AND total_episodes > 0 AND title != '' AND cover_url != ''"
        params = []
        if genre: q += " AND genres LIKE %s"; params.append(f"%{genre}%")
        cur.execute(q + " ORDER BY updated_at DESC LIMIT 30", tuple(params))
        seen = set(); result = []
        for o in cur.fetchall():
            t = (o.get('title') or '').strip()
            if t and t in seen: continue
            if t: seen.add(t)
            result.append({"url": o.get('original_url') or f"https://hongguoduanju.com/detail?series_id={o['series_id']}", "series_id": o['series_id'], "title": o.get('title'), "cover_url": o.get('cover_url'), "total_episodes": o['total_episodes'], "genres": o.get('genres', ''), "cover_base64": o.get('cover_b64') or ""})
        cur.close(); c.close()
        return result
    return await asyncio.to_thread(_fetch_normal)

async def _backfill_covers(results):
    thieu = [r for r in results if not str(r.get("cover_url", "")).strip() and r.get("series_id")]
    if not thieu: return
    sem = asyncio.Semaphore(6)
    async def _one(r):
        sid = str(r["series_id"])
        async with sem:
            try: meta = await asyncio.to_thread(fetch_series_metadata, sid)
            except: meta = None
        if meta and meta.get("cover_url"):
            r["cover_url"] = meta["cover_url"]
            try:
                c = get_mysql_connection()
                if c: cc = c.cursor(); cc.execute("UPDATE jobs SET cover_url = %s, title = COALESCE(NULLIF(title,''), %s), total_episodes = GREATEST(total_episodes, %s) WHERE series_id = %s", (meta["cover_url"], meta.get("title", ""), meta.get("total_episodes", 0), sid)); c.commit(); cc.close(); c.close()
            except: pass
    await asyncio.gather(*[_one(r) for r in thieu], return_exceptions=True)

@app.get("/api/client/cover/{series_id}")
async def client_cover(series_id: str, user: dict = Depends(require_client)):
    sid = str(series_id).strip()
    if not sid: raise HTTPException(404, "no sid")
    fpath = os.path.join(COVERS_DIR, f"{sid}.jpg")
    if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
        with open(fpath, 'rb') as f: return Response(content=f.read(), media_type="image/jpeg")
    cover_url = ""
    def _get_db_cover():
        c = get_mysql_connection()
        if c:
            cur = c.cursor(dictionary=True)
            cur.execute("SELECT cover_b64, cover_url FROM jobs WHERE series_id = %s LIMIT 1", (sid,))
            row = cur.fetchone(); cur.close(); c.close()
            return row
        return None
    row = await asyncio.to_thread(_get_db_cover)
    if row:
        b64 = row.get('cover_b64') or ''
        if len(b64) > 500:
            try:
                raw = _base64.b64decode(b64)
                with open(fpath, 'wb') as f: f.write(raw)
                return Response(content=raw, media_type="image/jpeg")
            except: pass
        cover_url = row.get('cover_url') or ''
    if not cover_url:
        try:
            meta = await asyncio.to_thread(fetch_series_metadata, sid)
            if meta and meta.get("cover_url"):
                cover_url = meta["cover_url"]
                def _update_c_url():
                    c2 = get_mysql_connection()
                    if c2: cc = c2.cursor(); cc.execute("UPDATE jobs SET cover_url = %s WHERE series_id = %s", (cover_url, sid)); c2.commit(); cc.close(); c2.close()
                await asyncio.to_thread(_update_c_url)
        except: pass
    if cover_url:
        if cover_url.startswith('//'): cover_url = 'https:' + cover_url
        try:
            async with httpx.AsyncClient(timeout=10.0) as client: 
                # FIX 4: User-Agent chống chặn cho client load ảnh
                r = await client.get(cover_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Referer": "https://hongguoduanju.com/"})
            if r.status_code == 200 and len(r.content) > 500:
                image_data = r.content
                try:
                    img = Image.open(io.BytesIO(image_data)).convert('RGB')
                    out = io.BytesIO()
                    img.save(out, format='JPEG', quality=90)
                    image_data = out.getvalue()
                except Exception: pass
                with open(fpath, 'wb') as f: f.write(image_data)
                def _update_c_b64():
                    c3 = get_mysql_connection()
                    if c3: 
                        cc = c3.cursor()
                        cc.execute("UPDATE jobs SET cover_b64 = %s WHERE series_id = %s", (_base64.b64encode(image_data).decode('ascii'), sid))
                        c3.commit(); cc.close(); c3.close()
                await asyncio.to_thread(_update_c_b64)
                return Response(content=image_data, media_type="image/jpeg")
        except: pass
    raise HTTPException(404, "no cover")

@app.get("/api/client/search")
async def client_search(keyword: str, user: dict = Depends(require_client)):
    is_unlocked = await asyncio.to_thread(_get_vip_unlocked, user["sub"], user.get("platform", "honggou"))
    api_results = []
    if is_unlocked:
        try:
            payload = {"action": "hongguo-search", "partner_id": PARTNER_ID, "partner_key": PARTNER_KEY, "user_id": user["sub"], "key_b64": _base64.b64encode(keyword.encode("utf-8")).decode("ascii"), "limit": 15}
            async with httpx.AsyncClient(timeout=60.0) as client: 
                resp = await client.post(HONGGUO_API_URL, json=payload)
            json_data = resp.json()
            if json_data.get("ok"):
                api_results = [{"series_id": str(i.get("series_id", "")), "title": str(i.get("title", "")), "cover_url": i.get("cover", ""), "total_episodes": i.get("episode_cnt", 0), "is_local": False} for i in json_data.get("results", []) if str(i.get("title", "")) and str(i.get("series_id", ""))]
        except Exception: pass 
    def _fetch_local():
        lmap = {}
        try:
            conn = get_mysql_connection()
            if conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT series_id, title, cover_url, genres, total_episodes, status FROM jobs WHERE title LIKE %s LIMIT 20", (f"%{keyword}%",))
                for r in cursor.fetchall(): lmap[str(r['series_id'])] = r
                cursor.close(); conn.close()
        except: pass
        return lmap
    local_map = await asyncio.to_thread(_fetch_local)
    final_results = []
    seen_ids = set()
    for m in api_results:
        sid = m['series_id']
        seen_ids.add(sid)
        if sid in local_map:
            m['is_local'] = True
            st = local_map[sid].get('status')
            if st == 'completed': m['title'] = f"✅ {m['title']} (VIP)"
            elif st in ['pending', 'processing', 'partial']: m['title'] = f"⚡ {m['title']} (Đang trích)"
        final_results.append(m)
    for sid, r in local_map.items():
        if sid not in seen_ids:
            st = r.get('status')
            title = r['title']
            if st == 'completed': title = f"✅ {title} (VIP)"
            elif st in ['pending', 'processing', 'partial']: title = f"⚡ {title} (Đang trích)"
            else: title = f"🔍 {title} (Chờ xử lý)"
            final_results.append({"series_id": sid, "title": title, "cover_url": r.get('cover_url') or '', "total_episodes": r.get('total_episodes') or 0, "is_local": True})
    if not final_results:
        if not is_unlocked:
            return {"status": "error", "locked": True, "message": "🔒 Phim này chưa có trong kho. Tài khoản dùng thử chỉ tải được phim có sẵn.\nVui lòng liên hệ Admin để MỞ KHÓA VIP nhằm tìm & tải phim mới ngoài kho."}
        return {"status": "error", "message": "Không tìm thấy bộ phim nào phù hợp."}
    await _backfill_covers(final_results)
    if api_results:
        def _save_new_jobs():
            try:
                save_conn = get_mysql_connection()
                if save_conn:
                    sc = save_conn.cursor()
                    for m in api_results:
                        sc.execute("INSERT IGNORE INTO jobs (job_id, series_id, original_url, title, cover_url, total_episodes, status) VALUES (%s, %s, %s, %s, %s, %s, 'idle')", (f"HG_SEARCH_{int(time.time())}_{m['series_id']}", m['series_id'], f"https://hongguoduanju.com/detail?series_id={m['series_id']}", m['title'], m['cover_url'], m['total_episodes']))
                    save_conn.commit(); sc.close(); save_conn.close()
            except: pass
        await asyncio.to_thread(_save_new_jobs)
    return {"status": "success", "source": "mixed", "data": final_results[:15]}

@app.get("/api/client/balance/{username}")
def get_client_balance(username: str, user: dict = Depends(require_client)):
    if user["sub"] != username: raise HTTPException(403, "Không có quyền.")
    conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT balance_hongguo FROM users WHERE username = ? AND platform = 'honggou'", (username,)); row = cursor.fetchone(); conn.close()
    return {"status": "success", "balance": row["balance_hongguo"]} if row else {"status": "error", "balance": 0}

@app.get("/api/client/vip_status")
def get_client_vip_status(user: dict = Depends(require_client)):
    unlocked = _get_vip_unlocked(user["sub"], user.get("platform", "honggou"))
    return {"status": "success", "vip_unlocked": unlocked}

BULK_PRICE_RANDOM, BULK_PRICE_PICK = 2000, 2500
_bulk_sessions = {}
def _purge_bulk():
    now = time.time()
    for t in [k for k, v in _bulk_sessions.items() if v["expire"] < now]: _bulk_sessions.pop(t, None)
def _completed_series(cursor, exclude=None):
    cursor.execute("SELECT series_id, title, cover_url, total_episodes FROM jobs WHERE status = 'completed' AND total_episodes > 0 AND title != '' AND series_id != ''")
    rows = cursor.fetchall()
    if exclude: ex = set(str(x) for x in exclude); rows = [r for r in rows if str(r["series_id"]) not in ex]
    return rows
def _make_bulk_session(username, picked, price_per):
    _purge_bulk(); cost = len(picked) * price_per; token = secrets.token_urlsafe(16)
    _bulk_sessions[token] = {"username": username, "series_ids": [str(j["series_id"]) for j in picked], "cost": cost, "expire": time.time() + 300}
    return token, cost

@app.get("/api/client/catalog")
def client_catalog(username: str = "", keyword: str = "", page: int = 1, page_size: int = 40, user: dict = Depends(require_client)):
    page, page_size = max(1, int(page or 1)), max(10, min(int(page_size or 40), 100))
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "message": "Lỗi DB"}
    try:
        cur = conn.cursor(dictionary=True); where = "status = 'completed' AND total_episodes > 0 AND title != ''"; params = []
        if keyword.strip(): where += " AND title LIKE %s"; params.append(f"%{keyword.strip()}%")
        cur.execute(f"SELECT COUNT(*) AS c FROM jobs WHERE {where}", params); total = cur.fetchone()["c"]
        cur.execute(f"SELECT series_id, title, cover_url, total_episodes FROM jobs WHERE {where} ORDER BY updated_at DESC LIMIT %s OFFSET %s", params + [page_size, (page - 1) * page_size])
        rows = cur.fetchall(); cur.close()
    except Exception as e: return {"status": "error", "message": str(e)}
    finally: conn.close()
    return {"status": "success", "page": page, "page_size": page_size, "total": total, "price_per_series": BULK_PRICE_PICK, "series": [{"series_id": str(r["series_id"]), "title": r.get("title"), "cover_url": r.get("cover_url"), "total_episodes": int(r.get("total_episodes") or 0)} for r in rows]}

@app.post("/api/client/bulk/random_quote")
def bulk_random_quote(req: BulkRandomReq, user: dict = Depends(require_client)):
    if user["sub"] != req.username: raise HTTPException(403, "Không có quyền.")
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "message": "Lỗi DB"}
    try: cur = conn.cursor(dictionary=True); pool = _completed_series(cur, req.exclude_series_ids); cur.close()
    except Exception as e: return {"status": "error", "message": str(e)}
    finally: conn.close()
    if not pool: return {"status": "error", "message": "Không có bộ mới."}
    picked = random.sample(pool, min(max(1, min(int(req.num_series or 0), 500)), len(pool)))
    token, cost = _make_bulk_session(req.username, picked, BULK_PRICE_RANDOM)
    return {"status": "success", "token": token, "mode": "random", "num_series": len(picked), "price_per_series": BULK_PRICE_RANDOM, "cost": cost, "series": [{"series_id": str(j["series_id"]), "title": j.get("title"), "cover_url": j.get("cover_url"), "total_episodes": int(j.get("total_episodes") or 0)} for j in picked]}

@app.post("/api/client/bulk/pick_quote")
def bulk_pick_quote(req: BulkPickReq, user: dict = Depends(require_client)):
    if user["sub"] != req.username: raise HTTPException(403, "Không có quyền.")
    ids = [str(x) for x in (req.series_ids or [])]
    if not ids: return {"status": "error", "message": "Chưa chọn bộ nào."}
    if len(ids) > 500: return {"status": "error", "message": "Tối đa 500 bộ."}
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "message": "Lỗi DB"}
    try: cur = conn.cursor(dictionary=True); cur.execute(f"SELECT series_id, title, cover_url, total_episodes FROM jobs WHERE series_id IN ({','.join(['%s']*len(ids))}) AND status = 'completed' AND total_episodes > 0", ids); valid = cur.fetchall(); cur.close()
    except Exception as e: return {"status": "error", "message": str(e)}
    finally: conn.close()
    if not valid: return {"status": "error", "message": "Không hợp lệ."}
    token, cost = _make_bulk_session(req.username, valid, BULK_PRICE_PICK)
    return {"status": "success", "token": token, "mode": "pick", "num_series": len(valid), "price_per_series": BULK_PRICE_PICK, "cost": cost, "series": [{"series_id": str(j["series_id"]), "title": j.get("title"), "cover_url": j.get("cover_url"), "total_episodes": int(j.get("total_episodes") or 0)} for j in valid]}

@app.post("/api/client/bulk/confirm")
def bulk_confirm(req: BulkConfirmReq, user: dict = Depends(require_client)):
    if user["sub"] != req.username: raise HTTPException(403, "Không có quyền.")
    _purge_bulk()
    sess = _bulk_sessions.get(req.token)
    if not sess or sess["username"] != req.username: raise HTTPException(400, "Phiên hết hạn.")
    cost, series_ids = sess["cost"], sess["series_ids"]
    pay = get_db()
    try:
        pc = pay.cursor(); pc.execute("SELECT balance_hongguo FROM users WHERE username = ? AND platform = 'honggou'", (req.username,))
        row = pc.fetchone()
        if not row: pay.close(); raise HTTPException(403, "Tài khoản không tồn tại.")
        if cost > 0:
            pc.execute("UPDATE users SET balance_hongguo = balance_hongguo - ? WHERE username = ? AND platform = 'honggou' AND balance_hongguo >= ?", (cost, req.username, cost)); pay.commit()
            if pc.rowcount == 0: pay.close(); raise HTTPException(402, "Không đủ số dư.")
    finally:
        try: pay.close()
        except: pass
    _bulk_sessions.pop(req.token, None)   
    result = []; conn = get_mysql_connection()
    if conn:
        try:
            cur = conn.cursor(dictionary=True); cur.execute(f"SELECT j.series_id, je.episode_number, je.drive_link, je.file_name, j.title FROM job_episodes je JOIN jobs j ON j.job_id = je.job_id WHERE j.series_id IN ({','.join(['%s']*len(series_ids))}) AND je.drive_link != '' ORDER BY j.series_id, je.episode_number", series_ids); rows = cur.fetchall(); cur.close()
        except: rows = []
        finally: conn.close()
        by = {}
        for r in rows:
            sid = str(r["series_id"]); link = r.get("drive_link") or ""
            if link.startswith("r2://"):
                try: link = r2_presign_get(link[5:])
                except: link = ""
            if not link: continue
            by.setdefault(sid, {"series_id": sid, "title": r.get("title"), "episodes": []})
            by[sid]["episodes"].append({"episode_number": r["episode_number"], "url": link, "file_name": r.get("file_name")})
        result = list(by.values())
    if not result and cost > 0:
        try: rf = get_db(); rc = rf.cursor(); rc.execute("UPDATE users SET balance_hongguo = balance_hongguo + ? WHERE username = ? AND platform = 'honggou'", (cost, req.username)); rf.commit(); rf.close()
        except: pass
        return {"status": "error", "message": "Lỗi lấy link — đã hoàn tiền.", "series": []}
    return {"status": "success", "cost": cost, "series": result}

@app.post("/api/client/pay_for_download")
def pay_for_download(req: PayDownloadReq, user: dict = Depends(require_client)):
    if user["sub"] != req.username: raise HTTPException(403, "Không có quyền.")
    cost = req.num_episodes * 50
    conn = get_db(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT balance_hongguo FROM users WHERE username = ? AND platform = 'honggou'", (req.username,))
        row = cursor.fetchone()
        if not row: return {"status": "error", "message": "Không tồn tại!"}
        if (row["balance_hongguo"] or 0) < cost: return {"status": "error", "message": f"Cần {cost}đ."}
        return {"status": "success"}
    finally: conn.close()

@app.post("/api/client/stream_download_links")
async def stream_download_links(req: PayDownloadReq, user: dict = Depends(require_client)):
    if user["sub"] != req.username: raise HTTPException(403, "Không có quyền.")
    async def event_generator():
        # Guard: không có tập nào được chọn
        if not req.episodes:
            yield f"data: {json.dumps({'error': 'Chưa chọn tập nào.'})}\n\n"
            yield f"data: {json.dumps({'status': 'stream_finished'})}\n\n"
            return

        missing_eps = list(req.episodes)

        # Bước 1: lấy link từ R2/Drive trong DB trước
        if req.job_id and missing_eps:
            db = get_mysql_connection()
            if db:
                try:
                    db_cursor = db.cursor(dictionary=True)
                    db_cursor.execute(
                        f"SELECT episode_number, drive_link FROM job_episodes WHERE job_id = %s AND episode_number IN ({','.join(['%s']*len(missing_eps))}) AND drive_link != ''",
                        [req.job_id] + missing_eps
                    )
                    for r in db_cursor.fetchall():
                        ep, link = r['episode_number'], r['drive_link']
                        if link.startswith("r2://"):
                            try: link = r2_presign_get(link[5:])
                            except Exception as re: 
                                print(f"[WARN] R2 presign lỗi tập {ep}: {re}", flush=True)
                                link = ""
                        if link:
                            if ep in missing_eps: missing_eps.remove(ep)
                            yield f"data: {json.dumps({'episode_number': ep, 'url': link, 'source': 'r2'})}\n\n"
                    db_cursor.close()
                except Exception as dbe:
                    print(f"[WARN] DB query lỗi: {dbe}", flush=True)
                finally:
                    db.close()

        # Bước 2: tập còn thiếu → gọi Momigo
        if missing_eps and req.series_id:
            is_unlocked = await asyncio.to_thread(_get_vip_unlocked, user["sub"], user.get("platform", "honggou"))
            if not is_unlocked:
                yield f"data: {json.dumps({'error': '🔒 Các tập này chưa có trong kho. Cần MỞ KHÓA VIP để tải phim/tập mới. Vui lòng liên hệ Admin.', 'locked': True})}\n\n"
                yield f"data: {json.dumps({'status': 'stream_finished'})}\n\n"
                return

            print(f"[GRAB] user={user['sub']} series={req.series_id} missing_eps={missing_eps}", flush=True)

            # ==========================================================
            # PHÂN TRANG MOMIGO: chỉ grab ĐÚNG những trang chứa tập còn
            # thiếu (missing_eps đã trừ các tập có sẵn trên R2). Không quét
            # cả bộ từ offset=0 nữa - phim đã có nhiều tập trên kho thì chỉ
            # gọi vài trang cần thiết, nhanh hơn hẳn và đỡ chạm limit nguồn.
            # PAGE_SIZE=50: log thực đo cho thấy trang 100 tập chậm (~53s),
            # 50 nhẹ hơn mỗi trang nên an toàn timeout hơn.
            # ==========================================================
            PAGE_SIZE = 50

            # Hàm lưu 1 trang episodes vào DB (tái dùng cho mọi trang)
            def _batch_insert_episodes(eps_page):
                sub_db = get_mysql_connection()
                if not sub_db: return
                try:
                    s_c = sub_db.cursor(dictionary=True)
                    s_c.execute("SELECT episode_number, raw_video_url FROM job_episodes WHERE job_id = %s", (req.job_id,))
                    existing = {row['episode_number']: row['raw_video_url'] for row in s_c.fetchall()}
                    inserts, updates = [], []
                    for ep_data in eps_page:
                        ep_index = ep_data.get("index")
                        cdn_url = ep_data.get("cdn_url")
                        aes_key = ep_data.get("aes_key") or ""
                        if not cdn_url or ep_index is None: continue
                        try: ep_index = int(ep_index)
                        except: continue
                        raw_val = json.dumps({"url": cdn_url, "key": aes_key})
                        file_name = f"Tap_{ep_index:02d}.mp4"
                        if ep_index in existing:
                            if not existing[ep_index]: updates.append((raw_val, file_name, req.job_id, ep_index))
                        else:
                            inserts.append((req.job_id, ep_index, raw_val, file_name, ''))
                    if inserts: s_c.executemany("INSERT IGNORE INTO job_episodes (job_id, episode_number, raw_video_url, file_name, drive_link) VALUES (%s, %s, %s, %s, %s)", inserts)
                    if updates: s_c.executemany("UPDATE job_episodes SET raw_video_url = %s, file_name = %s WHERE job_id = %s AND episode_number = %s", updates)
                    s_c.execute("UPDATE jobs SET status = 'pending' WHERE job_id = %s AND status NOT IN ('completed', 'processing')", (req.job_id,))
                    sub_db.commit(); s_c.close()
                except Exception as ie:
                    print(f"[WARN] batch_insert lỗi: {ie}", flush=True)
                finally: sub_db.close()

            def _update_total(total_eps_grabbed):
                up_db = get_mysql_connection()
                if up_db:
                    try:
                        up_c = up_db.cursor()
                        up_c.execute("UPDATE jobs SET total_episodes = %s WHERE job_id = %s AND total_episodes != %s", (total_eps_grabbed, req.job_id, total_eps_grabbed))
                        up_db.commit(); up_c.close()
                    finally: up_db.close()

            # Gom episodes lấy được để đối chiếu tập thiếu
            all_eps = {}          # index(int) -> ep_data
            server_total = 0      # tổng tập báo từ nguồn (nếu có)
            total_updated = False

            # ---- Tính DANH SÁCH OFFSET cần grab từ missing_eps ----
            # Mỗi tập thiếu thuộc trang offset = (tap-1)//PAGE_SIZE*PAGE_SIZE.
            # Chỉ grab các trang này, bỏ qua trang mà mọi tập đã có trên R2.
            # (index tập của nguồn tính từ 1; nếu nguồn của bạn tính từ 0 thì
            #  bỏ phần "-1" đi cho khớp.)
            _missing_int = []
            for _m in missing_eps:
                try: _missing_int.append(int(_m))
                except: pass
            offsets_to_grab = sorted({ ((m - 1) // PAGE_SIZE) * PAGE_SIZE
                                       for m in _missing_int if m >= 1 })
            if not offsets_to_grab:
                offsets_to_grab = [0]  # fallback an toàn
            print(f"[GRAB PLAN] series={req.series_id} missing={len(_missing_int)} "
                  f"can_grab_{len(offsets_to_grab)}_trang offsets={offsets_to_grab}", flush=True)

            try:
                # Timeout nâng lên 300s để trang chậm (đo thực ~53s/100 tập)
                # không bị cắt oan; với PAGE_SIZE=50 mỗi trang còn nhẹ hơn nữa.
                async with httpx.AsyncClient(timeout=300.0) as client:
                    for _pi, offset in enumerate(offsets_to_grab):
                        # Keep-alive: nhả 1 comment SSE trước mỗi lần gọi nguồn
                        # để client luôn thấy có tín hiệu, không đếm ngược timeout
                        # trong lúc server đang chờ Momigo trả trang.
                        yield ": keepalive\n\n"

                        payload = {
                            "action": "hongguo-grab",
                            "partner_id": PARTNER_ID,
                            "partner_key": PARTNER_KEY,
                            "user_id": user["sub"],
                            "series_id": req.series_id,
                            "offset": offset,
                            "limit": PAGE_SIZE
                        }
                        print(f"[GRAB PAGE] series={req.series_id} offset={offset} limit={PAGE_SIZE} "
                              f"({_pi+1}/{len(offsets_to_grab)})", flush=True)
                        _t_page_start = time.time()
                        resp = await client.post(HONGGUO_API_URL, json=payload)
                        _t_page_elapsed = time.time() - _t_page_start

                        # Kiểm tra HTTP status
                        if resp.status_code >= 400:
                            print(f"[LỖI GRAB] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
                            yield f"data: {json.dumps({'error': f'Lỗi HTTP {resp.status_code} từ nguồn.'})}\n\n"
                            yield f"data: {json.dumps({'status': 'stream_finished'})}\n\n"
                            return

                        try:
                            grab_data = resp.json()
                        except Exception:
                            print(f"[LỖI GRAB] Response không phải JSON: {resp.text[:200]}", flush=True)
                            yield f"data: {json.dumps({'error': 'Nguồn trả dữ liệu không hợp lệ.'})}\n\n"
                            yield f"data: {json.dumps({'status': 'stream_finished'})}\n\n"
                            return

                        if not grab_data.get("ok"):
                            err_code = grab_data.get("error", "unknown")
                            print(f"[LỖI GRAB] Momigo từ chối: {err_code} | user={user['sub']} series={req.series_id} offset={offset}", flush=True)
                            err_map = {
                                "het_han": "Tài khoản hết hạn sử dụng. Vui lòng liên hệ Admin để gia hạn.",
                                "het_luot": "Đã dùng hết lượt tải hôm nay. Vui lòng thử lại sau 0h.",
                                "action_khong_ho_tro": "Action không được hỗ trợ. Vui lòng liên hệ Admin.",
                                "sai_key": "Sai partner key. Vui lòng liên hệ Admin.",
                                "removed": "Phim đã bị gỡ khỏi nguồn.",
                            }
                            err_msg = err_map.get(err_code, f"Lỗi nguồn: {err_code}")
                            yield f"data: {json.dumps({'error': err_msg, 'error_code': err_code})}\n\n"
                            yield f"data: {json.dumps({'status': 'stream_finished'})}\n\n"
                            return

                        if grab_data.get("removed"):
                            yield f"data: {json.dumps({'error': 'Phim đã bị gỡ khỏi nguồn.'})}\n\n"
                            yield f"data: {json.dumps({'status': 'stream_finished'})}\n\n"
                            return

                        page_eps = grab_data.get("episodes") or []
                        # tổng tập của cả bộ (nguồn thường trả 'total' cố định qua các trang)
                        page_total = grab_data.get("total") or 0
                        if page_total: server_total = page_total

                        # Đếm tập có link vs thiếu link ngay trong trang này để soi
                        # đúng chỗ tập nào bị nguồn trả thiếu cdn_url.
                        _page_with_link = sum(1 for _e in page_eps if _e.get("cdn_url"))
                        _page_no_link = len(page_eps) - _page_with_link
                        _idx_no_link = [_e.get("index") for _e in page_eps if not _e.get("cdn_url")]
                        print(f"[GRAB PAGE OK] series={req.series_id} offset={offset} got={len(page_eps)} "
                              f"total={server_total} thoi_gian={_t_page_elapsed:.1f}s "
                              f"co_link={_page_with_link} thieu_link={_page_no_link}"
                              + (f" cac_tap_thieu={_idx_no_link}" if _idx_no_link else ""), flush=True)

                        # Cập nhật tổng tập vào DB (1 lần, khi biết total)
                        if server_total and req.job_id and not total_updated:
                            await asyncio.to_thread(_update_total, server_total)
                            total_updated = True

                        # Lưu trang này vào DB ngay (giảm rủi ro mất dữ liệu giữa chừng)
                        if req.job_id and page_eps:
                            await asyncio.to_thread(_batch_insert_episodes, page_eps)

                        # Gom vào bộ nhớ để trả link cho tập thiếu
                        for e in page_eps:
                            idx = e.get("index")
                            if idx is None: continue
                            try: idx = int(idx)
                            except: continue
                            all_eps[idx] = e

                print(f"[GRAB DONE] series={req.series_id} tong_lay={len(all_eps)} server_total={server_total}", flush=True)

                # Trả link cho từng tập thiếu (đối chiếu với toàn bộ trang đã lấy)
                for ep_num in missing_eps:
                    try: key = int(ep_num)
                    except: key = ep_num
                    ep_data = all_eps.get(key)
                    if not ep_data:
                        print(f"[WARN] Không tìm thấy tập {ep_num} trong response Momigo", flush=True)
                        yield f"data: {json.dumps({'episode_number': ep_num, 'status': 'error', 'message': 'Tập không có trong nguồn'})}\n\n"
                        continue
                    cdn_url = ep_data.get("cdn_url")
                    if not cdn_url:
                        print(f"[WARN] Tập {ep_num} thiếu cdn_url", flush=True)
                        yield f"data: {json.dumps({'episode_number': ep_num, 'status': 'error', 'message': 'Thiếu link tải'})}\n\n"
                        continue
                    aes_key = ep_data.get("aes_key") or ""
                    yield f"data: {json.dumps({'episode_number': ep_num, 'url': cdn_url, 'aes_key': aes_key, 'source': 'momigo_raw'})}\n\n"

            except httpx.TimeoutException:
                print(f"[LỖI GRAB] Timeout sau 300s | user={user['sub']} series={req.series_id}", flush=True)
                yield f"data: {json.dumps({'error': 'Nguồn phản hồi quá chậm (timeout). Vui lòng thử lại.'})}\n\n"
            except Exception as e:
                import traceback
                print(f"[LỖI GRAB] Exception: {traceback.format_exc()}", flush=True)
                yield f"data: {json.dumps({'error': 'Lỗi kết nối tới nguồn.', 'detail': str(e)})}\n\n"

        yield f"data: {json.dumps({'status': 'stream_finished'})}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/client/retry_dead_link")
async def retry_dead_link(req: RetryDeadLinkReq, user: dict = Depends(require_client)):
    if user["sub"] != req.username: raise HTTPException(403, "Forbidden")
    db = get_mysql_connection()
    if not db: raise HTTPException(500, "Lỗi DB")
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, drive_link FROM job_episodes WHERE job_id = %s AND episode_number = %s", (req.job_id, req.episode_number))
        row = cursor.fetchone()
        if not row: return {"status": "error", "message": "Chưa thanh toán.", "episode_number": req.episode_number}
        drive_link = row.get("drive_link") or ""
        if drive_link.startswith("r2://"):
            try:
                new_url = r2_presign_get(drive_link[5:])
                return {"status": "success", "url": new_url, "aes_key": "", "episode_number": req.episode_number}
            except Exception as e:
                return {"status": "error", "message": f"Lỗi sinh link R2: {str(e)}", "episode_number": req.episode_number}
        is_unlocked = _get_vip_unlocked(user["sub"], user.get("platform", "honggou"))
        if not is_unlocked:
            cursor.close()
            return {"status": "error", "locked": True, "message": "🔒 Cần MỞ KHÓA VIP để lấy lại link tập này. Vui lòng liên hệ Admin.", "episode_number": req.episode_number}
        cursor.execute("UPDATE job_episodes SET drive_link = '', raw_video_url = NULL WHERE job_id = %s AND episode_number = %s", (req.job_id, req.episode_number))
        db.commit()
        cursor.close()
    finally: db.close()
    
    async with httpx.AsyncClient(timeout=200.0) as client:
        try:
            resp = await client.post(HONGGUO_API_URL, json={"action": "hongguo-grab", "partner_id": PARTNER_ID, "partner_key": PARTNER_KEY, "user_id": user["sub"], "series_id": req.series_id, "limit": 0})
            grab_data = resp.json()
            if not grab_data.get("ok"): return {"status": "error", "message": grab_data.get("error", "Lỗi nguồn"), "episode_number": req.episode_number}
            ep_data = next((e for e in grab_data.get("episodes", []) if str(e.get("index")) == str(req.episode_number)), None)
            if not ep_data: return {"status": "error", "message": "Không tìm thấy tập", "episode_number": req.episode_number}
            cdn_url, aes_key = ep_data.get("cdn_url"), ep_data.get("aes_key", "")
            db = get_mysql_connection()
            if db:
                try: 
                    cursor = db.cursor()
                    cursor.execute("UPDATE job_episodes SET raw_video_url = %s WHERE job_id = %s AND episode_number = %s", (json.dumps({"url": cdn_url, "key": aes_key}), req.job_id, req.episode_number))
                    db.commit()
                    cursor.close()
                except: pass
                finally: db.close()
            return {"status": "success", "url": cdn_url, "aes_key": aes_key, "episode_number": req.episode_number}
        except Exception as e: return {"status": "error", "message": str(e), "episode_number": req.episode_number}
@app.post("/api/client/add_job")
def client_add_job(req: AddJobReq, user: dict = Depends(require_client)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "message": "Lỗi DB"}
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM jobs WHERE series_id = %s", (req.series_id,))
        existing_job = cursor.fetchone()
        
        if existing_job:
            # Xóa sạch kết quả đọc thừa để tránh lỗi unread result
            cursor.fetchall() 
            
            job_id = existing_job['job_id']
            cursor.execute("SELECT episode_number, drive_link, file_name FROM job_episodes WHERE job_id = %s ORDER BY episode_number ASC", (job_id,))
            episodes = _sign_episodes(cursor.fetchall(), req.series_id)
            
            if existing_job['status'] == 'idle' or (req.expected_total > len(episodes) and existing_job['status'] not in ['pending', 'processing']):
                try:
                    cursor.execute("UPDATE jobs SET total_episodes = %s WHERE job_id = %s", (req.expected_total, job_id))
                    conn.commit()
                except Exception:
                    # Bỏ qua lỗi khóa chéo (deadlock) khi chạy đa luồng, rollback và vẫn trả về status idle
                    conn.rollback()
                return {"status": "idle", "job_id": job_id, "series_id": req.series_id, "episodes": episodes}
                
            if existing_job['status'] == 'completed': return {"status": "cache_hit", "job_id": job_id, "series_id": req.series_id, "episodes": episodes}
            elif existing_job['status'] == 'partial': return {"status": "partial", "job_id": job_id, "series_id": req.series_id, "episodes": episodes}
            else: return {"status": "processing", "job_id": job_id, "series_id": req.series_id, "episodes": episodes}
            
        job_id = f"HG_{int(time.time())}"
        if not _get_vip_unlocked(user["sub"], user.get("platform", "honggou")):
            return {"status": "locked", "message": "🔒 Phim này chưa có trong kho. Cần MỞ KHÓA VIP để quét & tải phim mới. Vui lòng liên hệ Admin."}
            
        try: 
            cursor.execute("INSERT INTO jobs (job_id, series_id, original_url, title, cover_url, total_episodes, status) VALUES (%s, %s, %s, %s, %s, %s, %s)", (job_id, req.series_id, req.url, req.title, req.cover_url, req.expected_total, "idle"))
            conn.commit()
        except: 
            conn.rollback()
        return {"status": "cache_miss", "job_id": job_id, "series_id": req.series_id}

    except Exception as e:
        return {"status": "error", "message": f"Lỗi xử lý: {str(e)}"}
    finally:
        # Quan trọng nhất: Bắt buộc phải đóng kết nối trả về Pool dù có lỗi hay không
        try: cursor.close()
        except: pass
        try: conn.close()
        except: pass

@app.get("/api/client/job_status/{job_id}")
def get_job_status(job_id: str, user: dict = Depends(require_client)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True); cursor.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,)); job_info = cursor.fetchone()
    if not job_info: return {"status": "waiting"}
    cursor.execute("SELECT episode_number, drive_link, file_name FROM job_episodes WHERE job_id = %s", (job_id,))
    episodes_data = _sign_episodes(cursor.fetchall(), job_info.get("series_id")); cursor.close(); conn.close()
    return {"status": job_info["status"], "total_episodes": job_info["total_episodes"], "episodes": episodes_data}

@app.post("/api/client/heartbeat")
def client_heartbeat(data: ClientHeartbeatReq, user: dict = Depends(require_client)):
    CLIENT_HEARTBEATS[user["sub"]] = {"time": time.time(), "platform": user.get("platform", ""), "current_job_id": data.current_job_id, "series_id": data.series_id, "action": data.action}; return {"status": "ok"}

@app.get("/api/worker/watchdog_ping")
def watchdog_ping(request: Request, worker_id: str = "Unknown", _=Depends(require_worker)):
    return {"status": "ok", "command": WATCHDOG_COMMANDS.pop(worker_id, None)}

@app.get("/api/worker/get_job")
def worker_get_job(request: Request, worker_id: str = "Unknown", _=Depends(require_worker)):
    WORKER_HEARTBEATS[worker_id] = {"time": time.time(), "action": "Vừa nhận Job..."}
    # RETRY DEADLOCK: cau UPDATE ... FOR UPDATE ben duoi khi nhieu worker goi
    # cung luc se khoa hang cheo nhau -> MySQL bao loi 1213 (deadlock). Day la
    # chuyen BINH THUONG - MySQL tu huy 1 ben va bao "try restarting transaction".
    # Ta bat dung loi do, rollback + thu lai toi da 3 lan. Da so lan 2 la qua.
    import time as _t_retry
    last_deadlock = None
    for _attempt in range(3):
        conn = None
        try:
            conn = get_mysql_connection()
            if not conn: return {"status": "error"}
            return _worker_get_job_inner(conn, worker_id)
        except mysql.connector.Error as _e:
            # 1213 = deadlock, 1205 = lock wait timeout -> thu lai
            if getattr(_e, "errno", None) in (1213, 1205):
                last_deadlock = _e
                try:
                    if conn: conn.rollback()
                except Exception: pass
                _t_retry.sleep(0.1 * (_attempt + 1))  # 0.1s, 0.2s, 0.3s
                continue
            raise
        finally:
            # LUON dong connection de tra ve pool - tranh ro ri connection khi
            # co loi giua chung (day la 1 ly do connection bi giu lau, can pool).
            # ROLLBACK truoc khi close: neu con transaction dang mo (vd return som
            # o nhanh blacklist, hoac loi giua chung) thi huy no truoc, de connection
            # tra ve pool o trang thai SACH - khong giu lock, khong lam cham reset.
            try:
                if conn: conn.rollback()
            except Exception: pass
            try:
                if conn: conn.close()
            except Exception: pass
    # Het 3 lan van deadlock -> tra no_job (worker se goi lai sau), khong crash.
    return {"status": "no_job", "note": "deadlock_retry_exhausted"}


def _worker_get_job_inner(conn, worker_id):
    """Logic cap job that su, tach ra de retry deadlock o ham ngoai goi lai duoc.
    conn.close() do ham NGOAI lo (trong finally) - o day KHONG close."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT worker_id FROM worker_blacklist WHERE worker_id = %s", (worker_id,))
    if cursor.fetchone():
        # Worker bi chan -> return som. Rollback + close cursor NGAY de khong de
        # lai transaction treo (day la 1 nguon ro ri connection gay 503).
        try: conn.rollback()
        except Exception: pass
        cursor.close()
        return {"status": "pause", "message": "Bị chặn"}
    cursor.execute("START TRANSACTION;")
    cursor.execute("SELECT e.id, e.job_id, j.series_id, e.episode_number, e.raw_video_url, e.file_name FROM job_episodes e JOIN jobs j ON e.job_id = j.job_id WHERE j.worker_id = %s AND e.raw_video_url IS NOT NULL AND e.drive_link = '' AND e.worker_id IS NULL ORDER BY e.episode_number ASC LIMIT 1 FOR UPDATE SKIP LOCKED", (worker_id,))
    piggyback_job = cursor.fetchone()
    if not piggyback_job:
        cursor.execute("UPDATE jobs SET worker_id = NULL WHERE worker_id = %s AND status <> 'processing' AND NOT EXISTS (SELECT 1 FROM job_episodes e WHERE e.job_id = jobs.job_id AND e.raw_video_url IS NOT NULL AND e.drive_link = '' AND (e.worker_id IS NULL OR e.worker_id = %s))", (worker_id, worker_id))
        cursor.execute("SELECT j.job_id, j.series_id, j.original_url, j.total_episodes, j.cover_url, j.title FROM jobs j WHERE (j.worker_id IS NULL OR j.worker_id = '') AND EXISTS (SELECT 1 FROM job_episodes e WHERE e.job_id = j.job_id AND e.raw_video_url IS NOT NULL AND e.drive_link = '' AND e.worker_id IS NULL) ORDER BY j.updated_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED")
        owner_job = cursor.fetchone()
        if owner_job:
            cursor.execute("UPDATE jobs SET worker_id = %s, updated_at = NOW() WHERE job_id = %s", (worker_id, owner_job['job_id']))
            if (not owner_job.get('total_episodes') or owner_job['total_episodes'] <= 0) or (not owner_job.get('cover_url')): _queue_meta_target(owner_job['job_id'], owner_job['series_id'], owner_job.get('original_url'))
            cursor.execute("SELECT e.id, e.job_id, j.series_id, e.episode_number, e.raw_video_url, e.file_name FROM job_episodes e JOIN jobs j ON e.job_id = j.job_id WHERE e.job_id = %s AND e.raw_video_url IS NOT NULL AND e.drive_link = '' AND e.worker_id IS NULL ORDER BY e.episode_number ASC LIMIT 1 FOR UPDATE SKIP LOCKED", (owner_job['job_id'],))
            piggyback_job = cursor.fetchone()
    if piggyback_job:
        cursor.execute("UPDATE job_episodes SET worker_id = %s, updated_at = NOW() WHERE id = %s", (worker_id, piggyback_job['id']))
        conn.commit(); cursor.close(); _flush_meta_targets()
        return {"status": "has_piggyback_job", "job": {"job_id": piggyback_job["job_id"], "series_id": piggyback_job["series_id"], "episode_number": piggyback_job["episode_number"], "raw_video_url": piggyback_job["raw_video_url"], "file_name": piggyback_job["file_name"]}}
    conn.commit(); cursor.close(); _flush_meta_targets(); return {"status": "no_job"}

@app.post("/api/worker/fail_piggyback")
def worker_fail_piggyback(req: PiggybackFailReq, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if conn: cursor = conn.cursor(); cursor.execute("UPDATE job_episodes SET raw_video_url = NULL, worker_id = NULL WHERE job_id = %s AND episode_number = %s", (req.job_id, req.episode_number)); conn.commit(); cursor.close(); conn.close()
    return {"status": "ok"}

@app.get("/api/worker/all_series")
def worker_all_series(offset: int = 0, limit: int = 30, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "jobs": [], "total": 0}
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS c FROM jobs WHERE series_id IS NOT NULL AND series_id <> ''"); total = (cur.fetchone() or {}).get('c', 0)
        cur.execute("SELECT series_id, cover_url, original_url FROM jobs WHERE series_id IS NOT NULL AND series_id <> '' ORDER BY series_id ASC LIMIT %s OFFSET %s", (max(1, min(100, limit)), max(0, offset)))
        rows = cur.fetchall(); return {"status": "success", "jobs": rows, "total": total, "offset": max(0, offset), "returned": len(rows)}
    except Exception as e: return {"status": "error", "message": str(e), "jobs": [], "total": 0}
    finally: cur.close(); conn.close()

@app.get("/api/worker/cover_jobs")
def worker_cover_jobs(limit: int = 10, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "jobs": [], "total_missing": 0}
    cur = conn.cursor(dictionary=True)
    try:
        cond = "(cover_b64 IS NULL OR cover_b64 = '' OR LENGTH(cover_b64) < 500) AND series_id IS NOT NULL AND series_id <> ''"
        cur.execute(f"SELECT COUNT(*) AS c FROM jobs WHERE {cond}"); total_missing = (cur.fetchone() or {}).get('c', 0)
        cur.execute(f"SELECT series_id, cover_url, original_url FROM jobs WHERE {cond} ORDER BY updated_at DESC LIMIT %s", (max(1, min(50, limit)),))
        return {"status": "success", "jobs": cur.fetchall(), "total_missing": total_missing}
    except Exception as e: return {"status": "error", "message": str(e), "jobs": [], "total_missing": 0}
    finally: cur.close(); conn.close()

@app.post("/api/worker/submit_cover")
def worker_submit_cover(data: SubmitCoverReq, _=Depends(require_worker)):
    sid = str(data.series_id or "").strip()
    if not sid or not data.cover_b64: return {"status": "error", "message": "Thiếu dữ liệu."}
    try:
        with open(os.path.join(COVERS_DIR, f"{sid}.jpg"), 'wb') as f: f.write(_base64.b64decode(data.cover_b64))
    except: pass
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cur = conn.cursor()
    try:
        if data.cover_url: cur.execute("UPDATE jobs SET cover_b64 = %s, cover_url = %s WHERE series_id = %s", (data.cover_b64, data.cover_url, sid))
        else: cur.execute("UPDATE jobs SET cover_b64 = %s WHERE series_id = %s", (data.cover_b64, sid))
        conn.commit(); return {"status": "success"}
    except Exception as e: conn.rollback(); return {"status": "error", "message": str(e)}
    finally: cur.close(); conn.close()

@app.get("/api/worker/search_in_db")
def worker_search_in_db(keyword: str, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cur = conn.cursor(dictionary=True)
    try:
        kw = (keyword or "").strip(); out = []
        if kw:
            cur.execute("SELECT series_id, title, total_episodes, cover_url FROM jobs WHERE title LIKE %s AND series_id <> '' ORDER BY (title = %s) DESC, updated_at DESC LIMIT 20", (f"%{kw}%", kw))
            seen = set()
            for r in cur.fetchall():
                sid = str(r.get('series_id') or '')
                if not sid or sid in seen: continue
                seen.add(sid); out.append({"series_id": sid, "title": r.get('title') or '', "cover_url": r.get('cover_url') or '', "total_episodes": r.get('total_episodes') or 0})
        return {"status": "success", "data": out}
    except Exception as e: return {"status": "error", "message": str(e), "data": []}
    finally: cur.close(); conn.close()

@app.get("/api/worker/search_by_name")
def worker_search_by_name(keyword: str, _=Depends(require_worker)):
    try:
        resp = requests.post(HONGGUO_API_URL, json={"action": "hongguo-search", "partner_id": PARTNER_ID, "partner_key": PARTNER_KEY, "user_id": "worker", "key_b64": _base64.b64encode(keyword.encode("utf-8")).decode("ascii"), "limit": 15}, timeout=60)
        data = resp.json()
        if not data.get("ok"): return {"status": "error", "message": data.get("error", "Lỗi nguồn")}
        return {"status": "success", "data": [{"series_id": str(it.get("series_id", "")), "title": str(it.get("title", "")), "cover_url": str(it.get("cover", "")), "total_episodes": it.get("episode_cnt", 0)} for it in data.get("results", [])]}
    except Exception as e: return {"status": "error", "message": str(e), "data": []}

@app.post("/api/worker/register_manual_series")
def worker_register_manual_series(data: ManualSeriesReq, _=Depends(require_worker)):
    title, cover_url, total = data.title or "", data.cover_url or "", data.total_episodes or 0
    if not cover_url or not title or total <= 0:
        try:
            meta = fetch_series_metadata(data.series_id, None)
            if not title and meta.get("title"): title = meta["title"]
            if not cover_url and meta.get("cover_url"): cover_url = meta["cover_url"]
            if total <= 0 and meta.get("total_episodes"): total = meta["total_episodes"]
        except: pass
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT job_id FROM jobs WHERE series_id = %s ORDER BY job_id DESC LIMIT 1", (data.series_id,))
        row = cursor.fetchone()
        if row: job_id = row['job_id']; cursor.execute("UPDATE jobs SET title = COALESCE(NULLIF(%s,''), title), cover_url = COALESCE(NULLIF(%s,''), cover_url), total_episodes = GREATEST(total_episodes, %s), updated_at = NOW() WHERE job_id = %s", (title, cover_url, total, job_id))
        else: job_id = f"HG_MANUAL_{int(time.time())}_{data.series_id}"; cursor.execute("INSERT INTO jobs (job_id, series_id, original_url, title, cover_url, total_episodes, status) VALUES (%s, %s, %s, %s, %s, %s, 'idle')", (job_id, data.series_id, f"https://hongguoduanju.com/detail?series_id={data.series_id}", title, cover_url, total))
        conn.commit(); return {"status": "success", "job_id": job_id, "cover_url": cover_url, "title": title, "total_episodes": total}
    except Exception as e: conn.rollback(); return {"status": "error", "message": str(e)}
    finally: cursor.close(); conn.close()

@app.post("/api/admin/cover_scan/toggle")
def admin_cover_scan_toggle(_=Depends(require_admin)):
    global _cover_scan_on; _cover_scan_on = not _cover_scan_on
    if _cover_scan_on: _cover_scan_status.update({"done": 0, "fail": 0, "last": "Bắt đầu..."})
    return {"status": "ok", "on": _cover_scan_on}

@app.get("/api/admin/cover_scan/status")
def admin_cover_scan_status(_=Depends(require_admin)):
    conn = get_mysql_connection(); thieu = 0
    if conn:
        try: cur = conn.cursor(); cur.execute("SELECT COUNT(*) FROM jobs WHERE (cover_b64 IS NULL OR cover_b64 = '') AND series_id <> ''"); thieu = cur.fetchone()[0]; cur.close(); conn.close()
        except: pass
    return {"status": "ok", "on": _cover_scan_on, "thieu": thieu, **_cover_scan_status}

@app.get("/api/admin/export_idle_links")
def admin_export_idle_links(_=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return PlainTextResponse("Lỗi DB", status_code=500)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT series_id, title, total_episodes FROM jobs WHERE status = 'idle' ORDER BY updated_at DESC"); seen = set(); lines = []
        for r in cursor.fetchall():
            sid = str(r.get('series_id') or '')
            if not sid or sid in seen: continue
            seen.add(sid); lines.append(f"{(r.get('title') or '').strip() or '(chưa có tên)'} | {r.get('total_episodes') or 0} tập | https://hongguoduanju.com/detail?series_id={sid}")
        return PlainTextResponse("\n".join(lines) if lines else "(Không có)", headers={"Content-Disposition": "attachment; filename=idle_links.txt"})
    except Exception as e: return PlainTextResponse(f"Lỗi: {e}", status_code=500)
    finally: cursor.close(); conn.close()

@app.get("/api/worker/check_series_status")
def worker_check_series_status(series_id: str, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT job_id FROM jobs WHERE series_id = %s ORDER BY job_id DESC LIMIT 1", (series_id,)); row = cursor.fetchone()
        if not row: return {"status": "success", "existing_eps": [], "total_episodes": 0}
        cursor.execute("SELECT episode_number FROM job_episodes WHERE job_id = %s AND drive_link != ''", (row['job_id'],)); eps = sorted([r['episode_number'] for r in cursor.fetchall()])
        cursor.execute("SELECT total_episodes FROM jobs WHERE job_id = %s", (row['job_id'],)); tr = cursor.fetchone()
        return {"status": "success", "existing_eps": eps, "total_episodes": (tr or {}).get('total_episodes', 0)}
    except Exception as e: return {"status": "error", "message": str(e), "existing_eps": []}
    finally: cursor.close(); conn.close()

@app.get("/api/worker/get_upload_url")
def worker_get_upload_url(series_id: str, file_name: str, _=Depends(require_worker)):
    if not r2_enabled(): return {"status": "error", "message": "Chưa cấu hình R2"}
    safe_name = re.sub(r'[\\/*?:"<>|]', '', file_name)
    try: return {"status": "success", "upload_url": r2_presign_put(f"{series_id}/{safe_name}"), "key": f"r2://{series_id}/{safe_name}"}
    except Exception as e: return {"status": "error", "message": str(e)}

@app.post("/api/worker/update_scan")
def update_scan(data: ScanUpdate, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT worker_id FROM jobs WHERE job_id = %s", (data.job_id,)); job = cursor.fetchone()
    if job and job.get('worker_id'): WORKER_HEARTBEATS[job['worker_id']] = {"time": time.time(), "action": data.action}
    if data.title and data.cover_url: cursor.execute("UPDATE jobs SET total_episodes = %s, title = %s, cover_url = %s, updated_at = NOW() WHERE job_id = %s", (data.total_episodes, data.title, data.cover_url, data.job_id))
    else: cursor.execute("UPDATE jobs SET total_episodes = %s, updated_at = NOW() WHERE job_id = %s", (data.total_episodes, data.job_id))
    if data.cover_b64: cursor.execute("UPDATE jobs SET cover_b64 = %s WHERE job_id = %s", (data.cover_b64, data.job_id))
    conn.commit(); cursor.close(); conn.close(); return {"status": "ok"}

@app.post("/api/worker/update_episode")
def update_episode(data: EpisodeUpdate, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True)
    try:
        real_job_id = data.job_id
        if real_job_id == "recovery" and data.series_id:
            cursor.execute("SELECT job_id FROM jobs WHERE series_id = %s ORDER BY job_id DESC LIMIT 1", (data.series_id,)); row = cursor.fetchone()
            if row: real_job_id = row['job_id']
            else: real_job_id = f"HG_RECOVERY_{data.series_id}"; cursor.execute("INSERT IGNORE INTO jobs (job_id, series_id, total_episodes, status) VALUES (%s, %s, 0, 'completed')", (real_job_id, data.series_id))
        cursor.execute("SELECT worker_id FROM jobs WHERE job_id = %s", (real_job_id,)); job_row = cursor.fetchone()
        if job_row and job_row.get('worker_id'): WORKER_HEARTBEATS[job_row['worker_id']] = {"time": time.time(), "action": f"Up xong Tập {data.episode_number}..."}
        cursor.execute("SELECT id FROM job_episodes WHERE job_id = %s AND episode_number = %s", (real_job_id, data.episode_number))
        if cursor.fetchone(): cursor.execute("UPDATE job_episodes SET drive_link = %s, file_name = %s, raw_video_url = NULL, worker_id = NULL WHERE job_id = %s AND episode_number = %s", (data.drive_link, data.file_name, real_job_id, data.episode_number))
        else: cursor.execute("INSERT INTO job_episodes (job_id, episode_number, drive_link, file_name) VALUES (%s, %s, %s, %s)", (real_job_id, data.episode_number, data.drive_link, data.file_name))
        cursor.execute("UPDATE jobs SET updated_at = NOW() WHERE job_id = %s", (real_job_id,)); conn.commit()
    except Exception as e:
        conn.rollback()
        try: cursor.close(); conn.close()
        except: pass
        return {"status": "error", "message": f"Lỗi DB: {str(e)}"}
    try:
        cursor.execute("SELECT COUNT(*) as cnt FROM job_episodes WHERE job_id = %s AND drive_link != ''", (real_job_id,)); actual = cursor.fetchone()['cnt']
        cursor.execute("SELECT total_episodes FROM jobs WHERE job_id = %s", (real_job_id,)); job = cursor.fetchone(); expected = job['total_episodes'] if job else 0
        if expected > 0 and actual >= expected: cursor.execute("UPDATE jobs SET status = 'completed', worker_id = NULL, completed_at = COALESCE(completed_at, NOW()), updated_at = NOW() WHERE job_id = %s", (real_job_id,)); conn.commit(); trigger_hot_refresh()
    except:
        try: conn.rollback()
        except: pass
    finally:
        try: cursor.close(); conn.close()
        except: pass
    return {"status": "ok"}

@app.post("/api/worker/complete_job")
def worker_complete_job(req: CompleteJobReq, _=Depends(require_worker)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True); new_status = "partial"
    try:
        cursor.execute("SELECT COUNT(*) as cnt FROM job_episodes WHERE job_id = %s AND drive_link != ''", (req.job_id,)); actual = cursor.fetchone()['cnt']
        cursor.execute("SELECT total_episodes FROM jobs WHERE job_id = %s", (req.job_id,)); job = cursor.fetchone(); expected = job['total_episodes'] if job else 0
        new_status = "completed" if expected > 0 and actual >= expected else "partial"
        if new_status == "completed": cursor.execute("UPDATE jobs SET status = %s, worker_id = NULL, completed_at = COALESCE(completed_at, NOW()), updated_at = NOW() WHERE job_id = %s", (new_status, req.job_id)); trigger_hot_refresh()
        else: cursor.execute("UPDATE jobs SET status = %s, worker_id = NULL, updated_at = NOW() WHERE job_id = %s", (new_status, req.job_id))
        conn.commit()
    except: conn.rollback()
    finally: cursor.close(); conn.close()
    return {"status": "ok", "job_status": new_status}

@app.post("/api/worker/verify_total")
def worker_verify_total(data: VerifyTotalReq, _=Depends(require_worker)):
    web_total = 0
    try: web_total = get_real_web_total(requests.get(f"https://hongguoduanju.com/detail?series_id={data.series_id}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept-Encoding": "gzip, deflate"}, timeout=15.0).text)
    except: pass
    conn = get_mysql_connection()
    if web_total > 0:
        if conn: cursor = conn.cursor(); cursor.execute("UPDATE jobs SET total_episodes = %s, updated_at = NOW() WHERE job_id = %s", (web_total, data.job_id)); conn.commit(); conn.close()
        return {"action": "done" if data.current_count >= web_total else "continue", "total": web_total}
    else:
        if conn: cursor = conn.cursor(); cursor.execute("UPDATE jobs SET total_episodes = %s, updated_at = NOW() WHERE job_id = %s", (data.current_count, data.job_id)); conn.commit(); conn.close()
        return {"action": "accept", "total": data.current_count}

@app.post("/api/admin/login")
def admin_login(req: AdminLoginReq, request: Request):
    if not rate_limiter.is_allowed(f"admin_login:{get_client_ip(request)}", 50, 300): raise HTTPException(429, "Quá nhanh, chờ 5 phút.")
    if req.password == ADMIN_PASSWORD: return {"status": "success", "token": create_admin_token()}
    raise HTTPException(401, "Sai pass!")

@app.get("/api/admin/maintenance_status")
def get_maintenance_status(_=Depends(require_admin)): return {"maintenance": MAINTENANCE_MODE}
@app.post("/api/admin/toggle_maintenance")
def toggle_maintenance(req: MaintenanceReq, _=Depends(require_admin)):
    global MAINTENANCE_MODE; MAINTENANCE_MODE = req.enabled; return {"status": "success", "message": "ĐÃ BẬT" if req.enabled else "ĐÃ TẮT", "maintenance": MAINTENANCE_MODE}

@app.get("/api/admin/stats")
def get_admin_stats(_=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT expiry_date, balance_hongguo, platform FROM users"); users = cursor.fetchall(); conn.close(); now = datetime.datetime.now(); v=0; n=0
    for u in users:
        is_vip = False
        if u["expiry_date"]:
            try:
                if datetime.datetime.strptime(u["expiry_date"], "%Y-%m-%d %H:%M:%S") > now: is_vip = True
            except: pass
        if is_vip: v += 1
        else: n += 1
    return {"total_users": len(users), "honggou_users": sum(1 for u in users if u["platform"] == "honggou"), "douyin_users": sum(1 for u in users if u["platform"] == "douyin"), "allinone_users": sum(1 for u in users if u["platform"] == "allinone"), "vip_count": v, "normal_count": n, "total_balance_hongguo": sum((u["balance_hongguo"] or 0) for u in users)}

@app.get("/api/admin/users")
def get_all_users(platform: str = Query(default=None), _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor()
    if platform: cursor.execute("SELECT username, zalo, hwid, expiry_date, balance_hongguo, platform, vip_unlocked, created_at, vip_unlocked_at FROM users WHERE platform = ? ORDER BY expiry_date DESC", (platform,))
    else: cursor.execute("SELECT username, zalo, hwid, expiry_date, balance_hongguo, platform, vip_unlocked, created_at, vip_unlocked_at FROM users ORDER BY expiry_date DESC")
    users = [dict(row) for row in cursor.fetchall()]; conn.close(); return users

@app.post("/api/admin/users/{username}/toggle_vip")
def admin_toggle_vip(username: str, platform: str = Query(default="honggou"), _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT vip_unlocked FROM users WHERE username = ? AND platform = ?", (username, platform)); row = cursor.fetchone()
    if not row: conn.close(); return {"status": "error", "message": "Không tồn tại!"}
    new_val = 0 if bool(row["vip_unlocked"]) else 1
    if new_val:
        cursor.execute("UPDATE users SET vip_unlocked = ?, vip_unlocked_at = ? WHERE username = ? AND platform = ?", (new_val, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username, platform))
    else:
        cursor.execute("UPDATE users SET vip_unlocked = ?, vip_unlocked_at = NULL WHERE username = ? AND platform = ?", (new_val, username, platform))
    conn.commit(); conn.close()
    return {"status": "success", "vip_unlocked": bool(new_val), "message": ("Đã MỞ KHÓA VIP!" if new_val else "Đã KHÓA lại (chỉ dùng kho).")}

@app.get("/api/admin/users/daily_stats")
def admin_users_daily_stats(platform: str = Query(default="honggou"), days: int = Query(default=7), _=Depends(require_admin)):
    days = max(1, min(int(days or 7), 31))
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT date(created_at) AS d, COUNT(*) AS c FROM users WHERE platform = ? AND created_at IS NOT NULL GROUP BY date(created_at)", (platform,))
    created_map = {r["d"]: r["c"] for r in cursor.fetchall()}
    cursor.execute("SELECT date(vip_unlocked_at) AS d, COUNT(*) AS c FROM users WHERE platform = ? AND vip_unlocked = 1 AND vip_unlocked_at IS NOT NULL GROUP BY date(vip_unlocked_at)", (platform,))
    unlocked_map = {r["d"]: r["c"] for r in cursor.fetchall()}
    month_str = datetime.datetime.now().strftime("%Y-%m")
    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE platform = ? AND created_at IS NOT NULL AND strftime('%Y-%m', created_at) = ?", (platform, month_str))
    month_created = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE platform = ? AND vip_unlocked = 1 AND vip_unlocked_at IS NOT NULL AND strftime('%Y-%m', vip_unlocked_at) = ?", (platform, month_str))
    month_unlocked = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE platform = ?", (platform,))
    total_users = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE platform = ? AND vip_unlocked = 1", (platform,))
    total_unlocked = cursor.fetchone()["c"]
    conn.close()

    today = datetime.datetime.now().date()
    daily = []
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        daily.append({
            "date": ds,
            "label": d.strftime("%d/%m"),
            "created": created_map.get(ds, 0),
            "unlocked": unlocked_map.get(ds, 0),
        })
    return {
        "status": "success",
        "daily": daily,
        "month": {
            "label": datetime.datetime.now().strftime("%m/%Y"),
            "created": month_created,
            "unlocked": month_unlocked,
        },
        "total_users": total_users,
        "total_unlocked": total_unlocked,
        "total_locked": total_users - total_unlocked,
    }

@app.get("/api/admin/users/by_date")
def admin_users_by_date(date: str = Query(...), platform: str = Query(default="honggou"), _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT username, expiry_date, vip_unlocked, created_at, vip_unlocked_at FROM users WHERE platform = ? AND date(created_at) = ? ORDER BY created_at DESC", (platform, date))
    created = [dict(r) for r in cursor.fetchall()]
    cursor.execute("SELECT username, expiry_date, vip_unlocked, created_at, vip_unlocked_at FROM users WHERE platform = ? AND vip_unlocked = 1 AND date(vip_unlocked_at) = ? ORDER BY vip_unlocked_at DESC", (platform, date))
    unlocked = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"status": "success", "date": date, "created": created, "unlocked": unlocked,
            "created_count": len(created), "unlocked_count": len(unlocked)}

@app.post("/api/admin/users/{username}/add_balance_hongguo")
def admin_add_balance(username: str, req: TopupReq, _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("UPDATE users SET balance_hongguo = balance_hongguo + ? WHERE username = ? AND platform = 'honggou'", (req.amount, username)); conn.commit(); conn.close(); return {"status": "success"}

@app.post("/api/admin/users/{username}/deduct_balance_hongguo")
def admin_deduct_balance(username: str, req: TopupReq, _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT balance_hongguo FROM users WHERE username = ? AND platform = 'honggou'", (username,)); row = cursor.fetchone()
    if not row: conn.close(); return {"status": "error", "message": "Không tồn tại!"}
    nb = max(0, (row["balance_hongguo"] or 0) - req.amount)
    cursor.execute("UPDATE users SET balance_hongguo = ? WHERE username = ? AND platform = 'honggou'", (nb, username)); conn.commit(); conn.close()
    return {"status": "success", "message": f"Còn {nb}đ."}

@app.post("/api/admin/users/create_hongguo")
def admin_create_user(req: AdminCreateUserReq, _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); uname = (req.username or "").strip()
    if not uname: conn.close(); return {"status": "error", "message": "Thiếu username."}
    cursor.execute("SELECT username FROM users WHERE username = ? AND platform = 'honggou'", (uname,))
    if cursor.fetchone(): conn.close(); return {"status": "error", "message": "Đã tồn tại!"}
    try:
        data = requests.post(HONGGUO_API_URL, json={"action": "sub-renew", "partner_id": PARTNER_ID, "partner_key": PARTNER_KEY, "user_id": uname, "days": req.days or 30}, timeout=60).json()
        if not data.get("ok"): conn.close(); return {"status": "error", "message": f"API Lỗi: {data.get('error')}"}
    except Exception as e: conn.close(); return {"status": "error", "message": str(e)}
    cursor.execute("INSERT INTO users (username, password, zalo, hwid, expiry_date, balance_hongguo, platform, created_at) VALUES (?, ?, ?, ?, ?, ?, 'honggou', ?)", (uname, hash_password(req.password or "123456"), req.zalo or "", "", (datetime.datetime.now() + datetime.timedelta(days=req.days or 30)).strftime("%Y-%m-%d %H:%M:%S"), req.balance or 0, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))); conn.commit(); conn.close()
    return {"status": "success", "message": "Đã tạo!"}

@app.post("/api/admin/users/{username}/add_vip")
def admin_add_vip(username: str, req: AddVipReq, platform: str = Query(default="honggou"), _=Depends(require_admin)):
    if platform == "honggou":
        try:
            data = requests.post(HONGGUO_API_URL, json={"action": "sub-renew", "partner_id": PARTNER_ID, "partner_key": PARTNER_KEY, "user_id": username, "days": req.days}, timeout=60).json()
            if not data.get("ok"): return {"status": "error", "message": data.get('error')}
        except Exception as e: return {"status": "error", "message": str(e)}
    conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT expiry_date FROM users WHERE username = ? AND platform = ?", (username, platform)); user = cursor.fetchone()
    if user and user["expiry_date"]:
        try: cursor.execute("UPDATE users SET expiry_date = ? WHERE username = ? AND platform = ?", ((max(datetime.datetime.now(), datetime.datetime.strptime(user["expiry_date"], "%Y-%m-%d %H:%M:%S")) + datetime.timedelta(days=req.days)).strftime("%Y-%m-%d %H:%M:%S"), username, platform)); conn.commit()
        except: pass
    conn.close(); return {"status": "success"}

@app.get("/api/admin/users/{username}/quota")
def check_user_quota(username: str, _=Depends(require_admin)):
    try:
        payload = {"action": "sub-status", "partner_id": PARTNER_ID, "partner_key": PARTNER_KEY, "user_id": username}
        r = requests.post(HONGGUO_API_URL, json=payload, timeout=10)
        data = r.json()
        if data.get("ok"):
            q = data.get("quota", {})
            return {"status": "success", "left": q.get("left", 0), "limit": q.get("limit", 0)}
        return {"status": "error", "message": data.get("error", "Lỗi API Nguồn")}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/admin/users/{username}/reset_hwid")
def reset_hwid(username: str, platform: str = Query(default="honggou"), _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("UPDATE users SET hwid = '' WHERE username = ? AND platform = ?", (username, platform)); conn.commit(); conn.close(); return {"status": "success"}
@app.post("/api/admin/users/{username}/reset_password")
def reset_password(username: str, platform: str = Query(default="honggou"), _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("UPDATE users SET password = ? WHERE username = ? AND platform = ?", (hash_password("123456"), username, platform)); conn.commit(); conn.close(); return {"status": "success", "message": "Về 123456."}
@app.post("/api/admin/users/{username}/delete")
def delete_user(username: str, platform: str = Query(default="honggou"), _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("DELETE FROM users WHERE username = ? AND platform = ?", (username, platform)); conn.commit(); conn.close(); return {"status": "success"}

# ==========================================
# ADMIN BOOM STORY — TÀI KHOẢN RIÊNG
# ==========================================
@app.get("/api/admin/boomstory/accounts")
def admin_list_boomstory_accounts(_=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT username, note, hwid, expiry_date, active, created_at, last_login FROM boom_users ORDER BY created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close(); return rows

@app.post("/api/admin/boomstory/accounts")
def admin_create_boomstory_account(req: BoomCreateAccountReq, _=Depends(require_admin)):
    username = (req.username or "").strip()
    password = req.password or ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}", username):
        return {"status": "error", "message": "Username cần 3-50 ký tự: chữ, số, dấu chấm, gạch ngang hoặc gạch dưới."}
    if len(password) < 6:
        return {"status": "error", "message": "Mật khẩu phải có ít nhất 6 ký tự."}
    days = max(1, min(3650, int(req.days or 30)))
    now = datetime.datetime.now(); expiry = (now + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT username FROM boom_users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close(); return {"status": "error", "message": "Tài khoản BOOM Story đã tồn tại!"}
    cursor.execute(
        "INSERT INTO boom_users (username, password, note, hwid, expiry_date, active, created_at) VALUES (?, ?, ?, '', ?, 1, ?)",
        (username, hash_password(password), (req.note or "")[:300], expiry, now.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit(); conn.close()
    return {"status": "success", "message": "Đã tạo tài khoản BOOM Story!", "username": username, "expiry": expiry}

@app.post("/api/admin/boomstory/accounts/{username}/add_days")
def admin_add_boomstory_days(username: str, req: BoomAddDaysReq, _=Depends(require_admin)):
    days = max(1, min(3650, int(req.days or 30)))
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT expiry_date FROM boom_users WHERE username = ?", (username,)); row = cursor.fetchone()
    if not row:
        conn.close(); return {"status": "error", "message": "Không tìm thấy tài khoản BOOM Story."}
    base = datetime.datetime.now()
    if row["expiry_date"]:
        try: base = max(base, datetime.datetime.strptime(row["expiry_date"], "%Y-%m-%d %H:%M:%S"))
        except Exception: pass
    expiry = (base + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE boom_users SET expiry_date = ? WHERE username = ?", (expiry, username))
    conn.commit(); conn.close(); return {"status": "success", "message": "Đã cộng ngày!", "expiry": expiry}

@app.post("/api/admin/boomstory/accounts/{username}/password")
def admin_change_boomstory_password(username: str, req: BoomChangePasswordReq, _=Depends(require_admin)):
    if len(req.password or "") < 6:
        return {"status": "error", "message": "Mật khẩu phải có ít nhất 6 ký tự."}
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE boom_users SET password = ? WHERE username = ?", (hash_password(req.password), username)); changed = cursor.rowcount
    conn.commit(); conn.close()
    return {"status": "success" if changed else "error", "message": "Đã đổi mật khẩu!" if changed else "Không tìm thấy tài khoản."}

@app.post("/api/admin/boomstory/accounts/{username}/toggle")
def admin_toggle_boomstory_account(username: str, _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT active FROM boom_users WHERE username = ?", (username,)); row = cursor.fetchone()
    if not row:
        conn.close(); return {"status": "error", "message": "Không tìm thấy tài khoản."}
    active = 0 if bool(row["active"]) else 1
    cursor.execute("UPDATE boom_users SET active = ? WHERE username = ?", (active, username))
    conn.commit(); conn.close(); return {"status": "success", "active": bool(active)}

@app.post("/api/admin/boomstory/accounts/{username}/delete")
def admin_delete_boomstory_account(username: str, _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("DELETE FROM boom_users WHERE username = ?", (username,)); changed = cursor.rowcount
    conn.commit(); conn.close(); return {"status": "success" if changed else "error"}

# ==========================================
# ADMIN BOOM REVIEW — TÀI KHOẢN RIÊNG
# ==========================================
@app.get("/api/admin/boomreview/accounts")
def admin_list_boomreview_accounts(_=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT username, note, expiry_date, active, created_at, last_login FROM boom_review_users ORDER BY created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close(); return rows

@app.post("/api/admin/boomreview/accounts")
def admin_create_boomreview_account(req: BoomCreateAccountReq, _=Depends(require_admin)):
    username = (req.username or "").strip()
    password = req.password or ""
    if not username or not password:
        return {"status": "error", "message": "Thiếu username hoặc password."}
    if len(password) < 6:
        return {"status": "error", "message": "Mật khẩu phải có ít nhất 6 ký tự."}
    days = max(1, min(3650, int(req.days or 30)))
    now = datetime.datetime.now(); expiry = (now + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT username FROM boom_review_users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close(); return {"status": "error", "message": "Tài khoản BOOM Review đã tồn tại!"}
    cursor.execute(
        "INSERT INTO boom_review_users (username, password, note, expiry_date, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
        (username, hash_password(password), (req.note or "")[:300], expiry, now.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit(); conn.close()
    return {"status": "success", "message": "Đã tạo tài khoản BOOM Review!", "username": username, "expiry": expiry}

@app.post("/api/admin/boomreview/accounts/{username}/add_days")
def admin_add_boomreview_days(username: str, req: BoomAddDaysReq, _=Depends(require_admin)):
    days = max(1, min(3650, int(req.days or 30)))
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT expiry_date FROM boom_review_users WHERE username = ?", (username,)); row = cursor.fetchone()
    if not row:
        conn.close(); return {"status": "error", "message": "Không tìm thấy tài khoản BOOM Review."}
    base = datetime.datetime.now()
    if row["expiry_date"]:
        try:
            p = datetime.datetime.strptime(row["expiry_date"], "%Y-%m-%d %H:%M:%S")
            if p > base: base = p
        except Exception: pass
    expiry = (base + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE boom_review_users SET expiry_date = ? WHERE username = ?", (expiry, username))
    conn.commit(); conn.close(); return {"status": "success", "message": "Đã cộng ngày!", "expiry": expiry}

@app.post("/api/admin/boomreview/accounts/{username}/password")
def admin_change_boomreview_password(username: str, req: BoomChangePasswordReq, _=Depends(require_admin)):
    if len(req.password or "") < 6:
        return {"status": "error", "message": "Mật khẩu phải có ít nhất 6 ký tự."}
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE boom_review_users SET password = ? WHERE username = ?", (hash_password(req.password), username)); changed = cursor.rowcount
    conn.commit(); conn.close()
    return {"status": "success" if changed else "error", "message": "Đã đổi mật khẩu!" if changed else "Không tìm thấy tài khoản."}

@app.post("/api/admin/boomreview/accounts/{username}/toggle")
def admin_toggle_boomreview_account(username: str, _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT active FROM boom_review_users WHERE username = ?", (username,)); row = cursor.fetchone()
    if not row:
        conn.close(); return {"status": "error", "message": "Không tìm thấy tài khoản."}
    active = 0 if bool(row["active"]) else 1
    cursor.execute("UPDATE boom_review_users SET active = ? WHERE username = ?", (active, username))
    conn.commit(); conn.close(); return {"status": "success", "active": bool(active)}

@app.post("/api/admin/boomreview/accounts/{username}/delete")
def admin_delete_boomreview_account(username: str, _=Depends(require_admin)):
    conn = get_db(); cursor = conn.cursor(); cursor.execute("DELETE FROM boom_review_users WHERE username = ?", (username,)); changed = cursor.rowcount
    conn.commit(); conn.close(); return {"status": "success" if changed else "error"}

@app.get("/api/admin/workers")
def admin_get_workers(_=Depends(require_admin)):
    now = time.time(); workers = {}
    for wid, wdata in WORKER_HEARTBEATS.items(): workers[wid] = {"worker_id": wid, "last_seen": wdata["time"] if isinstance(wdata, dict) else wdata, "ago_seconds": int(now - (wdata["time"] if isinstance(wdata, dict) else wdata)), "job": None, "status": "idle", "blocked": False, "action": wdata.get("action", "") if isinstance(wdata, dict) else ""}
    conn = get_mysql_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT j.worker_id, j.job_id, j.series_id, j.title, j.total_episodes, COUNT(e.id) as has_link FROM jobs j LEFT JOIN job_episodes e ON j.job_id = e.job_id AND e.drive_link != '' WHERE j.status = 'processing' AND j.worker_id IS NOT NULL GROUP BY j.job_id")
        for r in cursor.fetchall():
            wid = r['worker_id']
            if wid not in workers: workers[wid] = {"worker_id": wid, "last_seen": 0, "ago_seconds": 999999, "job": None, "status": "offline", "blocked": False, "action": ""}
            workers[wid]["job"] = {"job_id": r["job_id"], "series_id": r["series_id"], "title": r["title"] or r["series_id"], "progress": f"{r['has_link']}/{r['total_episodes']}"}
        cursor.execute("SELECT e.worker_id, e.job_id, e.episode_number, j.series_id, j.title FROM job_episodes e JOIN jobs j ON e.job_id = j.job_id WHERE e.worker_id IS NOT NULL")
        for r in cursor.fetchall():
            wid = r['worker_id']
            if wid not in workers: workers[wid] = {"worker_id": wid, "last_seen": 0, "ago_seconds": 999999, "job": None, "status": "offline", "blocked": False, "action": ""}
            workers[wid]["job"] = {"job_id": r["job_id"], "series_id": r["series_id"], "title": f"[KÉ] {r['title'] or r['series_id']}", "progress": f"Tập {r['episode_number']}"}
        cursor.execute("SELECT worker_id FROM worker_blacklist")
        for r in cursor.fetchall():
            wid = r['worker_id']
            if wid not in workers: workers[wid] = {"worker_id": wid, "last_seen": 0, "ago_seconds": 999999, "job": None, "status": "blocked", "blocked": True, "action": ""}
            else: workers[wid]["blocked"] = True
        cursor.close(); conn.close()
    for w in workers.values():
        if w["blocked"]: w["status"] = "blocked"
        elif w["job"]: w["status"] = "working" if w["ago_seconds"] < WORKER_TIMEOUT else "stuck"
        elif w["ago_seconds"] < WORKER_TIMEOUT: w["status"] = "idle"
        else: w["status"] = "offline"
    return sorted(workers.values(), key=lambda x: x["ago_seconds"])

@app.get("/api/admin/online_clients")
def admin_online_clients(_=Depends(require_admin)):
    now = time.time(); online = []
    for u, i in list(CLIENT_HEARTBEATS.items()):
        ago = int(now - i["time"])
        if ago > CLIENT_TIMEOUT: continue
        entry = {"username": u, "platform": i.get("platform", ""), "ago_seconds": ago, "action": i.get("action", ""), "series_id": i.get("series_id", ""), "current_job_id": i.get("current_job_id", ""), "movie_title": ""}
        if i.get("series_id"):
            try:
                conn = get_mysql_connection()
                if conn: cursor = conn.cursor(dictionary=True); cursor.execute("SELECT title FROM jobs WHERE series_id = %s LIMIT 1", (i["series_id"],)); row = cursor.fetchone(); entry["movie_title"] = row["title"] if row else ""; cursor.close(); conn.close()
            except: pass
        online.append(entry)
    return sorted(online, key=lambda x: x["ago_seconds"])

@app.get("/api/admin/movie_stats")
def admin_movie_stats(_=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"today": 0, "yesterday": 0, "this_week": 0, "total": 0}
    cursor = conn.cursor(dictionary=True); cursor.execute("SELECT SUM(CASE WHEN DATE(completed_at) = CURDATE() THEN 1 ELSE 0 END) as today, SUM(CASE WHEN DATE(completed_at) = CURDATE() - INTERVAL 1 DAY THEN 1 ELSE 0 END) as yesterday, SUM(CASE WHEN completed_at >= CURDATE() - INTERVAL (WEEKDAY(CURDATE())) DAY THEN 1 ELSE 0 END) as this_week, COUNT(*) as total FROM jobs WHERE status = 'completed' AND completed_at IS NOT NULL"); row = cursor.fetchone(); cursor.close(); conn.close()
    return {"today": int(row["today"] or 0), "yesterday": int(row["yesterday"] or 0), "this_week": int(row["this_week"] or 0), "total": int(row["total"] or 0)}

@app.post("/api/admin/workers/{worker_id}/stop")
def admin_stop_worker(worker_id: str, _=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(); cursor.execute("INSERT IGNORE INTO worker_blacklist (worker_id) VALUES (%s)", (worker_id,)); cursor.execute("UPDATE jobs SET status = 'pending', worker_id = NULL, updated_at = NOW() WHERE worker_id = %s AND status = 'processing'", (worker_id,)); cursor.execute("UPDATE job_episodes SET worker_id = NULL WHERE worker_id = %s", (worker_id,)); conn.commit(); conn.close(); return {"status": "ok"}
@app.post("/api/admin/workers/{worker_id}/activate")
def admin_activate_worker(worker_id: str, _=Depends(require_admin)):
    conn = get_mysql_connection(); cursor = conn.cursor(); cursor.execute("DELETE FROM worker_blacklist WHERE worker_id = %s", (worker_id,)); conn.commit(); conn.close(); return {"status": "ok"}
@app.post("/api/admin/workers/{worker_id}/reset")
def admin_reset_worker(worker_id: str, _=Depends(require_admin)):
    WATCHDOG_COMMANDS[worker_id] = "reset"; conn = get_mysql_connection()
    if conn: cursor = conn.cursor(); cursor.execute("UPDATE jobs SET status = 'pending', worker_id = NULL, updated_at = NOW() WHERE worker_id = %s AND status = 'processing'", (worker_id,)); cursor.execute("UPDATE job_episodes SET worker_id = NULL WHERE worker_id = %s", (worker_id,)); conn.commit(); conn.close()
    return {"status": "ok"}

@app.post("/api/admin/publish_update")
def admin_publish_update(req: PublishUpdateReq, _=Depends(require_admin)):
    _save_update_info({"latest_version": req.latest_version, "download_url": req.download_url, "changelog": req.changelog, "force_update": req.force_update}); return {"status": "success"}

@app.get("/api/admin/update_info")
def admin_get_update_info(_=Depends(require_admin)):
    """Xem thông tin bản cập nhật hiện tại (version, có đang ép hay không)."""
    return _load_update_info()

@app.post("/api/admin/set_force_update")
def admin_set_force_update(force: bool = Query(...), _=Depends(require_admin)):
    """Bật/tắt CHẾ ĐỘ ÉP cập nhật cho bản hiện có, KHÔNG cần build lại.
    Quy trình an toàn: push bản mới (force=false) -> tự test kỹ -> khi chắc
    chắn ổn mới gọi endpoint này với force=true để đẩy cho toàn bộ khách."""
    info = _load_update_info()
    info["force_update"] = bool(force)
    _save_update_info(info)
    return {"status": "success", "force_update": bool(force),
            "latest_version": info.get("latest_version", "")}

@app.get("/api/admin/honggou_movies")
def admin_honggou_movies(_=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return []
    cursor = conn.cursor(dictionary=True); cursor.execute("SELECT j.job_id, j.series_id, j.total_episodes, j.status, j.worker_id, j.updated_at, j.title, j.genres, COUNT(e.id) as db_episode_count, SUM(CASE WHEN e.drive_link != '' THEN 1 ELSE 0 END) as db_has_link FROM jobs j LEFT JOIN job_episodes e ON j.job_id = e.job_id GROUP BY j.job_id ORDER BY j.updated_at DESC"); movies = cursor.fetchall()
    for m in movies: m['updated_at'] = str(m['updated_at']) if m.get('updated_at') else None
    cursor.close(); conn.close(); return movies

@app.post("/api/admin/delete_movie/{job_id}")
def admin_delete_movie(job_id: str, bg_tasks: BackgroundTasks, _=Depends(require_admin)):
    def task():
        conn = get_mysql_connection()
        if conn:
            try: cursor = conn.cursor(); cursor.execute("DELETE FROM job_episodes WHERE job_id = %s", (job_id,)); cursor.execute("DELETE FROM jobs WHERE job_id = %s", (job_id,)); conn.commit(); cursor.close()
            except: conn.rollback()
            finally: conn.close()
    bg_tasks.add_task(task); return {"status": "success", "message": "Đang dọn..."}

@app.post("/api/admin/delete_all_pending")
def admin_del_pend(bg_tasks: BackgroundTasks, _=Depends(require_admin)):
    def task():
        conn = get_mysql_connection()
        if conn:
            try: cursor = conn.cursor(); cursor.execute("DELETE e FROM job_episodes e INNER JOIN jobs j ON e.job_id = j.job_id WHERE j.status = 'pending'"); cursor.execute("DELETE FROM jobs WHERE status = 'pending'"); conn.commit(); cursor.close()
            except: conn.rollback()
            finally: conn.close()
    bg_tasks.add_task(task); return {"status": "success", "message": "Đang dọn..."}

@app.post("/api/admin/delete_all_idle")
def admin_del_idle(_=Depends(require_admin)):
    # Xoa cac phim 'idle': chua co worker, chua co tap nao co drive_link.
    # KHONG dung background task de tra ve so luong da xoa cho admin biet.
    # Chi xoa job KHONG co bat ky drive_link nao -> tuyet doi khong dung vao
    # phim da tai duoc tap (an toan hon nut 'Xoa het Pending').
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "message": "DB chua san sang."}
    cursor = conn.cursor()
    try:
        # Chi lay job idle & khong co link -> danh sach job_id can xoa.
        cursor.execute("""
            SELECT j.job_id FROM jobs j
            WHERE j.status = 'idle'
              AND NOT EXISTS (
                  SELECT 1 FROM job_episodes e
                  WHERE e.job_id = j.job_id AND e.drive_link <> ''
              )
        """)
        ids = [r[0] for r in cursor.fetchall()]
        if not ids:
            cursor.close(); conn.close()
            return {"status": "success", "deleted": 0, "message": "Khong co phim Idle nao de xoa."}
        # Xoa theo lo 500 job_id/lan -> tranh cau IN() qua dai & giu lock lau.
        deleted = 0
        for i in range(0, len(ids), 500):
            batch = ids[i:i+500]
            ph = ",".join(["%s"] * len(batch))
            cursor.execute(f"DELETE FROM job_episodes WHERE job_id IN ({ph})", tuple(batch))
            cursor.execute(f"DELETE FROM jobs WHERE job_id IN ({ph})", tuple(batch))
            conn.commit()
            deleted += len(batch)
        cursor.close()
        return {"status": "success", "deleted": deleted, "message": f"Da xoa {deleted} phim Idle (chua co link)."}
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return {"status": "error", "message": str(e)}
    finally:
        try: conn.close()
        except Exception: pass

@app.post("/api/admin/purge_fake_movies")
def admin_purge(_=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True); cursor.execute("SELECT j.job_id, j.series_id, j.total_episodes, COUNT(e.id) as has_link FROM jobs j LEFT JOIN job_episodes e ON j.job_id = e.job_id AND e.drive_link != '' WHERE j.total_episodes > 0 GROUP BY j.job_id HAVING has_link >= j.total_episodes"); movies = cursor.fetchall(); d=0; det=[]
    for m in movies:
        sid, tot = m['series_id'], m['total_episodes']; dr = verify_storage_episodes(sid); dc = len(dr) if dr else 0
        if dc < tot: cursor.execute("DELETE FROM jobs WHERE job_id = %s", (m['job_id'],)); d+=1; det.append(f"Xóa {sid} (DB: {tot}, R2: {dc})")
    conn.commit(); cursor.close(); conn.close()
    if not det: det.append("Không có phim ảo!")
    return {"status": "success", "deleted": d, "details": det}

@app.post("/api/admin/fetch_hot_movies")
async def admin_fetch_hot_movies(pages: int = 1, _=Depends(require_admin)):
    # ĐÃ GỠ BỎ: tính năng cào phim hot qua API 52api (hg_new_top). Không dùng nữa.
    return {"status": "error", "message": "Tính năng cào phim hot đã bị gỡ bỏ."}

@app.post("/api/admin/heal_metadata")
async def admin_heal_metadata(_=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True); cursor.execute("SELECT job_id, series_id, title, cover_url, genres, total_episodes FROM jobs"); jobs = cursor.fetchall(); h=0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for job in jobs:
            try:
                html = (await client.get(f"https://hongguoduanju.com/detail?series_id={job['series_id']}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept-Encoding": "gzip, deflate"})).text; rt=""; rc=""
                jm = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*;?\s*</script>', html, re.DOTALL)
                if jm:
                    try: det = json.loads(jm.group(1)).get("loaderData", {}).get("detail_page", {}).get("seriesDetail", {}); rt = det.get("series_name", ""); rc = det.get("series_cover", "") or det.get("cover_url", "")
                    except: pass
                if not rt:
                    tm = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</h1>', html)
                    if tm: rt = tm.group(1).strip()
                if not rc:
                    cm = re.search(r'<img[^>]*class="arco-image-img"[^>]*src="([^"]+)"', html)
                    if cm: rc = cm.group(1)
                rg = extract_and_translate_genres(html); ft = rt or job['title']; fc = rc or job['cover_url']; fg = rg; og = str(job.get('genres') or "")
                if not fg:
                    cg = re.sub(r'[\u4e00-\u9fff]+', '', re.sub(r'<[^>]+>', '', og).replace('>', '').replace('Chưa có', ''))
                    fg = ", ".join([p.strip() for p in cg.split(',') if p.strip()])
                bt = [t for t in ["BXH Đề Cử", "BXH Lượt Xem", "BXH Phim Mới", "BXH Hoạt Hình", "Lịch Phim"] if t in og]
                if bt:
                    bs = ", ".join(bt)
                    if fg:
                        for t in bt: fg = fg.replace(t, "").replace(", ,", ",").strip(", ")
                        fg = bs + (", " + fg if fg else "")
                    else: fg = bs
                wt = get_real_web_total(html)
                if wt > 0 and job['total_episodes'] != wt: cursor.execute("UPDATE jobs SET title = %s, cover_url = %s, genres = %s, total_episodes = %s WHERE job_id = %s", (ft, fc, fg or "", wt, job['job_id']))
                else: cursor.execute("UPDATE jobs SET title = %s, cover_url = %s, genres = %s WHERE job_id = %s", (ft, fc, fg or "", job['job_id']))
                h+=1
            except: pass
            await asyncio.sleep(0.5)
    conn.commit(); cursor.close(); conn.close(); return {"status": "ok", "message": f"Đã chữa {h} phim."}

@app.post("/api/admin/repair_movies")
async def admin_repair_movies(_=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True); f=0
    cursor.execute("DELETE e1 FROM job_episodes e1 INNER JOIN job_episodes e2 ON e1.job_id = e2.job_id AND e1.episode_number = e2.episode_number AND e1.id < e2.id"); cursor.execute("DELETE FROM job_episodes WHERE drive_link = ''"); cursor.execute("DELETE e FROM job_episodes e INNER JOIN jobs j ON e.job_id = j.job_id WHERE j.total_episodes > 0 AND e.episode_number > j.total_episodes"); cursor.execute("SELECT job_id, series_id, total_episodes, original_url FROM jobs WHERE total_episodes >= 100 OR total_episodes = 0")
    async with httpx.AsyncClient(timeout=10.0) as client:
        for m in cursor.fetchall():
            try:
                rt = get_real_web_total((await client.get(m.get("original_url") or f"https://hongguoduanju.com/detail?series_id={m['series_id']}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept-Encoding": "gzip, deflate"})).text)
                if rt > 0 and rt != m['total_episodes']: cursor.execute("UPDATE jobs SET total_episodes = %s WHERE job_id = %s", (rt, m['job_id']))
            except: pass
    cursor.execute("SELECT j.job_id, j.series_id, j.total_episodes, COUNT(e.id) as has_link FROM jobs j LEFT JOIN job_episodes e ON j.job_id = e.job_id AND e.drive_link != '' WHERE j.status = 'completed' AND j.total_episodes > 0 GROUP BY j.job_id HAVING has_link < j.total_episodes")
    for m in cursor.fetchall(): cursor.execute("UPDATE jobs SET status = 'pending', worker_id = NULL, updated_at = NOW() WHERE job_id = %s", (m['job_id'],)); f+=1
    cursor.execute("UPDATE jobs SET status = 'pending', worker_id = NULL, updated_at = NOW() WHERE status = 'completed' AND total_episodes = 0"); conn.commit(); conn.close(); return {"status": "ok", "fixed": f, "details": []}

@app.post("/api/admin/check_fix_all")
async def admin_check_fix_all(_=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor(dictionary=True); cursor.execute("SELECT j.job_id, j.series_id, j.total_episodes, j.status, j.original_url, COUNT(e.id) as has_link FROM jobs j LEFT JOIN job_episodes e ON j.job_id = e.job_id AND e.drive_link != '' GROUP BY j.job_id"); am = cursor.fetchall(); f=0; det=[]; tc=len(am)
    cursor.execute("DELETE e1 FROM job_episodes e1 INNER JOIN job_episodes e2 ON e1.job_id = e2.job_id AND e1.episode_number = e2.episode_number AND e1.id < e2.id"); cursor.execute("DELETE FROM job_episodes WHERE drive_link = ''"); cursor.execute("DELETE e FROM job_episodes e INNER JOIN jobs j ON e.job_id = j.job_id WHERE j.total_episodes > 0 AND e.episode_number > j.total_episodes")
    async with httpx.AsyncClient(timeout=15.0) as client:
        for m in am:
            sid, nf, fr, rt = m['series_id'], False, [], m['total_episodes']
            try:
                wt = get_real_web_total((await client.get(m.get("original_url") or f"https://hongguoduanju.com/detail?series_id={sid}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept-Encoding": "gzip, deflate"})).text)
                if wt > 0 and wt != m['total_episodes']: cursor.execute("UPDATE jobs SET total_episodes = %s WHERE job_id = %s", (wt, m['job_id'])); fr.append(f"Tổng {m['total_episodes']}->{wt}"); rt=wt; nf=True
            except: pass
            hl = m['has_link'] or 0
            if rt > 0 and hl < rt: fr.append(f"DB {hl}/{rt}"); nf=True
            de = verify_storage_episodes(sid)
            if de and rt > 0:
                dm = len([ep for ep in range(1, rt + 1) if ep not in de])
                if dm > 0: fr.append(f"R2 Thiếu {dm}"); nf=True
            if nf and m['status'] != 'processing': cursor.execute("UPDATE jobs SET status = 'pending', worker_id = NULL, updated_at = NOW() WHERE job_id = %s", (m['job_id'],)); f+=1; det.append(f"{sid}: {', '.join(fr)}")
            elif not nf and hl >= rt and rt > 0: det.append(f"OK {sid}: {hl}/{rt}")
    conn.commit(); conn.close(); return {"status": "ok", "total_checked": tc, "fixed": f, "details": det}

@app.post("/api/admin/fix_movie/{job_id}")
def admin_fix_movie(job_id: str, web_total: int = 0, _=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error"}
    cursor = conn.cursor()
    if web_total > 0: cursor.execute("UPDATE jobs SET total_episodes = %s WHERE job_id = %s", (web_total, job_id))
    cursor.execute("UPDATE jobs SET status = 'pending', worker_id = NULL, updated_at = NOW() WHERE job_id = %s", (job_id,)); cursor.execute("DELETE e1 FROM job_episodes e1 INNER JOIN job_episodes e2 ON e1.job_id = e2.job_id AND e1.episode_number = e2.episode_number AND e1.id < e2.id WHERE e1.job_id = %s", (job_id,))
    if web_total > 0: cursor.execute("DELETE FROM job_episodes WHERE job_id = %s AND episode_number > %s", (job_id, web_total))
    conn.commit(); conn.close(); return {"status": "ok", "message": "Đã sửa!"}

@app.get("/api/admin/verify_movie/{job_id}")
async def admin_verify_movie(job_id: str, _=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"error": "Lỗi DB"}
    cursor = conn.cursor(dictionary=True); cursor.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,)); job = cursor.fetchone()
    if not job: conn.close(); return {"error": "Không thấy"}
    res = {"job_id": job_id, "series_id": job["series_id"], "status": job["status"], "total_episodes_db": job["total_episodes"]}
    cursor.execute("SELECT episode_number FROM job_episodes WHERE job_id = %s AND drive_link != ''", (job_id,)); db_eps = [r['episode_number'] for r in cursor.fetchall()]; res["db_episodes"] = len(db_eps)
    cursor.execute("SELECT episode_number, COUNT(*) as cnt FROM job_episodes WHERE job_id = %s GROUP BY episode_number HAVING cnt > 1", (job_id,)); res["db_duplicates"] = [{"ep": d['episode_number'], "count": d['cnt']} for d in cursor.fetchall()]
    conn.close(); drive_eps = await asyncio.to_thread(verify_storage_episodes, job["series_id"]); res["drive_episodes"] = len(drive_eps)
    wt = 0; wtit = ""
    try:
        html = (await httpx.AsyncClient(timeout=15.0).get(job.get("original_url") or f"https://hongguoduanju.com/detail?series_id={job['series_id']}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept-Encoding": "gzip, deflate"})).text
        jm = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*;?\s*</script>', html, re.DOTALL)
        if jm:
            try: wtit = json.loads(jm.group(1)).get("loaderData", {}).get("detail_page", {}).get("seriesDetail", {}).get("series_name", "")
            except: pass
        if not wtit:
            tm = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</h1>', html)
            if tm: wtit = tm.group(1).strip()
        wt = get_real_web_total(html)
    except Exception as e: wtit = f"Lỗi: {e}"
    res["web_total"] = wt; res["web_title"] = wtit; ex = max(res["total_episodes_db"], wt)
    if ex > 0:
        res["missing_db"] = sorted([ep for ep in range(1, ex + 1) if ep not in db_eps])[:30]
        res["missing_drive"] = sorted([ep for ep in range(1, ex + 1) if ep not in drive_eps])[:30]
        res["missing_db_count"] = len([ep for ep in range(1, ex + 1) if ep not in db_eps])
        res["missing_drive_count"] = len([ep for ep in range(1, ex + 1) if ep not in drive_eps])
        res["extra_db"] = sorted([ep for ep in db_eps if ep > ex])
        res["extra_drive"] = sorted([ep for ep in drive_eps if ep > ex])
    return res

@app.post("/api/admin/sync_drive_to_db/{job_id}")
def admin_sync_drive_to_db(job_id: str, _=Depends(require_admin)):
    conn = get_mysql_connection()
    if not conn: return {"status": "error", "message": "Lỗi kết nối DB"}
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT series_id, total_episodes FROM jobs WHERE job_id = %s", (job_id,)); job = cursor.fetchone()
        if not job: return {"status": "error", "message": "Không tìm thấy."}
        sid = job["series_id"]; cursor.execute("SELECT episode_number FROM job_episodes WHERE job_id = %s AND drive_link != ''", (job_id,)); db_eps = {r['episode_number'] for r in cursor.fetchall()}; a=0
        if r2_enabled():
            for p in get_r2_client().get_paginator("list_objects_v2").paginate(Bucket=R2_BUCKET, Prefix=f"{sid}/"):
                for o in p.get("Contents", []):
                    k = o["Key"]; n = k.rsplit("/", 1)[-1]; en = parse_episode_from_name(n)
                    if en > 0 and en not in db_eps:
                        cursor.execute("SELECT id FROM job_episodes WHERE job_id = %s AND episode_number = %s", (job_id, en))
                        if cursor.fetchone(): cursor.execute("UPDATE job_episodes SET drive_link = %s, file_name = %s WHERE job_id = %s AND episode_number = %s", (f"r2://{k}", n, job_id, en))
                        else: cursor.execute("INSERT INTO job_episodes (job_id, episode_number, drive_link, file_name) VALUES (%s, %s, %s, %s)", (job_id, en, f"r2://{k}", n))
                        db_eps.add(en); a+=1
        else: return {"status": "error", "message": "Chưa hỗ trợ Drive, chỉ R2!"}
        if a > 0:
            cursor.execute("SELECT COUNT(*) as cnt FROM job_episodes WHERE job_id = %s AND drive_link != ''", (job_id,)); act = cursor.fetchone()['cnt']
            if job['total_episodes'] > 0 and act >= job['total_episodes']: cursor.execute("UPDATE jobs SET status = 'completed', worker_id = NULL, completed_at = COALESCE(completed_at, NOW()), updated_at = NOW() WHERE job_id = %s", (job_id,)); trigger_hot_refresh()
            else: cursor.execute("UPDATE jobs SET status = 'partial', updated_at = NOW() WHERE job_id = %s", (job_id,))
        conn.commit(); return {"status": "success", "message": f"Kéo thành công {a} link!"}
    except Exception as e: return {"status": "error", "message": str(e)}
    finally: cursor.close(); conn.close()

@app.post("/api/admin/sync_all_drive_to_db")
def admin_sync_all_drive_to_db(_=Depends(require_admin)): return {"status": "success", "message": "Hãy dùng nút 'Kéo Drive' cho từng phim để an toàn."}

# ==========================================
# TRANG ADMIN DASHBOARD 
# ==========================================
def _inject_boom_tabs(html: str) -> str:
    """Chèn tab BOOM Story + BOOM Review; gỡ bỏ Douyin và All In One."""
    nav_anchor = '<button onclick="switchTab(\'tab-movies\', this)"'
    page_anchor = '<div id="tab-movies"'
    js_anchor = '            async function addBalanceHongguo'
    if nav_anchor not in html or page_anchor not in html or js_anchor not in html:
        return html

    # --- CSS badges ---
    html = html.replace('</style>',
        '.badge-boomstory{background:#e0f2fe;color:#075985;border:1px solid #7dd3fc;}'
        '.badge-boomreview{background:#f0fdf4;color:#166534;border:1px solid #86efac;}'
        '</style>', 1)

    # --- Xóa tab Douyin & All In One khỏi nav ---
    html = html.replace('<button onclick="switchTab(\'tab-douyin\', this)" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fab fa-tiktok mr-1"></i> KH Douyin</button>', '')
    html = html.replace('<button onclick="switchTab(\'tab-allinone\', this)" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-layer-group mr-1"></i> KH All In One</button>', '')

    # --- Thêm nav BOOM Story + BOOM Review ---
    boom_nav = (
        '''<button onclick="switchTab('tab-boomstory', this); loadBoomAccounts()" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-book-open mr-1"></i> BOOM Story</button>'''
        '''<button onclick="switchTab('tab-boomreview', this); loadBoomReviewAccounts()" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-video mr-1"></i> BOOM Review</button>'''
    )
    html = html.replace(nav_anchor, boom_nav + nav_anchor, 1)

    # --- Trang BOOM Story ---
    boom_page = (
        '''<div id="tab-boomstory" class="tab-content hidden max-w-7xl mx-auto space-y-4 px-4"><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-4 border-b pb-2"><div><h2 class="text-lg font-bold text-gray-700"><span class="platform-badge badge-boomstory mr-2"><i class="fas fa-book-open"></i> BOOM STORY</span> Tài khoản riêng</h2></div><div class="flex gap-2"><button onclick="createBoomAccount()" class="bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-user-plus mr-1"></i> Tạo tài khoản</button><button onclick="createBoomStoryTestAccount()" class="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-magic mr-1"></i> Tạo Test 1 Ngày</button><button onclick="loadBoomAccounts()" class="bg-sky-500 hover:bg-sky-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-sync-alt mr-1"></i> Làm mới</button></div></div><div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm"><thead><tr class="bg-gray-50 text-gray-500 uppercase text-xs border-b"><th class="p-3">Tài khoản</th><th class="p-3">Ghi chú</th><th class="p-3 text-center">Hạn sử dụng</th><th class="p-3 text-center">Trạng thái</th><th class="p-3 text-center">Đăng nhập gần nhất</th><th class="p-3 text-center">Hành động</th></tr></thead><tbody id="boomAccountsBody"><tr><td colspan="6" class="p-6 text-center text-gray-400">Bấm Làm mới để xem tài khoản.</td></tr></tbody></table></div></div></div>'''
        # --- Trang BOOM Review ---
        '''<div id="tab-boomreview" class="tab-content hidden max-w-7xl mx-auto space-y-4 px-4"><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-4 border-b pb-2"><div><h2 class="text-lg font-bold text-gray-700"><span class="platform-badge badge-boomreview mr-2"><i class="fas fa-video"></i> BOOM REVIEW</span> Tài khoản riêng</h2></div><div class="flex gap-2"><button onclick="createBoomReviewAccount()" class="bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-user-plus mr-1"></i> Tạo tài khoản</button><button onclick="createBoomReviewTestAccount()" class="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-magic mr-1"></i> Tạo Test 1 Ngày</button><button onclick="loadBoomReviewAccounts()" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-sync-alt mr-1"></i> Làm mới</button></div></div><div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm"><thead><tr class="bg-gray-50 text-gray-500 uppercase text-xs border-b"><th class="p-3">Tài khoản</th><th class="p-3">Ghi chú</th><th class="p-3 text-center">Hạn sử dụng</th><th class="p-3 text-center">Trạng thái</th><th class="p-3 text-center">Đăng nhập gần nhất</th><th class="p-3 text-center">Hành động</th></tr></thead><tbody id="boomReviewAccountsBody"><tr><td colspan="6" class="p-6 text-center text-gray-400">Bấm Làm mới để xem tài khoản.</td></tr></tbody></table></div></div></div>'''
    )
    html = html.replace(page_anchor, boom_page + page_anchor, 1)

    # --- JS chung và riêng ---
    boom_js = r'''
            function boomEsc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
            function renderBoomRow(a, prefix, toggleFn, addDaysFn, changePwdFn, deleteFn) {
                const key = encodeURIComponent(a.username || '');
                const active = !!a.active;
                const state = active ? '<span class="bg-green-100 text-green-700 px-2 py-1 rounded-full text-xs font-bold">Đang mở</span>' : '<span class="bg-red-100 text-red-700 px-2 py-1 rounded-full text-xs font-bold">Đã khóa</span>';
                const toggleText = active ? 'Khóa' : 'Mở';
                return '<tr class="border-b border-gray-100 hover:bg-gray-50"><td class="p-3 font-bold text-gray-700">' + boomEsc(a.username) + '</td><td class="p-3 text-gray-500">' + boomEsc(a.note || '') + '</td><td class="p-3 text-center">' + formatExpiryHtml(a.expiry_date) + '</td><td class="p-3 text-center">' + state + '</td><td class="p-3 text-center text-xs text-gray-500">' + boomEsc(a.last_login || 'Chưa đăng nhập') + '</td><td class="p-3 text-center whitespace-nowrap"><button onclick="' + addDaysFn + '(decodeURIComponent(\'' + key + '\'))" class="bg-purple-100 text-purple-700 px-2 py-1 rounded text-xs font-bold mr-1">+ Ngày</button><button onclick="' + changePwdFn + '(decodeURIComponent(\'' + key + '\'))" class="bg-blue-100 text-blue-700 px-2 py-1 rounded text-xs font-bold mr-1">Đổi MK</button><button onclick="' + toggleFn + '(decodeURIComponent(\'' + key + '\'))" class="bg-orange-100 text-orange-700 px-2 py-1 rounded text-xs font-bold mr-1">' + toggleText + '</button><button onclick="' + deleteFn + '(decodeURIComponent(\'' + key + '\'))" class="bg-red-100 text-red-700 px-2 py-1 rounded text-xs font-bold">Xóa</button></td></tr>';
            }
            // --- BOOM STORY ---
            async function loadBoomAccounts() {
                const body = document.getElementById('boomAccountsBody');
                try {
                    const res = await adminFetch('/api/admin/boomstory/accounts'); const accounts = await res.json();
                    if (!Array.isArray(accounts) || accounts.length === 0) { body.innerHTML = '<tr><td colspan="6" class="p-6 text-center text-gray-400">Chưa có tài khoản BOOM Story.</td></tr>'; return; }
                    body.innerHTML = accounts.map(a => renderBoomRow(a, 'story', 'boomToggleAccount', 'boomAddDays', 'boomChangePassword', 'boomDeleteAccount')).join('');
                } catch(e) { body.innerHTML = '<tr><td colspan="6" class="p-6 text-center text-red-500">Không tải được tài khoản BOOM Story.</td></tr>'; }
            }
            async function createBoomAccount() {
                const username = (prompt('Tên tài khoản BOOM Story:') || '').trim(); if (!username) return;
                const password = prompt('Mật khẩu (ít nhất 6 ký tự):', '123456'); if (password === null || !password) return;
                const days = parseInt(prompt('Số ngày sử dụng:', '30') || '30');
                const note = prompt('Ghi chú (có thể để trống):', '') || '';
                try {
                    const res = await adminFetch('/api/admin/boomstory/accounts', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password,days,note})});
                    const data = await res.json(); alert(data.message || 'Đã xử lý.'); if (data.status === 'success') loadBoomAccounts();
                } catch(e) { alert('Lỗi kết nối server.'); }
            }
            async function boomAddDays(username) {
                const days = parseInt(prompt('Cộng thêm bao nhiêu ngày cho [' + username + ']?', '30') || '0'); if (!days) return;
                const res = await adminFetch('/api/admin/boomstory/accounts/' + encodeURIComponent(username) + '/add_days', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days})}); const data = await res.json(); alert(data.message || 'Đã xử lý.'); loadBoomAccounts();
            }
            async function boomChangePassword(username) {
                const password = prompt('Mật khẩu mới cho [' + username + '] (ít nhất 6 ký tự):', '123456'); if (password === null || !password) return;
                const res = await adminFetch('/api/admin/boomstory/accounts/' + encodeURIComponent(username) + '/password', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})}); const data = await res.json(); alert(data.message || 'Đã xử lý.');
            }
            async function boomToggleAccount(username) {
                if (!confirm('Đổi trạng thái tài khoản [' + username + ']?')) return;
                await adminFetch('/api/admin/boomstory/accounts/' + encodeURIComponent(username) + '/toggle', {method:'POST'}); loadBoomAccounts();
            }
            async function boomDeleteAccount(username) {
                if (!confirm('Xóa vĩnh viễn tài khoản BOOM Story [' + username + ']?')) return;
                await adminFetch('/api/admin/boomstory/accounts/' + encodeURIComponent(username) + '/delete', {method:'POST'}); loadBoomAccounts();
            }
            async function createBoomStoryTestAccount() {
                const randomNum = Math.floor(1000 + Math.random() * 9000);
                const username = 'test' + randomNum;
                const password = '123';
                try {
                    const res = await adminFetch('/api/admin/boomstory/accounts', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password,days:1,note:'Test account'})});
                    const data = await res.json();
                    if (data.status === 'success') {
                        loadBoomAccounts();
                        prompt('✅ Đã tạo TK Test 1 ngày BOOM Story! Nhấn Ctrl+C để copy gửi khách:', 'Tài khoản: ' + username + ' | Mật khẩu: ' + password);
                    } else { alert(data.message || 'Lỗi tạo tài khoản.'); }
                } catch(e) { alert('Lỗi kết nối server.'); }
            }
            // --- BOOM REVIEW ---
            async function loadBoomReviewAccounts() {
                const body = document.getElementById('boomReviewAccountsBody');
                try {
                    const res = await adminFetch('/api/admin/boomreview/accounts'); const accounts = await res.json();
                    if (!Array.isArray(accounts) || accounts.length === 0) { body.innerHTML = '<tr><td colspan="6" class="p-6 text-center text-gray-400">Chưa có tài khoản BOOM Review.</td></tr>'; return; }
                    body.innerHTML = accounts.map(a => renderBoomRow(a, 'review', 'boomReviewToggle', 'boomReviewAddDays', 'boomReviewChangePwd', 'boomReviewDelete')).join('');
                } catch(e) { body.innerHTML = '<tr><td colspan="6" class="p-6 text-center text-red-500">Không tải được tài khoản BOOM Review.</td></tr>'; }
            }
            async function createBoomReviewAccount() {
                const username = (prompt('Tên tài khoản BOOM Review:') || '').trim(); if (!username) return;
                const password = prompt('Mật khẩu (ít nhất 6 ký tự):', '123456'); if (password === null || !password) return;
                const days = parseInt(prompt('Số ngày sử dụng:', '30') || '30');
                const note = prompt('Ghi chú (có thể để trống):', '') || '';
                try {
                    const res = await adminFetch('/api/admin/boomreview/accounts', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password,days,note})});
                    const data = await res.json(); alert(data.message || 'Đã xử lý.'); if (data.status === 'success') loadBoomReviewAccounts();
                } catch(e) { alert('Lỗi kết nối server.'); }
            }
            async function boomReviewAddDays(username) {
                const days = parseInt(prompt('Cộng thêm bao nhiêu ngày cho [' + username + ']?', '30') || '0'); if (!days) return;
                const res = await adminFetch('/api/admin/boomreview/accounts/' + encodeURIComponent(username) + '/add_days', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days})}); const data = await res.json(); alert(data.message || 'Đã xử lý.'); loadBoomReviewAccounts();
            }
            async function boomReviewChangePwd(username) {
                const password = prompt('Mật khẩu mới cho [' + username + '] (ít nhất 6 ký tự):', '123456'); if (password === null || !password) return;
                const res = await adminFetch('/api/admin/boomreview/accounts/' + encodeURIComponent(username) + '/password', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})}); const data = await res.json(); alert(data.message || 'Đã xử lý.');
            }
            async function boomReviewToggle(username) {
                if (!confirm('Đổi trạng thái tài khoản [' + username + ']?')) return;
                await adminFetch('/api/admin/boomreview/accounts/' + encodeURIComponent(username) + '/toggle', {method:'POST'}); loadBoomReviewAccounts();
            }
            async function boomReviewDelete(username) {
                if (!confirm('Xóa vĩnh viễn tài khoản BOOM Review [' + username + ']?')) return;
                await adminFetch('/api/admin/boomreview/accounts/' + encodeURIComponent(username) + '/delete', {method:'POST'}); loadBoomReviewAccounts();
            }
            async function createBoomReviewTestAccount() {
                const randomNum = Math.floor(1000 + Math.random() * 9000);
                const username = 'test' + randomNum;
                const password = '123';
                try {
                    const res = await adminFetch('/api/admin/boomreview/accounts', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password,days:1,note:'Test account'})});
                    const data = await res.json();
                    if (data.status === 'success') {
                        loadBoomReviewAccounts();
                        prompt('✅ Đã tạo TK Test 1 ngày BOOM Review! Nhấn Ctrl+C để copy gửi khách:', 'Tài khoản: ' + username + ' | Mật khẩu: ' + password);
                    } else { alert(data.message || 'Lỗi tạo tài khoản.'); }
                } catch(e) { alert('Lỗi kết nối server.'); }
            }
'''
    html = html.replace(js_anchor, boom_js + js_anchor, 1)
    # Cập nhật checkAdminSession để tải cả BOOM Review
    html = html.replace(
        'loadAllInOneUsers(); loadDailyStats();',
        'loadBoomAccounts(); loadBoomReviewAccounts(); loadDailyStats();'
    )
    return html

@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard_with_boom_tabs():
    return _inject_boom_tabs(admin_dashboard())

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard():
    return """<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><title>SaaS Admin - AnhStudio</title><script src="https://cdn.tailwindcss.com"></script><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet"><style>.tab-btn.active{background-color:#f3f4f6;color:#1f2937;border-bottom:2px solid #3b82f6;}body{background-color:#f0f2f5;}.platform-badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:700;}.badge-honggou{background:#fef3c7;color:#92400e;border:1px solid #fde68a;}.badge-douyin{background:#fce7f3;color:#9d174d;border:1px solid #fbcfe8;}.badge-allinone{background:#e0e7ff;color:#3730a3;border:1px solid #c7d2fe;}</style></head><body class="text-gray-800 font-sans pb-10"><div id="login-overlay" class="fixed inset-0 bg-gray-900 flex items-center justify-center z-50"><div class="bg-white rounded-2xl shadow-2xl p-10 w-96"><h1 class="text-2xl font-bold text-center mb-6 text-gray-700"><i class="fas fa-lock text-blue-500 mr-2"></i>Admin Login</h1><input type="password" id="admin-password" placeholder="Mật khẩu Admin" class="w-full p-3 border border-gray-300 rounded-lg mb-4 focus:border-blue-500 focus:outline-none" onkeydown="if(event.key==='Enter') adminLogin()"><button onclick="adminLogin()" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-lg transition-all">Đăng Nhập</button><p id="login-error" class="text-red-500 text-center mt-3 text-sm hidden">Sai mật khẩu!</p></div></div><div id="main-content" class="hidden"><div class="bg-white shadow-sm p-4 mb-4 flex justify-between items-center"><h1 class="text-xl font-bold text-gray-700"><i class="fas fa-server text-blue-500 mr-2"></i> Hệ Thống Quản Lý AnhStudio</h1><div class="space-x-2 flex items-center"><button id="btn-maintenance" onclick="toggleMaintenance()" class="px-4 py-2 text-sm font-bold text-orange-500 hover:bg-orange-50 rounded border border-orange-200 transition-all mr-2"><i class="fas fa-tools mr-1"></i> Bật Bảo Trì</button><button onclick="switchTab('tab-overview', this)" class="tab-btn active px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-chart-pie mr-1"></i> Tổng Quan</button><button onclick="switchTab('tab-honggou', this)" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-film mr-1"></i> KH Honggou</button><button onclick="switchTab('tab-douyin', this)" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fab fa-tiktok mr-1"></i> KH Douyin</button><button onclick="switchTab('tab-allinone', this)" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-layer-group mr-1"></i> KH All In One</button><button onclick="switchTab('tab-movies', this)" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-database mr-1"></i> Kho Phim</button><button onclick="switchTab('tab-workers', this)" class="tab-btn px-4 py-2 text-sm font-bold text-gray-500 transition-all"><i class="fas fa-robot mr-1"></i> Worker</button><button onclick="adminLogout()" class="px-4 py-2 text-sm font-bold text-red-500 hover:bg-red-50 rounded transition-all"><i class="fas fa-sign-out-alt mr-1"></i> Thoát</button></div></div><div id="tab-overview" class="tab-content max-w-7xl mx-auto space-y-4 px-4"><h2 class="text-lg font-bold text-gray-700 mb-2 border-b pb-2"><i class="fas fa-tachometer-alt text-blue-500 mr-2"></i> Báo Cáo Dữ Liệu Thực Tế</h2><div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4"><div class="bg-gradient-to-r from-gray-700 to-gray-900 text-white p-4 rounded-lg shadow-sm border border-gray-600"><div class="text-xs font-semibold mb-1 text-gray-300"><i class="fas fa-users mr-1"></i> TỔNG USER</div><div class="text-3xl font-bold" id="card-total-users">0</div></div><div class="bg-gradient-to-r from-amber-400 to-orange-500 text-white p-4 rounded-lg shadow-sm border border-orange-400"><div class="text-xs font-semibold mb-1 text-orange-100"><i class="fas fa-film mr-1"></i> HONGGOU</div><div class="text-3xl font-bold" id="card-honggou-users">0</div></div><div class="bg-gradient-to-r from-pink-500 to-rose-600 text-white p-4 rounded-lg shadow-sm border border-rose-500"><div class="text-xs font-semibold mb-1 text-pink-200"><i class="fab fa-tiktok mr-1"></i> DOUYIN</div><div class="text-3xl font-bold" id="card-douyin-users">0</div></div><div class="bg-gradient-to-r from-indigo-500 to-purple-600 text-white p-4 rounded-lg shadow-sm border border-indigo-500"><div class="text-xs font-semibold mb-1 text-indigo-200"><i class="fas fa-layer-group mr-1"></i> ALL IN ONE</div><div class="text-3xl font-bold" id="card-allinone-users">0</div></div><div class="bg-gradient-to-r from-purple-500 to-indigo-600 text-white p-4 rounded-lg shadow-sm border border-indigo-500"><div class="text-xs font-semibold mb-1 text-indigo-200"><i class="fas fa-crown mr-1"></i> TK VIP</div><div class="text-3xl font-bold" id="card-vip-users">0</div></div><div class="bg-gradient-to-r from-emerald-400 to-teal-500 text-white p-4 rounded-lg shadow-sm border border-teal-400"><div class="text-xs font-semibold mb-1 text-teal-100"><i class="fas fa-user mr-1"></i> TK THƯỜNG</div><div class="text-3xl font-bold" id="card-normal-users">0</div></div><div class="bg-gradient-to-r from-sky-400 to-blue-500 text-white p-4 rounded-lg shadow-sm border border-blue-400"><div class="text-xs font-semibold mb-1 text-blue-100"><i class="fas fa-wallet mr-1"></i> SỐ DƯ HONGGOU</div><div class="text-2xl font-bold truncate" id="card-balance">0 đ</div></div></div><div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4"><div class="bg-gradient-to-r from-green-500 to-emerald-600 text-white p-4 rounded-lg shadow-sm border border-green-400"><div class="text-xs font-semibold mb-1 text-green-100"><i class="fas fa-calendar-day mr-1"></i> HÔM NAY</div><div class="text-3xl font-bold" id="card-today">0</div><div class="text-xs text-green-200 mt-1">bộ phim mới</div></div><div class="bg-gradient-to-r from-blue-500 to-cyan-600 text-white p-4 rounded-lg shadow-sm border border-blue-400"><div class="text-xs font-semibold mb-1 text-blue-100"><i class="fas fa-history mr-1"></i> HÔM QUA</div><div class="text-3xl font-bold" id="card-yesterday">0</div><div class="text-xs text-blue-200 mt-1">bộ phim mới</div></div><div class="bg-gradient-to-r from-violet-500 to-purple-600 text-white p-4 rounded-lg shadow-sm border border-violet-400"><div class="text-xs font-semibold mb-1 text-violet-100"><i class="fas fa-calendar-week mr-1"></i> TUẦN NAY</div><div class="text-3xl font-bold" id="card-this-week">0</div><div class="text-xs text-violet-200 mt-1">bộ phim mới</div></div><div class="bg-gradient-to-r from-rose-500 to-red-600 text-white p-4 rounded-lg shadow-sm border border-rose-400"><div class="text-xs font-semibold mb-1 text-rose-100"><i class="fas fa-film mr-1"></i> TỔNG CỘNG</div><div class="text-3xl font-bold" id="card-total-movies">0</div><div class="text-xs text-rose-200 mt-1">bộ trong kho</div></div></div><div class="mt-4 bg-white p-4 rounded-lg shadow-sm border border-gray-200"><div class="flex justify-between items-center mb-3 border-b pb-2"><h3 class="font-bold text-gray-700"><i class="fas fa-circle text-green-500 mr-2"></i> Khách Đang Online</h3><span class="text-sm text-gray-500" id="online-count">0 người</span></div><div id="onlineClientsBody" class="space-y-2"><p class="text-gray-400 text-center py-4">Chưa có khách online</p></div></div><div class="mt-2 bg-white p-6 rounded-lg shadow-sm border border-gray-200 text-center text-gray-500"><button onclick="loadOverviewStats()" class="mt-2 bg-gray-100 hover:bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm font-bold border border-gray-300"><i class="fas fa-sync-alt"></i> Tải lại số liệu</button></div></div><div id="tab-honggou" class="tab-content hidden max-w-7xl mx-auto space-y-4 px-4"><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-3 border-b pb-2"><h2 class="text-lg font-bold text-gray-700"><i class="fas fa-calendar-check text-emerald-500 mr-2"></i> Thống Kê Tài Khoản</h2><button onclick="loadDailyStats()" class="bg-emerald-500 hover:bg-emerald-600 text-white px-3 py-1.5 rounded shadow text-xs font-bold"><i class="fas fa-sync-alt mr-1"></i> Làm mới</button></div><div id="monthSummary" class="mb-3"></div><div id="dailyStatsRow" class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2 mb-3"></div><div id="dayDetail" class="hidden bg-gray-50 border border-gray-200 rounded-lg p-4"></div></div><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-4 border-b pb-2"><h2 class="text-lg font-bold text-gray-700"><span class="platform-badge badge-honggou mr-2"><i class="fas fa-film"></i> HONGGOU</span> Khách Hàng</h2><div class="flex gap-2"><button onclick="createHongguoUser()" class="bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-user-plus mr-1"></i> Tạo Khách</button><button onclick="createTestUser()" class="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-magic mr-1"></i> Tạo Test 1 Ngày</button><button onclick="loadHonggouUsers()" class="bg-amber-500 hover:bg-amber-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-sync-alt mr-2"></i> Làm mới</button></div></div><div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm"><thead><tr class="bg-gray-50 text-gray-500 uppercase text-xs border-b"><th class="p-3">Khách hàng</th><th class="p-3 text-center">Ngày tạo</th><th class="p-3 text-center">Gói VIP</th><th class="p-3 text-center">Số Dư</th><th class="p-3 text-center">Khóa Máy</th><th class="p-3 text-center">Hành Động</th></tr></thead><tbody id="honggouTableBody"></tbody></table></div></div></div><div id="tab-douyin" class="tab-content hidden max-w-7xl mx-auto space-y-4 px-4"><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-4 border-b pb-2"><h2 class="text-lg font-bold text-gray-700"><span class="platform-badge badge-douyin mr-2"><i class="fab fa-tiktok"></i> DOUYIN</span> Khách Hàng</h2><button onclick="loadDouyinUsers()" class="bg-pink-500 hover:bg-pink-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-sync-alt mr-2"></i> Làm mới</button></div><div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm"><thead><tr class="bg-gray-50 text-gray-500 uppercase text-xs border-b"><th class="p-3">Khách hàng</th><th class="p-3 text-center">Gói VIP</th><th class="p-3 text-center">Khóa Máy</th><th class="p-3 text-center">Hành Động</th></tr></thead><tbody id="douyinTableBody"></tbody></table></div></div></div><div id="tab-allinone" class="tab-content hidden max-w-7xl mx-auto space-y-4 px-4"><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-4 border-b pb-2"><h2 class="text-lg font-bold text-gray-700"><span class="platform-badge badge-allinone mr-2"><i class="fas fa-layer-group"></i> ALL IN ONE</span> Khách Hàng</h2><button onclick="loadAllInOneUsers()" class="bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-sync-alt mr-2"></i> Làm mới</button></div><div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm"><thead><tr class="bg-gray-50 text-gray-500 uppercase text-xs border-b"><th class="p-3">Khách hàng</th><th class="p-3 text-center">Gói VIP</th><th class="p-3 text-center">Khóa Máy</th><th class="p-3 text-center">Hành Động</th></tr></thead><tbody id="allinoneTableBody"></tbody></table></div></div></div><div id="tab-movies" class="tab-content hidden max-w-7xl mx-auto space-y-4 px-4"><div class="grid grid-cols-4 gap-4 mb-4"><div class="bg-green-50 p-3 rounded-lg border border-green-200 text-center shadow-sm"><div class="text-xs text-green-600 font-bold uppercase mb-1">Hoàn thành</div><div class="text-2xl font-bold text-green-700" id="count-completed">0</div></div><div class="bg-blue-50 p-3 rounded-lg border border-blue-200 text-center shadow-sm"><div class="text-xs text-blue-600 font-bold uppercase mb-1">Chờ xử lý</div><div class="text-2xl font-bold text-blue-700" id="count-pending">0</div></div><div class="bg-yellow-50 p-3 rounded-lg border border-yellow-200 text-center shadow-sm"><div class="text-xs text-yellow-600 font-bold uppercase mb-1">Đang tải</div><div class="text-2xl font-bold text-yellow-700" id="count-processing">0</div></div><div class="bg-red-50 p-3 rounded-lg border border-red-200 text-center shadow-sm"><div class="text-xs text-red-600 font-bold uppercase mb-1">Thiếu tập</div><div class="text-2xl font-bold text-red-700" id="count-partial">0</div></div></div><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-4 border-b pb-2"><h2 class="text-lg font-bold text-gray-700"><i class="fas fa-database text-blue-500 mr-2"></i> Kho Phim</h2><div class="flex flex-wrap gap-2 justify-end"><button onclick="loadMovies()" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-sync-alt mr-1"></i> Làm mới</button><button onclick="healMetadata()" class="bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-magic mr-1"></i> Chữa Lành Metadata</button><button onclick="repairMovies()" class="bg-orange-500 hover:bg-orange-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-wrench mr-1"></i> Sửa thiếu tập</button><button onclick="checkFixAll()" class="bg-purple-500 hover:bg-purple-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-check-double mr-1"></i> Check + Sửa tất cả</button><button onclick="purgeFakeMovies()" class="bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-skull-crossbones mr-1"></i> Xóa Phim Ảo</button><button onclick="syncAllDrive()" class="bg-teal-500 hover:bg-teal-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-cloud-download-alt mr-1"></i> Đồng bộ Drive</button><button onclick="exportIdleLinks()" class="bg-indigo-500 hover:bg-indigo-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-file-export mr-1"></i> Xuất Link Idle</button><button id="btnCoverScan" onclick="toggleCoverScan()" class="bg-pink-500 hover:bg-pink-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-image mr-1"></i> Quét Ảnh Bìa</button><button onclick="deleteAllIdle()" class="bg-slate-600 hover:bg-slate-700 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-broom mr-1"></i> Xóa hết Idle</button><button onclick="deleteAllPending()" class="bg-red-600 hover:bg-red-700 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-trash-alt mr-1"></i> Xóa hết Pending</button></div></div><div id="verifyResult" class="mb-4 hidden bg-blue-50 p-4 rounded-lg border border-blue-200 text-sm"></div><div class="flex flex-wrap items-center gap-2 mb-3"><input type="text" id="movieSearch" oninput="onMovieSearch()" placeholder="🔍 Tìm theo tên phim hoặc series_id..." class="border border-gray-300 rounded px-3 py-2 text-sm flex-1 min-w-[220px]"><select id="movieStatusFilter" onchange="onMovieSearch()" class="border border-gray-300 rounded px-2 py-2 text-sm font-bold"><option value="">Tất cả trạng thái</option><option value="completed">Hoàn thành</option><option value="partial">Thiếu tập</option><option value="processing">Đang tải</option><option value="pending">Chờ xử lý</option><option value="idle">Idle</option></select><span id="movieCountLabel" class="text-sm text-gray-500 font-bold"></span></div><div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm"><thead><tr class="bg-gray-50 text-gray-500 uppercase text-xs border-b"><th class="p-3">Series ID / Tên Phim</th><th class="p-3">Thể loại</th><th class="p-3 text-center">Trạng Thái</th><th class="p-3 text-center">Worker</th><th class="p-3 text-center">Tổng</th><th class="p-3 text-center">Có Link</th><th class="p-3 text-center">Cập nhật</th><th class="p-3 text-center">Xác thực</th></tr></thead><tbody id="moviesTableBody"></tbody></table></div><div id="moviePager" class="flex items-center justify-center gap-2 mt-4"></div></div></div><div id="tab-workers" class="tab-content hidden max-w-7xl mx-auto space-y-4 px-4"><div class="bg-white p-4 rounded-lg shadow-sm border border-gray-100"><div class="flex justify-between items-center mb-4 border-b pb-2"><h2 class="text-lg font-bold text-gray-700"><i class="fas fa-robot text-blue-500 mr-2"></i> Quản Lý Worker</h2><button onclick="loadWorkers()" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-1.5 rounded shadow text-sm font-bold"><i class="fas fa-sync-alt mr-1"></i> Làm mới</button></div><div id="workerStats" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4"></div><div class="overflow-x-auto"><table class="w-full text-left border-collapse text-sm"><thead><tr class="bg-gray-50 text-gray-500 uppercase text-xs border-b"><th class="p-3">Worker ID</th><th class="p-3 text-center">Trạng thái</th><th class="p-3">Chi tiết</th><th class="p-3 text-center">Heartbeat</th><th class="p-3 text-center">Hành động</th></tr></thead><tbody id="workerTableBody"></tbody></table></div><div id="workerEmpty" class="hidden text-center text-gray-400 py-8"><i class="fas fa-robot text-4xl mb-2"></i><p>Chưa có Worker kết nối.</p></div></div></div></div><script>
            let adminToken = sessionStorage.getItem('adminToken') || ''; let isMaintenance = false;
            if (adminToken) { checkAdminSession(); }
            function formatExpiryHtml(dateStr) {
                if(!dateStr) return '<span class="text-gray-400 font-bold text-xs">Chưa VIP</span>';
                let isoDate = dateStr.replace(' ', 'T');
                const exp = new Date(isoDate);
                const now = new Date();
                const diff = exp - now;
                let badge = '';
                if(diff <= 0) { badge = '<span class="bg-red-100 text-red-600 px-2 py-0.5 rounded text-[11px] font-bold">Hết hạn</span>'; } else {
                    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
                    const hours = Math.floor((diff / (1000 * 60 * 60)) % 24);
                    if(days > 0) badge = '<span class="bg-purple-100 text-purple-600 px-2 py-0.5 rounded text-[11px] font-bold">Còn ' + days + ' ngày</span>';
                    else badge = '<span class="bg-purple-100 text-purple-600 px-2 py-0.5 rounded text-[11px] font-bold">Còn ' + hours + ' giờ</span>';
                }
                return '<div class="flex flex-col items-center gap-0.5"><div class="text-gray-400 text-[10px]">' + dateStr.substring(0, 10) + '</div>' + badge + '</div>';
            }
            async function loadMaintenanceStatus() { try { const res = await adminFetch('/api/admin/maintenance_status'); const data = await res.json(); isMaintenance = data.maintenance; updateMaintenanceButton(); } catch(e) {} }
            function updateMaintenanceButton() { const btn = document.getElementById('btn-maintenance'); if (!btn) return; if (isMaintenance) { btn.innerHTML = '<i class="fas fa-play-circle mr-1"></i> Mở Lại Server'; btn.className = "px-4 py-2 text-sm font-bold text-white bg-green-500 hover:bg-green-600 rounded shadow transition-all mr-2"; } else { btn.innerHTML = '<i class="fas fa-tools mr-1"></i> Bật Bảo Trì'; btn.className = "px-4 py-2 text-sm font-bold text-orange-500 hover:bg-orange-50 rounded border border-orange-200 transition-all mr-2"; } }
            async function toggleMaintenance() { const newState = !isMaintenance; const msg = newState ? "BẬT bảo trì?" : "TẮT bảo trì?"; if(!confirm(msg)) return; try { const res = await adminFetch('/api/admin/toggle_maintenance', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ enabled: newState }) }); const data = await res.json(); isMaintenance = data.maintenance; updateMaintenanceButton(); alert(data.message); } catch(e) { alert("Lỗi"); } }
            async function adminLogin() { const pwd = document.getElementById('admin-password').value; try { const res = await fetch('/api/admin/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: pwd}) }); if (res.ok) { const data = await res.json(); adminToken = data.token; sessionStorage.setItem('adminToken', adminToken); document.getElementById('login-overlay').classList.add('hidden'); document.getElementById('main-content').classList.remove('hidden'); loadMaintenanceStatus(); loadOverviewStats(); loadHonggouUsers(); loadDouyinUsers(); loadAllInOneUsers(); loadDailyStats(); } else { const errData = await res.json(); document.getElementById('login-error').innerText = errData.detail || "Sai mật khẩu!"; document.getElementById('login-error').classList.remove('hidden'); } } catch(e) { alert('Lỗi!'); } }
            function adminLogout() { sessionStorage.removeItem('adminToken'); adminToken = ''; document.getElementById('main-content').classList.add('hidden'); document.getElementById('login-overlay').classList.remove('hidden'); document.getElementById('admin-password').value = ''; }
            async function checkAdminSession() { try { const res = await adminFetch('/api/admin/stats'); if (res.ok) { document.getElementById('login-overlay').classList.add('hidden'); document.getElementById('main-content').classList.remove('hidden'); loadMaintenanceStatus(); loadOverviewStats(); loadHonggouUsers(); loadDouyinUsers(); loadAllInOneUsers(); loadDailyStats(); } else { adminLogout(); } } catch(e) { adminLogout(); } }
            function adminFetch(url, options = {}) { if (!options.headers) options.headers = {}; options.headers['Authorization'] = 'Admin ' + adminToken; return fetch(url, options).then(res => { if (res.status === 401) { adminLogout(); throw new Error('Hết phiên'); } return res; }); }
            function switchTab(tabId, btnElement) { document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden')); document.getElementById(tabId).classList.remove('hidden'); document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active', 'bg-gray-100')); btnElement.classList.add('active', 'bg-gray-100'); if(tabId === 'tab-movies') loadMovies(); if(tabId === 'tab-workers') loadWorkers(); if(tabId === 'tab-honggou') loadDailyStats(); }
            function renderOnlineClients(clients) { document.getElementById('online-count').innerText = clients.length + ' người'; if (clients.length === 0) { document.getElementById('onlineClientsBody').innerHTML = '<p class="text-gray-400 text-center py-4">Chưa có khách</p>'; return; } let oh = ''; clients.forEach(c => { let pBadge = c.platform === 'honggou' ? 'badge-honggou' : c.platform === 'douyin' ? 'badge-douyin' : 'badge-allinone'; let pLabel = c.platform || 'unknown'; let movieInfo = c.movie_title ? '<span class="text-blue-600 font-medium">' + c.movie_title + '</span>' : (c.action || '<span class="text-gray-400">Đang lướt</span>'); let agoText = c.ago_seconds < 10 ? 'vừa xong' : c.ago_seconds + 's'; oh += '<div class="flex items-center justify-between p-2 bg-gray-50 rounded border border-gray-100"><div class="flex items-center gap-3"><i class="fas fa-circle text-green-400 text-xs"></i><span class="font-bold text-gray-700">' + c.username + '</span><span class="platform-badge ' + pBadge + '">' + pLabel + '</span></div><div class="flex items-center gap-4"><span class="text-sm">' + movieInfo + '</span><span class="text-xs text-gray-400">' + agoText + '</span></div></div>'; }); document.getElementById('onlineClientsBody').innerHTML = oh; }
            async function loadOverviewStats() { try { const res = await adminFetch('/api/admin/stats'); const data = await res.json(); document.getElementById('card-total-users').innerText = data.total_users; document.getElementById('card-honggou-users').innerText = data.honggou_users; document.getElementById('card-douyin-users').innerText = data.douyin_users; document.getElementById('card-allinone-users').innerText = data.allinone_users; document.getElementById('card-vip-users').innerText = data.vip_count; document.getElementById('card-normal-users').innerText = data.normal_count; document.getElementById('card-balance').innerText = data.total_balance_hongguo.toLocaleString('vi-VN') + ' đ'; const statsRes = await adminFetch('/api/admin/movie_stats'); const stats = await statsRes.json(); document.getElementById('card-today').innerText = stats.today; document.getElementById('card-yesterday').innerText = stats.yesterday; document.getElementById('card-this-week').innerText = stats.this_week; document.getElementById('card-total-movies').innerText = stats.total; const onlineRes = await adminFetch('/api/admin/online_clients'); const clients = await onlineRes.json(); renderOnlineClients(clients); } catch(e) {} }
            function renderUserRow(u, platform) { 
                let hwidBadge = (u.hwid && u.hwid.trim() !== "") ? '<button onclick="resetHwid(&quot;' + u.username + '&quot;, &quot;' + platform + '&quot;)" class="bg-green-100 text-green-600 px-3 py-1 rounded-full text-xs font-bold border border-green-200">Đã khóa</button>' : '<span class="bg-gray-100 text-gray-500 px-3 py-1 rounded-full text-xs font-bold border border-green-200">Trống</span>'; 
                let balanceCol = platform === 'honggou' ? '<td class="p-3 text-center"><div class="font-bold text-emerald-500 mb-1">' + (u.balance_hongguo || 0).toLocaleString('vi-VN') + ' đ</div><button onclick="addBalanceHongguo(&quot;' + u.username + '&quot;)" class="bg-emerald-100 hover:bg-emerald-200 text-emerald-600 border border-emerald-200 px-2 py-0.5 rounded text-xs font-bold mr-1">Nạp</button><button onclick="deductBalanceHongguo(&quot;' + u.username + '&quot;)" class="bg-orange-100 hover:bg-orange-200 text-orange-600 border border-orange-200 px-2 py-0.5 rounded text-xs font-bold">Trừ</button><button onclick="checkQuota(&quot;' + u.username + '&quot;)" class="bg-blue-100 hover:bg-blue-200 text-blue-600 border border-blue-200 px-2 py-0.5 rounded text-xs font-bold ml-1" title="Kiểm tra lượt tải từ Momigo">Lượt</button></td>' : ''; 
                let unlocked = !!u.vip_unlocked;
                let vipBadge = unlocked ? '<span class="ml-2 bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full text-xs font-bold border border-emerald-200" title="Được dùng đầy đủ tính năng"><i class="fas fa-unlock mr-1"></i>Full</span>' : '<span class="ml-2 bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full text-xs font-bold border border-gray-200" title="Chỉ dùng phim trong kho (test)"><i class="fas fa-lock mr-1"></i>Test</span>';
                let vipToggleBtn = unlocked
                    ? '<button onclick="toggleVip(&quot;' + u.username + '&quot;, &quot;' + platform + '&quot;)" class="bg-amber-100 hover:bg-amber-200 text-amber-700 border border-amber-200 px-2 py-1 rounded text-xs font-bold ml-1" title="Khóa lại (chỉ cho dùng phim trong kho)"><i class="fas fa-lock"></i> Khóa</button>'
                    : '<button onclick="toggleVip(&quot;' + u.username + '&quot;, &quot;' + platform + '&quot;)" class="bg-emerald-500 hover:bg-emerald-600 text-white px-2 py-1 rounded text-xs font-bold ml-1" title="Mở khóa VIP: cho tìm & tải phim ngoài kho"><i class="fas fa-unlock"></i> Mở khóa VIP</button>';
                let createdCol = platform === 'honggou' ? '<td class="p-3 text-center">' + formatCreatedHtml(u.created_at) + '</td>' : '';
                return '<tr class="border-b border-gray-100 hover:bg-gray-50"><td class="p-3 font-bold text-gray-700">' + u.username + vipBadge + '</td>' + createdCol + '<td class="p-3 text-center">' + formatExpiryHtml(u.expiry_date) + '<div class="mt-1"><button onclick="addVip(&quot;' + u.username + '&quot;, &quot;' + platform + '&quot;)" class="bg-purple-100 hover:bg-purple-200 text-purple-600 border border-purple-200 px-2 py-0.5 rounded text-xs font-bold">Cộng VIP</button></div></td>' + balanceCol + '<td class="p-3 text-center">' + hwidBadge + '</td><td class="p-3 text-center"><button onclick="resetPwd(&quot;' + u.username + '&quot;, &quot;' + platform + '&quot;)" class="text-blue-500 hover:text-blue-700 bg-blue-50 p-1.5 rounded border border-blue-100 mr-1" title="Reset MK"><i class="fas fa-key"></i></button><button onclick="deleteUser(&quot;' + u.username + '&quot;, &quot;' + platform + '&quot;)" class="text-red-500 hover:text-red-700 bg-red-50 p-1.5 rounded border border-red-100" title="Xóa"><i class="fas fa-trash"></i></button>' + vipToggleBtn + '</td></tr>'; 
            }
            function formatCreatedHtml(dateStr) { if (!dateStr) return '<span class="text-gray-400 text-xs">—</span>'; let d = new Date(String(dateStr).replace(' ', 'T')); if (isNaN(d.getTime())) return '<span class="text-gray-400 text-xs">' + dateStr + '</span>'; let dd = String(d.getDate()).padStart(2,'0'); let mm = String(d.getMonth()+1).padStart(2,'0'); let yy = d.getFullYear(); let hh = String(d.getHours()).padStart(2,'0'); let mi = String(d.getMinutes()).padStart(2,'0'); return '<div class="font-bold text-gray-600">' + dd + '/' + mm + '/' + yy + '</div><div class="text-xs text-gray-400">' + hh + ':' + mi + '</div>'; }
            async function toggleVip(username, platform) {
                if(!confirm('Đổi trạng thái MỞ KHÓA VIP cho [' + username + ']?')) return;
                try {
                    const res = await adminFetch('/api/admin/users/' + username + '/toggle_vip?platform=' + platform, { method: 'POST' });
                    const d = await res.json();
                    if(d.message) alert(d.message);
                    if(platform === 'honggou') loadHonggouUsers();
                    else if(platform === 'douyin') loadDouyinUsers();
                    else loadAllInOneUsers();
                } catch(e) { alert('Lỗi kết nối'); }
            }
            async function checkQuota(username) { try { const res = await adminFetch('/api/admin/users/' + username + '/quota'); const d = await res.json(); if(d.status === 'success') { alert('Tài khoản [' + username + ']:\\nCòn ' + d.left + ' / ' + d.limit + ' lượt tải phim hôm nay.'); } else { alert('Lỗi: ' + d.message); } } catch(e) { alert("Lỗi kết nối"); } }
            async function loadHonggouUsers() { try { const res = await adminFetch('/api/admin/users?platform=honggou'); const users = await res.json(); document.getElementById('honggouTableBody').innerHTML = users.map(u => renderUserRow(u, 'honggou')).join(''); } catch(e) {} }
            let _selectedDay = null;
            async function loadDailyStats() {
                try {
                    const res = await adminFetch('/api/admin/users/daily_stats?platform=honggou&days=7');
                    const d = await res.json();
                    if(d.status !== 'success') return;
                    const m = d.month || {};
                    document.getElementById('monthSummary').innerHTML =
                        '<div class="grid grid-cols-2 md:grid-cols-4 gap-3">'
                        + '<div class="bg-gradient-to-r from-emerald-500 to-teal-600 text-white p-3 rounded-lg"><div class="text-xs text-emerald-100 font-bold"><i class="fas fa-calendar-alt mr-1"></i> THÁNG ' + (m.label||'') + ' - TẠO</div><div class="text-2xl font-bold">' + (m.created||0) + '</div><div class="text-xs text-emerald-200">tài khoản mới</div></div>'
                        + '<div class="bg-gradient-to-r from-amber-500 to-orange-600 text-white p-3 rounded-lg"><div class="text-xs text-amber-100 font-bold"><i class="fas fa-unlock mr-1"></i> THÁNG ' + (m.label||'') + ' - MỞ KHÓA</div><div class="text-2xl font-bold">' + (m.unlocked||0) + '</div><div class="text-xs text-amber-200">lượt mở VIP</div></div>'
                        + '<div class="bg-gradient-to-r from-blue-500 to-indigo-600 text-white p-3 rounded-lg"><div class="text-xs text-blue-100 font-bold"><i class="fas fa-users mr-1"></i> TỔNG TK</div><div class="text-2xl font-bold">' + (d.total_users||0) + '</div><div class="text-xs text-blue-200">toàn hệ thống</div></div>'
                        + '<div class="bg-gradient-to-r from-purple-500 to-fuchsia-600 text-white p-3 rounded-lg"><div class="text-xs text-purple-100 font-bold"><i class="fas fa-crown mr-1"></i> ĐANG MỞ KHÓA</div><div class="text-2xl font-bold">' + (d.total_unlocked||0) + '</div><div class="text-xs text-purple-200">khóa: ' + (d.total_locked||0) + '</div></div>'
                        + '</div>';
                    let html = '';
                    (d.daily||[]).forEach(day => {
                        const active = (_selectedDay === day.date) ? ' ring-2 ring-emerald-500' : '';
                        html += '<button onclick="showDayDetail(&quot;' + day.date + '&quot;)" class="text-left bg-white hover:bg-emerald-50 border border-gray-200 rounded-lg p-2 transition-all' + active + '">'
                            + '<div class="text-xs font-bold text-gray-500 mb-1"><i class="fas fa-calendar-day mr-1"></i>' + day.label + '</div>'
                            + '<div class="flex items-center justify-between"><span class="text-xs text-emerald-600 font-bold">Tạo</span><span class="text-lg font-bold text-emerald-700">' + day.created + '</span></div>'
                            + '<div class="flex items-center justify-between"><span class="text-xs text-amber-600 font-bold">Mở khóa</span><span class="text-lg font-bold text-amber-700">' + day.unlocked + '</span></div>'
                            + '</button>';
                    });
                    document.getElementById('dailyStatsRow').innerHTML = html;
                    if(_selectedDay) showDayDetail(_selectedDay);
                } catch(e) {}
            }
            async function showDayDetail(date) {
                _selectedDay = date;
                document.querySelectorAll('#dailyStatsRow button').forEach(b => b.classList.remove('ring-2','ring-emerald-500'));
                const box = document.getElementById('dayDetail');
                box.classList.remove('hidden');
                box.innerHTML = '<p class="text-gray-400 text-center py-4">Đang tải...</p>';
                try {
                    const res = await adminFetch('/api/admin/users/by_date?platform=honggou&date=' + date);
                    const d = await res.json();
                    const fmtDay = date.split('-').reverse().join('/');
                    function rowList(list) {
                        if(!list || !list.length) return '<p class="text-gray-400 text-sm py-2">Không có tài khoản nào.</p>';
                        return '<div class="space-y-1">' + list.map(u => {
                            const badge = u.vip_unlocked ? '<span class="bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full text-xs font-bold ml-2"><i class="fas fa-unlock"></i> Full</span>' : '<span class="bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full text-xs font-bold ml-2"><i class="fas fa-lock"></i> Test</span>';
                            return '<div class="flex items-center bg-white border border-gray-100 rounded px-3 py-1.5"><span class="font-bold text-gray-700">' + u.username + '</span>' + badge + '</div>';
                        }).join('') + '</div>';
                    }
                    box.innerHTML = '<div class="flex justify-between items-center mb-3 border-b pb-2"><h3 class="font-bold text-gray-700"><i class="fas fa-calendar-check text-emerald-500 mr-2"></i> Ngày ' + fmtDay + '</h3><button onclick="document.getElementById(&quot;dayDetail&quot;).classList.add(&quot;hidden&quot;); _selectedDay=null; loadDailyStats();" class="text-gray-400 hover:text-gray-600 text-sm"><i class="fas fa-times"></i> Đóng</button></div>'
                        + '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">'
                        + '<div><div class="text-sm font-bold text-emerald-600 mb-2"><i class="fas fa-user-plus mr-1"></i> TẠO trong ngày (' + d.created_count + ')</div>' + rowList(d.created) + '</div>'
                        + '<div><div class="text-sm font-bold text-amber-600 mb-2"><i class="fas fa-unlock mr-1"></i> MỞ KHÓA trong ngày (' + d.unlocked_count + ')</div>' + rowList(d.unlocked) + '</div>'
                        + '</div>';
                } catch(e) { box.innerHTML = '<p class="text-red-500 text-center py-4">Lỗi tải dữ liệu.</p>'; }
            }
            async function loadDouyinUsers() { try { const res = await adminFetch('/api/admin/users?platform=douyin'); const users = await res.json(); document.getElementById('douyinTableBody').innerHTML = users.map(u => renderUserRow(u, 'douyin')).join(''); } catch(e) {} }
            async function loadAllInOneUsers() { try { const res = await adminFetch('/api/admin/users?platform=allinone'); const users = await res.json(); document.getElementById('allinoneTableBody').innerHTML = users.map(u => renderUserRow(u, 'allinone')).join(''); } catch(e) {} }
            async function addBalanceHongguo(username) { let amount = prompt('Nạp cho [' + username + ']:'); if(amount && !isNaN(amount)) { await adminFetch('/api/admin/users/' + username + '/add_balance_hongguo', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ amount: parseInt(amount) }) }); loadHonggouUsers(); loadOverviewStats(); } }
            async function deductBalanceHongguo(username) { let amount = prompt('Trừ của [' + username + ']:'); if(amount && !isNaN(amount)) { const res = await adminFetch('/api/admin/users/' + username + '/deduct_balance_hongguo', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ amount: parseInt(amount) }) }); const d = await res.json(); if(d.message) alert(d.message); loadHonggouUsers(); loadOverviewStats(); } }
            
            async function createHongguoUser() { 
                let baseName = prompt('Tên khách hàng (Hệ thống sẽ tự thêm 4 số ngẫu nhiên):'); 
                if(!baseName) return; 
                let randomNum = Math.floor(1000 + Math.random() * 9000);
                let username = baseName.trim() + randomNum.toString();
                let password = prompt('Mật khẩu:', '123456') || '123456'; 
                let days = prompt('Số ngày VIP:', '30'); 
                if(days === null) return; days = parseInt(days) || 30; 
                let balance = prompt('Số dư (đồng):', '0'); 
                if(balance === null) return; balance = parseInt(balance) || 0; 
                let zalo = prompt('Zalo:', '') || ''; 
                const res = await adminFetch('/api/admin/users/create_hongguo', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ username: username, password: password, days: days, balance: balance, zalo: zalo }) }); 
                const d = await res.json(); 
                if(d.status === 'success') { 
                    loadHonggouUsers(); loadOverviewStats(); 
                    prompt('✅ Đã tạo thành công! Nhấn Ctrl+C để Copy gửi khách:', 'Tài khoản: ' + username + ' | Mật khẩu: ' + password);
                } else { alert(d.message || 'Lỗi'); } 
            }

            async function createTestUser() { 
                let randomNum = Math.floor(1000 + Math.random() * 9000);
                let username = "test" + randomNum.toString();
                let password = "123"; 
                const res = await adminFetch('/api/admin/users/create_hongguo', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ username: username, password: password, days: 1, balance: 0, zalo: '' }) }); 
                const d = await res.json(); 
                if(d.status === 'success') { 
                await adminFetch('/api/admin/users/' + username + '/toggle_vip?platform=honggou', { method: 'POST' });
        
                loadHonggouUsers(); loadOverviewStats(); 
                prompt('✅ Đã tạo TK Test 1 ngày (ĐÃ MỞ KHÓA VIP SẴN)! Nhấn Ctrl+C để Copy gửi khách:', 'Tài khoản: ' + username + ' | Mật khẩu: ' + password);
                } else { alert(d.message || 'Lỗi'); } 
            }

            async function addVip(username, platform) { let days = prompt('Cộng ngày VIP cho [' + username + ']:', '30'); if(days && !isNaN(days)) { await adminFetch('/api/admin/users/' + username + '/add_vip?platform=' + platform, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ days: parseInt(days) }) }); if(platform==='honggou') loadHonggouUsers(); else if(platform==='douyin') loadDouyinUsers(); else loadAllInOneUsers(); loadOverviewStats(); } }
            async function resetHwid(username, platform) { if(confirm('Mở khóa HWID cho [' + username + ']?')) { await adminFetch('/api/admin/users/' + username + '/reset_hwid?platform=' + platform, { method: 'POST' }); if(platform==='honggou') loadHonggouUsers(); else if(platform==='douyin') loadDouyinUsers(); else loadAllInOneUsers(); } }
            async function resetPwd(username, platform) { if(confirm('Reset MK [' + username + '] về "123456"?')) { const res = await adminFetch('/api/admin/users/' + username + '/reset_password?platform=' + platform, { method: 'POST' }); const d = await res.json(); alert(d.message); } }
            async function deleteUser(username, platform) { if(confirm('Xóa [' + username + ']?')) { await adminFetch('/api/admin/users/' + username + '/delete?platform=' + platform, { method: 'POST' }); if(platform==='honggou') loadHonggouUsers(); else if(platform==='douyin') loadDouyinUsers(); else loadAllInOneUsers(); loadOverviewStats(); } }
            let _allMovies = []; let _moviePage = 1; const MOVIE_PAGE_SIZE = 50;
            function _renderMovieRow(m) { const sc = {'completed': 'bg-green-100 text-green-700', 'processing': 'bg-yellow-100 text-yellow-700', 'pending': 'bg-blue-100 text-blue-700', 'partial': 'bg-red-100 text-red-700', 'idle': 'bg-gray-100 text-gray-500'}; const stMap = {'completed': 'Hoàn thành', 'processing': 'Đang tải', 'pending': 'Chờ xử lý', 'partial': 'Thiếu tập', 'idle': 'idle'}; const sClass = sc[m.status] || 'bg-gray-100 text-gray-700'; const ds = stMap[m.status] || m.status; const hl = m.db_has_link||0; const tot = m.total_episodes||0; const ok = (hl>=tot && tot>0); const pct = tot>0 ? Math.min(100, Math.round((hl/tot)*100)) : 0; const barC = ok ? '#10b981' : '#f59e0b'; const txtC = ok ? 'text-green-600' : 'text-orange-500'; const titleD = m.title ? m.title : '<span class="text-red-400 italic text-xs">Thiếu tên</span>'; const genreD = m.genres ? m.genres : '<span class="text-gray-400 italic text-xs">Chưa có</span>'; return '<tr class="border-b border-gray-100 hover:bg-gray-50"><td class="p-3 font-mono text-xs font-bold">' + m.series_id + '<br><span class="text-gray-500 font-sans text-sm">' + titleD + '</span></td><td class="p-3 text-xs text-blue-600 font-medium max-w-[150px] truncate">' + genreD + '</td><td class="p-3 text-center"><span class="px-2 py-1 rounded-full text-xs font-bold ' + sClass + '">' + ds + '</span></td><td class="p-3 text-center text-xs">' + (m.worker_id||'—') + '</td><td class="p-3 text-center font-bold">' + tot + '</td><td class="p-3 text-center"><div class="flex items-center gap-2 justify-center"><div class="w-20 bg-gray-200 rounded-full h-2"><div class="h-2 rounded-full" style="width:' + pct + '%;background:' + barC + '"></div></div><span class="font-bold ' + txtC + '">' + hl + '/' + tot + '</span></div></td><td class="p-3 text-center text-xs text-gray-500">' + (m.updated_at||'—') + '</td><td class="p-3 text-center"><button onclick="verifyMovie(&quot;' + m.job_id + '&quot;, &quot;' + m.series_id + '&quot;)" class="bg-indigo-100 hover:bg-indigo-200 text-indigo-600 px-3 py-1 rounded text-xs font-bold">Check</button></td></tr>'; }
            function _filteredMovies() { const kw = (document.getElementById('movieSearch').value || '').toLowerCase().trim(); const st = document.getElementById('movieStatusFilter').value || ''; return _allMovies.filter(m => { if (st && m.status !== st) return false; if (kw) { const t = (m.title || '').toLowerCase(); const s = (m.series_id || '').toLowerCase(); if (t.indexOf(kw) === -1 && s.indexOf(kw) === -1) return false; } return true; }); }
            function renderMoviePage() { const list = _filteredMovies(); const totalPages = Math.max(1, Math.ceil(list.length / MOVIE_PAGE_SIZE)); if (_moviePage > totalPages) _moviePage = totalPages; const start = (_moviePage - 1) * MOVIE_PAGE_SIZE; const pageItems = list.slice(start, start + MOVIE_PAGE_SIZE); document.getElementById('moviesTableBody').innerHTML = pageItems.map(_renderMovieRow).join(''); document.getElementById('movieCountLabel').innerText = 'Hiển thị ' + pageItems.length + ' / ' + list.length + ' phim'; let pg = ''; pg += '<button onclick="gotoMoviePage(' + (_moviePage-1) + ')" class="px-3 py-1 rounded border text-sm ' + (_moviePage<=1?'text-gray-300 border-gray-200':'text-gray-700 border-gray-300 hover:bg-gray-100') + '">‹ Trước</button>'; pg += '<span class="text-sm font-bold text-gray-600">Trang ' + _moviePage + ' / ' + totalPages + '</span>'; pg += '<button onclick="gotoMoviePage(' + (_moviePage+1) + ')" class="px-3 py-1 rounded border text-sm ' + (_moviePage>=totalPages?'text-gray-300 border-gray-200':'text-gray-700 border-gray-300 hover:bg-gray-100') + '">Sau ›</button>'; document.getElementById('moviePager').innerHTML = pg; }
            function gotoMoviePage(p) { const list = _filteredMovies(); const totalPages = Math.max(1, Math.ceil(list.length / MOVIE_PAGE_SIZE)); if (p < 1 || p > totalPages) return; _moviePage = p; renderMoviePage(); }
            let _searchTimer = null; function onMovieSearch() { clearTimeout(_searchTimer); _searchTimer = setTimeout(() => { _moviePage = 1; renderMoviePage(); }, 200); }
            async function loadMovies() { try { const res = await adminFetch('/api/admin/honggou_movies'); const movies = await res.json(); _allMovies = movies || []; let cC=0, cPe=0, cPr=0, cPa=0; _allMovies.forEach(m => { if(m.status==='completed') cC++; else if(m.status==='pending') cPe++; else if(m.status==='processing') cPr++; else if(m.status==='partial') cPa++; }); document.getElementById('count-completed').innerText = cC; document.getElementById('count-pending').innerText = cPe; document.getElementById('count-processing').innerText = cPr; document.getElementById('count-partial').innerText = cPa; _moviePage = 1; renderMoviePage(); } catch(e) {} }
            async function healMetadata() { if(!confirm('Quét metadata cho tất cả?')) return; const r = document.getElementById('verifyResult'); r.classList.remove('hidden'); r.innerHTML = '<p class="text-indigo-500 font-bold">Đang quét...</p>'; try { const res = await adminFetch('/api/admin/heal_metadata', { method: 'POST' }); const d = await res.json(); r.innerHTML = d.status==='ok' ? '<p class="text-green-600">' + d.message + '</p>' : '<p class="text-red-500">' + d.message + '</p>'; loadMovies(); } catch(e) {} }
            let _coverScanTimer = null;
            async function toggleCoverScan() { try { const res = await adminFetch('/api/admin/cover_scan/toggle', { method: 'POST' }); const d = await res.json(); updateCoverScanBtn(d.on); if (d.on) { startCoverScanPoll(); } else { stopCoverScanPoll(); } } catch(e) {} }
            function updateCoverScanBtn(on) { const b = document.getElementById('btnCoverScan'); if (!b) return; if (on) { b.innerHTML = 'Đang quét (bấm để dừng)'; b.classList.remove('bg-pink-500','hover:bg-pink-600'); b.classList.add('bg-red-500','hover:bg-red-600'); } else { b.innerHTML = 'Quét Ảnh Bìa'; b.classList.remove('bg-red-500','hover:bg-red-600'); b.classList.add('bg-pink-500','hover:bg-pink-600'); } }
            function startCoverScanPoll() { stopCoverScanPoll(); _coverScanTimer = setInterval(async () => { try { const res = await adminFetch('/api/admin/cover_scan/status'); const d = await res.json(); updateCoverScanBtn(d.on); const b = document.getElementById('btnCoverScan'); if (b && d.on) b.title = `Thiếu: ${d.thieu} | ${d.last||''}`; if (!d.on) stopCoverScanPoll(); } catch(e) {} }, 3000); }
            function stopCoverScanPoll() { if (_coverScanTimer) { clearInterval(_coverScanTimer); _coverScanTimer = null; } }
            async function exportIdleLinks() { try { const res = await adminFetch('/api/admin/export_idle_links'); const text = await res.text(); const blob = new Blob([text], { type: 'text/plain;charset=utf-8' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'idle_links.txt'; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url); } catch(e) { } }
            async function syncAllDrive() { alert('Vui lòng dùng nút kéo drive cho từng phim.'); }
            async function checkFixAll() { if(!confirm('Check + Sửa tất cả?')) return; const r = document.getElementById('verifyResult'); r.classList.remove('hidden'); r.innerHTML = '<p class="text-purple-500 font-bold">Đang quét...</p>'; try { const res = await adminFetch('/api/admin/check_fix_all', { method: 'POST' }); const d = await res.json(); let h = '<h3 class="font-bold text-gray-700 mb-3">Kết quả</h3><p>Tổng: ' + d.total_checked + ' | Sửa: ' + d.fixed + '</p><div class="space-y-1 max-h-60 overflow-y-auto mt-2">'; (d.details||[]).forEach(l => { h += '<p class="text-sm">' + l + '</p>'; }); h += '</div>'; r.innerHTML = h; loadMovies(); } catch(e) {} }
            async function purgeFakeMovies() { if(!confirm('Xóa phim ảo?')) return; const r = document.getElementById('verifyResult'); r.classList.remove('hidden'); r.innerHTML = '<p class="text-fuchsia-600 font-bold">Đang quét Drive...</p>'; try { const res = await adminFetch('/api/admin/purge_fake_movies', { method: 'POST' }); const d = await res.json(); let h = '<p class="font-bold text-red-500 mb-2">Xóa: ' + d.deleted + ' phim</p><div class="space-y-1 max-h-60 overflow-y-auto text-sm">'; (d.details||[]).forEach(l => { h += '<p>' + l + '</p>'; }); h += '</div>'; r.innerHTML = h; loadMovies(); } catch(e) {} }
            async function repairMovies() { if(!confirm('Sửa phim thiếu tập?')) return; try { const res = await adminFetch('/api/admin/repair_movies', { method: 'POST' }); const d = await res.json(); alert('Đã sửa ' + d.fixed + ' phim.'); loadMovies(); } catch(e) {} }
            async function deleteAllPending() { if(!confirm('Xóa TOÀN BỘ phim Pending?')) return; const r = document.getElementById('verifyResult'); r.classList.remove('hidden'); r.innerHTML = '<p class="text-red-500 font-bold">Đang xóa...</p>'; try { const res = await adminFetch('/api/admin/delete_all_pending', { method: 'POST' }); const d = await res.json(); alert(d.message); loadMovies(); r.classList.add('hidden'); } catch(e) {} }
            async function deleteAllIdle() { if(!confirm('Xóa TOÀN BỘ phim Idle (chưa có worker, chưa có link nào)? An toàn: KHÔNG đụng vào phim đang tải dở.')) return; const r = document.getElementById('verifyResult'); r.classList.remove('hidden'); r.innerHTML = '<p class="text-slate-600 font-bold">Đang xóa phim Idle...</p>'; try { const res = await adminFetch('/api/admin/delete_all_idle', { method: 'POST' }); const d = await res.json(); alert(d.message); loadMovies(); r.classList.add('hidden'); } catch(e) { r.innerHTML = '<p class="text-red-500">Lỗi</p>'; } }
            async function verifyMovie(jobId, sid) { const r = document.getElementById('verifyResult'); r.classList.remove('hidden'); r.innerHTML = '<p class="text-blue-500 font-bold">Đang quét...</p>'; try { const res = await adminFetch('/api/admin/verify_movie/' + jobId); const d = await res.json(); let dup = ''; if (d.db_duplicates && d.db_duplicates.length > 0) { let dt = d.db_duplicates.map(x => 'Tập ' + x.ep + ' (' + x.count + 'x)').join(', '); dup = '<div class="mt-2 p-2 bg-red-50 border border-red-200 rounded"><b class="text-red-600">TRÙNG:</b> ' + dt + '</div>'; } let mdb = (d.missing_db_count > 0) ? '<span class="text-red-500 font-bold">Thiếu ' + d.missing_db_count + ': [' + d.missing_db.join(',') + ']</span>' : '<span class="text-green-500 font-bold">Đủ</span>'; let mdr = (d.missing_drive_count > 0) ? '<span class="text-red-500 font-bold">Thiếu ' + d.missing_drive_count + ': [' + d.missing_drive.join(',') + ']</span>' : '<span class="text-green-500 font-bold">Đủ</span>'; let needFix = (d.missing_db_count > 0 || d.missing_drive_count > 0 || (d.web_total > 0 && d.web_total != d.total_episodes_db)); let wt = d.web_total || '?'; let title = d.web_title || sid; let fixBtn = ''; if (needFix) { let mf = wt !== '?' ? wt : d.db_episodes; fixBtn = '<div class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg flex flex-wrap items-center gap-3"><input type="number" id="mt_' + jobId + '" value="' + mf + '" class="border border-red-300 rounded px-3 py-1.5 w-24 text-center font-bold"><button onclick="fixMovie(&quot;' + jobId + '&quot;, document.getElementById(&quot;mt_' + jobId + '&quot;).value)" class="bg-red-500 hover:bg-red-600 text-white px-4 py-1.5 rounded font-bold text-sm">Sửa</button><button onclick="syncDriveToDb(&quot;' + jobId + '&quot;)" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-1.5 rounded font-bold text-sm">Kéo Drive</button><button onclick="deleteSingleMovie(&quot;' + jobId + '&quot;)" class="bg-gray-800 hover:bg-gray-900 text-white px-4 py-1.5 rounded font-bold text-sm">Xóa</button></div>'; } else { fixBtn = '<div class="mt-3 text-green-600 font-bold bg-green-50 p-2 border border-green-200 rounded flex justify-between items-center"><span>OK!</span><button onclick="deleteSingleMovie(&quot;' + jobId + '&quot;)" class="bg-gray-800 hover:bg-gray-900 text-white px-4 py-1.5 rounded font-bold text-sm">Xóa</button></div>'; } r.innerHTML = '<h3 class="font-bold text-gray-700 mb-3">' + title + '</h3><div class="grid grid-cols-3 gap-4 mb-3"><div class="bg-white p-3 rounded border"><div class="text-xs text-gray-500">Web</div><div class="text-2xl font-bold text-blue-600">' + wt + '</div></div><div class="bg-white p-3 rounded border"><div class="text-xs text-gray-500">DB</div><div class="text-2xl font-bold text-purple-600">' + d.db_episodes + '</div></div><div class="bg-white p-3 rounded border"><div class="text-xs text-gray-500">Drive</div><div class="text-2xl font-bold text-green-600">' + d.drive_episodes + '</div></div></div><p><b>DB:</b> ' + mdb + '</p><p><b>Drive:</b> ' + mdr + '</p>' + dup + fixBtn; } catch(e) {} }
            async function fixMovie(jobId, webTotal) { try { const res = await adminFetch('/api/admin/fix_movie/' + jobId + '?web_total=' + (webTotal||0), { method: 'POST' }); const d = await res.json(); alert(d.message); loadMovies(); document.getElementById('verifyResult').classList.add('hidden'); } catch(e) {} }
            async function syncDriveToDb(jobId) { if(!confirm('Kéo link từ Drive (R2)?')) return; const r = document.getElementById('verifyResult'); r.innerHTML = '<p class="text-blue-500 font-bold">Đang kéo...</p>'; try { const res = await adminFetch('/api/admin/sync_drive_to_db/' + jobId, { method: 'POST' }); const d = await res.json(); alert(d.message); loadMovies(); r.classList.add('hidden'); } catch(e) {} }
            async function deleteSingleMovie(jobId) { if(!confirm('Xóa vĩnh viễn?')) return; try { const res = await adminFetch('/api/admin/delete_movie/' + jobId, { method: 'POST' }); const d = await res.json(); alert(d.message); if(d.status==='success') { document.getElementById('verifyResult').classList.add('hidden'); loadMovies(); } } catch(e) {} }
            async function loadWorkers() { try { const res = await adminFetch('/api/admin/workers'); const workers = await res.json(); if (workers.length === 0) { document.getElementById('workerTableBody').innerHTML = ''; document.getElementById('workerEmpty').classList.remove('hidden'); document.getElementById('workerStats').innerHTML = ''; return; } document.getElementById('workerEmpty').classList.add('hidden'); let cW=0, cI=0, cB=0, cO=0; workers.forEach(w => { if(w.status==='working') cW++; else if(w.status==='idle') cI++; else if(w.status==='blocked') cB++; else cO++; }); document.getElementById('workerStats').innerHTML = '<div class="bg-green-50 p-3 rounded border border-green-200 text-center"><div class="text-xs text-green-600 font-bold">LÀM VIỆC</div><div class="text-2xl font-bold text-green-700">' + cW + '</div></div><div class="bg-yellow-50 p-3 rounded border border-yellow-200 text-center"><div class="text-xs text-yellow-600 font-bold">RẢNH</div><div class="text-2xl font-bold text-yellow-700">' + cI + '</div></div><div class="bg-red-50 p-3 rounded border border-red-200 text-center"><div class="text-xs text-red-600 font-bold">DỪNG</div><div class="text-2xl font-bold text-red-700">' + cB + '</div></div><div class="bg-gray-50 p-3 rounded border border-gray-200 text-center"><div class="text-xs text-gray-600 font-bold">OFFLINE</div><div class="text-2xl font-bold text-gray-700">' + cO + '</div></div>'; const sm = { 'working': '<span class="px-2 py-1 rounded-full text-xs font-bold bg-green-100 text-green-700">Đang làm</span>', 'idle': '<span class="px-2 py-1 rounded-full text-xs font-bold bg-yellow-100 text-yellow-700">Rảnh</span>', 'blocked': '<span class="px-2 py-1 rounded-full text-xs font-bold bg-red-100 text-red-700">Dừng</span>', 'stuck': '<span class="px-2 py-1 rounded-full text-xs font-bold bg-orange-100 text-orange-700">Kẹt</span>', 'offline': '<span class="px-2 py-1 rounded-full text-xs font-bold bg-gray-100 text-gray-500">Offline</span>' }; let html = ''; workers.forEach(w => { let ago = w.ago_seconds; let at = '—'; if (w.last_seen > 0) { if(ago<60) at=ago+' giây'; else if(ago<3600) at=Math.floor(ago/60)+' phút'; else at=Math.floor(ago/3600)+' giờ'; } let aH = w.action ? '<br><span class="text-xs text-blue-600 font-medium italic bg-blue-50 px-2 py-0.5 rounded border border-blue-200 mt-1 inline-block">' + w.action + '</span>' : ''; let jI = w.job ? '<span class="font-bold">' + w.job.title + '</span><br><span class="text-xs text-gray-500">' + w.job.progress + '</span>' + aH : (w.action ? aH : '—'); let aBtn = w.blocked ? '<button onclick="activateWorker(&quot;' + w.worker_id + '&quot;)" class="bg-green-500 hover:bg-green-600 text-white px-3 py-1 rounded text-xs font-bold">Kích hoạt</button>' : '<button onclick="stopWorker(&quot;' + w.worker_id + '&quot;)" class="bg-red-500 hover:bg-red-600 text-white px-3 py-1 rounded text-xs font-bold">Dừng</button>'; aBtn += ' <button onclick="resetWorker(&quot;' + w.worker_id + '&quot;)" class="bg-orange-500 hover:bg-orange-600 text-white px-3 py-1 rounded text-xs font-bold">Reset</button>'; html += '<tr class="border-b border-gray-100 hover:bg-gray-50"><td class="p-3 font-mono text-xs font-bold">' + w.worker_id + '</td><td class="p-3 text-center">' + sm[w.status] + '</td><td class="p-3 text-sm">' + jI + '</td><td class="p-3 text-center text-xs text-gray-500">' + at + ' trước</td><td class="p-3 text-center">' + aBtn + '</td></tr>'; }); document.getElementById('workerTableBody').innerHTML = html; } catch(e) {} }
            async function stopWorker(wid) { if(confirm('Dừng ' + wid + '?')) { await adminFetch('/api/admin/workers/' + wid + '/stop', {method:'POST'}); loadWorkers(); } }
            async function activateWorker(wid) { if(confirm('Kích hoạt ' + wid + '?')) { await adminFetch('/api/admin/workers/' + wid + '/activate', {method:'POST'}); loadWorkers(); } }
            async function resetWorker(wid) { if(confirm('Reset ' + wid + '?')) { await adminFetch('/api/admin/workers/' + wid + '/reset', {method:'POST'}); loadWorkers(); } }
        </script></body></html>"""

if __name__ == "__main__":
    import uvicorn
    # Vô hiệu hóa lệnh run tự động này để chuẩn bị chạy bằng CMD với Uvicorn multi-worker
    # Lệnh chạy mới: uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
    # uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
    pass
