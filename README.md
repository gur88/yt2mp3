# YT → MP3

Извлекает аудио из YouTube (и других сайтов) напрямую в MP3 — **видео не скачивается**.

## Требования

- Python 3.9+
- ffmpeg (установлен в системе)

## Установка и запуск

```bash
# 1. Создать виртуальное окружение и установить зависимости
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip install -r requirements.txt

# 2. Запустить сервер
.venv/bin/python app.py                     # Windows: .venv\Scripts\python app.py

# 3. Открыть в браузере
http://localhost:5000
```

## Как это работает

`yt-dlp` с флагом `--extract-audio` скачивает **только аудиопоток** (обычно `.webm` или `.m4a`),
а `ffmpeg` конвертирует его в MP3. Видеодорожка никогда не загружается.

После скачивания файл автоматически удаляется с сервера.
