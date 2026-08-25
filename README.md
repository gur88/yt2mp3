# AudioGrab (yt2mp3)

Извлекает аудиодорожку из YouTube, TikTok, SoundCloud и VK в MP3, AAC или Opus — **видео не скачивается**. Работает публично на [audiograb.ru](https://audiograb.ru).

Есть обрезка фрагмента, нормализация громкости, редактирование тегов, встроенная обложка и отдельная страница загрузки обложек YouTube. Файл отдаётся один раз и сразу удаляется с сервера — ничего не хранится.

## Требования

- Python 3.12 (версия на проде)
- ffmpeg в системе

## Запуск локально

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python app.py                     # Windows: .venv\Scripts\python app.py
```

Откроется на `http://localhost:5000`.

## Документация

Подробности — в `.claude/skills/project-knowledge/references/`:

- `project.md` — что это, для кого, границы задачи
- `architecture.md` — стек, структура, как устроены загрузка и конвертация
- `patterns.md` — принятые в проекте соглашения
- `deployment.md` — сервер, CI/CD, мониторинг, откат
