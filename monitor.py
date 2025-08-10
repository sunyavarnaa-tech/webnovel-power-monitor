#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, time, random
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Tuple

STATE_FILE = Path("data/seen_titles.json")
URL = "https://www.webnovel.com/ranking/novel/monthly/power_rank"

# -------------------- HTTP --------------------

def make_scraper():
    """
    Всегда используем cloudscraper (поверх requests), чтобы обойти Cloudflare.
    """
    try:
        import cloudscraper
    except ImportError:
        raise SystemExit("Требуется пакет 'cloudscraper'. Он устанавливается в workflow.")
    # эмулируем Chrome на Windows
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return scraper

def fetch_html_with_retries(url: str, retries: int = 4, base_delay: float = 2.0) -> str:
    s = make_scraper()
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = s.get(url, timeout=40)
            # 2xx — отлично
            if 200 <= r.status_code < 300 and r.text:
                return r.text
            # 403/5xx — пробуем ещё раз
            if r.status_code in (403, 429) or r.status_code >= 500:
                raise RuntimeError(f"Bad status: {r.status_code}")
            # другие неожиданные коды тоже считаем ошибкой
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_exc = e
            # джиттер + экспоненциальный бэкофф
            sleep_s = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.8)
            print(f"[warn] fetch attempt {attempt}/{retries} failed: {e}. Sleeping {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise SystemExit(f"Не удалось получить HTML после {retries} попыток: {last_exc}")

# -------------------- Парсинг --------------------

def parse_titles(html: str) -> List[Tuple[str, str]]:
    """
    Возвращает список (book_id, title) в порядке рейтинга.
    Берём ссылки вида /book/<id> и их текст.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise SystemExit("Требуется beautifulsoup4 (установит workflow).")

    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_ids = set()

    # Бежим по ссылкам и вытаскиваем /book/<id>
    for a in soup.find_all("a", href=True):
        m = re.search(r"/book/(\d+)", a["href"])
        if not m:
            continue
        book_id = m.group(1)
        title = (a.get_text() or "").strip()
        # отсечём технические надписи
        if not title or len(title) < 2 or title.lower() in {"read", "add in library"}:
            continue
        if book_id not in seen_ids:
            seen_ids.add(book_id)
            items.append((book_id, title))

    return items[:50]

# -------------------- Состояние --------------------

def load_seen_ids() -> List[str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8")).get("ids", [])
        except Exception:
            return []
    return []

def save_seen_ids(ids: List[str], snapshot: List[Tuple[str, str]]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ids": ids,
        "snapshot": [{"id": i, "title": t} for i, t in snapshot],
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": URL,
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

# -------------------- Уведомления --------------------

def send_telegram(message: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_* не заданы — пропускаю отправку.")
        return
    import requests
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(api, data=data, timeout=30)
    try:
        r.raise_for_status()
    except Exception as e:
        print("Ошибка Telegram:", e, r.text[:300])

# -------------------- Main --------------------

def main():
    html = fetch_html_with_retries(URL)
    current = parse_titles(html)
    if not current:
        raise SystemExit("Не удалось распарсить список — структура страницы могла измениться.")

    current_ids = [i for i, _ in current]
    seen_ids = load_seen_ids()
    first_run = not STATE_FILE.exists()
    new_ids = [i for i in current_ids if i not in seen_ids]

    # Тестовый режим: отправить сообщение всегда, если установлена переменная ALWAYS_ALERT_ON_RUN=1
    always_alert = os.getenv("ALWAYS_ALERT_ON_RUN") == "1"

    if first_run and not always_alert:
        save_seen_ids(current_ids, current)
        print(f"Первый запуск. Сохранено {len(current_ids)} ID.")
        return

    if new_ids or always_alert:
        pos = {i: (idx + 1) for idx, (i, _) in enumerate(current)}
        lines = []
        for i in (new_ids if new_ids else current_ids[:5]):  # в тесте покажем первые 5
            title = next(t for (bid, t) in current if bid == i)
            p = pos.get(i, "?")
            lines.append(f"#{p:02d} — {title}")
        intro = "Тестовое уведомление (ручной запуск):" if always_alert and not new_ids else \
                "Появились <b>новые тайтлы</b>:"
        msg = (
            "<b>Webnovel • Monthly Power Rank</b>\n"
            f"{intro}\n\n"
            + "\n".join(lines) +
            "\n\nИсточник: https://www.webnovel.com/ranking/novel/monthly/power_rank"
        )
        send_telegram(msg)
    else:
        print("Новых тайтлов не появилось.")

    save_seen_ids(current_ids, current)

if __name__ == "__main__":
    main()
