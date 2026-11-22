# Провайдер Yandex Station: почему 1.7.9 и как это не потерять

**Коротко: в образе Music Assistant лежит провайдер Yandex Station версии 1.5.1.
Он не умеет играть на прошивке Станции Макс `3.335.1.13.38660886874.20260226.tv/98`.
Нужен 1.7.9 из https://github.com/trudenboy/ma-provider-yandex-station, поэтому
MA собирается из локального Dockerfile. Если пересоздать контейнер из штатного
образа — музыка перестанет играть.**

## Как это выглядело

Всё «настроено и работает», но на колонке тишина:

- навык отвечает: «Включаю Amatory.»
- MA показывает `state: playing`, очередь двигается, `далее` переключает треки
- в логах MA: `play_media called` → `Stream URL: http://192.0.2.10:8097/flow/…` →
  `radio_play result: {'status': 'SUCCESS'}`
- **но колонка не открывает соединение на 8097**, ffmpeg не запускается, звука нет

`tcpdump` на сервере во время «воспроизведения»:

```
192.0.2.132 > 224.0.0.224.7447  mDNS, каждые 2.5 с
ни одного пакета к 192.0.2.10:8097
```

То есть колонка на команду отвечает «принято», но за потоком не идёт.

## Что было проверено и исключено (чтобы не искать заново)

| Проверка | Результат |
|---|---|
| Сеть до колонки, порт 1961 | ок, ping и TCP проходят, ручной WebSocket из контейнера поднимается |
| Ссылка потока | рабочая: curl с другого хоста LAN → `200`, настоящий FLAC, 1.1 МБ за 8 с |
| Публичный URL вместо LAN | та же тишина (`https://music.example.com/ann.mp3` — 200, колонка не просит) |
| Кодек | mp3 вместо flac — то же самое |
| Announcement (`players/cmd/play_announcement`) | тоже проигнорирован |
| Колонка разбужена / уже играет радио | не влияет |
| Мультирум, вторая колонка | вторая станция удалена из аккаунта, ARP-запросы к ней прекратились — не причина |
| Версия MA | 2.10.12 и 2.10.10 — одинаково (провайдер в обоих образах один и тот же) |
| Команды управления | работают: `setVolume` меняет реальную громкость, `stop` переводит в idle |

Последнее и было ключом: **Glagol-соединение живо и команды доходят, значит проблема
не в сети и не в авторизации — только в команде воспроизведения.**

## Что именно чинит 1.7.9

В CHANGELOG провайдера между 1.5.x и 1.7.9 — переработка воспроизведения:
`v1.7.9` (релиз 28.10.2026) и предыдущие версии меняют работу с состоянием и
командой потока; на новой прошивке Макса именно это и заработало. Проверено
фактом: после подмены провайдера колонка сразу открыла соединение на 8097:

```
Registering speaker: Яндекс Станция Макс [yandexstation_2]
WebSocket connected
192.0.2.10:8097 ← 192.0.2.132:56654   ESTAB, данные идут
ffmpeg: 2 процесса
```

## Как это устроено сейчас (локальная сборка)

`/opt/music-assistant/Dockerfile`:

```dockerfile
FROM ghcr.io/music-assistant/server:2.10.12
RUN pip install --no-cache-dir "ya-passport-auth[ma]==2.2.0"
COPY yandex_station/ /app/venv/lib/python3.14/site-packages/music_assistant/providers/yandex_station/
```

`docker-compose.yml` — `build: .` и `image: ma-station-local:latest` **вместо**
`image: ghcr.io/music-assistant/server:latest`.

`yandex_station/` — содержимое каталога `provider/` из архива релиза v1.7.9.
В нём лежит и `VERSION` с `1.7.9` (в самом релизе файла нет, это наша метка).

`ya-passport-auth[ma]==2.2.0` — требование манифеста 1.7.9. В образе MA стоит 1.7.0,
и без обновления пакета провайдер не загружается:

```
Error loading provider(instance) yandex_station:
cannot import name 'NoUsableCredentialsError' from 'ya_passport_auth.exceptions'
```

## Как обновляться

1. Скачать новый релиз провайдера:

```bash
cd /tmp && rm -rf ysp && mkdir ysp && cd ysp
curl -sL https://github.com/trudenboy/ma-provider-yandex-station/archive/refs/tags/vX.Y.Z.tar.gz | tar xz
```

2. Заменить файлы провайдера:

```bash
cd /opt/music-assistant
rm -rf yandex_station && cp -r /tmp/ysp/ma-provider-yandex-station-X.Y.Z/provider yandex_station
echo "X.Y.Z" > yandex_station/VERSION
```

3. Посмотреть в новом `yandex_station/manifest.json` требование к `ya-passport-auth`
   и при необходимости поправить `RUN pip install ...` в Dockerfile.

4. При желании обновить базовый образ — поменять тег в `FROM` (не `latest`, чтобы
   обновление было осознанным).

5. Пересобрать и поднять:

```bash
cd /opt/music-assistant && docker compose build && docker compose up -d
```

6. Проверить, что провайдер загрузился и колонка тянет поток:

```bash
docker logs music-assistant --since 2m | grep -iE "Registering speaker|WebSocket connected"
ss -tn | grep 8097          # должно быть ESTAB с 192.0.2.132
```

## Если понадобится откат

- провайдер 1.5.1: `/opt/music-assistant/yandex_station.1.5.1.bak/`
- база MA: `/opt/music-assistant/data/library.db.bak-2.10.12`
- compose до сборки: `/opt/music-assistant/docker-compose.yml.bak-before-build`

## Важные следствия

- **`docker compose pull` больше не обновляет MA** — образ собирается локально.
  Обновление = правка `FROM` + `build`.
- При смене Python в базовом образе путь `/app/venv/lib/python3.14/...` изменится —
  тогда `COPY` в Dockerfile надо поправить (проверить: `docker run --rm <образ> ls /app/venv/lib`).
- Провайдер — community-проект (`trudenboy`), обновляется чаще образа MA. Если Яндекс
  снова обновит прошивку колонки — сначала смотреть релизы провайдера, а не MA.
