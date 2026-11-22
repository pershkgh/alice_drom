using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;

namespace MusicControl
{
    public sealed class StatusResponse
    {
        public bool ok { get; set; }
        public string skill_name { get; set; }
        public string error { get; set; }
        public StationInfo station { get; set; }
        public QueueInfo queue { get; set; }
        public string title { get; set; }
        public string artist { get; set; }
        public string album { get; set; }
        public string track { get; set; }
        public string cover { get; set; }
        public double elapsed { get; set; }
        public double duration { get; set; }
    }

    public sealed class StationInfo
    {
        public string name { get; set; }
        public string state { get; set; }
        public bool available { get; set; }
        public int volume_1_10 { get; set; }
    }

    public sealed class QueueInfo
    {
        public int index { get; set; }
        public int items { get; set; }
        public bool shuffle { get; set; }
        public string repeat { get; set; }
    }

    public sealed class EventRow
    {
        public long id { get; set; }
        public double ts { get; set; }
        public string level { get; set; }
        public string source { get; set; }
        public string text { get; set; }
    }

    public sealed class EventsResponse
    {
        public List<EventRow> events { get; set; }
        public long last { get; set; }
    }

    public sealed class CommandResponse
    {
        public bool ok { get; set; }
        public string text { get; set; }
        public string error { get; set; }
    }

    public sealed class QueueItem
    {
        public int index { get; set; }
        public string title { get; set; }
        public string artist { get; set; }
        public string album { get; set; }
        public double duration { get; set; }
        public string cover { get; set; }
    }

    public sealed class QueueResponse
    {
        public bool ok { get; set; }
        public string error { get; set; }
        public int total { get; set; }
        public int? current { get; set; }
        public int offset { get; set; }
        public List<QueueItem> items { get; set; }
    }

    public sealed class DiagCheck
    {
        public string title { get; set; }
        public string state { get; set; }
        public string detail { get; set; }
        public string hint { get; set; }
    }

    public sealed class DiagnosticsResponse
    {
        public bool ok { get; set; }
        public string error { get; set; }
        public long ms { get; set; }
        public List<DiagCheck> checks { get; set; }
    }

    /// <summary>Клиент HTTP-API навыка «Домашняя музыка».</summary>
    public sealed class Api
    {
        private readonly HttpClient _http;

        public string BaseUrl { get; private set; }
        public string Token { get; private set; }

        /// <summary>Указан ли адрес сервера — без него показываем экран настроек.</summary>
        public bool IsConfigured => !string.IsNullOrWhiteSpace(BaseUrl);

        public static string ConfigPath => Path.Combine(AppContext.BaseDirectory, "config.json");

        public static string ConfigFolder => AppContext.BaseDirectory;

        public Api() : this(ReadSetting("baseUrl"), ReadSetting("apiToken"))
        {
        }

        public Api(string baseUrl, string token)
        {
            BaseUrl = (baseUrl ?? "").Trim().TrimEnd('/');
            Token = (token ?? "").Trim();

            if (!string.IsNullOrWhiteSpace(BaseUrl) && !BaseUrl.StartsWith("http", StringComparison.OrdinalIgnoreCase))
            {
                BaseUrl = "http://" + BaseUrl;
            }

            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(25) };
            _http.DefaultRequestHeaders.Add("X-Source", "app");
            if (!string.IsNullOrWhiteSpace(Token))
            {
                _http.DefaultRequestHeaders.Add("X-Api-Token", Token);
            }
            if (!string.IsNullOrWhiteSpace(BaseUrl))
            {
                try
                {
                    _http.BaseAddress = new Uri(BaseUrl + "/");
                }
                catch
                {
                    BaseUrl = "";
                }
            }
        }

        /// <summary>Сохранить адрес и токен в config.json рядом с exe.</summary>
        public static void SaveConfig(string baseUrl, string token)
        {
            var body = "{\n  \"baseUrl\": " + Json.Escape((baseUrl ?? "").Trim().TrimEnd('/')) +
                       ",\n  \"apiToken\": " + Json.Escape((token ?? "").Trim()) + "\n}\n";
            File.WriteAllText(ConfigPath, body, new UTF8Encoding(false));
        }

        public static string ReadSetting(string key)
        {
            try
            {
                if (!File.Exists(ConfigPath))
                {
                    return "";
                }
                var root = Json.Parse(File.ReadAllText(ConfigPath));
                return Json.Str(root, key) ?? "";
            }
            catch
            {
                return "";
            }
        }

        /// <summary>URL обложки (токен MA приложению не нужен — его подставит навык).</summary>
        public string CoverUrl(string relative)
        {
            if (string.IsNullOrWhiteSpace(relative) || string.IsNullOrWhiteSpace(BaseUrl))
            {
                return null;
            }
            var url = BaseUrl + relative;
            if (!string.IsNullOrWhiteSpace(Token))
            {
                url += (url.Contains("?") ? "&" : "?") + "token=" + Uri.EscapeDataString(Token);
            }
            return url;
        }

