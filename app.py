import time
import queue
import threading
from collections import deque
from flask import Flask, render_template, jsonify, request
import requests
from parser import parse_media_info

app = Flask(__name__)

# === THÔNG TIN NOTION CỦA BẠN ===
NOTION_TOKEN = "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"
NOTION_DATABASE_ID = "3a54cc55b91180d3adf0f03fa1de61b1"
PROGRESS_TITLE = "__PROGRESS_TRACKER__"
DEFAULT_START_ID = 60001

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

# Hàng chờ lưu 60 dòng log mới nhất để hiển thị real-time
recent_logs = deque(maxlen=60)
log_lock = threading.Lock()

def add_log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    print(entry)
    with log_lock:
        recent_logs.append(entry)

def get_progress_from_notion() -> int:
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Title",
            "rich_text": {
                "equals": PROGRESS_TITLE
            }
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=10)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                props = results[0].get("properties", {})
                media_id_list = props.get("Media ID", {}).get("title", [])
                if media_id_list:
                    return int(media_id_list[0]["text"]["content"])
    except Exception as e:
        print(f"[PROGRESS FETCH EXCEPTION] {e}")
    return DEFAULT_START_ID - 1

def update_progress_to_notion(last_id: int):
    url_query = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload_query = {
        "filter": {
            "property": "Title",
            "rich_text": {
                "equals": PROGRESS_TITLE
            }
        }
    }
    try:
        resp = requests.post(url_query, json=payload_query, headers=NOTION_HEADERS, timeout=10)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                page_id = results[0]["id"]
                url_update = f"https://api.notion.com/v1/pages/{page_id}"
                payload_update = {
                    "properties": {
                        "Media ID": {
                            "title": [{"text": {"content": str(last_id)}}]
                        }
                    }
                }
                requests.patch(url_update, json=payload_update, headers=NOTION_HEADERS, timeout=10)
                return

        url_create = "https://api.notion.com/v1/pages"
        payload_create = {
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": {
                "Media ID": {
                    "title": [{"text": {"content": str(last_id)}}]
                },
                "Title": {
                    "rich_text": [{"text": {"content": PROGRESS_TITLE}}]
                }
            }
        }
        requests.post(url_create, json=payload_create, headers=NOTION_HEADERS, timeout=10)
    except Exception as e:
        print(f"[PROGRESS UPDATE EXCEPTION] {e}")

def is_in_notion(media_id: int) -> bool:
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Media ID",
            "title": {
                "equals": str(media_id)
            }
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=10)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            for page in results:
                props = page.get("properties", {})
                title_list = props.get("Title", {}).get("rich_text", [])
                title = title_list[0]["text"]["content"] if title_list else ""
                
                # Bỏ qua dòng ghi nhớ tiến trình, chỉ tính trùng nếu là bộ phim thực tế
                if title != PROGRESS_TITLE:
                    return True
    except Exception as e:
        print(f"[NOTION QUERY EXCEPTION] {e}")
    return False

def save_to_notion(item: dict):
    url = "https://api.notion.com/v1/pages"
    
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Media ID": {
                "title": [{"text": {"content": str(item["id"])}}]
            },
            "Title": {
                "rich_text": [{"text": {"content": item["title"]}}]
            },
            "URL": {
                "url": item["url"]
            },
            "Cover URL": {
                "url": item["image"]
            }
        }
    }

    if item.get("image"):
        payload["cover"] = {
            "type": "external",
            "external": {"url": item["image"]}
        }

    try:
        resp = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=10)
        if resp.status_code in [200, 201]:
            add_log(f"✅ Đã lưu ID {item['id']} - '{item['title']}' vào Notion!")
        else:
            add_log(f"❌ Lỗi lưu Notion ID {item['id']} (Code {resp.status_code})")
    except Exception as e:
        add_log(f"❌ Exception lưu Notion: {e}")

def fetch_all_from_notion() -> list:
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    results = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        try:
            resp = requests.post(url, json=payload, headers=NOTION_HEADERS, timeout=10)
            if resp.status_code != 200:
                break
            
            data = resp.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                
                title_list = props.get("Title", {}).get("rich_text", [])
                title = title_list[0]["text"]["content"] if title_list else ""

                # Ẩn bản ghi lưu tiến trình khỏi danh sách bảng Web
                if title == PROGRESS_TITLE:
                    continue

                media_id_list = props.get("Media ID", {}).get("title", [])
                media_id_str = media_id_list[0]["text"]["content"] if media_id_list else "0"
                try:
                    media_id = int(media_id_str)
                except ValueError:
                    media_id = media_id_str

                if not title:
                    title = f"Media {media_id}"
                
                url_val = props.get("URL", {}).get("url") or ""
                cover_url = props.get("Cover URL", {}).get("url") or ""

                if not cover_url and page.get("cover"):
                    cover_obj = page.get("cover", {})
                    if cover_obj.get("type") == "external":
                        cover_url = cover_obj.get("external", {}).get("url", "")
                    elif cover_obj.get("type") == "file":
                        cover_url = cover_obj.get("file", {}).get("url", "")

                results.append({
                    "id": media_id,
                    "title": title,
                    "image": cover_url,
                    "url": url_val
                })

            has_more = data.get("has_more", False)
            next_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"[FETCH EXCEPTION] {e}")
            break

    return results

