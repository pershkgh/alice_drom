"""Вебхук навыка «Домашняя музыка» для Яндекс Алисы.

Схема:
    Алиса (колонка) -> https://<домен>/alice -> этот сервис
                                                       -> Music Assistant :8095
                                                       -> станция по Glagol :1961

Сам навык музыку не играет (протокол Диалогов этого не позволяет) — он только
командует Music Assistant, который и отдаёт FLAC на колонку.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import threading
import pathlib
import random
import socket
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from intents import HELP_TEXT, Intent, parse
from ma import MA, MAError

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("alice")

MA_URL = os.getenv("MA_URL", "http://127.0.0.1:8095")
MA_TOKEN = os.getenv("MA_TOKEN", "")
NAVIDROME_URL = (os.getenv("NAVIDROME_URL") or "").strip()
SKILL_NAME = (os.getenv("SKILL_NAME") or "Домашняя музыка").strip()  # как вы назвали навык в Диалогах
# Опубликованный навык может запустить любой пользователь Алисы — и управлять ВАШЕЙ
# колонкой. Поэтому по умолчанию разрешаем только владельца: его user_id Яндекс
# кладёт в session.user.user_id. Пусто = разрешены все (для черновика это нормально).
OWNER_USER_ID = (os.getenv("OWNER_USER_ID") or "").strip()
# Секретный сегмент URL: Яндекс не подписывает запросы, поэтому единственная
# защита вебхука — непубличный адрес. Ставится инсталлером: /alice/<secret>.
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()
# Опционально: id навыка из Диалогов (доп. проверка тела запроса).
SKILL_ID = (os.getenv("SKILL_ID") or "").strip()
PLAYER_NAME = os.getenv("PLAYER_NAME", "")  # id или имя; пусто = единственная доступная станция
API_TOKEN = (os.getenv("API_TOKEN") or "").strip()  # пусто = API открыт (только своя сеть!)
STARTED_AT = time.time()
FAVORITES_LIMIT = int(os.getenv("FAVORITES_LIMIT", "200"))
SILENCE_MS = int(os.getenv("SILENCE_MS", "125"))
# Держать ли сессию открытой после ответа. Открытая сессия = Алиса слушает
# следующую команду, но колонка всё это время приглушает музыку.
SESSION_HOLD = os.getenv("SESSION_HOLD", "1") not in {"0", "false", "no"}
# Держать громкость статичной: после «далее»/«назад»/«паузы» станция иногда
# возвращает себе громкость, отличную от выставленной (её же озвучка названия
# трека громче тихого уровня). Навык через секунду возвращает последнюю заданную.
VOLUME_LOCK = os.getenv("VOLUME_LOCK", "1") not in {"0", "false", "no"}  # пауза вместо подтверждения; короче — меньше «приседание» музыки


# --- журнал событий для приложения -----------------------------------------


class EventBuffer:
    """Кольцевой журнал для приложения: забирается через /api/events.

    ВАЖНО: именно класс, а не logging.Handler. Обработчик, повешенный на
    корневой логгер, умудрился подвесить весь uvicorn (все запросы висели до
    таймаута) — поэтому события пишем явно, из нужных мест кода.
    """

    def __init__(self, size: int = 1000) -> None:
        self.items: list[dict[str, Any]] = []
        self.size = size
        self.seq = 0
        self.lock = threading.Lock()

    def record(self, level: str, text: str, source: str = "навык") -> None:
        with self.lock:
            self.seq += 1
            self.items.append(
                {
                    "id": self.seq,
                    "ts": time.time(),
                    "level": level,
                    "source": source,
                    "text": text,
                }
            )
            if len(self.items) > self.size:
                del self.items[: len(self.items) - self.size]

    def snapshot(self, since: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            return [e for e in self.items if e["id"] > since]


EVENTS = EventBuffer()


def event(level: str, text: str, source: str = "навык") -> None:
    """Записать строку в журнал приложения (и в обычный лог)."""
    EVENTS.record(level, text, source)
    log.log(getattr(logging, level, logging.INFO), text)


def _log_source(headers) -> str:
    return headers.get("x-source") or "app"


app = FastAPI(title="Alice home music skill")
ma = MA(MA_URL, MA_TOKEN)


# --- ответы ----------------------------------------------------------------


def reply(text: str, *, tts: str | None = None, end: bool = True) -> dict[str, Any]:
    response: dict[str, Any] = {"text": text, "end_session": end}
    if tts:
        response["tts"] = tts
    return {"version": "1.0", "response": response}


def ask(text: str, *, tts: str | None = None) -> dict[str, Any]:
    """Оставить сессию открытой, чтобы «дальше/пауза» работали без перезапуска."""
    return reply(text, tts=tts, end=not SESSION_HOLD)


def silent() -> dict[str, Any]:
    """Ничего не говорить: короткая пауза вместо подтверждения.

    Иначе на «далее» навык отвечает «Дальше» и звучит как эхо за пользователем.
    """
    return reply("", tts=f"sil <[{SILENCE_MS}]>", end=not SESSION_HOLD)


def fmt_item(item: dict[str, Any] | None, with_album: bool = True) -> str:
    if not item:
        return ""
    name = item.get("name") or item.get("title") or ""
    artists = item.get("artists") or []
    artist = ""
    if artists:
        first = artists[0]
        artist = first.get("name", "") if isinstance(first, dict) else str(first)
    album = item.get("album") or {}
    album_name = album.get("name", "") if isinstance(album, dict) else ""

    text = " — ".join(part for part in (artist, name) if part)
    return f"{text}, из альбома {album_name}" if (with_album and album_name) else text







async def play_uri_after_reply(player_id: str, queue_id: str, uri: str,
                               index: int | None, total: int) -> None:
    """Включить конкретный трек в фоне, с повтором при таймауте провайдера."""
    use_next = isinstance(index, int) and bool(total) and total < 500
    for attempt in (1, 2):
        try:
            if use_next:
                # вставляем сразу после текущего и прыгаем на него: очередь и поток
                # не пересоздаются, переход звучит как обычная смена трека
                await ma.play_media(queue_id, uri, option="next", timeout=30)
                await ma.play_index(queue_id, index + 1, timeout=30)
            else:
                await ma.play_media(queue_id, uri, option="replace", timeout=30)
            await ma.seek(queue_id, 0, timeout=20)
            pos_set(queue_id, 0)
            await ensure_playing(player_id)
            event("INFO", f"включила {uri} (попытка {attempt})")
            return
        except Exception as exc:  # noqa: BLE001 — фон
            event("WARNING", f"включение не удалось (попытка {attempt}): {exc}")
            await asyncio.sleep(1.0)


async def relock_volume_after_reply(player_id: str, level: int | None) -> None:
    """Вернуть громкость, которую пользователь задал последней.

    Станция на низкой громкости (1–3) после собственной озвучки названия трека
    может «прибавить» — в MA при этом ничего не меняется, поэтому чинить надо
    здесь: через паузу (Алиса уже договорила) выставляем прежний уровень.
    """
    if not VOLUME_LOCK or level is None:
        return
    try:
        await asyncio.sleep(1.2)
        await ma.volume_set(player_id, max(0, min(100, level * 10)))
        log.info("громкость возвращена к %s/10", level)
    except Exception as exc:  # noqa: BLE001 — фон не должен падать молча
        log.warning("не удалось вернуть громкость: %s", exc)


async def start_index_after_reply(player_id: str, queue_id: str, index: int,
                                  generation: int | None = None) -> None:
    """Включить нужный элемент очереди сразу и один раз проверить звук потом."""
    try:
        if generation is not None and not nav_generation_ok(player_id, generation):
            log.info("навигация устарела (index=%s) — пропускаю", index)
            return
        await ma.play_index(queue_id, index, timeout=20)
        if generation is not None and not nav_generation_ok(player_id, generation):
            return
        # Сразу возвращаем трек в начало: поток MA продолжает «тикать» с прошлой
        # позиции, и станция, подключившись, попадала в середину песни.
        await ma.seek(queue_id, 0, timeout=20)
        pos_set(queue_id, 0)
        await ensure_playing(player_id, queue_id, index=index)
    except Exception as exc:  # noqa: BLE001 — фон не должен падать молча
        log.warning("start_index не удался: %s", exc)


async def start_queue_after_reply(player_id: str, queue_id: str, uris: list[str]) -> None:
    """Заменить очередь сразу — не дожидаясь, пока Алиса договорит.

    Ждать нельзя: пока идёт озвучка, станция всё равно на паузе (её ставит
    voice_control), а к концу фразы поток уже готов — музыка подхватывается
    без «спиннера» и тишины. Сама замена медленная (2–5 с), поэтому в фоне:
    в бюджет ответа 4.5 с она не влезает.
    """
    try:
        await ma.play_media(queue_id, uris, option="replace", timeout=20)
        await ma.seek(queue_id, 0, timeout=20)   # новый трек — с начала
        pos_set(queue_id, 0)
        event("INFO", f"очередь заменена: {len(uris)} треков")
        await ensure_playing(player_id, queue_id, index=0)
    except Exception as exc:  # noqa: BLE001 — фон не должен падать молча
        log.warning("замена очереди не удалась: %s", exc)


async def ensure_playing(player_id: str, queue_id: str | None = None,
                         index: int | None = None) -> None:
    """Одна проверка через 3 секунды: если звук так и не пошёл — поднять его.

    Ровно одна попытка: каждая лишняя — это новый поток, «спиннер» и трек
    заново с нуля. Если поток жив (voice_control уже вернул звук) — не трогаем.
    """
    try:
        await asyncio.sleep(3.0)
        if stream_alive():
            return
        state = await _player_state(player_id)
        if index is not None:
            await ma.play_index(queue_id, index, timeout=20)
            await asyncio.sleep(0.6)
        if await _player_state(player_id) != "playing":
            await ma.play(player_id, timeout=20)
            event("INFO", f"дожали воспроизведение (было {state})")
        # Пока шла озвучка, поток MA продолжал «тикать» — станция подключается
        # к нему и попадает в середину трека. Возвращаем начало явно.
        if queue_id is not None and index is not None:
            queue = await ma.queue(queue_id)
            if (queue.get("elapsed_time") or 0) > 1.5:
                await ma.seek(queue_id, 0, timeout=20)
                event("INFO", f"вернули трек в начало (было {round(queue.get('elapsed_time') or 0, 1)} с)")
    except Exception as exc:  # noqa: BLE001 — фон не должен падать молча
        log.warning("ensure_playing не удался: %s", exc)


STREAM_PORT = int(os.getenv("STREAM_PORT", "8097"))


def stream_alive(port: int | None = None) -> bool:
    """Есть ли соединение колонки с потоком MA.

    Раньше требовали маленький Send-Q («колонка быстро читает»), но при потоковом
    воспроизведении MA специально держит впереди буфер в сотни килобайт — проверка
    давала ложное «соединения нет» при играющей музыке. Теперь достаточно ESTAB.
    """
    port = port or STREAM_PORT
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = pathlib.Path(path).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) < 5 or f[3] != "01":      # 01 = ESTAB
                continue
            try:
                if int(f[1].rsplit(":", 1)[1], 16) != port:
                    continue
                send_q = int(f[4].split(":")[0], 16)
            except (IndexError, ValueError):
                continue
            return True
    return False


async def _player_state(player_id: str) -> str:
    result = await ma.cmd("players/get", player_id=player_id)
    player = result if isinstance(result, dict) and "player_id" in result else (result or {}).get("result") or {}
    return str(player.get("state") or "")


async def shuffle_playback(player_id: str, queue_id: str, total: int, current: int) -> None:
    """Перемешать очередь и перезапустить воспроизведение на другом треке.

    Делается в фоне: shuffle + stop + play_index в сумме легко выходят за 4.5 с
    бюджета ответа Алисе, да и озвучка ответа совпадает с пересозданием потока.
    """
    try:
        index = random.randrange(total) if total else 0
        if total > 1:
            while index == current:
                index = random.randrange(total)
        await ma.stop(player_id)          # станция отпускает старый URL
        await asyncio.sleep(0.3)
        await ma.play_index(queue_id, index)
        pos_set(queue_id, 0)
        event("INFO", f"перемешала: играю индекс {index} из {total}")
    except Exception as exc:  # noqa: BLE001 — фон не должен падать молча в никуда
        log.warning("shuffle: не удалось перезапустить воспроизведение: %s", exc)


def pick_media(search: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Выбрать лучший результат по качеству совпадения имени.

    Точное совпадение > начинается с > содержит. Небольшой бонус исполнителю и
    альбому: на «включи X» чаще имеют в виду артиста. Если ни одно имя даже не
    содержит запрос — возвращаем None, чтобы не включать случайный трек.
    """

    def norm(value: str) -> str:
        return (value or "").lower().replace("ё", "е").strip()

    q = norm(query)
    groups = ("artists", "albums", "playlists", "tracks")
    bonus = {"artist": 0.5, "album": 0.25}

    best: dict[str, Any] | None = None
    best_score = 0.0
    for group in groups:
        for item in search.get(group) or []:
            name = norm(item.get("name", ""))
            if not name:
                continue
            if name == q:
                score = 3.0
            elif name.startswith(q):
                score = 2.0
            elif q in name:
                score = 1.0
            else:
                continue
            score += bonus.get(str(item.get("media_type", "")), 0.0)
            if score > best_score:
                best, best_score = item, score
    return best


