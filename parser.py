import html
import re
from datetime import datetime
from bs4 import BeautifulSoup

def format_date_iso(date_raw: str) -> str:
    """Chuyển 'Oct 5, 2004' thành '2004-10-05' để sắp xếp chuẩn theo thời gian"""
    if not date_raw:
        return ""
    try:
        dt = datetime.strptime(date_raw.strip(), "%b %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return date_raw.strip()

def parse_media_info(html_content: str, media_id: int, log_func=None) -> dict | None:
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "lxml")
    result = None

    try:
        if soup.select_one(".bstar-error"):
            if log_func: log_func(f"❌ ID {media_id}: Trang không tồn tại")
            return None

        if "This video has been removed" in html_content or "Video này đã bị gỡ" in html_content:
            if log_func: log_func(f"❌ ID {media_id}: Video đã bị gỡ")
            return None

        # 1. Trích xuất Region
        region = ""
        labels = soup.select(".detail-table__label")
        for label in labels:
            label_text = label.get_text(strip=True).lower()
            if "region" in label_text:
                next_elem = label.find_next_sibling()
                if next_elem:
                    region = next_elem.get_text(strip=True)
                break

        if not region:
            reg_match = re.search(r'Region["\s:]+([^"<&\n]+)', html_content, re.IGNORECASE)
            if reg_match:
                region = reg_match.group(1).strip()

        if not region:
            if log_func: log_func(f"⚠️ ID {media_id}: Không tìm thấy thông tin Region")
            return None

        # LỌC DUY NHẤT REGION: JAPAN
        if "japan" not in region.lower():
            if log_func: log_func(f"⏭️ ID {media_id}: Bỏ qua (Region là '{region}')")
            return None

        # 2. Trích xuất Starting (Ngày phát sóng)
        starting_raw = ""
        for label in labels:
            label_text = label.get_text(strip=True).lower()
            if "starting" in label_text:
                next_elem = label.find_next_sibling()
                if next_elem:
                    starting_raw = next_elem.get_text(strip=True)
                break

        if not starting_raw:
            m = re.search(r'Starting["\s:]+([^"<&\n]+)', html_content, re.IGNORECASE)
            if m:
                starting_raw = m.group(1).strip()

        starting_date = format_date_iso(starting_raw)

        # 3. Trích xuất Title
        title_elem = soup.select_one(".detail-header__title")
        title = title_elem.get_text(strip=True) if title_elem else f"Media {media_id}"
        title = html.unescape(title)

        # 4. Trích xuất Image Cover
        img_elem = soup.select_one(".media-info__cover img")
        image_url = ""
        if img_elem:
            image_url = img_elem.get("src") or img_elem.get("data-src") or ""

        url = f"https://www.bilibili.tv/en/media/{media_id}"

        if log_func: log_func(f"🎉 ID {media_id}: TÌM THẤY PHIM JAPAN '{title}' ({starting_date})")

        result = {
            "id": media_id,
            "title": title,
            "image": image_url,
            "url": url,
            "starting": starting_date
        }
    finally:
        soup.decompose()

    return result