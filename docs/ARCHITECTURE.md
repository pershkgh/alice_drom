# Архитектура

## Что происходит, когда я говорю «Алиса, включи сплин»

```
1. Станция ловит фразу → Яндекс (облако) распознаёт → находит навык «Домашняя музыка»
2. POST https://music.example.com:8443/alice/  {request.command: "запусти навык домашняя музыка, включи сплин"}
3. Caddy: handle_path /alice/* → reverse_proxy 127.0.0.1:8100  (контейнер alice-skill)
4. Навык: отрезает «запусти навык ...», парсит «включи сплин» → Intent.PLAY
5. Навык → MA: POST http://127.0.0.1:8095/api  music/search(search_query="сплин")
6. Навык → MA: player_queues/play_media(queue_id, [uri], option="next"|"replace")
7. MA находит текущую очередь, вставляет треки сразу после текущего и прыгает на них
8. MA отдаёт колонке команду radio_play с URL потока:
   http://192.0.2.10:8097/flow/<session>/<queue>/<item>/ys_XXXXXXXXXXXXXXXX.flac
9. Станция открывает это соединение САМА и тянет FLAC (ffmpeg на стороне MA пасsthrough)
10. Навык отвечает «Включаю Сплин.» (tts тот же текст)
```

Ключевое: **аудио идёт по локалке, шаг 9 — прямое соединение колонки на 8097**. Яндекс
на шагах 1–2 видит только текст команды и текст ответа, трек целиком не проходит.

## Что происходит, когда я говорю «Алиса, далее»

```
1. Фраза уходит НЕ в навык, а в умный дом: Яндекс → навык умного дома (наш плагин в MA)
2. POST https://music.example.com/api/yandex_smarthome/v1.0/user/devices/action
3. Caddy: handle /api/yandex_smarthome* → reverse_proxy 127.0.0.1:8095
4. Плагин Yandex Smart Home (в MA) → player_queues/play_index(index+1)
5. Колонка: то же радио-поток-переключение
```

Здесь **нет сессии навыка вовсе** — поэтому «далее» работает всегда, хоть через час,
и Алиса при этом не «приседает» по громкости (нет диалога).

## Порты (сервер, 192.0.2.10)

| Порт | Кто слушает | Наружу |
|---|---|---|
| 443 | Caddy | **да**, проброс с роутера |
| 8443 | Caddy (тот же сайт) | да, исторически первый проброс |
| 4533 | Navidrome | через Caddy как `/rest/*` |
| 8095 | Music Assistant (API + UI) | только `/api/yandex_smarthome*` через Caddy |
| 8097 | MA, сервер потока для плееров | нет (колонка ходит по локалке) |
| 8100 | навык (uvicorn) | через Caddy как `/alice/*` |
| 1961 | **колонка**, Glagol (WebSocket) | MA подключается к колонке сам |

## Файлы на сервер

```
/opt/alice-skill/
  app.py            FastAPI-вебхук, интенты, отслеживание позиции/громкости
  ma.py             клиент Music Assistant (JSON-RPC)
  intents.py        разбор фраз, числа, «перемотай на 1 минуту 30 секунд»
  Dockerfile        python:3.13-slim + ffmpeg
  docker-compose.yml network_mode: host, .env рядом
  .env              MA_URL, MA_TOKEN, PLAYER_NAME, SILENCE_MS, SESSION_HOLD, FAVORITES_LIMIT

/opt/music-assistant/
  docker-compose.yml  build: . (локальный Dockerfile!)
  Dockerfile          FROM ghcr.io/music-assistant/server:2.10.12 + ya-passport-auth 2.2.0
                      + провайдер yandex_station 1.7.9 поверх встроенного 1.5.1
  yandex_station/     провайдер 1.7.9 (копия релиза trudenboy/ma-provider-yandex-station)
  README-local.md     зачем локальная сборка и как обновлять
  data/settings.json  конфиг MA (шифрует секреты ключом encryption_key оттуда же)
  data/library.db     метаданные, очереди, плееры

/etc/caddy/Caddyfile  сайт music.example.com (+ алиас :8443)
```

## Маршруты Caddy, которые важны (порядок имеет значение)

```
handle /api/yandex_smarthome* { reverse_proxy 127.0.0.1:8095 }   ← ДО @navidrome!
@navidrome path /app/* /rest/* /api/* /share/* /ping /auth/*      ← ловит весь /api/*
handle @navidrome { reverse_proxy 127.0.0.1:4533 }
handle_path /alice/* { reverse_proxy 127.0.0.1:8100 }
handle { root * /var/www/music ... }                              ← VD39, всё остальное
```

Если поставить маршрут умного дома **после** `@navidrome`, `/api/yandex_smarthome`
уйдёт в Navidrome и плагин будет получать 404 — Яндекс не сможет связать аккаунт.

## Умный дом: как связан

- плагин **Yandex Smart Home** (в MA) в режиме `direct`, External Base URL `https://music.example.com`
- в Яндексе создан приватный навык `Music Assistant` (id `6d95b34d-…`), бэкенд
  `https://music.example.com/api/yandex_smarthome`, OAuth — эндпоинты самого MA
- в MA сохранён инстанс плагина и `direct_access_token`; Яндекс шлёт
  `Authorization: Bearer <token>` на каждый запрос
- устройства: 4 плеера MA (Станция Макс, Mi TV, мак, веб). Можно ограничить
  одним в поле Exposed Players

## Метрики и алерты

`monitoring/prometheus/rules/home.yml` (репозиторий monitoring):

| Алерт | Условие |
|---|---|
| `VmMemPressure` | память сервер > 85% десять минут |
| `VmOomKill` | `increase(node_vmstat_oom_kill[10m]) > 0` |
| `VmSwapPressure` | свободного swap < 10% пятнадцать минут |
| `AliceSkillDown` | `https://music.example.com:8443/alice/health` не отдаёт 200 пять минут |

Проба вебхука — в `prometheus.yml`, job `blackbox-https`, label `service="alice-skill"`.
