#!/usr/bin/env bash
# Сухой прогон install.sh без Docker: подставные docker/curl/dig, настоящие вопросы и .env.
# Проверяет логику скрипта (вопросы, .env, права, сообщения) и ничего не устанавливает.
set -u

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
FAIL=0

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }

trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/shim"
cp -r "$REPO/install.sh" "$REPO/doctor.sh" "$REPO/docker-compose.yml" "$REPO/.env.example" \
      "$REPO/deploy" "$REPO/skill" "$REPO/ma-station" "$WORK"/
chmod +x "$WORK/install.sh" "$WORK/doctor.sh"

# ── подставные команды ───────────────────────────────────────────────────────
cat > "$WORK/shim/docker" <<'SH'
#!/usr/bin/env bash
echo "docker $*" >> "$SHIM_LOG"
case "$*" in
  *"compose version"*) echo "Docker Compose version v2.29.0" ;;
  *"compose config"*)  echo "services: {}" ;;
  *"compose"*"up"*)    echo " Container alice-skill  Started" ;;
  *"compose"*"ps"*)    echo "alice-skill  Up" ;;
  *"ps"*)              echo "alice-skill" ;;
  *) : ;;
esac
exit 0
SH

cat > "$WORK/shim/curl" <<'SH'
#!/usr/bin/env bash
echo "curl $*" >> "$SHIM_LOG"
case "$*" in
  *"/health"*)     echo '{"ok":true}' ;;
  *"/api/status"*) echo '{"ok":true,"station":{"available":true,"volume_1_10":4},"title":"Тестовый трек"}' ;;
  *) echo '' ;;
esac
exit 0
SH

cat > "$WORK/shim/dig" <<'SH'
#!/usr/bin/env bash
echo "203.0.113.10"
SH

cat > "$WORK/shim/ss" <<'SH'
#!/usr/bin/env bash
exit 0
SH

chmod +x "$WORK"/shim/*
export SHIM_LOG="$WORK/commands.log"
export PATH="$WORK/shim:$PATH"

ANSWERS="music.example.com
me@example.com
2
1
http://127.0.0.1:8095
ma-token-песочница
Станция (тест)
Моя радиоточка
"

say "1. Прогон install.sh с ответами"
printf '%s' "$ANSWERS" | (cd "$WORK" && ./install.sh) > "$WORK/run.log" 2>&1
CODE=$?
[ "$CODE" = "0" ] && ok "скрипт завершился с кодом 0" || bad "код выхода $CODE"

say "2. Что записано в .env"
for key in DOMAIN EMAIL MA_URL MA_TOKEN PLAYER_NAME SKILL_NAME API_TOKEN; do
  if grep -q "^$key=" "$WORK/.env" 2>/dev/null; then
    ok "$key=$(grep "^$key=" "$WORK/.env" | cut -d= -f2-)"
  else
    bad "нет $key в .env"
  fi
done

say "3. Права и секреты"
perm=$(stat -c '%a' "$WORK/.env" 2>/dev/null || stat -f '%Lp' "$WORK/.env")
[ "$perm" = "600" ] && ok ".env с правами 600" || bad ".env с правами $perm"
tok=$(grep '^API_TOKEN=' "$WORK/.env" | cut -d= -f2-)
[ ${#tok} -ge 32 ] && ok "API_TOKEN сгенерирован (${#tok} символов)" || bad "API_TOKEN короткий: $tok"

say "4. Сообщения для пользователя"
grep -q "music.example.com/alice" "$WORK/run.log" && ok "печатает вебхук с нужным доменом" || bad "вебхук не напечатан"
grep -q "Моя радиоточка" "$WORK/run.log" && ok "печатает ваше имя навыка" || bad "имя навыка не напечатано"
grep -qi "Яндекс.Диалог" "$WORK/run.log" && ok "напоминает про Яндекс.Диалоги" || bad "нет напоминания про Диалоги"
grep -q "docker compose" "$WORK/commands.log" && ok "вызывал docker compose" || bad "docker compose не вызывался"

say "5. Повторный запуск (прежний .env сохраняется)"
printf '%s' "$ANSWERS" | (cd "$WORK" && ./install.sh) > "$WORK/run2.log" 2>&1
ls "$WORK"/.env.backup.* >/dev/null 2>&1 && ok "сделан бэкап прежнего .env" || bad "бэкап не создан"

say "6. Начало вывода установщика"
sed -n '1,12p' "$WORK/run.log" | sed 's/^/   /'

say "ИТОГ"
if [ "$FAIL" = "0" ]; then printf '  \033[32mвсе проверки прошли\033[0m\n'; else printf '  \033[31mпровалено: %s\033[0m\n' "$FAIL"; fi
exit "$FAIL"
