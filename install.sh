#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Домашняя музыка — установщик навыка для Алисы.
#
#  Что делает: проверяет окружение, спрашивает данные, генерирует .env,
#  поднимает контейнеры и показывает, что осталось сделать руками
#  (зарегистрировать навык в Яндекс.Диалогах).
#
#  Использование:
#     ./install.sh                    — интерактивно
#     DOMAIN=... MA_TOKEN=... ./install.sh --yes   — без вопросов
#     ./install.sh --help
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
RED=$'\033[31m'; BLUE=$'\033[36m'; RESET=$'\033[0m'

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
err()  { printf '%s✗%s %s\n' "$RED" "$RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }
hr()   { printf '%s──────────────────────────────────────────────────%s\n' "$DIM" "$RESET"; }

ask() {  # ask "вопрос" "по умолчанию" -> echo значение
  local q="$1" def="${2:-}" answer
  if [ "$ASSUME_YES" = "1" ] && [ -n "$def" ]; then printf '%s' "$def"; return; fi
  if [ -n "$def" ]; then
    printf '%s%s%s [%s]: ' "$BOLD" "$q" "$RESET" "$def" >&2
  else
    printf '%s%s%s: ' "$BOLD" "$q" "$RESET" >&2
  fi
  read -r answer || true
  printf '%s' "${answer:-$def}"
}

ask_secret() {  # без эха
  local q="$1" answer
  printf '%s%s%s: ' "$BOLD" "$q" "$RESET" >&2
  stty -echo 2>/dev/null || true
  read -r answer || true
  stty echo 2>/dev/null || true
  printf '\n' >&2
  printf '%s' "$answer"
}

ask_choice() {  # ask_choice "вопрос" "1" "варианты..."
  # варианты печатаем в stderr: stdout уходит в $(...), иначе меню пропадёт
  local q="$1" def="$2"; shift 2
  local i=1
  printf '\n' >&2
  for opt in "$@"; do printf '  %s%s%s) %s\n' "$BOLD" "$i" "$RESET" "$opt" >&2; i=$((i+1)); done
  local answer; answer=$(ask "$q" "$def")
  printf '%s' "${answer:-$def}"
}

gen_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# ── шапка ────────────────────────────────────────────────────────────────────
say ""
say "${BOLD}Домашняя музыка${RESET} — навык для Алисы"
say "${DIM}играет вашу библиотеку Navidrome через Music Assistant на Яндекс.Станции${RESET}"
hr

# ── 1. окружение ─────────────────────────────────────────────────────────────
say ""
say "${BOLD}1. Проверяю окружение${RESET}"

command -v docker >/dev/null 2>&1 || die "нет docker. Установите: https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || die "docker не отвечает. Запущен ли демон? Нужны права: sudo usermod -aG docker \$USER"
ok "Docker $(docker --version | awk '{print $3}' | tr -d ,)"

if docker compose version >/dev/null 2>&1; then
  ok "Docker Compose $(docker compose version --short)"
else
  die "нет docker compose v2 (плагин). Поставьте docker-compose-plugin"
fi

command -v curl >/dev/null 2>&1 || warn "curl не найден — проверки после установки будут недоступны"

# занятые порты
port_busy() { ss -ltn 2>/dev/null | grep -q ":$1 " && echo 1 || echo 0; }
for p in 8100 8095; do
  if [ "$(port_busy $p)" = "1" ]; then
    warn "порт $p уже занят — если это не наш контейнер, освободите его"
  fi
done

# ── 2. домен ─────────────────────────────────────────────────────────────────
say ""
say "${BOLD}2. Адрес, по которому Яндекс будет стучаться в навык${RESET}"
say "${DIM}Домен должен уже указывать на этот сервер (A-запись), порты 80/443 — быть свободны.${RESET}"

DOMAIN="${DOMAIN:-}"
[ -z "$DOMAIN" ] && DOMAIN=$(ask "Домен" "")
[ -n "$DOMAIN" ] || die "без домена навык не заработает: Яндексу нужен публичный HTTPS-адрес"
DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"

EMAIL="${EMAIL:-}"
if [ -z "$EMAIL" ]; then EMAIL=$(ask "Email для сертификата Let's Encrypt" "admin@$DOMAIN"); fi

PROXY=$(ask_choice "У вас уже есть свой обратный прокси (nginx/Caddy/Traefik) на этом сервере?" "1" \
  "Да, есть — я сам проксирую" "Нет — поставьте Caddy из комплекта")

# ── 3. Music Assistant ───────────────────────────────────────────────────────
say ""
say "${BOLD}3. Music Assistant${RESET}"

MA_MODE=$(ask_choice "Что с Music Assistant?" "1" \
  "Уже есть — введу адрес и токен" \
  "Нет — поднимите из комплекта (с рабочим провайдером станции)")

MA_TOKEN="${MA_TOKEN:-}"
PLAYER_NAME="${PLAYER_NAME:-}"
MA_PROFILE=""

if [ "$MA_MODE" = "1" ]; then
  MA_URL="${MA_URL:-}"
  [ -z "$MA_URL" ] && MA_URL=$(ask "Адрес MA" "http://127.0.0.1:8095")
  if [ -z "$MA_TOKEN" ]; then
    say "${DIM}Токен: Music Assistant → Настройки → Профиль → «Долгоживущий токен» → Создать${RESET}"
    MA_TOKEN=$(ask_secret "Токен MA")
  fi
  [ -n "$MA_TOKEN" ] || die "без токена MA навык не сможет управлять музыкой"
  say "${DIM}Проверяю MA…${RESET}"
  if curl -fsS -m 10 -H "Authorization: Bearer $MA_TOKEN" \
       "${MA_URL%/}/api" -H 'Content-Type: application/json' \
       -d '{"message_id":"install","command":"players/all","args":{}}' >/dev/null 2>&1; then
    ok "MA отвечает"
  else
    warn "MA не ответил на проверку — продолжим, но проверьте адрес и токен"
  fi
else
  MA_URL="http://127.0.0.1:8095"
  MA_PROFILE="ma"
  MA_TOKEN=""
  ok "MA будет поднят из комплекта (версия с провайдером станции 1.7.9)"
fi

PLAYER_NAME=$(ask "Имя/id станции (пусто — взять единственную)" "$PLAYER_NAME")

# ── 4. .env ──────────────────────────────────────────────────────────────────
say ""
say "${BOLD}4. Записываю настройки${RESET}"

API_TOKEN="${API_TOKEN:-$(gen_token)}"

if [ -f .env ] && [ "$ASSUME_YES" != "1" ]; then
  cp .env ".env.backup.$(date +%Y%m%d-%H%M%S)"
  warn "старый .env сохранён рядом как .env.backup.*"
fi

umask 077
cat > .env <<ENV
# Сгенерировано install.sh $(date '+%Y-%m-%d %H:%M:%S')
DOMAIN=$DOMAIN
EMAIL=$EMAIL
MA_URL=$MA_URL
MA_TOKEN=$MA_TOKEN
PLAYER_NAME=$PLAYER_NAME
API_TOKEN=$API_TOKEN
TZ=${TZ:-Europe/Moscow}
LOG_LEVEL=INFO
SESSION_HOLD=1
ENV
chmod 600 .env
ok ".env записан (права 600)"

# ── 5. запуск ────────────────────────────────────────────────────────────────
say ""
say "${BOLD}5. Поднимаю контейнеры${RESET}"

PROFILES=""
[ -n "$MA_PROFILE" ] && PROFILES="$PROFILES --profile ma"
[ "$PROXY" = "2" ] && PROFILES="$PROFILES --profile caddy"

# shellcheck disable=SC2086
docker compose $PROFILES up -d --build 2>&1 | sed 's/^/   /'

say "${DIM}жду, пока навык ответит…${RESET}"
READY=0
for _ in $(seq 1 40); do
  if curl -fsS -m 3 http://127.0.0.1:8100/health >/dev/null 2>&1; then READY=1; break; fi
  sleep 3
done

if [ "$READY" = "1" ]; then
  ok "навык запущен"
  STATUS=$(curl -fsS -m 10 -H "X-Api-Token: $API_TOKEN" http://127.0.0.1:8100/api/status 2>/dev/null || echo '{}')
  case "$STATUS" in
    *'"ok":true'*) ok "MA и станция на связи" ;;
    *'"available":false'*) warn "станция офлайн — включите её и подождите ~30 секунд" ;;
    *) warn "навык поднялся, но связь с MA не подтверждена — запустите ./doctor.sh" ;;
  esac