        // ── запросы ─────────────────────────────────────────────────────────
        public async Task<StatusResponse> GetStatusAsync()
        {
            var root = await GetJsonAsync("api/status").ConfigureAwait(false);
            var res = new StatusResponse
            {
                ok = Json.Bool(root, "ok"),
                skill_name = Json.Str(root, "skill_name"),
                error = Json.Str(root, "error"),
                title = Json.Str(root, "title"),
                artist = Json.Str(root, "artist"),
                album = Json.Str(root, "album"),
                track = Json.Str(root, "track"),
                cover = Json.Str(root, "cover"),
                elapsed = Json.Num(root, "elapsed"),
                duration = Json.Num(root, "duration")
            };
            var st = Json.Get(root, "station");
            if (st is Dictionary<string, object>)
            {
                res.station = new StationInfo
                {
                    name = Json.Str(st, "name"),
                    state = Json.Str(st, "state"),
                    available = Json.Bool(st, "available"),
                    volume_1_10 = Json.Int(st, "volume_1_10")
                };
            }
            var q = Json.Get(root, "queue");
            if (q is Dictionary<string, object>)
            {
                res.queue = new QueueInfo
                {
                    index = Json.Int(q, "index"),
                    items = Json.Int(q, "items"),
                    shuffle = Json.Bool(q, "shuffle"),
                    repeat = Json.Str(q, "repeat")
                };
            }
            return res;
        }

        public async Task<CommandResponse> SendCommandAsync(string command)
        {
            var body = "{\"text\":" + Json.Escape(command) + "}";
            var root = await PostJsonAsync("api/command", body).ConfigureAwait(false);
            return new CommandResponse
            {
                ok = Json.Bool(root, "ok"),
                text = Json.Str(root, "text"),
                error = Json.Str(root, "error")
            };
        }

        public async Task<CommandResponse> PlayIndexAsync(int index)
        {
            var root = await PostJsonAsync("api/play-index", "{\"index\":" + index + "}").ConfigureAwait(false);
            return new CommandResponse
            {
                ok = Json.Bool(root, "ok"),
                error = Json.Str(root, "error")
            };
        }

        public async Task<QueueResponse> GetQueueAsync(int offset, int limit, int near = -1)
        {
            var nearArg = near >= 0 ? "&near=" + near : "";
            var root = await GetJsonAsync("api/queue?offset=" + offset + "&limit=" + limit + nearArg).ConfigureAwait(false);
            var res = new QueueResponse
            {
                ok = Json.Bool(root, "ok"),
                error = Json.Str(root, "error"),
                total = Json.Int(root, "total"),
                current = Json.IntOrNull(root, "current"),
                offset = Json.Int(root, "offset"),
                items = new List<QueueItem>()
            };
            var items = Json.Arr(root, "items");
            if (items != null)
            {
                foreach (var node in items)
                {
                    res.items.Add(new QueueItem
                    {
                        index = Json.Int(node, "index"),
                        title = Json.Str(node, "title"),
                        artist = Json.Str(node, "artist"),
                        album = Json.Str(node, "album"),
                        duration = Json.Num(node, "duration"),
                        cover = Json.Str(node, "cover")
                    });
                }
            }
            return res;
        }

        public async Task<DiagnosticsResponse> GetDiagnosticsAsync()
        {
            var root = await GetJsonAsync("api/diagnostics").ConfigureAwait(false);
            var res = new DiagnosticsResponse
            {
                ok = Json.Bool(root, "ok"),
                error = Json.Str(root, "error"),
                ms = (long)Json.Num(root, "ms"),
                checks = new List<DiagCheck>()
            };
            var checks = Json.Arr(root, "checks");
            if (checks != null)
            {
                foreach (var node in checks)
                {
                    res.checks.Add(new DiagCheck
                    {
                        title = Json.Str(node, "title"),
                        state = Json.Str(node, "state"),
                        detail = Json.Str(node, "detail"),
                        hint = Json.Str(node, "hint")
                    });
                }
            }
            return res;
        }

        public async Task<EventsResponse> GetEventsAsync(long since, int limit = 200)
        {
            var root = await GetJsonAsync("api/events?since=" + since + "&limit=" + limit).ConfigureAwait(false);
            var res = new EventsResponse { events = new List<EventRow>(), last = (long)Json.Num(root, "last") };
            var events = Json.Arr(root, "events");
            if (events != null)
            {
                foreach (var node in events)
                {
                    res.events.Add(new EventRow
                    {
                        id = (long)Json.Num(node, "id"),
                        ts = Json.Num(node, "ts"),
                        level = Json.Str(node, "level"),
                        source = Json.Str(node, "source"),
                        text = Json.Str(node, "text")
                    });
                }
            }
            return res;
        }

        // ── транспорт ───────────────────────────────────────────────────────
        private async Task<object> GetJsonAsync(string path)
        {
            if (!IsConfigured)
            {
                return null;
            }
            try
            {
                using (var resp = await _http.GetAsync(path).ConfigureAwait(false))
                {
                    var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                    return Json.Parse(text);
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        private async Task<object> PostJsonAsync(string path, string body)
        {
            if (!IsConfigured)
            {
                return null;
            }
            try
            {
                using (var content = new StringContent(body, Encoding.UTF8, "application/json"))
                using (var resp = await _http.PostAsync(path, content).ConfigureAwait(false))
                {
                    var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                    return Json.Parse(text);
                }
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
