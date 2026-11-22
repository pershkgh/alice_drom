# Пульт для Windows

<p align="center"><img src="icon-512.png" width="112" alt="Иконка пульта"></p>

Одно окно вместо консоли: плеер, журнал, диагностика и очередь с обложками.

## Не хочу собирать — дайте готовый файл

Откройте **Releases** репозитория и скачайте `MusicControl.exe` (он там с версии `v1.0.0`).
Рядом положите `config.json` из того же релиза и впишите свой адрес сервера и `API_TOKEN`.

## Сборка

Нужен .NET 8 SDK (или соберитесь в CI — см. `.gitlab-ci.yml`, job `windows:exe`).

```bash
cd windows
dotnet publish -c Release -r win-x64 --self-contained true \
  -p:PublishSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:EnableCompressionInSingleFile=true \
  -o publish
```

Получится `publish/MusicControl.exe` (~70 МБ): .NET внутри, устанавливать ничего не нужно.
Собирается и на macOS/Linux — свойство `EnableWindowsTargeting` уже включено в проект.

## Настройка

Рядом с exe положите `config.json`:

```json
{
  "baseUrl": "http://192.0.2.10:8100",
  "apiToken": "ваш API_TOKEN из .env"
}
```

## Что внутри

| Раздел | Что делает |
|---|---|
| Домой | текущий трек, прогресс, громкость, быстрые действия, журнал, поле фразы |
| Проверка | диагностика цепочки навык → MA → станция → поток → библиотека |
| Очередь | что играет дальше, с обложками; клик — включить трек |

Токен нужен потому, что `/api/*` закрыт: приложение шлёт его в заголовке `X-Api-Token`.

## Если Windows ругается

SmartScreen на неподписанный exe: «Подробнее» → «Выполнить в любом случае»,
либо снимите блокировку в свойствах файла («Разблокировать»).
