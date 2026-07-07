# Manual online source

Ручной источник онлайнов — `source="manual"`, третий наравне с `telegram` / `discord`.

## Storage-слой (`core/storage.py`)

- Колонка `manual_status` в таблице `users` (рядом с `telegram_status` / `discord_status`).
  Добавлена в актуальную схему, в legacy-миграцию и как `ALTER TABLE` для уже-мигрированных БД
  (добавляется автоматически при `storage.init()`, без потери данных).
- Маппинг `source → колонка` — словарь `_STATUS_COLUMN` + хелпер `_status_column()`.
  Раньше был хардкод `telegram_status if source=="telegram" else discord_status` —
  с ним `manual` молча писался бы в `discord_status`. Не возвращать тернарник.
- `current_status = online`, если онлайн **любой** из трёх источников
  (telegram/discord/manual). Общая константа `_RECOMPUTE_CURRENT_STATUS`,
  используется в `append_event()` и `update_user_status()`.
- API: `_user_summary` в `web/routes.py` отдаёт `manual_status`.

Дедуп, детекция сна и timeline-график работают с manual «бесплатно»:
дедуп per-source; sleep-детектор читает merged-поток всех источников;
график (`static/js/charts.js`) для незнакомого source берёт `successColor`
и имя source как метку строки.

## Хранение времени

Всё в БД — **UTC**, ISO 8601 с суффиксом `Z` (`"2026-07-07T06:00:00Z"`).
Фронтенд при показе timeline/таблиц сдвигает на пояс **браузера** (`new Date(iso)`).
`current_tz_offset` (оценка по сну) — отдельная штука, только для индикатора
«локальное время пользователя» в шапке карточки, к хранению не относится.

## Утилита `add_manual.py`

Добавляет ручной онлайн «от и до»: пишет 2 события `manual`
(`online` в начале, `offline` в конце) + пересчёт сна `analyze(..., full=True)`.

```bash
python add_manual.py <data_dir> --user <ident> \
    --from "2026-07-06 23:00 +03:00" --to "2026-07-07 08:00 +03:00"
```

- **Время только с явным оффсетом** (`+03:00` / `+0300` / `+03`, разделитель дата/время —
  пробел или `T`, оффсет через пробел или слитно). `Z`/UTC и время без оффсета → ошибка.
  Внутри конвертит в UTC `...Z`.
- `--user` резолвится по порядку: internal `user_id` → `telegram_id` → `discord_id` → `label`
  (регистронезависимо). Не нашёл → печатает список пользователей.
  ⚠️ Числовой резолв идёт первым, поэтому чисто числовой `label` перехватится как id.
- `--dry-run` — превью без записи. `--to` должен быть строго позже `--from` (иначе exit 2).
- Использует ту же `load_config` и путь БД (`<data_dir>/nickutc.db`), что и `main.py`.
- `full=True` осознанно: интервал может лечь в любую точку истории, вне инкрементального окна.