else
  warn "навык не ответил за 2 минуты. Логи: docker compose logs -f skill"
fi

# ── 6. что дальше ────────────────────────────────────────────────────────────
say ""
hr
say "${BOLD}Готово. Осталось зарегистрировать навык у Яндекса.${RESET}"
say ""
say "  1. Откройте ${BLUE}https://dialogs.yandex.ru/developer${RESET}"
say "  2. «Создать диалог» → ${BOLD}Навык для Алисы${RESET}"
say "  3. В поле «Webhook URL» вставьте:"
say ""
say "       ${BOLD}${GREEN}https://$DOMAIN/alice${RESET}"
say ""
say "  4. Сохраните. Статус «Черновик» — достаточно, модерация не нужна."
say "     Публичным навык делать НЕ надо: иначе вашей колонкой сможет управлять кто угодно."
say "  5. Скажите колонке: «${BOLD}Алиса, запусти навык Домашняя музыка${RESET}»"
say ""
hr
say "Пульт и диагностика: ${BLUE}http://$(hostname -I 2>/dev/null | awk '{print $1}'):8100${RESET}"
say "Приложение для Windows: см. ${BOLD}windows/README.md${RESET}"
say "Токен API (для пульта): ${DIM}$API_TOKEN${RESET}"
say "Если что-то молчит: ${BOLD}./doctor.sh${RESET}"
say ""
