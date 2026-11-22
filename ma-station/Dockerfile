# Music Assistant с рабочим провайдером Яндекс.Станции.
#
# Зачем нужен отдельный образ: в официальном MA провайдер станции старый и на
# актуальных прошивках станции просто молчит. Здесь поверх официального образа
# кладётся community-провайдер 1.7.9 и нужная версия ya-passport-auth.
#
# Если у вас уже есть свой MA с провайдером станции — этот образ не нужен,
# просто укажите его адрес и токен в .env.
FROM ghcr.io/music-assistant/server:2.10.12

# ya-passport-auth 1.7.0 (внутри образа) не совместим с провайдером 1.7.9:
# падает с ImportError: cannot import name 'NoUsableCredentialsError'
RUN pip install --no-cache-dir --force-reinstall --retries 5 --timeout 60 \
    "ya-passport-auth[ma]==2.2.0" "segno==1.6.6"

# community-провайдер (MIT, адаптация AlexxIT/YandexStation, автор @trudenboy)
COPY yandex_station/ /app/venv/lib/python3.14/site-packages/music_assistant/providers/yandex_station/

LABEL org.opencontainers.image.title="Alice Music — Music Assistant с провайдером Яндекс.Станции" \
      org.opencontainers.image.description="Music Assistant 2.10.12 + yandex_station 1.7.9" \
      org.opencontainers.image.licenses="MIT"