scanner_state = {
    "is_running": False,
    "scanned": 0,
    "current_id": 0
}
state_lock = threading.Lock()

def scan_single_id(media_id, delay_sec):
    global scanner_state
    if not scanner_state["is_running"]:
        return

    add_log(f"🔍 Kiểm tra Media ID: {media_id}...")

    if not is_in_notion(media_id):
        url = f"https://www.bilibili.tv/en/media/{media_id}"
        max_retries = 3
        for attempt in range(max_retries):
            if not scanner_state["is_running"]:
                break
            try:
                # Tự động đóng Session bằng `with` để giải phóng RAM
                with requests.Session() as session:
                    session.headers.update(DEFAULT_HEADERS)
                    resp = session.get(url, timeout=10)
                    if resp.status_code == 200:
                        resp.encoding = 'utf-8'
                        info = parse_media_info(resp.text, media_id, log_func=add_log)
                        if info and scanner_state["is_running"]:
                            save_to_notion(info)
                    else:
                        add_log(f"❌ ID {media_id}: Lỗi HTTP {resp.status_code}")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5)
                else:
                    add_log(f"❌ ID {media_id}: Lỗi kết nối sau {max_retries} lần thử")

        if delay_sec > 0:
            time.sleep(delay_sec)
    else:
        add_log(f"⏭️ ID {media_id}: Đã có sẵn trên Notion (Skip)")

    with state_lock:
        scanner_state["scanned"] += 1
        scanner_state["current_id"] = media_id
        
        # Cứ mỗi 10 ID thì lưu vị trí lên Notion
        if scanner_state["scanned"] % 10 == 0:
            update_progress_to_notion(media_id)

def run_background_scanner(threads=2, delay_ms=500):
    global scanner_state
    
    start_id = get_progress_from_notion() + 1
    add_log(f"🚀 Khởi chạy tiến trình quét từ ID: {start_id}")

    with state_lock:
        scanner_state["is_running"] = True
        scanner_state["scanned"] = 0
        scanner_state["current_id"] = start_id - 1

    delay_sec = delay_ms / 1000.0
    task_queue = queue.Queue(maxsize=100)

    def worker():
        while scanner_state["is_running"]:
            try:
                m_id = task_queue.get(timeout=1)
            except queue.Empty:
                continue

            scan_single_id(m_id, delay_sec)
            task_queue.task_done()

    workers = []
    for _ in range(threads):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        workers.append(t)

    current_m_id = start_id
    while scanner_state["is_running"]:
        try:
            task_queue.put(current_m_id, timeout=0.5)
            current_m_id += 1
        except queue.Full:
            continue

    for t in workers:
        t.join()

    if scanner_state["current_id"] > 0:
        update_progress_to_notion(scanner_state["current_id"])

    with state_lock:
        scanner_state["is_running"] = False
    add_log("🏁 Tiến trình quét đã tạm dừng!")

# === CHẾ ĐỘ AUTO-START: KHỞI CHẠY NGAY KHI MỞ APP ===
def auto_start_scanner():
    time.sleep(3)
    with state_lock:
        already_running = scanner_state["is_running"]
    if not already_running:
        add_log("🚀 Tự động kích hoạt Auto-Start 24/7...")
        thread = threading.Thread(target=run_background_scanner, args=(2, 500))
        thread.daemon = True
        thread.start()

init_thread = threading.Thread(target=auto_start_scanner, daemon=True)
init_thread.start()

# === ROUTES ===

@app.route("/")
def index():
    return render_template("index.html")

# Ping route nhẹ cho UptimeRobot
@app.route("/ping")
def ping():
    return "OK", 200

@app.route("/api/progress")
def get_progress():
    last_id = get_progress_from_notion()
    return jsonify({
        "last_scanned_id": last_id,
        "next_start_id": last_id + 1
    })

@app.route("/api/start", methods=["POST"])
def start_scan():
    global scanner_state
    if scanner_state["is_running"]:
        return jsonify({"status": "error", "message": "Tiến trình quét đang chạy ngầm!"}), 400

    data = request.json or {}
    threads = int(data.get("threads", 2))
    delay = int(data.get("delay", 500))

    thread = threading.Thread(target=run_background_scanner, args=(threads, delay))
    thread.daemon = True
    thread.start()

    return jsonify({"status": "success", "message": "Đã bấm tiếp tục quét!"})

@app.route("/api/stop", methods=["POST"])
def stop_scan():
    global scanner_state
    with state_lock:
        scanner_state["is_running"] = False
    return jsonify({"status": "success", "message": "Đã gửi lệnh dừng tiến trình!"})

@app.route("/api/status")
def get_status():
    with state_lock:
        with log_lock:
            logs = list(recent_logs)
        return jsonify({
            "is_running": scanner_state["is_running"],
            "scanned": scanner_state["scanned"],
            "current_id": scanner_state["current_id"],
            "logs": logs
        })

@app.route("/api/results")
def get_results():
    results = fetch_all_from_notion()
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)