import os
import time
import queue
import threading
from collections import deque
from flask import Flask, render_template, jsonify, request
import requests
from parser import parse_media_info

app = Flask(__name__)

NOTION_TOKEN = "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"
NOTION_DATABASE_ID = "3a54cc55b91180d3adf0f03fa1de61b1"
PROGRESS_TITLE = "__PROGRESS_TRACKER__"
DEFAULT_START_ID = 30001

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

results_cache = []
results_lock = threading.Lock()

recent_logs = deque(maxlen=60)
log_lock = threading.Lock()

scanner_state = {
    "is_running": False,
    "scanned": 0,
    "current_id": DEFAULT_START_ID
}
state_lock = threading.Lock()

auto_started = False
auto_start_lock = threading.Lock()

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
        add_log(f"⚠️ Lỗi đọc progress Notion: {e}")
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
    with results_lock:
        if any(item["id"] == media_id for item in results_cache):
            return True

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
                
                if title != PROGRESS_TITLE:
                    return True
    except Exception as e:
        print(f"[NOTION QUERY EXCEPTION] {e}")
    return False

def save_to_notion(item: dict):
    with results_lock:
        if not any(r["id"] == item["id"] for r in results_cache):
            results_cache.append(item)

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
            },
            "Starting": {
                "rich_text": [{"text": {"content": item.get("starting", "")}}]
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

def load_initial_cache_from_notion():
    global results_cache
    add_log("🔄 Tải dữ liệu cũ từ Notion vào RAM ngầm...")
    
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    results = []
    seen_ids = set()
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

                if title == PROGRESS_TITLE:
                    continue

                media_id_list = props.get("Media ID", {}).get("title", [])
                media_id_str = media_id_list[0]["text"]["content"] if media_id_list else "0"
                try:
                    media_id = int(media_id_str)
                except ValueError:
                    media_id = media_id_str

                if media_id in seen_ids:
                    continue
                seen_ids.add(media_id)

                if not title:
                    title = f"Media {media_id}"
                
                url_val = props.get("URL", {}).get("url") or ""
                cover_url = props.get("Cover URL", {}).get("url") or ""
                
                starting_list = props.get("Starting", {}).get("rich_text", [])
                starting_val = starting_list[0]["text"]["content"] if starting_list else ""

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
                    "url": url_val,
                    "starting": starting_val
                })

            has_more = data.get("has_more", False)
            next_cursor = data.get("next_cursor")
        except Exception as e:
            print(f"[FETCH EXCEPTION] {e}")
            break

    with results_lock:
        results_cache = results
    add_log(f"⚡ Đã tải xong {len(results)} phim cũ vào RAM!")

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
        
        if scanner_state["scanned"] % 10 == 0:
            update_progress_to_notion(media_id)

def run_background_scanner(threads=2, delay_ms=500):
    global scanner_state
    
    # 1. BẬT TRẠNG THÁI RUNNING NGAY LẬP TỨC
    with state_lock:
        scanner_state["is_running"] = True
        scanner_state["scanned"] = 0

    add_log("🚀 KÍCH HOẠT TIẾN TRÌNH QUÉT NGẦM...")

    try:
        # Lấy mốc ID an toàn
        try:
            start_id = get_progress_from_notion() + 1
        except Exception as ex:
            add_log(f"⚠️ Không đọc được progress, dùng mặc định {DEFAULT_START_ID}")
            start_id = DEFAULT_START_ID

        with state_lock:
            scanner_state["current_id"] = start_id

        add_log(f"▶️ Bắt đầu quét từ Media ID: {start_id}")

        # Tải cache cũ ngầm
        threading.Thread(target=load_initial_cache_from_notion, daemon=True).start()

        delay_sec = delay_ms / 1000.0
        task_queue = queue.Queue(maxsize=100)

        def worker():
            while scanner_state["is_running"]:
                try:
                    m_id = task_queue.get(timeout=1)
                except queue.Empty:
                    continue

                try:
                    scan_single_id(m_id, delay_sec)
                except Exception as e:
                    add_log(f"❌ Lỗi xử lý ID {m_id}: {e}")
                finally:
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
            t.join(timeout=2)

    except Exception as main_ex:
        add_log(f"❌ [CRASH BỘ QUÉT]: {main_ex}")
    finally:
        if scanner_state["current_id"] > 0:
            try:
                update_progress_to_notion(scanner_state["current_id"])
            except Exception:
                pass

        with state_lock:
            scanner_state["is_running"] = False
        add_log("🏁 Tiến trình quét đã tạm dừng!")

def trigger_scanner():
    with state_lock:
        already_running = scanner_state["is_running"]
    if not already_running:
        thread = threading.Thread(target=run_background_scanner, args=(2, 500), daemon=True)
        thread.start()

@app.before_request
def ensure_auto_start():
    global auto_started
    if not auto_started:
        with auto_start_lock:
            if not auto_started:
                auto_started = True
                add_log("🚀 Tự động kích hoạt Auto-Start 24/7...")
                trigger_scanner()

# === ROUTES ===

@app.route("/")
def index():
    return render_template("index.html")

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
        return jsonify({"status": "error", "message": "Tiến trình quét đang chạy ngầm rồi!"}), 400

    data = request.json or {}
    threads = int(data.get("threads", 2))
    delay = int(data.get("delay", 500))

    thread = threading.Thread(target=run_background_scanner, args=(threads, delay), daemon=True)
    thread.start()

    return jsonify({"status": "success", "message": "Đã tiếp tục quét!"})

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
    with results_lock:
        return jsonify(list(results_cache))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)