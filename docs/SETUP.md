# Как заставить Яндекс Станцию играть музыку с домашнего медиасервера

Практическое руководство: собираем связку **Алиса → навык → Music Assistant → Станция**,
чтобы колонка играла FLAC с домашнего сервера, управлялась голосом и при этом
аудио не проходило через облако Яндекса.

Документ писался по итогам двух дней мучений (14–15.11.2026), поэтому в нём есть
не только «как надо», но и все грабли, на которые мы наступили, — с симптомами,
причинами и лечением. Если у вас «всё настроено, но звука нет» — начните с
раздела [Чек-лист: звука нет](#чек-лист-звука-нет).

---

## 1. Что получится

- «Алиса, запусти навык домашняя музыка, включи сплин» — играет ваш FLAC с сервера.
- «Алиса, далее» / «пауза» / «громкость 5» — работает **без запуска навыка**, через умный дом.
- «Алиса, … что играет», «избранное», «похожее», «перемешай», «перемотай», «лайк» — навыком.
- Аудио идёт по локальной сети: колонка сама тянет поток с вашего сервера.

Что **не** получится (ограничения платформы, см. раздел 10):

- навык не может сам отдавать Алисе произвольную музыку (только файлы до 120 с);
- без умного дома транспортные команды работают лишь внутри 5-секундной сессии;
- Алиса не умеет голосом спрашивать «что играет на колонке» через умный дом.

---

## 2. Как это устроено

```
Алиса (облако) ──HTTPS──► Caddy :443 ──► навык (FastAPI) :8100
      │                       │                │
      │                       │                ▼ JSON-RPC
      │                       └──► Music Assistant :8095
      │                                     │
      └──умный дом──HTTPS──► Caddy ─────────┘
                                            ├─► Navidrome :4533 (FLAC с диска)
                                            └─► Станция :1961 (Glagol, локально)
```

Почему так, а не проще:

1. **Навык Алисы не умеет играть музыку.** В Диалогах навык может отдать только
   предзагруженный файл до 120 секунд и 5 МБ. Никаких «вот тебе URL, играй».
2. **Значит, играть должен кто-то другой.** Music Assistant умеет отдавать поток и
   знает протокол колонки (Glagol). Навык — только «мозг»: разбирает фразу и дёргает MA.
3. **Транспорт — через умный дом.** Сессия навыка живёт ~5 секунд тишины, и продлить
   её нельзя. Поэтому «далее/пауза/громкость» вынесены в плагин Yandex Smart Home —
   он работает без сессии вообще.

---

## 3. Что понадобится

| Компонент | Что у нас | Обязательность |
|---|---|---|
| Яндекс Станция | Станция Макс (`yandexstation_2`) | обязательно, любая Станция/Мини с Glagol |
| Сервер 24/7 | сервер, Debian, 6 ГБ RAM + 2 ГБ swap | обязательно |
| Медиасервер | Navidrome в Docker, библиотека FLAC | обязателен (или любой Subsonic-совместимый) |
| Music Assistant | Docker, порт 8095 | обязателен |
| Обратный прокси | Caddy, TLS, внешний 443 | обязателен (Яндексу нужен HTTPS до вебхука) |
| Домен | `music.example.ru` | обязателен: Яндекс не принимает IP и самоподписанные |
| Колонка и сервер в одной сети | 192.0.2.132 и 192.0.2.10 | крайне желательно: поток локальный |

Требования сети:

- открытый **443** снаружи (не 8443 — см. раздел 9.2);
- сервер должен видеть колонку: TCP **1961** (Glagol/WebSocket);
- колонка должна видеть сервер: TCP **8097** (поток MA).

---

## 4. Шаг 1. Медиасервер

Любой Subsonic-совместимый сервер подойдёт (Navidrome, Airsonic, Gonic).
У нас Navidrome:

```yaml
# docker-compose.yml (фрагмент)
services:
  navidrome:
    image: deluan/navidrome:latest
    network_mode: host          # или проброс 4533
    volumes:
      - /mnt/music:/music:ro
      - ./data:/data
    environment:
      ND_MUSICFOLDER: /music
      ND_SCANSCHEDULE: "1h"
```

Проверка: `curl -s 'http://127.0.0.1:4533/rest/ping?u=USER&p=PASS&v=1.16.1&c=test&f=json'`
должен вернуть `"status":"ok"`.

**Грабли.** Если библиотека большая (у нас 331 ГБ), первый импорт в Music Assistant
съедает память. У нас это закончилось OOM-killer'ом и падением всей VM
(4 ГБ ОЗУ без swap). Лечится: не меньше 6 ГБ + 2 ГБ swap
(`fallocate -l 2G /swapfile && mkswap /swapfile && swapon /swapfile` и в `/etc/fstab`).

---

## 5. Шаг 2. Music Assistant

```yaml
# /opt/music-assistant/docker-compose.yml — ВАЖНО: build, а не image (см. раздел 6)
services:
  music-assistant:
    build: .
    image: ma-station-local:latest
    network_mode: host
    volumes:
      - ./data:/data
    restart: unless-stopped
```

Поднимаем, заходим в UI `http://<сервер>:8095`, добавляем провайдер **Navidrome**
(host, port, user, password) и ждём окончания импорта метаданных.

Токен для внешних вызовов (нужен навыку): **Настройки → Профиль → Long-lived access tokens**.
Он должен быть **только из ASCII** — кириллица в заголовке `Authorization` даёт
`UnicodeEncodeError`.

Проверка API:

```bash
curl -s -X POST http://127.0.0.1:8095/api \
  -H "Authorization: Bearer <токен>" -H 'Content-Type: application/json' \
  -d '{"message_id":"1","command":"players/all","args":{}}'
```

**Грабли.**

- `core.log_level: debug` в `settings.json` **не работает**. Только переменная
  окружения `LOG_LEVEL=debug` в compose.
- Плеер по умолчанию может оказаться `output_codec = wav` — это 1411 кбит/с без
  сжатия, поток не успевает и MA перезапускает его каждые 30 секунд. Должно быть
  **`flac`** (`http_profile = forced_content_length`).

---

## 6. Шаг 3. Провайдер Yandex Station — главная засада

### 6.1. Встроенный провайдер не работает на новых прошивках

В образе Music Assistant лежит провайдер **1.5.1**. На прошивке Станции Макс
`3.335.1.13.38660886874.20260226.tv/98` он **не играет**: команда уходит, колонка
отвечает `{'status': 'SUCCESS'}` и молчит. Ни ошибок, ни refused — просто тишина.

Актуальный провайдер — [trudenboy/ma-provider-yandex-station](https://github.com/trudenboy/ma-provider-yandex-station),
у нас работает **1.7.9**.

### 6.2. Собираем свой образ

```dockerfile
# /opt/music-assistant/Dockerfile
FROM ghcr.io/music-assistant/server:2.10.12

# --force-reinstall обязателен: без него pip оставит старые файлы пакета,
# обновит метаданные — и провайдер упадёт с
# "cannot import name 'NoUsableCredentialsError' ... Did you mean: 'InvalidCredentialsError'?"
RUN pip install --no-cache-dir --force-reinstall --retries 5 --timeout 60 \
    "ya-passport-auth[ma]==2.2.0"

COPY yandex_station/ /app/venv/lib/python3.14/site-packages/music_assistant/providers/yandex_station/
```

```bash
cd /tmp && rm -rf ysp && mkdir ysp && cd ysp
curl -sL https://github.com/trudenboy/ma-provider-yandex-station/archive/refs/tags/v1.7.9.tar.gz | tar xz
cd /opt/music-assistant
rm -rf yandex_station && cp -r /tmp/ysp/ma-provider-yandex-station-1.7.9/provider yandex_station
echo "1.7.9" > yandex_station/VERSION
docker compose build && docker compose up -d
```

В логе должно появиться:

```
Yandex Station: Registering speaker: Яндекс Станция Макс [yandexstation_2]
Yandex Station.glagol: WebSocket connected
```

### 6.3. Подключение аккаунта

В UI: **Настройки → Провайдеры → Yandex Station → добавить** → вход по Device Flow/QR
**тем аккаунтом, где колонка в «Дом с Алисой»**. Провайдер найдёт колонку по mDNS
и подтянет device token.

### 6.4. Настройки плеера, которые важны

| Параметр | Значение | Зачем |
|---|---|---|
| `output_codec` | `flac` | wav/mp3 ломают поток |
| `http_profile` | `forced_content_length` | колонка ждёт Content-Length |
| `voice_control` | `True` | провайдер ставит паузу перед ответом Алисы и возвращает звук после |
| `intercept_enabled` | `False` | перехват родного воспроизведения — нам не нужен |

**Грабли.** `voice_control` ставит паузу **до** ответа Алисы, но снимает её не
всегда, а `play_index` при состоянии `paused` воспроизведение **не запускает**.
Поэтому после программных действий нужно явно проверять состояние и звать
`players/cmd/play` (см. раздел 8.3).

---

## 7. Шаг 4. Прокси, TLS и внешний доступ

Яндекс требует HTTPS и валидный сертификат. У нас Caddy:

```caddyfile
music.example.ru {
    # Умный дом: ОБЯЗАТЕЛЬНО до @navidrome, иначе /api/* уйдёт в Navidrome
    handle /api/yandex_smarthome* {
        reverse_proxy 127.0.0.1:8095
    }

    @navidrome path /app/* /rest/* /api/* /share/* /ping /auth/*
    handle @navidrome {
        reverse_proxy 127.0.0.1:4533
    }

    # Вебхук навыка
    handle_path /alice/* {
        reverse_proxy 127.0.0.1:8100
    }

    handle {
        root * /var/www/music
        file_server
    }
}
```

Проверка снаружи:

```bash
curl -s https://music.example.ru/alice/health          # навык
curl -s -o /dev/null -w '%{http_code}\n' https://music.example.ru/api/yandex_smarthome/v1.0/user/devices
```

**Грабли.**

- Порт **8443** тут не годится: Диалоги принимают навык с любым портом, а вот
  приватный навык умного дома при публикации/валидации отказывается — `Validate uri error`.
  Нужен нормальный 443.
- Порядок маршрутов в Caddy — не косметика: `@navidrome path /api/*` жадно ловит
  всё, поэтому маршрут умного дома должен стоять **выше**.

---

## 8. Шаг 5. Навык Алисы

### 8.1. Каркас

FastAPI-приложение, один POST-эндпоинт, ответ по схеме Диалогов:

```python
@app.post("/")
async def webhook(request: Request) -> JSONResponse:
    body = await request.json()
    session = body.get("session") or {}
    req = body.get("request") or {}
    command = req.get("command") or req.get("original_utterance") or ""

    # ВАЖНО: команду разбираем и в новой сессии. При запуске навыка Яндекс
    # присылает session.new=true ВМЕСТЕ с командой в request.command.
    # Если ответить только приветствием — команда потеряется.
    if not command.strip():
        result = ask("Домашняя музыка на связи. Скажи «включи» и название.")
    else:
        result = await handle(command)

    result["session"] = session
    return JSONResponse(result)
```

Ответ:

```json
{"response": {"text": "Включаю Сплин.", "tts": "Включаю Сплин.", "end_session": false},
 "session": {"new": false}, "version": "1.0"}
```

### 8.2. Правила ответов (важные!)

- **Никогда не отвечать пустотой.** `text: ""` Алиса трактует как «навык закончил»
  и мгновенно закрывает сессию. Все команды отвечают голосом.
- **Приветствие — только на пустую команду.** Иначе теряется «запуск + команда».
- **Коротко.** Пока Алиса говорит, колонка приглушена. «далее» → название трека
  без альбома; «пауза» → «Тихо.»; «стоп» → «Выключила.».
- **Запуск одной фразой.** «Алиса, запусти навык домашняя музыка, далее» — навык
  отрезает префикс и разбирает остаток.

Переменные окружения:

| Переменная | Значение | Смысл |
|---|---|---|
| `MA_URL` | `http://127.0.0.1:8095` | MA на тот же хост |
| `MA_TOKEN` | long-lived токен | только ASCII |
| `PLAYER_NAME` | `ys_XXXXXXXXXXXXXXXX` | id плеера-колонки в MA |
| `SESSION_HOLD` | `1` | держать сессию: команды подряд работают ~5 с, но Яндекс говорит «вы в навыке…» |
| `SILENCE_MS` | `1` | длина «тишины» вместо подтверждения |

### 8.3. Воспроизведение: как не получить тишину

Три правила, добытые кровью:

```python
# 1. Тяжёлые операции — в фон: в 4.5 с бюджета ответа они не влезают
asyncio.create_task(start_queue_after_reply(player_id, queue_id, uris))

# 2. Запускаем СРАЗУ, не дожидаясь конца озвучки: пока Алиса говорит,
#    станция всё равно на паузе, а к концу фразы поток уже готов.
await ma.play_media(queue_id, uris, option="replace", timeout=20)

# 3. Одна проверка через 3 секунды: поток жив — не трогаем, мёртв — поднимаем
await asyncio.sleep(3.0)
if stream_alive():
    return
await ma.play_index(queue_id, index, timeout=20)
if await _player_state(player_id) != "playing":
    await ma.play(player_id, timeout=20)      # play_index не снимает паузу!
```

`stream_alive()` — проверка по сокетам: навык в host-сети читает `/proc/net/tcp`
и ищет ESTAB на порт потока (8097) с небольшим Send-Q. `state` в MA оптимистичный,
а `elapsed_time` у станции всегда 0 — оба для этой проверки не годятся.

**Грабли.**

- `stop` перед `play_index` **вредит**: разрыв потока = «спиннер» в UI и повторная
  загрузка FLAC на несколько секунд. Гасить стоит только когда поток реально мёртв.
- Таймауты фронтовых вызовов MA держите короткими (2.5 с), фоновых — длинными (20 с):
  под нагрузкой (автоплей, поиск похожих) MA отвечает дольше 2.5 с, и фоновый
  перезапуск падал с «нет связи с Music Assistant».
- Очередь: `option="next"` + `play_index` дают переход «как далее», `option="replace"`
  пересоздаёт очередь.

### 8.4. Регистрация в Диалогах

1. Создать навык, указать Backend URL `https://music.example.ru/alice/`.
2. Опубликовать (черновик деплоить нельзя).
3. Проверить: `curl -s -X POST https://music.example.ru/alice/ -d '{"session":{"new":true},"request":{"command":"что играет"},"version":"1.0"}'`.

---

## 9. Шаг 6. Умный дом (транспорт без сессии)

Плагин **Yandex Smart Home** в Music Assistant, режим `direct`:

| Поле | Значение |
|---|---|
| Connection Type | `direct` |
| External Base URL | `https://music.example.ru` (**без порта**) |
| Skill ID | id уже опубликованного навыка умного дома |
| Exposed Players | только колонка (приватнее) |

Затем в «Дом с Алисой» → Устройства → Добавить → Умный дом → ваш навык → связать.

### 9.1. Грабли привязки

- **`Draft is not allowed to deploy`** — автосоздание навыка через API создаёт черновик,
  а черновики Яндекс не публикует. Решение: создать навык руками, опубликовать и
  указать его Skill ID в плагине (в `setup_flow.py` нужна правка — в апстриме
  ручной ввод для `direct` отсутствует).
- **`OAuth client_secret mismatch` → 401 на `/auth/token`** — OAuth-приложение навыка
  создано прерванным автосозданием, секрет в нём другой. Лечение: ослабить проверку
  в `direct.py` (пускать по валидному одноразовому коду) или пересоздать OAuth-приложение.
- Токен для плагина берётся так: откройте
  `https://oauth.yandex.ru/authorize?response_type=token&client_id=<id навыка>`,
  разрешите доступ и скопируйте `access_token` из адресной строки целиком
  (браузер любит обрезать — используйте `copy(location.hash)` в консоли).

### 9.2. Что даёт умный дом

- «далее», «назад», «пауза», «продолжи», «громкость 5» работают **всегда**, без
  запуска навыка и без 5-секундной сессии;
- диалога нет → колонка не приглушается;
- названия треков в Яндекс **не передаются** (ограничение протокола) — спрашивать
  «что играет» надо у навыка.

---

## 10. Ограничения платформы (обойти нельзя)

| Ограничение | Следствие |
|---|---|
| Навык не может играть произвольное аудио | только файлы ≤120 с / ≤5 МБ через `dialogs-upload` |
| Сессия навыка ~5 с тишины | транспорт вынесен в умный дом; продлить нельзя |
| Нет события «сессия закрыта» | нельзя узнать, что Алиса перестала слушать |
| Пустой `text` закрывает навык | отвечать всегда голосом |
| Умный дом не передаёт названия треков | «что играет» — только навыком |
| Порт 8443 не проходит валидацию умного дома | нужен 443 |
| Колонка не отдаёт прогресс (`elapsed_time` = 0) | позицию приходится оценивать самим |
| Приглушение музыки во время диалога | поведение колонки, не настраивается |

---

## 11. Чек-лист: звука нет

Идём сверху вниз, пока не найдём.

```bash
# 1. Провайдер вообще загрузился?
docker logs music-assistant --since 5m | grep -E "Registering speaker|Error loading provider"
#    нет "Registering speaker" → см. ya-passport-auth (раздел 6.2)

# 2. Плеер виден и доступен?
curl -s http://127.0.0.1:8100/health          # в списке должна быть станция

# 3. Колонка подключена к MA?
ss -tn | grep ':1961'                          # ESTAB с IP колонки = Glagol жив

# 4. Идёт ли поток?
ss -tn | grep ':8097'                          # ESTAB с колонкой + Send-Q небольшой
docker exec music-assistant ps aux | grep -c "[f]fmpeg"   # 1-2 = стрим работает

# 5. Что в логах MA?
docker logs music-assistant --since 2m | grep -E "play_media|Stream URL|radio_play|Start Queue Flow"
#    "radio_play result: SUCCESS" без запросов на 8097 → старая версия провайдера

# 6. Кодек и настройки плеера
#    output_codec=flac, http_profile=forced_content_length, voice_control=True
```

Типичные диагнозы:

| Симптом | Причина |
|---|---|
| `radio_play result: SUCCESS`, ноль пакетов на 8097 | провайдер 1.5.1 — обновите до 1.7.9 |
| Плеера нет в `players/all` | `ya-passport-auth` 1.7.0 вместо 2.2.0 |
| MA пишет `playing`, звука нет | поток висит с Send-Q ≈325 КБ → `stop` + `play_index` |
| Играет один трек по кругу | `output_codec = wav` |
| После ответа Алисы тишина 5–20 с | `voice_control` поставил паузу, нужен `players/cmd/play` |
| `Connect call failed (ip, 1961)` каждые 5 минут | провайдер завис → `docker restart music-assistant` |
| 404 на `/api/yandex_smarthome` | маршрут Caddy стоит после `@navidrome` |

---

## 12. Обслуживание

```bash
# навык
cd /opt/alice-skill && docker compose up -d --build && curl -s http://127.0.0.1:8100/health

# Music Assistant (только так — образ локальный!)
cd /opt/music-assistant && docker compose build && docker compose up -d

# переавторизация колонки (токены Яндекса живут ограниченно)
#   MA → Провайдеры → Yandex Station → войти заново; в логе ждём
#   "Got device token" → "WebSocket connected"
```

Мониторинг (Prometheus + blackbox + алерты): проба `https://music.example.ru/alice/health`,
алерты на недоступность навыка, давление памяти и OOM на сервере.

---

## 13. Что мы поняли в итоге

1. **Проблема почти всегда не там, где ищешь.** Полтора дня ушло на сеть, кодеки,
   firewall и аккаунт — а виновата была версия community-провайдера.
2. **Проверяйте состояние по сокетам, а не по `state` в UI.** MA оптимистичен:
   показывает `playing`, когда колонка уже отвалилась.
3. **Не рвите поток без нужды.** Каждый `stop` — это спиннер, повторная загрузка
   FLAC и пара секунд тишины.
4. **Тяжёлое — в фон, лёгкое — сразу.** 4.5 секунды бюджета ответа не терпят
   импорта метаданных, но отлично терпят `asyncio.create_task`.
5. **Колонка не любит, когда её дёргают во время речи.** Отсюда `voice_control`
   и правило «запускай поток сразу, пока Алиса говорит».
6. **Всё, что узнали, — записывайте.** Этот файл появился потому, что через день
   уже не помнишь, почему в Dockerfile стоит `--force-reinstall`.

Удачной музыки.
