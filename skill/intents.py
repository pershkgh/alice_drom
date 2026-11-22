"""Разбор фразы пользователя в интент.

Алиса отдаёт готовый текст в request.command / request.original_utterance,
никакого NLU и слотов у навыка нет — поэтому всё на регулярках, от частных
шаблонов к общим. Порядок проверок важен: «поставь лайк» не должно уехать
в «поставь <что-то>», а «включи избранное» — в поиск по слову «избранное».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    PING = "ping"
    LAUNCH = "launch"
    HELP = "help"
    NOW = "now"
    NEXT = "next"
    PREV = "prev"
    PAUSE = "pause"
    STOP = "stop"
    RESUME = "resume"
    LOUDER = "louder"
    QUIETER = "quieter"
    VOLUME = "volume"
    SHUFFLE = "shuffle"
    SHUFFLE_OFF = "shuffle_off"
    SIMILAR = "similar"
    REPEAT = "repeat"
    SEEK = "seek"
    FAVORITES = "favorites"
    LIKE = "like"
    PLAY = "play"
    UNKNOWN = "unknown"


@dataclass
class Parsed:
    intent: Intent
    query: str = ""
    level: int | None = None
    mode: str = ""       # для REPEAT: one / all / off
    seek: str = ""       # для SEEK: start / end / middle / "+60" / "-30"


NUMBERS = {
    "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "ноль": 0,
}

PLAY_VERBS = r"(включи|вруби|поставь|запусти|сыграй|включить)"
MEDIA_WORDS = r"(песню|трек|композицию|альбом|пластинку|плейлист|группу|исполнителя|музыку|сборник)"

RE_PLAY = re.compile(rf"^{PLAY_VERBS}\s+(?:мне\s+)?(?:{MEDIA_WORDS}\s+)?(?P<q>.+)$")
RE_VOLUME = re.compile(r"громкост\w*\s*(?:на\s*)?(?P<v>\d{1,2}|[а-я]+)")
RE_NOW = re.compile(
    r"(что|кто|какая|какой|какое).{0,20}(играет|поет|звучит|песня|трек|композиция|записал)"
    r"|что сейчас|что за песня|что за трек"
)
RE_LIKE = re.compile(r"\b(лайк|лайкни|лайкнуть|нравится|в избранное|добавь в избранное|в любимые)\b")
RE_FAVORITES = re.compile(r"(?:включи|поставь|запусти|переключи|играй)?\s*(?:мо[еию])?\s*(?:избранное|избранные|любимые|любимое|favorites)")
RE_SIMILAR = re.compile(r"(похоже|похожую|похожие|в том же духе|такое же|того же исполнителя|такой же стиль)")
RE_REPEAT_OFF = re.compile(r"\b(не повторяй|выключи повтор\w*|без повтор\w*|не надо повтор\w*)\b")
RE_REPEAT_ALL = re.compile(r"(повтори все|повторяй все|повтори очередь|повторяй очередь|весь альбом повторяй)")
RE_REPEAT = re.compile(r"\b(повтори|повторяй|повтор|на повтор)\b")
RE_SEEK = re.compile(r"(перемот|в начало|в конец|середин)")
RE_SHUFFLE_OFF = re.compile(r"\b(не перемешивай|выключи перемеш\w*|без перемеш\w*|по порядку|обычный порядок)\b")
RE_SHUFFLE = re.compile(r"\b(перемешай|перемешать|перемешивай|вперемешку|случайно|шафл)\b")
RE_NEXT = re.compile(r"\b(далее|дальше|следующ\w*|переключи|переключить|пропусти|скип)\b")
RE_PREV = re.compile(r"\b(предыдущ\w*|назад|верни|вернуть)\b")
RE_STOP = re.compile(r"\b(стоп|хватит|останови|остановить|выключи музыку|прекрати)\b")
RE_PAUSE = re.compile(r"\b(пауза|паузу|приостанови|замолчи)\b")
RE_RESUME = re.compile(r"\b(продолжи|продолжай|продолжить|играй|играть|запусти музыку|включи музыку)\b")
RE_LOUDER = re.compile(r"\b(громче|погромче|прибавь)\b")
RE_QUIETER = re.compile(r"\b(тише|потише|убавь)\b")
RE_LAUNCH = re.compile(r"^(запусти|открой|включи|стартуй)\s+(?:навык\s+)?(.+)$")
SKILL_NAME = re.compile(r"^(домашн\w*\s+музык\w*|домашн\w*|музык\w*)\s*")
RE_HELP = re.compile(r"\b(помощь|что ты умеешь|что умеешь|как пользоваться|навык)\b")

HELP_TEXT = (
    "Это пульт для домашней музыки. Скажи «что играет», «дальше», «назад», «пауза», «стоп», "
    "«продолжи», «громче», «тише», «громкость пять», «перемешай», «не перемешивай». "
    "Можно попросить: «включи Квин», «поставь альбом Rumours», «включи избранное», «включи похожее», "
    "«повтори», «перемотай на минуту вперёд», а «лайк» добавит текущий трек в избранное."
)


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_volume(text: str) -> int | None:
    match = RE_VOLUME.search(text)
    if not match:
        return None
    raw = match.group("v")
    if raw.isdigit():
        level = int(raw)
    else:
        level = NUMBERS.get(raw)
    if level is None:
        return None
    return max(0, min(10, level))


def parse_seek(text: str) -> str | None:
    """Во что перематывать: start / end / middle / "+60" / "-30"."""
    if "в начало" in text or "к началу" in text:
        return "start"
    if "в конец" in text:
        return "end"
    if "середин" in text:
        return "middle"

    compound = re.search(
        r"(?:(\d{1,2}|[а-я]+)\s*(?:минут\w*|мин)\s*(?:и\s*)?)?(\d{1,2})\s*(?:секунд\w*|сек)",
        text,
    )
    if compound and (compound.group(1) or "мин" in text):
        minutes_raw = compound.group(1)
        minutes = int(minutes_raw) if minutes_raw and minutes_raw.isdigit() else (NUMBERS.get(minutes_raw) or 0)
        seconds = int(compound.group(2))
        if minutes or "сек" in text:
            sign = -1 if "назад" in text else 1
            return f"{sign * (minutes * 60 + seconds):+d}"

    word_units = {"минуту": 60, "минуты": 120, "секунду": 1, "полминуты": 30, "полчаса": 1800}
    word_match = re.search(r"(вперед|назад)?\s*на\s+(минуту|минуты|секунду|полминуты|полчаса)", text)
    if word_match:
        sign = -1 if "назад" in text else 1
        return f"{sign * word_units[word_match.group(2)]:+d}"

    match = re.search(
        r"(вперед|назад)?\s*(?:на\s+)?(\d{1,3}|[а-я]+)\s*(секунд\w*|сек|минут\w*|мин)",
        text,
    )
    if not match:
        return None
    amount_raw, unit = match.group(2), match.group(3)
    amount = int(amount_raw) if amount_raw.isdigit() else NUMBERS.get(amount_raw)
    if amount is None:
        return None
    if unit.startswith("мин"):
        amount *= 60
    sign = -1 if "назад" in text else 1
    return f"{sign * amount:+d}"


def parse(raw: str) -> Parsed:
    text = normalize(raw or "")
    if not text:
        return Parsed(Intent.UNKNOWN)

    if text in {"ping", "пинг"} or text.startswith("ping"):
        return Parsed(Intent.PING)

    # «запусти навык домашняя музыка, далее» — одной фразой, чтобы не гонять
    # запуск и команду по отдельности (сессия навыка живёт всего несколько секунд)
    launch = RE_LAUNCH.match(text)
    if launch and ("навык" in text or launch.group(2).startswith("домашн")):
        rest = SKILL_NAME.sub("", launch.group(2)).strip()
        text = rest
        if not text:
            return Parsed(Intent.LAUNCH)

    if RE_HELP.search(text):
        return Parsed(Intent.HELP)

    # «громкость пять» / «громкость 7» — раньше, чем «громче»
    if "громкост" in text:
        level = parse_volume(text)
        if level is not None:
            return Parsed(Intent.VOLUME, level=level)

    if RE_LIKE.search(text):
        return Parsed(Intent.LIKE)

    if RE_FAVORITES.search(text):
        return Parsed(Intent.FAVORITES)

    if RE_SIMILAR.search(text):
        return Parsed(Intent.SIMILAR)

    if RE_NOW.search(text):
        return Parsed(Intent.NOW)

    if RE_REPEAT_OFF.search(text):
        return Parsed(Intent.REPEAT, mode="off")
    if RE_REPEAT_ALL.search(text):
        return Parsed(Intent.REPEAT, mode="all")
    if RE_REPEAT.search(text):
        return Parsed(Intent.REPEAT, mode="one")

    if RE_SEEK.search(text):
        seek = parse_seek(text)
        if seek:
            return Parsed(Intent.SEEK, seek=seek)

    if RE_SHUFFLE_OFF.search(text):
        return Parsed(Intent.SHUFFLE_OFF)
    if RE_SHUFFLE.search(text):
        return Parsed(Intent.SHUFFLE)

    if RE_NEXT.search(text):
        return Parsed(Intent.NEXT)
    if RE_PREV.search(text):
        return Parsed(Intent.PREV)
    if RE_RESUME.search(text):
        return Parsed(Intent.RESUME)

    play = RE_PLAY.match(text)
    if play:
        query = play.group("q").strip()
        # «включи включи evanescence» — устойчиво к задвоенному глаголу
        # (приложение добавляет «включи » к тому, что уже написано с глаголом)
        again = re.match(rf"^(?:{PLAY_VERBS})\s+(?:мне\s+)?(?:{MEDIA_WORDS}\s+)?(?P<q2>.+)$", query)
        if again:
            query = again.group("q2").strip()
        if query in {"музыку", "музыка", "что нибудь", "что нить"}:
            return Parsed(Intent.RESUME)
        return Parsed(Intent.PLAY, query)

    if RE_STOP.search(text):
        return Parsed(Intent.STOP)
    if RE_PAUSE.search(text):
        return Parsed(Intent.PAUSE)
    if RE_LOUDER.search(text):
        return Parsed(Intent.LOUDER)
    if RE_QUIETER.search(text):
        return Parsed(Intent.QUIETER)

    return Parsed(Intent.UNKNOWN)
