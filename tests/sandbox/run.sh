#!/usr/bin/env bash
# Изолированная проверка навыка на macOS: реальный контейнер + мок Music Assistant.
# Ничего общего с рабочим сервером: отдельный порт, отдельный каталог, всё удаляется.
set -u
PYTHON="${PYTHON:-python3}"

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="${WORK:-$(mktemp -d)}"
MOCK_PORT="${MOCK_PORT:-18095}"
SKILL_PORT="${SKILL_PORT:-18100}"
IMAGE="alice-sandbox-skill:test"
TOKEN="sandbox-token-123"
NAME="Тестовая музыка"
FAIL=0

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
note() { printf '  \033[2m%s\033[0m\n' "$*"; }

cleanup() {
  docker rm -f alice-sandbox >/dev/null 2>&1
  [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null
}
trap cleanup EXIT

# ── 1. мок Music Assistant ───────────────────────────────────────────────────
say "1. Поднимаю мок Music Assistant на :$MOCK_PORT"
pkill -f "mock-ma.py $MOCK_PORT" 2>/dev/null || true
"$PYTHON" "$REPO/tests/sandbox/mock-ma.py" "$MOCK_PORT" "$WORK/calls.log" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 20); do
  curl -fsS -m 1 "http://127.0.0.1:$MOCK_PORT/info" >/dev/null 2>&1 && break
  sleep 0.3
done
if curl -fsS -m 1 "http://127.0.0.1:$MOCK_PORT/info" >/dev/null 2>&1; then
  ok "мок отвечает"
else
  bad "мок не поднялся"; cat "$WORK/mock.log"; exit 1
fi

# ── 2. сборка образа ─────────────────────────────────────────────────────────
say "2. Собираю образ навыка (skill/)"
rm -rf "$WORK/skill" && cp -r "$REPO/skill" "$WORK/skill"
if docker build -q -t "$IMAGE" "$WORK/skill" > "$WORK/build.log" 2>&1; then
  ok "образ собран: $(docker images -q "$IMAGE" | head -1 | cut -c1-12)"
else
  bad "сборка не удалась"; tail -20 "$WORK/build.log"; exit 1
fi

# ── 3. запуск ────────────────────────────────────────────────────────────────
say "3. Запускаю навык на :$SKILL_PORT (порт хоста → 8100 в контейнере)"
docker rm -f alice-sandbox >/dev/null 2>&1
docker run -d --name alice-sandbox \
  --add-host=host.docker.internal:host-gateway \
  -p "$SKILL_PORT:8100" \
  -e "MA_URL=http://host.docker.internal:$MOCK_PORT" \
  -e "MA_TOKEN=sandbox-ma-token" \
  -e "API_TOKEN=$TOKEN" \
  -e "SKILL_NAME=$NAME" \
  -e "OWNER_USER_ID=owner-777" \
  -e "WEBHOOK_SECRET=s3cret-test" \
  -e "TZ=Europe/Moscow" \
  -e "LOG_LEVEL=INFO" \
  "$IMAGE" > /dev/null

READY=0
for _ in $(seq 1 40); do
  if curl -fsS -m 2 "http://127.0.0.1:$SKILL_PORT/health" >/dev/null 2>&1; then READY=1; break; fi
  sleep 1
done
if [ "$READY" = "1" ]; then ok "навык поднялся и отвечает на /health"; else bad "навык не ответил за 40 с"; docker logs --tail 20 alice-sandbox; exit 1; fi

api() { curl -s -m 10 -H "X-Api-Token: $TOKEN" "http://127.0.0.1:$SKILL_PORT$1"; }
post() { curl -s -m 20 -H "X-Api-Token: $TOKEN" -H "Content-Type: application/json" \
           -d "$2" "http://127.0.0.1:$SKILL_PORT$1"; }

# ── 4. авторизация ───────────────────────────────────────────────────────────
say "4. Авторизация"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$SKILL_PORT/api/status")
[ "$code" = "401" ] && ok "без токена /api/status → 401" || bad "без токена вернулось $code, ждали 401"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$SKILL_PORT/health")
[ "$code" = "200" ] && ok "/health открыт (200)" || bad "/health вернул $code"
H=$(curl -s -m 5 "http://127.0.0.1:$SKILL_PORT/health")
echo "$H" | grep -q '"players"' && bad "/health отдаёт список плееров наружу" || ok "/health без подробностей"

# ── 5. состояние ─────────────────────────────────────────────────────────────
say "5. /api/status"
STATUS=$(api /api/status)
echo "$STATUS" | grep -q '"ok":true'                  && ok "ok:true" || bad "нет ok:true: $STATUS"
echo "$STATUS" | grep -q "\"skill_name\":\"$NAME\""   && ok "имя навыка из SKILL_NAME: $NAME" || bad "имя навыка не пришло"
echo "$STATUS" | grep -q '"title":"Bring Me To Life"'  && ok "трек из мока: Bring Me To Life" || bad "трек не разобран: $STATUS"
echo "$STATUS" | grep -q '"artist":"Evanescence"'      && ok "исполнитель: Evanescence" || bad "исполнитель не разобран"
echo "$STATUS" | grep -q '"duration":235'              && ok "длительность 235 с" || bad "длительность не та"
echo "$STATUS" | grep -q '"volume_1_10":4'             && ok "громкость 40 → 4/10" || bad "громкость не пересчитана"
if echo "$STATUS" | grep -q '"cover":"/api/cover?id='; then
  ok "статус отдаёт обложку текущего трека"
  CV=$(echo "$STATUS" | sed -n 's/.*"cover":"\([^"]*\)".*/\1/p')
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H "X-Api-Token: $TOKEN" "http://127.0.0.1:$SKILL_PORT$CV")
  [ "$code" = "200" ] && ok "обложка из статуса скачивается (200)" || bad "обложка из статуса: код $code"
