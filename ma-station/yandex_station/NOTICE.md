# Лицензия и происхождение кода

Каталог `yandex_station/` — **community-провайдер Яндекс.Станции** для Music Assistant,
положен сюда **без изменений** (файл `manifest.json` — из самого пакета).

| | |
|---|---|
| Версия | 1.7.9 |
| Автор community-версии | [@trudenboy](https://github.com/trudenboy) |
| Основа | [AlexxIT/YandexStation](https://github.com/AlexxIT/YandexStation) (MIT) |
| Лицензия | MIT, текст — в файле [LICENSE](LICENSE) рядом (© 2020 AlexxIT) |
| Документация MA | https://www.music-assistant.io/player-support/yandex-station/ |

Мы не модифицируем код провайдера: `ma-station/Dockerfile` только копирует его
в образ Music Assistant и ставит совместимые зависимости (`ya-passport-auth[ma]==2.2.0`,
`segno==1.6.6`).

Music Assistant — Apache 2.0, © Music Assistant. Мы его не меняем, только пересобираем
с другим набором зависимостей.
