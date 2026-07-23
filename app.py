import time
import queue
import threading
from flask import Flask, render_template, jsonify, request
import requests
from parser import parse_media_info

app = Flask(__name__)

# === CẤU HÌNH NOTION API CỦA BẠN ===
NOTION_TOKEN = "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"
NOTION_DATABASE_ID = "3a54cc55b91180d3adf0f03fa1de61b1"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# Header giả lập trình duyệt Chrome tiêu chuẩn chống ngắt kết nối
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

# 1. Kiểm tra Media ID đã tồn tại trên Notion chưa
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
            return len(results) > 0
    except Exception as e:
        print(f"[NOTION QUERY EXCEPTION] {e}")
    return False

# 2. Lưu phim mới vào Notion Database
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
            print(f"✅ [NOTION SUCCESS] Đã lưu thành công ID {item['id']} - '{item['title']}' vào Notion!")
        else:
            print(f"❌ [NOTION ERROR {resp.status_code}] Khi lưu ID {item['id']}: {resp.text}")
    except Exception as e:
        print(f"[NOTION SAVE EXCEPTION] {e}")

# 3. Lấy toàn bộ danh sách phim từ Notion hiển thị lên Web
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
                
                media_id_list = props.get("Media ID", {}).get("title", [])
                media_id_str = media_id_list[0]["text"]["content"] if media_id_list else "0"
                try:
                    media_id = int(media_id_str)
                except ValueError:
                    media_id = media_id_str

                title_list = props.get("Title", {}).get("rich_text", [])
                title = title_list[0]["text"]["content"] if title_list else f"Media {media_id}"
                
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

# Quản lý trạng thái tiến trình quét
scanner_state = {
    "is_running": False,
    "scanned": 0,
    "total": 0,
    "current_id": 0
}
state_lock = threading.Lock()

# Xử lý quét 1 ID đơn lẻ
def scan_single_id(media_id, delay_sec):
    global scanner_state
    if not scanner_state["is_running"]:
        return

    print(f"\n🔍 [START SCAN] Đang kiểm tra Media ID: {media_id}...")

    if not is_in_notion(media_id):
        url = f"https://www.bilibili.tv/en/media/{media_id}"
        max_retries = 3
        for attempt in range(max_retries):
            if not scanner_state["is_running"]:
                break
            try:
                # Tự động đóng Session giải phóng bộ nhớ RAM bằng `with`
                with requests.Session() as session:
                    session.headers.update(DEFAULT_HEADERS)
                    resp = session.get(url, timeout=10)
                    if resp.status_code == 200:
                        resp.encoding = 'utf-8'
                        info = parse_media_info(resp.text, media_id)
                        if info and scanner_state["is_running"]:
                            save_to_notion(info)
                    else:
                        print(f"  [HTTP ERROR {resp.status_code}] ID {media_id}")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5)
                else:
                    print(f"  [FAILED] Lỗi kết nối ID {media_id}: {e}")

        if delay_sec > 0:
            time.sleep(delay_sec)
    else:
        print(f"  [SKIPPED] ID {media_id} đã có sẵn trên Notion.")

    with state_lock:
        scanner_state["scanned"] += 1
        scanner_state["current_id"] = media_id

# Điều phối đa luồng tiết kiệm RAM bằng Hàng chờ (Queue)
def run_background_scanner(start_id, end_id, threads, delay_ms):
    global scanner_state
    
    with state_lock:
        scanner_state["is_running"] = True
        scanner_state["scanned"] = 0
        scanner_state["total"] = end_id - start_id + 1

    delay_sec = delay_ms / 1000.0
    
    # Giới hạn tối đa 100 task trong RAM để không bao giờ tràn 512MB RAM Render
    task_queue = queue.Queue(maxsize=100)

    def worker():
        while scanner_state["is_running"]:
            try:
                m_id = task_queue.get(timeout=1)
            except queue.Empty:
                if task_queue.empty() and scanner_state["scanned"] >= scanner_state["total"]:
                    break
                continue

            scan_single_id(m_id, delay_sec)
            task_queue.task_done()

    workers = []
    for _ in range(threads):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        workers.append(t)

    # Nạp từng ID từ từ vào hàng chờ
    for m_id in range(start_id, end_id + 1):
        if not scanner_state["is_running"]:
            break
        while scanner_state["is_running"]:
            try:
                task_queue.put(m_id, timeout=0.5)
                break
            except queue.Full:
                continue

    for t in workers:
        t.join()

    with state_lock:
        scanner_state["is_running"] = False
    print("\n🏁 [HOÀN THÀNH] Tiến trình quét đã xong!")

# === FLASK ROUTES ===

@app.route("/")
def index():
    return render_template("index.html")

# Đường dẫn siêu nhẹ dành riêng cho UptimeRobot
@app.route("/ping")
def ping():
    return "OK", 200

@app.route("/api/start", methods=["POST"])
def start_scan():
    global scanner_state
    if scanner_state["is_running"]:
        return jsonify({"status": "error", "message": "Tiến trình quét đang chạy!"}), 400

    data = request.json or {}
    start_id = int(data.get("start_id", 0))
    end_id = int(data.get("end_id", 0))
    threads = int(data.get("threads", 2))  # Mặc định 2 luồng cực nhẹ
    delay = int(data.get("delay", 500))     # Mặc định delay 500ms an toàn

    if start_id > end_id or start_id <= 0:
        return jsonify({"status": "error", "message": "ID không hợp lệ!"}), 400

    thread = threading.Thread(target=run_background_scanner, args=(start_id, end_id, threads, delay))
    thread.daemon = True
    thread.start()

    return jsonify({"status": "success", "message": "Bắt đầu quét ngầm thành công!"})

@app.route("/api/stop", methods=["POST"])
def stop_scan():
    global scanner_state
    with state_lock:
        scanner_state["is_running"] = False
    return jsonify({"status": "success", "message": "Đã gửi lệnh dừng!"})

@app.route("/api/status")
def get_status():
    with state_lock:
        return jsonify({
            "is_running": scanner_state["is_running"],
            "scanned": scanner_state["scanned"],
            "total": scanner_state["total"],
            "current_id": scanner_state["current_id"]
        })

@app.route("/api/results")
def get_results():
    results = fetch_all_from_notion()
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)