#!/usr/bin/env bash
# Диагностика: проверяет всю цепочку навык → MA → станция → поток → библиотека.
set -uo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "нет .env — сначала запустите ./install.sh" >&2
  exit 1
fi

docker compose exec -T skill python - <<'PY'
import json, os, urllib.request

token = os.getenv("API_TOKEN", "").strip()
req = urllib.request.Request(
    "http://127.0.0.1:8100/api/diagnostics",
    headers={"X-Api-Token": token} if token else {},
)
try:
    data = json.load(urllib.request.urlopen(req, timeout=30))
except Exception as exc:
    print(f"не удалось получить диагностику: {exc}")
    raise SystemExit(1)

icon = {"ok": "\033[32m✓\033[0m", "warn": "\033[33m!\033[0m", "fail": "\033[31m✗\033[0m"}
print()
for check in data.get("checks", []):
    print(f"  {icon.get(check['state'], '?')} {check['title']}: {check['detail']}")
    if check.get("hint"):
        print(f"      → {check['hint']}")
print()
verdict = "всё в порядке" if data.get("ok") else f"сломано: {data.get('failed')}"
print(f"  {verdict} · проверка за {data.get('ms')} мс")
print()
raise SystemExit(0 if data.get("ok") else 2)
PY
