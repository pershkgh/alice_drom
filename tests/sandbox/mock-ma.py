#!/usr/bin/env python3
"""Мок Music Assistant для изолированной проверки навыка.

Отвечает на JSON-RPC POST /api по тем командам, которые реально дёргает навык.
Станция «играет» трек, очередь из 5 штук, громкость 40. Никакого реального MA
и никакой колонки — только stdlib.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18095
CALLS_LOG = sys.argv[2] if len(sys.argv) > 2 else "/tmp/alice-sandbox/calls.log"
PLAYER_ID = "ys_SANDBOX"

TRACK = {
    "name": "Evanescence — Bring Me To Life",
    "uri": "library://track/1",
    "media_item": {
        "name": "Bring Me To Life",
        "uri": "library://track/1",
        "duration": 235,
        "artists": [{"name": "Evanescence"}],
        "album": {"name": "Fallen"},
        "image": {"proxy_id": "sandbox-cover-1"},
    },
}

PLAYER = {
    "player_id": PLAYER_ID,
    "display_name": "Станция (песочница)",
    "state": "playing",
    "available": True,
    "volume_level": 40,
    "provider": "yandex_station",
    "powered": True,
    "active_source": f"{PLAYER_ID}_queue",
    "can_seek": True,
}

QUEUE = {
    "queue_id": PLAYER_ID,
    "display_name": "Очередь станции",
    "state": "playing",
    "current_item": TRACK,
    "items": len(ITEMS) if False else 5,
    "current_index": 2,
    "index_in_buffer": 2,
    "elapsed_time": 42,
    "shuffle_enabled": False,
    "repeat_mode": "off",
    "active": True,
}

ITEMS = [
    {
        "name": f"Трек {i + 1}",
        "uri": f"library://track/{i + 1}",
        "media_item": {
            "name": f"Трек {i + 1}",
            "duration": 200,
            "artists": [{"name": "Тестовый исполнитель"}],
            "album": {"name": "Тестовый альбом"},
            "image": {"proxy_id": f"sandbox-cover-{i + 1}"},
        },
    }
    for i in range(5)
]

CALLS: list[dict] = []


def handle(command: str, args: dict):
    if command == "players/all":
        return [PLAYER]
    if command == "players/get":
        return PLAYER
    if command == "player_queues/all":
        return [QUEUE]
    if command == "player_queues/get_active_queue":
        return QUEUE
    if command == "player_queues/get":
        return QUEUE
    if command == "player_queues/items":
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 100)
        # настоящее MA отдаёт на player_queues/items обычный список
        return ITEMS[offset:offset + limit]
    if command == "music/tracks/library_items":
        return [
            {"name": "Bring Me To Life", "uri": "library://track/1",
             "artists": [{"name": "Evanescence"}], "album": {"name": "Fallen"}},
            {"name": "My Immortal", "uri": "library://track/2",
             "artists": [{"name": "Evanescence"}], "album": {"name": "Fallen"}},
        ]
    if command == "music/search":
        return {
            "tracks": [
                {"name": "Bring Me To Life", "uri": "library://track/1",
                 "artists": [{"name": "Evanescence"}], "album": {"name": "Fallen"},
                 "duration": 235}
            ],
            "artists": [{"name": "Evanescence", "uri": "library://artist/1"}],
            "albums": [{"name": "Fallen", "uri": "library://album/1",
                        "artists": [{"name": "Evanescence"}]}],
        }
    if command == "players/cmd/volume_set":
        PLAYER["volume_level"] = args.get("volume_level", PLAYER["volume_level"])
        return None
    if command in {"player_queues/play_media", "player_queues/play_index"}:
        QUEUE["index_in_buffer"] = args.get("index", 0)
        QUEUE["state"] = "playing"
        PLAYER["state"] = "playing"
        return None
    if command == "players/cmd/pause":
        PLAYER["state"] = "paused"
        QUEUE["state"] = "paused"
        return None
    if command == "players/cmd/play":
        PLAYER["state"] = "playing"
        QUEUE["state"] = "playing"
        return None
    if command in {"players/cmd/next", "players/cmd/previous"}:
        step = 1 if command.endswith("next") else -1
        QUEUE["index_in_buffer"] = max(0, QUEUE["index_in_buffer"] + step)
        return None
    if command == "players/cmd/stop":
        PLAYER["state"] = "idle"
        QUEUE["state"] = "idle"
        return None
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # тише
        pass

    def do_GET(self):
        if self.path.startswith("/imageproxy/"):
            # крошечный PNG 1x1 — навык проксирует обложки из MA
            png = bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000100000001080600000"
                "01f15c4890000000a49444154789c6360000002000100ffff03000006"
                "000557bfabd40000000049454e44ae426082")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        if self.path == "/info":
            body = json.dumps({"mock": True, "calls": len(CALLS)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        command = payload.get("command", "")
        args = payload.get("args") or {}
        CALLS.append({"command": command, "args": args})
        try:
            with open(CALLS_LOG, "a", encoding="utf-8") as fh:
                fh.write(command + "\n")
        except OSError:
            pass
        result = handle(command, args)
        body = json.dumps({"message_id": payload.get("message_id"), "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"mock MA на :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