def queue_size(queue: dict[str, Any]) -> int:
    items = queue.get("items")
    if isinstance(items, int):
        return items
    if isinstance(items, list):
        return len(items)
    return 0


def volume_level(player: dict[str, Any]) -> int:
    """Шкала Алисы: 0..10, где 10 — 100%."""
    raw = player.get("volume_level") or 0
    return max(0, min(10, round(int(raw) / 10)))


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


_BG_TASKS: set[asyncio.Task] = set()  # держим ссылки: иначе GC может убить задачу
_NAV_GEN: dict[str, int] = {}  # поколение навигации: старые задачи не должны перебивать новые


def spawn(coro) -> None:
    """Запустить фоновую задачу и не потерять ссылку на неё."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def next_nav_generation(player_id: str) -> int:
    """Новая навигационная команда отменяет предыдущую (защита от «далее-далее»)."""
    generation = _NAV_GEN.get(player_id, 0) + 1
    _NAV_GEN[player_id] = generation
    return generation


def nav_generation_ok(player_id: str, generation: int) -> bool:
    return _NAV_GEN.get(player_id, generation) == generation


_VOL: dict[str, int] = {}  # player_id -> последний выставленный уровень 0..10
_POS: dict[str, tuple[float, float]] = {}  # queue_id -> (секунда, monotonic-метка; 0 = на паузе)


def pos_set(queue_id: str, seconds: float) -> None:
    _POS[queue_id] = (max(0.0, seconds), time.monotonic())


def pos_pause(queue_id: str) -> None:
    _POS[queue_id] = (pos_get(queue_id), 0.0)


def pos_get(queue_id: str) -> float:
    seconds, stamp = _POS.get(queue_id, (0.0, 0.0))
    return seconds + (time.monotonic() - stamp) if stamp else seconds


def mmss(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def is_playing(player: dict[str, Any]) -> bool:
    return str(player.get("state") or "") in {"playing", "paused"}


# --- обработка -------------------------------------------------------------


async def current_item(player_id: str) -> dict[str, Any] | None:
    """Текущий трек. MA отдаёт QueueItem-обёртку, сам трек лежит в media_item."""
    queue = await ma.queue(player_id)
    item = queue.get("current_item")
    if not item:
        items = queue.get("items")
        if isinstance(items, list) and items:
            item = items[0]
    if isinstance(item, dict):
        item = item.get("media_item") or item
    return item or None


async def handle(command: str) -> dict[str, Any]:
    parsed = parse(command, SKILL_NAME)
    log.info("фраза=%r интент=%s query=%r level=%s", command, parsed.intent, parsed.query, parsed.level)

    if parsed.intent is Intent.PING:
        return reply("ok")

    if parsed.intent is Intent.LAUNCH:
        return ask(
            f"{SKILL_NAME} на связи. Скажи «что играет», «далее» или «включи» и название."
        )

    if parsed.intent is Intent.HELP:
        return ask(HELP_TEXT)

    try:
        player = await ma.player(PLAYER_NAME)
        player_id = player["player_id"]

        if parsed.intent is Intent.NOW:
            item = await current_item(player_id)
            if not item:
                return ask("Сейчас ничего не играет.")
            return ask(f"Играет {fmt_item(item)}.")

        # ma.queue гарантирует dict, но подстрахуемся: без queue_id ни один
        # хендлер работать не может
        queue = await ma.queue(player_id)
        queue_id = (queue or {}).get("queue_id") or player_id

        if parsed.intent in (Intent.NEXT, Intent.PREV):
            # Переключаем не «следующий относительно текущего» (ma.next), а
            # явным индексом: если трек как раз дослушал, MA уже сам ушёл
            # вперёд, и ma.next перескочил бы через один — а названный трек
            # не совпадал бы с тем, что реально играет.
            queue = await ma.queue(player_id)
            queue_id = queue.get("queue_id", player_id)
            index = queue.get("current_index")
            index = index if isinstance(index, int) else 0
            total = queue.get("items") or 0
            target = index + 1 if parsed.intent is Intent.NEXT else index - 1
            if isinstance(total, int) and total > 0:
                target = max(0, min(target, total - 1))
            target = max(0, target)
            # Название берём из очереди по индексу, а не из current_item после
            # play_index: MA применяет переключение не мгновенно, и чтение сразу
            # после вызова возвращало ПРЕДЫДУЩИЙ трек («дальше» называла текущий).
            item = await ma.queue_item(queue_id, target)
            generation = next_nav_generation(player_id)
            spawn(start_index_after_reply(player_id, queue_id, target, generation))
            spawn(relock_volume_after_reply(player_id, _VOL.get(player_id, volume_level(player))))
            return ask(f"{fmt_item(item, with_album=False)}.") if item else ask("Готово.")

        if parsed.intent is Intent.PAUSE:
            await ma.pause(player_id)
            pos_pause(queue_id)
            spawn(relock_volume_after_reply(player_id, _VOL.get(player_id, volume_level(player))))
            return ask("Тихо.")

        if parsed.intent is Intent.STOP:
            await ma.stop(player_id)
            pos_set(queue_id, 0)
            return ask("Выключила.")

        if parsed.intent is Intent.RESUME:
            await ma.play(player_id)
            pos_set(queue_id, pos_get(queue_id))
            spawn(relock_volume_after_reply(player_id, _VOL.get(player_id, volume_level(player))))
            await asyncio.sleep(0.4)
            item = await current_item(player_id)
            return ask(f"{fmt_item(item, with_album=False)}.") if item else ask("Играю.")

        if parsed.intent in (Intent.LOUDER, Intent.QUIETER, Intent.VOLUME):
            # станция отдаёт громкость с задержкой, поэтому держим свою оценку
            current = _VOL.get(player_id, volume_level(player))
            if parsed.intent is Intent.VOLUME:
                level = parsed.level if parsed.level is not None else 5
            elif parsed.intent is Intent.LOUDER:
                level = min(10, current + 1)
            else:
                level = max(0, current - 1)
            _VOL[player_id] = level
            await ma.volume_set(player_id, level * 10)
            return ask(f"Громкость {level}.")

        if parsed.intent in (Intent.SHUFFLE, Intent.SHUFFLE_OFF):
            enabled = parsed.intent is Intent.SHUFFLE
            queue = await ma.queue(player_id)
            total = queue_size(queue)
            if enabled and not total:
                return ask("Очередь пуста — скажи «включи» и название, потом перемешаю.")
            queue_id = queue.get("queue_id", player_id)
            await ma.cmd(
                "player_queues/shuffle",
                queue_id=queue_id,
                shuffle_enabled=enabled,
            )
            if not enabled:
                return ask("Перемешивание выключила, играю по порядку.")
            queue_id = queue.get("queue_id", player_id)
            # Перемешивание в MA пересоздаёт поток: старый URL перестаёт обновляться,
            # и колонка замолкает до следующего трека. Поэтому сразу стартуем
            # play_index — он отдаёт станции новый поток. Индекс выбираем так,
            # чтобы он отличался от текущего: иначе та же песня начинается заново.
            current = queue.get("current_index")
            current = current if isinstance(current, int) else 0
            # Перезапуск — в фоне: не тратим бюджет ответа и не спорим с озвучкой
            # нашего же ответа (станция в этот момент проигрывает TTS Алисы).
            spawn(shuffle_playback(player_id, queue_id, total, current))
            return ask(
                f"Перемешала {total} {plural(total, 'трек', 'трека', 'треков')} и включаю."
            )

        if parsed.intent is Intent.REPEAT:
            queue = await ma.queue(player_id)
            await ma.repeat(queue.get("queue_id", player_id), parsed.mode or "one")
            text = {
                "one": "Повторяю этот трек.",
                "all": "Повторяю всю очередь.",
                "off": "Повтор выключен.",
            }.get(parsed.mode, "Повтор обновлён.")
            return ask(text)

        if parsed.intent is Intent.SEEK:
            queue = await ma.queue(player_id)
            media = (queue.get("current_item") or {}).get("media_item") or {}
            duration = int(media.get("duration") or 0)
            if not duration:
                return ask("Сейчас ничего не играет — нечего перематывать.")
            position = pos_get(queue.get("queue_id", player_id))
            if parsed.seek == "start":
                target = 0
            elif parsed.seek == "end":
                target = duration - 5
            elif parsed.seek == "middle":
                target = duration // 2
            else:
                target = position + int(parsed.seek)
            target = max(0, min(duration - 2, int(target)))
            await ma.seek(queue.get("queue_id", player_id), target)
            pos_set(queue.get("queue_id", player_id), target)
            return ask(f"Перемотала на {mmss(target)}.")

        if parsed.intent is Intent.SIMILAR:
            item = await current_item(player_id)
            queue = await ma.queue(player_id)
            queue_id = queue.get("queue_id", player_id)
            try:
                similar = await ma.similar_tracks(item) if item else []
            except MAError as exc:
                log.warning("похожие не получились: %s", exc)
                similar = []
            if similar:
                random.shuffle(similar)
                uris = [t["uri"] for t in similar[:25]]
                spawn(start_queue_after_reply(player_id, queue_id, uris))
                await ma.autoplay(queue_id, True)
                return ask(f"Включаю похожее: {len(uris)} {plural(len(uris), 'трек', 'трека', 'треков')}.")
            if not queue_size(queue):
                return ask("Сначала скажи «включи» и название — потом включу похожее.")
            await ma.autoplay(queue_id, True)
            if not is_playing(player):
                await ma.play(player_id)
            return ask("Похожих не нашла, включила автоподбор в очередь.")

        if parsed.intent is Intent.FAVORITES:
            tracks = await ma.favorite_tracks(FAVORITES_LIMIT)
            if not tracks:
                return ask("В избранном пока пусто — скажи «лайк», и я запомню трек.")
            random.shuffle(tracks)
            uris = [t["uri"] for t in tracks[:100]]
            queue = await ma.queue(player_id)
            queue_id = queue.get("queue_id", player_id)
            spawn(start_queue_after_reply(player_id, queue_id, uris))
            return ask(f"Включаю избранное: {len(uris)} {plural(len(uris), 'трек', 'трека', 'треков')}, вперемешку.")

        if parsed.intent is Intent.LIKE:
            item = await current_item(player_id)
            if not item:
                return ask("Сейчас ничего не играет — некого лайкать.")
            await ma.add_current_to_favorites(player_id)
            return ask(f"Добавила в избранное: {fmt_item(item)}.")

        if parsed.intent is Intent.PLAY:
            search = await ma.search(parsed.query)
            media = pick_media(search, parsed.query)
            if not media:
                return ask(f"Не нашла «{parsed.query}» в библиотеке.")
            uri = media.get("uri")
            if not uri:
                return ask("Нашла, но без ссылки на трек — не могу поставить.")
            queue = await ma.queue(player_id)
            queue_id = queue.get("queue_id", player_id)
            index = queue.get("current_index")
            total = queue_size(queue)
            # Само включение — в фоне. Провайдер станции на команду audio_play
            # иногда не отвечает вовремя (PlayerCommandFailed: timeout), и если
            # ждать его в ответе, Алиса слышит «музыкальный сервер не ответил»,
            # хотя трек потом всё равно включается.
            spawn(play_uri_after_reply(player_id, queue_id, uri, index, total))
            return ask(f"Включаю {media.get('name', parsed.query)}.")

    except MAError as exc:
        event("ERROR", f"Music Assistant: {exc}")
        if "нет связи" in str(exc):
            return ask("Музыкальный сервер не ответил вовремя.")
        return ask("Домашний сервер не отвечает или не нашёл колонку.")

    return ask("Не поняла. Скажи «что играет», «дальше», «пауза» или «включи» и название.")



def _token_ok(request: Request) -> bool:
    """Токен для /api/*: заголовок X-Api-Token или ?token= (для <img>)."""
    if not API_TOKEN:
        return False  # токен не задан → API выключен (не «открыт»!)
    given = (request.headers.get("x-api-token") or request.query_params.get("token") or "").strip()
    return bool(given) and hmac.compare_digest(given, API_TOKEN)


def _deny() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "нет доступа: нужен X-Api-Token (см. config.json на ПК)"},
        status_code=401,
    )


def _api_guard(request: Request) -> JSONResponse | None:
    """Проверка доступа к /api/*.

    Пустой API_TOKEN означает «API выключен», а не «API без пароля»: иначе при
    установке без токена весь пульт открывался наружу через /alice/api/*.
    """
    if not API_TOKEN:
        return JSONResponse(
            {"ok": False, "error": "API_TOKEN не задан на сервере — API выключен"},
            status_code=503,
        )
    return None if _token_ok(request) else _deny()


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    """Слушает ли кто-то порт на этой машине (Navidrome, Glagol-сокет станции)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _uptime_human() -> str:
    total = int(time.time() - STARTED_AT)
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours >= 24:
        return f"{hours // 24} сут {hours % 24} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _secret_ok(path_token: str) -> bool:
    """Совпадает ли сегмент URL с секретом из .env."""
    if not WEBHOOK_SECRET:
        return True  # секрет не задан (старая установка) — пускаем, но предупреждаем при старте
    return hmac.compare_digest(path_token or "", WEBHOOK_SECRET)


def _not_found() -> JSONResponse:
    # 404, а не 403: не подсказываем, что путь существует
    return JSONResponse({"detail": "Not Found"}, status_code=404)


async def _run_webhook(request: Request) -> JSONResponse:
    started = time.monotonic()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _not_found()

    session = body.get("session") or {}
    req = body.get("request") or {}
    command = req.get("command") or req.get("original_utterance") or ""

    # Если задан SKILL_ID — тело должно прийти именно от нашего навыка
    # (доп. проверка поверх секретного пути; Яндекс кладёт id в session.skill_id)
    skill_id = (session.get("skill_id") or "")
    if SKILL_ID and skill_id and skill_id != SKILL_ID:
        log.warning("вебхук: чужой skill_id %s", skill_id)
        return _not_found()

    # Кто говорит. Опубликованный навык доступен всем, поэтому чужого не слушаем.
    user_id = ((session.get("user") or {}).get("user_id") or "")
    if user_id:
        log.info("user_id=%s", user_id)  # по этой строке владелец узнаёт свой id
    if OWNER_USER_ID and user_id and user_id != OWNER_USER_ID:
        log.warning("чужой пользователь %s — отказываю", user_id)
        event("WARNING", f"отказ: чужой пользователь {user_id}", source="колонка")
        result = reply("Этот навык настроен на другого пользователя. Доступ закрыт.")
        result["session"] = session
        return JSONResponse(result)

    # ВАЖНО: команду разбираем и в новой сессии. Когда говорят
    # «Алиса, запусти навык домашняя музыка, включи избранное», Яндекс
    # открывает сессию (session.new) и кладёт в command уже саму команду —
    # если отвечать только приветствием, она теряется, навык закрывается,
    # и Алиса продолжает своей музыкой.
    if not command.strip():
        result = ask(
            f"{SKILL_NAME} на связи. Скажи «что играет», «дальше» или «включи» и название."
        )
    else:
        result = await handle(command)

    result["session"] = session
    answer = (result.get("response") or {}).get("text") or ""
    event("INFO", f"«{command}» → «{answer}»", source="колонка")
    log.info("ответ за %.0f мс (лимит 4500)", (time.monotonic() - started) * 1000)
    return JSONResponse(result)


async def _webhook_entry(request: Request, path_token: str = "") -> JSONResponse:
    """Единая точка входа: сначала секрет пути, потом (если задан) skill_id."""
    if not _secret_ok(path_token):
        log.warning("вебхук: неверный секрет в пути (%r)", path_token[:8])
        return _not_found()
    result = await _run_webhook(request)
    return result


@app.post("/")
async def webhook_root(request: Request) -> JSONResponse:
    # Старый адрес без секрета: работает только если WEBHOOK_SECRET не задан.
    if WEBHOOK_SECRET:
        return _not_found()
    return await _run_webhook(request)


@app.post("/{path_token}")
async def webhook_secret(request: Request, path_token: str) -> JSONResponse:
    return await _webhook_entry(request, path_token)


@app.post("/alice")
async def webhook_alice(request: Request) -> JSONResponse:
    if WEBHOOK_SECRET:
        return _not_found()
    return await _run_webhook(request)


@app.post("/alice/{path_token}")
async def webhook_alice_secret(request: Request, path_token: str) -> JSONResponse:
    return await _webhook_entry(request, path_token)


@app.get("/api/status")
async def api_status(request: Request) -> dict[str, Any]:
    """Состояние сервера, колонки и очереди — для строки статуса в приложении."""
    guard = _api_guard(request)
    if guard is not None:
        return guard
    started = time.monotonic()
    try:
        player = await ma.player(PLAYER_NAME)
        player_id = player.get("player_id") or PLAYER_NAME
        queue = await ma.queue(player_id)
        # MA отдаёт QueueItem-обёртку: исполнитель, альбом и длительность
        # лежат внутри media_item, а name — в обёртке
        raw_item = queue.get("current_item") or {}
        cover = _cover_of(raw_item) if isinstance(raw_item, dict) else None
        item = raw_item
        if isinstance(item, dict):
            item = item.get("media_item") or item
        volume = player.get("volume_level")
        return {
            "ok": True,
            "skill_name": SKILL_NAME,
            "station": {
                "player_id": player_id,
                "name": player.get("display_name") or player_id,
                "state": player.get("state"),
                "available": player.get("available"),
                "volume": volume,
                "volume_1_10": round((volume or 0) / 10),
            },
            "queue": {
                "index": queue.get("current_index"),
                "items": queue.get("items"),
                "shuffle": queue.get("shuffle_enabled"),
                "repeat": queue.get("repeat_mode"),
            },
            "track": fmt_item(item, with_album=False) if item else None,
            "album": (item.get("album") or {}).get("name") if item else None,
            "artist": ((item.get("artists") or [{}])[0] or {}).get("name") if item else None,
            "title": item.get("name") if item else None,
            "cover": f"/api/cover?id={cover}" if cover else None,
            "auth": bool(API_TOKEN),
            "uptime": _uptime_human(),
            "elapsed": round(queue.get("elapsed_time") or 0, 1),
            "duration": round(item.get("duration") or 0, 1),
            "ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 — приложение должно увидеть ошибку, а не 500
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/command")
async def api_command(request: Request) -> dict[str, Any]:
    """Выполнить фразу как от Алисы. Возвращает текст ответа навыка."""
    started = time.monotonic()
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    guard = _api_guard(request)
    if guard is not None:
        return guard
    text = str(payload.get("text") or "").strip()
    source = _log_source(request.headers)
    if not text:
        return {"ok": False, "error": "пустая команда"}
    event("INFO", f"запрос: «{text}»", source=source)
    try:
        result = await handle(text)
        answer = (result.get("response") or {}).get("text") or ""
    except MAError as exc:
        log.error("[%s] MA: %s", source, exc)
        return {"ok": False, "error": f"нет связи с Music Assistant: {exc}"}
    except Exception as exc:  # noqa: BLE001 — не роняем приложение
        log.exception("[%s] ошибка выполнения", source)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    duration = round((time.monotonic() - started) * 1000)
    event("INFO", f"«{answer}» ({duration} мс)", source=source)
    return {"ok": True, "text": answer, "ms": duration}


@app.get("/api/events")
async def api_events(request: Request, since: int = 0, limit: int = 300) -> dict[str, Any]:
    """Журнал: всё, что навык делал — и голосом, и из приложения."""
    guard = _api_guard(request)
    if guard is not None:
        return guard
    items = EVENTS.snapshot(since)[-limit:]
    return {"events": items, "last": EVENTS.seq}



# ======================== очередь, обложки, диагностика ========================

def _cover_of(item: dict[str, Any]) -> str | None:
    """proxy_id обложки: сначала у элемента очереди, потом у самого медиа."""
    image = item.get("image")
    if isinstance(image, dict) and image.get("proxy_id"):
        return image["proxy_id"]
    media = item.get("media_item") or {}
    images = (media.get("metadata") or {}).get("images") or []
    if isinstance(images, list):
        for entry in images:
            if isinstance(entry, dict) and entry.get("proxy_id"):
                return entry["proxy_id"]
    return None


def _queue_item_short(item: dict[str, Any]) -> dict[str, Any]:
    media = item.get("media_item") or {}
    artists = media.get("artists") or []
    artist = (artists[0] or {}).get("name") if artists else None
    album = (media.get("album") or {}).get("name")
    cover = _cover_of(item)
    return {
        "index": item.get("index"),
        "title": media.get("name") or item.get("name"),
        "artist": artist,
        "album": album,
        "duration": round(item.get("duration") or media.get("duration") or 0, 1),
        "cover": f"/api/cover?id={cover}" if cover else None,
    }


async def _player_and_queue() -> tuple[str, dict[str, Any]]:
    player = await ma.player(PLAYER_NAME)
    player_id = player.get("player_id") or PLAYER_NAME
    queue = await ma.queue(player_id)
    return player_id, queue


@app.get("/api/queue")
async def api_queue(request: Request, offset: int = 0, limit: int = 60, near: int = -1) -> dict[str, Any]:
    """Что играет дальше: элементы очереди с названиями и обложками."""
    guard = _api_guard(request)
    if guard is not None:
        return guard
    started = time.monotonic()
    try:
        player_id, queue = await _player_and_queue()
        queue_id = queue.get("queue_id") or player_id
        total = int(queue.get("items") or 0)
        current = queue.get("current_index")
        limit = max(1, min(200, limit))
        if near >= 0 and total:
            # показать окно вокруг играющего трека, а не начало очереди
            offset = max(0, min(near - 3, max(0, total - 1)))
        offset = max(0, min(offset, max(0, total - 1))) if total else 0
        items = []
        if total:
            raw = await ma.cmd(
                "player_queues/items", queue_id=queue_id, limit=limit, offset=offset
            ) or []
            # MA нумерует элементы относительно выданного окна — считаем абсолютный
            # индекс сами, иначе клик по треку включит не тот
            for pos, entry in enumerate(raw):
                if isinstance(entry, dict):
                    entry = dict(entry)
                    entry["index"] = offset + pos
                    items.append(entry)
        return {
            "ok": True,
            "total": total,
            "current": current,
            "offset": offset,
            "shuffle": bool(queue.get("shuffle_enabled")),
            "repeat": queue.get("repeat_mode"),
            "items": [_queue_item_short(i) for i in items if isinstance(i, dict)],
            "ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 — приложение должно увидеть текст ошибки
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/cover")
async def api_cover(request: Request, id: str = "") -> Any:
    """Прокси обложки: приложение не должно знать токен Music Assistant."""
    guard = _api_guard(request)
    if guard is not None:
        return guard
    if not id:
        return JSONResponse({"ok": False, "error": "нет id обложки"}, status_code=400)
    try:
        data, mime = await ma.image(id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    return Response(content=data, media_type=mime, headers={"Cache-Control": "max-age=86400"})


@app.post("/api/play-index")
async def api_play_index(request: Request) -> dict[str, Any]:
    """Включить конкретный элемент очереди (клик по треку в списке)."""
    guard = _api_guard(request)
    if guard is not None:
        return guard
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    try:
        index = int(payload.get("index"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "нужен целый index"}
    if index < 0:
        return {"ok": False, "error": "index не может быть отрицательным"}
    source = _log_source(request.headers)
    try:
        player_id, queue = await _player_and_queue()
        queue_id = queue.get("queue_id") or player_id
        spawn(start_index_after_reply(player_id, queue_id, index))
        event("INFO", f"включаю трек №{index + 1} из очереди", source=source)
        return {"ok": True, "index": index}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/diagnostics")
async def api_diagnostics(request: Request) -> dict[str, Any]:
    """Проверка всей цепочки: навык → MA → колонка → поток → Navidrome."""
    guard = _api_guard(request)
    if guard is not None:
        return guard

    checks: list[dict[str, Any]] = []

    def add(cid: str, title: str, state: str, detail: str, hint: str = "") -> None:
        checks.append({"id": cid, "title": title, "state": state, "detail": detail, "hint": hint})

    add("skill", "Навык", "ok",
        f"работает {_uptime_human()}, API-токен {'включён' if API_TOKEN else 'выключен'}",
        "" if API_TOKEN else "API открыт для всей домашней сети — включи токен")

    if WEBHOOK_SECRET:
        add("webhook", "Вебхук", "ok", "секретный путь задан: посторонний URL не выполнится", "")
    else:
        add("webhook", "Вебхук", "fail",
            "секрет не задан — навык выполнит команду от кого угодно, кто узнал адрес",
            "задайте WEBHOOK_SECRET в .env и укажите новый адрес в Яндекс.Диалогах")

    if OWNER_USER_ID:
        add("access", "Доступ", "ok", "только для вашего аккаунта", "")
    else:
        add("access", "Доступ", "warn",
            "навык слушает любого пользователя",
            "если навык опубликован — задайте OWNER_USER_ID (см. .env.example), "
            "иначе управлять колонкой сможет любой, кто знает имя навыка")

    players: list[dict[str, Any]] | None = None
    started = time.monotonic()
    try:
        players = await ma.cmd("players/all", timeout=4.0) or []
        add("ma", "Music Assistant", "ok",
            f"отвечает за {round((time.monotonic() - started) * 1000)} мс, плееров: {len(players)}")
    except Exception as exc:  # noqa: BLE001
        add("ma", "Music Assistant", "fail", f"нет ответа: {exc}",
            "проверь контейнер music-assistant на ваш-сервер")

    station: dict[str, Any] | None = None
    if players is not None:
        for pl in players:
            if not isinstance(pl, dict):
                continue
            pid = str(pl.get("player_id") or "")
            name = str(pl.get("display_name") or "")
            if PLAYER_NAME and PLAYER_NAME in (pid, name):
                station = pl
                break
            if station is None and "yandex" in str(pl.get("provider") or "").lower():
                station = pl
        if station is None:
            add("station", "Колонка", "fail", "станция не найдена среди плееров",
                "проверь провайдер Яндекс.Станции в Music Assistant")
        else:
            available = bool(station.get("available"))
            state = str(station.get("state") or "?")
            vol = round(station.get("volume_level") or 0)
            add("station", "Колонка",
                "ok" if available else "fail",
                f"{station.get('display_name') or station.get('player_id')}: {state}, громкость {vol}%",
                "" if available else "колонка офлайн: включи её и подожди ~30 секунд")


    stream = stream_alive()
    playing = str((station or {}).get("state") or "") == "playing"
    if stream:
        add("stream", "Поток на колонку (8097)", "ok", "колонка читает поток")
    elif playing:
        add("stream", "Поток на колонку (8097)", "fail",
            "MA считает, что играет, но соединения нет",
            "это и есть «тишина при играет»: нажми «продолжи» или перезапусти поток")
    else:
        add("stream", "Поток на колонку (8097)", "ok", "соединения нет (сейчас и не должно быть)")

    # Navidrome может жить на другом хосте (NAS) — тогда локальный порт ничего
    # не значит. Проверяем только если адрес задан явно: NAVIDROME_URL.
    if NAVIDROME_URL:
        host, _, port_s = NAVIDROME_URL.split("//", 1)[-1].partition(":")
        port = int(port_s.split("/")[0] or 80) if port_s else 80
        reachable = _port_open(port, host=host)
        add("navidrome", "Navidrome", "ok" if reachable else "fail",
            f"{NAVIDROME_URL}: библиотека отвечает" if reachable else f"{NAVIDROME_URL}: нет соединения",
            "" if reachable else "проверь, что Navidrome запущен и адрес верный")
    else:
        add("navidrome", "Navidrome", "skip",
            "адрес не задан — проверять нечего",
            "укажите NAVIDROME_URL в .env (например https://music.example.com), чтобы проверять библиотеку")

    try:
        player_id, queue = await _player_and_queue()
        items = int(queue.get("items") or 0)
        current = queue.get("current_index")
        where = f", играет №{(current or 0) + 1}" if current is not None else ""
        add("queue", "Очередь", "ok" if items else "warn",
            f"{items} треков{where}",
            "" if items else "очередь пуста — скажи «включи …» или выбери трек")
    except Exception as exc:  # noqa: BLE001
        add("queue", "Очередь", "fail", f"не удалось получить: {exc}",
            "обычно значит, что очередь не активна — включи любой трек")

    failed = [c for c in checks if c["state"] == "fail"]
    warned = [c for c in checks if c["state"] == "warn"]
    return {
        "ok": not failed,
        "checks": checks,
        "failed": len(failed),
        "warned": len(warned),
        "ts": time.time(),
        "ms": round((time.monotonic() - started) * 1000),
    }



@app.get("/health")
async def health() -> dict[str, Any]:
    # Наружу этот адрес открыт (по нему ждёт инсталлер), поэтому без подробностей:
    # список колонок и их имена — не то, что стоит показывать интернету.
    try:
        await ma.cmd("players/all")
        return {"ok": True}
    except MAError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("health failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

@app.get("/alice/health")
async def health_alice() -> dict[str, Any]:
    return await health()