else
  bad "в статусе нет обложки: $STATUS"
fi
note "$STATUS"

# ── 6. команды ───────────────────────────────────────────────────────────────
say "6. Команды через /api/command"
check_cmd() { # <фраза> <ожидаемая команда в моке|-> <текст ответа содержит>
  local res; res=$(post /api/command "{\"text\":\"$1\"}")
  sleep 0.5
  if echo "$res" | grep -q '"ok":true'; then ok "«$1» → ok"; else bad "«$1» → $res"; fi
  if [ "$2" != "-" ]; then
    if grep -q "$2" "$WORK/calls.log" 2>/dev/null; then ok "  дошло до MA: $2"; else bad "  MA не получил $2"; fi
  fi
  [ -n "${3:-}" ] && { echo "$res" | grep -q "$3" && ok "  ответ: $3" || bad "  в ответе нет «$3»: $res"; }
  note "$(echo "$res" | head -c 150)"
}
rm -f "$WORK/calls.log"
check_cmd "пауза"                "players/cmd/pause"
check_cmd "продолжи"             "players/cmd/play"
check_cmd "дальше"               "player_queues/play_index"
check_cmd "громкость 3"          "players/cmd/volume_set" "Громкость"
check_cmd "что играет"           "-" "Evanescence"
check_cmd "включи evanescence"   "player_queues/play_media" "Evanescence"

# ── 7. прочие ручки ──────────────────────────────────────────────────────────
say "7. Очередь, обложка, диагностика"
Q=$(api "/api/queue?offset=0&limit=5")
echo "$Q" | grep -q '"total"' && ok "очередь отдаётся" || bad "очередь: $Q"
echo "$Q" | grep -q 'Трек 1'   && ok "элементы очереди на месте" || bad "элементов нет"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H "X-Api-Token: $TOKEN" "http://127.0.0.1:$SKILL_PORT/api/cover?id=sandbox-cover-1")
[ "$code" = "200" ] && ok "обложка проксируется (/api/cover → 200)" || bad "обложка: код $code"
D=$(api /api/diagnostics)
echo "$D" | grep -q '"checks"' && ok "диагностика вернула список проверок" || bad "диагностика: $D"
echo "$D" | grep -q '"id":"access"' && ok "диагностика проверяет доступ" || bad "нет проверки доступа"
echo "$D" | grep -q '"failed":0' && ok "диагностика: ошибок нет" || note "диагностика не полностью зелёная (ожидаемо: нет колонки/потока)"

# ── 7.5 доступ по пользователю ───────────────────────────────────────────────
say "7.5 Доступ: только владелец"
webhook() { # <путь> <user_id> <фраза>
  curl -s -m 10 -H "Content-Type: application/json" \
    -d "{\"session\":{\"user\":{\"user_id\":\"$2\"}},\"request\":{\"command\":\"$3\"}}" \
    "http://127.0.0.1:$SKILL_PORT$1"
}
code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST -H "Content-Type: application/json" \
  -d '{"session":{"user":{"user_id":"owner-777"}},"request":{"command":"что играет"}}' \
  "http://127.0.0.1:$SKILL_PORT/")
[ "$code" = "404" ] && ok "старый путь / без секрета закрыт (404)" || bad "/ отдал $code, ждали 404"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST -H "Content-Type: application/json" \
  -d '{"session":{"user":{"user_id":"owner-777"}},"request":{"command":"что играет"}}' \
  "http://127.0.0.1:$SKILL_PORT/wrong-secret")
[ "$code" = "404" ] && ok "неверный секрет → 404" || bad "неверный секрет отдал $code"
R=$(webhook "/s3cret-test" "stranger-1" "пауза")
echo "$R" | grep -q "другого пользователя" && ok "чужой пользователь получает отказ" || bad "чужого пустили: $R"
R=$(webhook "/s3cret-test" "owner-777" "что играет")
echo "$R" | grep -q "Evanescence" && ok "владелец работает как обычно" || bad "владельцу не ответили: $R"

# ── 8. поведение без MA ──────────────────────────────────────────────────────
say "8. MA упал — навык не должен висеть"
kill "$MOCK_PID" 2>/dev/null; MOCK_PID=""
sleep 1
S2=$(api /api/status)
echo "$S2" | grep -q '"ok":false' && ok "/api/status → ok:false (честно) " || bad "статус не деградировал: $S2"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H "X-Api-Token: $TOKEN" "http://127.0.0.1:$SKILL_PORT/health")
[ "$code" = "200" ] && ok "/health остаётся 200 (контейнер жив)" || bad "/health вернул $code"
R=$(post /api/command '{"text":"дальше"}')
echo "$R" | grep -q 'не ответил' && ok "команда без MA: внятный ответ, а не зависание" || bad "команда без MA: $R"
note "$R"

# ── 9. логи ──────────────────────────────────────────────────────────────────
say "9. Логи навыка (последние строки)"
docker logs --tail 12 alice-sandbox 2>&1 | sed 's/^/   /'

say "ИТОГ"
if [ "$FAIL" = "0" ]; then
  printf '  \033[32mвсе проверки прошли\033[0m\n'
else
  printf '  \033[31mпровалено проверок: %s\033[0m\n' "$FAIL"
fi
exit "$FAIL"
