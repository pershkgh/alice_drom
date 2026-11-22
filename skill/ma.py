"""Клиент Music Assistant (JSON-RPC поверх HTTP).

Все команды идут POST-ом на {MA_URL}/api с Bearer-токеном:
    {"message_id": "<любая строка>", "command": "player_queues/play_media", "args": {...}}
Ответ — {"message_id": ..., "result": ...} либо {"error_code": ..., "details": ...}.

Команды, которые использует навык (из исходников MA 2.10):
    players/all                        список плееров
    player_queues/get_active_queue     очередь плеера (там current_item)
    player_queues/play_media           поставить медиа в очередь
    players/cmd/pause|play|next|previous|volume_up|volume_down
    music/search                       поиск по библиотеке и провайдерам
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

log = logging.getLogger("ma")


class MAError(Exception):
    """Music Assistant ответил ошибкой или недоступен."""


class MA:
    def __init__(self, url: str, token: str, timeout: float = 2.5) -> None:
        self.url = url.rstrip("/") + "/api"
        self.token = token
        self.timeout = timeout
        self._player_id: str | None = None

    def _check_token(self) -> None:
        if not self.token:
            raise MAError("MA_TOKEN не задан")
        if not self.token.isascii():
            raise MAError("MA_TOKEN содержит не-ASCII символы (похоже, остался плейсхолдер)")

    async def cmd(self, command: str, *, timeout: float | None = None, **args: Any) -> Any:
        self._check_token()
        payload = {"message_id": uuid.uuid4().hex, "command": command, "args": args}
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
                resp = await client.post(self.url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise MAError(f"нет связи с Music Assistant: {exc}") from exc

        if resp.status_code != 200:
            raise MAError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        if isinstance(data, dict) and (data.get("error_code") or data.get("error")):
            raise MAError(str(data.get("details") or data.get("error_code")))
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    # --- плеер -------------------------------------------------------------

    async def player(self, preferred_name: str = "") -> dict[str, Any]:
        """Найти колонку.

        Порядок: точное совпадение player_id/display_name -> единственный
        доступный плеер провайдера yandex_station -> единственный доступный.
        """
        players = await self.cmd("players/all")
        if isinstance(players, dict):
            players = players.get("result") or []
        if not players:
            raise MAError("MA не вернул ни одного плеера")

        def available(p: dict[str, Any]) -> bool:
            return bool(p.get("available", True))

        wanted = (preferred_name or "").lower().strip()
        if wanted:
            for p in players:
                names = (str(p.get("display_name", "")).lower(), str(p.get("player_id", "")).lower())
                if wanted in names and available(p):
                    self._player_id = p["player_id"]
                    return p

        if self._player_id:
            for p in players:
                if p.get("player_id") == self._player_id and available(p):
                    return p

        candidates = [p for p in players if p.get("provider") == "yandex_station" and available(p)]
        if len(candidates) == 1:
            self._player_id = candidates[0]["player_id"]
            return candidates[0]

        candidates = [p for p in players if available(p)]
        if len(candidates) == 1:
            self._player_id = candidates[0]["player_id"]
            return candidates[0]

        names = ", ".join(f"{p.get('display_name')} ({p.get('player_id')})" for p in players)
        raise MAError(f"не смогла выбрать колонку, доступны: {names}")

    async def queue(self, player_id: str) -> dict[str, Any]:
        """Очередь плеера. Всегда возвращает dict — никогда None.

        player_queues/get_active_queue отдаёт null, если очередь не активна
        (плеер давно простаивал). Раньше это роняло обработчик с
        AttributeError: 'NoneType' object has no attribute 'get' — снаружи
        выглядело как «навык молчит».
        """
        try:
            result = await self.cmd("player_queues/get_active_queue", player_id=player_id)
            if isinstance(result, dict) and result.get("queue_id"):
                return result
        except MAError:
            pass
        for command, args in (
            ("player_queues/get", {"queue_id": player_id}),
            ("player_queues/all", {}),
        ):
            try:
                result = await self.cmd(command, **args)
            except MAError:
                continue
            if command == "player_queues/all":
                for q in result or []:
                    if isinstance(q, dict) and q.get("queue_id") == player_id:
                        return q
                continue
            if isinstance(result, dict) and result.get("queue_id"):
                return result
        log.warning("очередь плеера %s не найдена, работаю по id плеера", player_id)
        return {"queue_id": player_id, "state": "idle", "current_index": 0, "items": 0}

    # --- команды -----------------------------------------------------------

    async def next(self, player_id: str) -> None:
        await self.cmd("players/cmd/next", player_id=player_id)

    async def previous(self, player_id: str) -> None:
        await self.cmd("players/cmd/previous", player_id=player_id)

    async def pause(self, player_id: str) -> None:
        await self.cmd("players/cmd/pause", player_id=player_id)

    async def play(self, player_id: str, timeout: float | None = None) -> None:
        await self.cmd("players/cmd/play", player_id=player_id, timeout=timeout)

    async def volume(self, player_id: str, up: bool) -> None:
        await self.cmd("players/cmd/volume_up" if up else "players/cmd/volume_down",
                       player_id=player_id)

    async def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        return await self.cmd(
            "music/search",
            search_query=query,
            media_types=["track", "album", "artist", "playlist"],
            limit=limit,
        )

    async def image(self, proxy_id: str, timeout: float | None = None) -> tuple[bytes, str]:
        """Обложка из прокси MA: GET {base}/imageproxy/{proxy_id} с Bearer-токеном.

        MA отдаёт в элементах очереди image.proxy_id — готовый ключ к картинке.
        """
        self._check_token()
        if not proxy_id or not all(c.isalnum() or c in "-_" for c in proxy_id):
            raise MAError("некорректный id обложки")
        base = self.url[:-4] if self.url.endswith("/api") else self.url
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(timeout=timeout or 10.0) as client:
                resp = await client.get(f"{base}/imageproxy/{proxy_id}", headers=headers)
        except httpx.HTTPError as exc:
            raise MAError(f"нет связи с Music Assistant: {exc}") from exc
        if resp.status_code != 200:
            raise MAError(f"HTTP {resp.status_code} на обложке")
        return resp.content, resp.headers.get("content-type", "image/jpeg")

    async def play_media(self, queue_id: str, uris: str | list[str],
                         option: str = "play", timeout: float | None = None) -> None:
        """Поставить медиа в очередь и начать играть.

        option="play" вставляет трек в текущую позицию и запускает — очередь и поток
        не пересоздаются, поэтому переход звучит как обычная смена трека («далее»),
        а не как перезапуск стрима. option="replace" — заменить очередь целиком.
        """
        media = [uris] if isinstance(uris, str) else list(uris)
        try:
            await self.cmd("player_queues/play_media", queue_id=queue_id, media=media,
                           option=option, timeout=timeout)
            return
        except MAError as exc:
            log.warning("play_media option=%s не прошёл (%s), пробую replace", option, exc)
        await self.cmd("player_queues/play_media", queue_id=queue_id, media=media,
                       option="replace", timeout=timeout)


    async def queue_item(self, queue_id: str, index: int) -> dict[str, Any] | None:
        """Элемент очереди по индексу (для «дальше»/«назад» без гонки с play_index)."""
        result = await self.cmd("player_queues/items", queue_id=queue_id,
                                limit=1, offset=max(0, int(index)))
        items = result if isinstance(result, list) else (result or {}).get("items") or []
        if not items:
            return None
        first = items[0] or {}
        media = first.get("media_item") or {}
        if media:
            return media
        name = first.get("name") or ""
        return {"name": name} if name else None

    async def play_index(self, queue_id: str, index: int,
                         timeout: float | None = None) -> None:
        await self.cmd("player_queues/play_index", queue_id=queue_id,
                       index=max(0, int(index)), timeout=timeout)

    async def repeat(self, queue_id: str, mode: str) -> None:
        await self.cmd("player_queues/repeat", queue_id=queue_id, repeat_mode=mode)

    async def seek(self, queue_id: str, position: int,
                   timeout: float | None = None) -> None:
        await self.cmd("player_queues/seek", queue_id=queue_id,
                       position=max(0, int(position)), timeout=timeout)

    async def similar_tracks(self, item: dict[str, Any], limit: int = 25) -> list[dict[str, Any]]:
        item_id = item.get("item_id")
        provider = item.get("provider") or item.get("provider_instance")
        if not item_id or not provider:
            return []
        result = await self.cmd(
            "music/tracks/similar_tracks",
            timeout=max(self.timeout, 3.5),  # Last.fm/провайдер может думать пару секунд
            item_id=item_id,
            provider_instance_id_or_domain=provider,
            limit=limit,
        )
        items = result if isinstance(result, list) else (result or {}).get("items") or []
        return [it for it in items if it.get("uri")]

    async def stop(self, player_id: str, timeout: float | None = None) -> None:
        await self.cmd("players/cmd/stop", player_id=player_id, timeout=timeout)

    async def volume_set(self, player_id: str, level_0_100: int) -> None:
        await self.cmd("players/cmd/volume_set", player_id=player_id,
                       volume_level=max(0, min(100, int(level_0_100))))

    async def autoplay(self, queue_id: str, enabled: bool = True) -> None:
        await self.cmd("player_queues/autoplay", queue_id=queue_id, autoplay_enabled=enabled)

    async def add_current_to_favorites(self, player_id: str) -> None:
        await self.cmd("players/add_currently_playing_to_favorites", player_id=player_id)

    async def favorite_tracks(self, limit: int = 200) -> list[dict[str, Any]]:
        result = await self.cmd("music/tracks/library_items", favorite=True, limit=limit)
        items = result if isinstance(result, list) else (result or {}).get("items") or []
        return [it for it in items if it.get("uri")]
