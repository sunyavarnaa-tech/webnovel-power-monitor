#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Tuple

STATE_FILE = Path("data/seen_titles.json")
URL = "https://www.webnovel.com/ranking/novel/monthly/power_rank"

def get_http_client():
    # стараемся быть «похожими» на браузер; при 403 пробуем cloudscraper
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
    try:
        import requests
        session = requests.Session()
        session.headers.update(headers)
        return session
    except Exception:
        raise

def fetch_html(session) -> str:
    r = session.get(URL, timeout=30)
    if r.status_code == 403:
        try:
            import cloudscraper
            session = cloudscraper.create_scraper()
            session.headers.update({"User-Agent": "Mozilla/5.0"})
            r = session.get(URL, timeout=30)
        except Exception:
            pass
    r.raise_for_status()
    return r.text

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
    for a in soup.find_all("a", href=True):
        m = re.search(r"/book/(\d+)", a["href"])
        if not m:
            continue
        book_id = m.group(1)
        title = (a.get_text() or "").strip()
        # Отфильтруем технические ссылки типа 'Read'/'Add In Library'
        if not title or len(title) < 2 or title.lower() in {"read", "add in library"}:
            continue
        if book_id not in seen_ids:
            seen_ids.add(book_id)
            items.append((book_id, title))
    # ограничимся первыми 50 для стабильности
    return items[:50]

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

def send_telegram(message: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_* не заданы — пропускаю отправку.")
        return
    import requests
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(api, data=data, timeout=30)
    try:
        r.raise_for_status()
    except Exception as e:
        print("Ошибка Telegram:", e, r.text[:300])

def main():
    session = get_http_client()
    html = fetch_html(session)
    current = parse_titles(html)  # [(id, title), ...] в порядке рейтинга
    if not current:
        raise SystemExit("Не удалось распарсить список — структура страницы могла измениться.")

    current_ids = [i for i, _ in current]
    seen_ids = load_seen_ids()

    first_run = not STATE_FILE.exists()
    new_ids = [i for i in current_ids if i not in seen_ids]
    if first_run:
        # Первая инициализация — сохраняем, но не тревожим вас алертом
        save_seen_ids(current_ids, current)
        print(f"Первый запуск. Сохранено {len(current_ids)} ID.")
        return

    if new_ids:
        # подготовим красивое сообщение с позициями
        pos = {i: (idx + 1) for idx, (i, _) in enumerate(current)}
        lines = []
        for i in new_ids:
            title = next(t for (bid, t) in current if bid == i)
            lines.append(f"#{pos[i]:02d} — {title}")
        msg = (
            "<b>Webnovel • Monthly Power Rank</b>\n"
            "Появились <b>новые тайтлы</b> в списке за последний час:\n\n"
            + "\n".join(lines) +
            "\n\nИсточник: https://www.webnovel.com/ranking/novel/monthly/power_rank"
        )
        send_telegram(msg)
    else:
        print("Новых тайтлов не появилось.")

    # Всегда сохраняем текущее состояние
    save_seen_ids(current_ids, current)

if __name__ == "__main__":
    main()
