# Music Assistant + провайдер Яндекс.Станции

Официальный образ Music Assistant с community-провайдером станции **1.7.9**.
Без него на актуальных прошивках станция принимает команды, но не играет
(в логах — тишина или `audio_play failed`).

## Когда нужен

- **Нет MA** — соберите этот образ, `docker compose --profile ma up -d`.
- **Есть MA, но станция молчит** — пересоберите свой MA по этому Dockerfile.
- **Есть MA и всё играет** — не нужен, укажите его в `.env`.

## Что внутри

| Компонент | Версия | Почему именно эта |
|---|---|---|
| Music Assistant | 2.10.12 | проверенная база |
| yandex_station | 1.7.9 | community-провайдер, работает локально через Glagol |
| ya-passport-auth | 2.2.0 | 1.7.0 из коробки несовместим с провайдером |
| segno | 1.6.6 | QR для входа в аккаунт |

## Атрибуция

Провайдер `yandex_station` — MIT, адаптация [AlexxIT/YandexStation](https://github.com/AlexxIT/YandexStation),
автор community-версии — [@trudenboy](https://github.com/trudenboy). Код лежит в
`yandex_station/` без изменений; лицензия MIT, см. `yandex_station/manifest.json`
и кредиты в шапке пакета.

Music Assistant — Apache 2.0, © Music Assistant. Мы его не модифицируем, только
пересобираем с другим набором зависимостей.
