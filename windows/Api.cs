using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace MusicControl
{
    /// <summary>Ответ /api/status.</summary>
    public sealed class StatusResponse
    {
        public bool ok { get; set; }
        public string error { get; set; }
        public StationInfo station { get; set; }
        public QueueInfo queue { get; set; }
        public string track { get; set; }
        public string album { get; set; }
        public string artist { get; set; }
        public string title { get; set; }
        public double elapsed { get; set; }
        public double duration { get; set; }
    }

    public sealed class StationInfo
    {
        public string player_id { get; set; }
        public string name { get; set; }
        public string state { get; set; }
        public bool available { get; set; }
        public int volume { get; set; }
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
        public long ms { get; set; }
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
        public string id { get; set; }
        public string title { get; set; }
        public string state { get; set; }
        public string detail { get; set; }
        public string hint { get; set; }
    }

    public sealed class DiagnosticsResponse
    {
        public bool ok { get; set; }
        public string error { get; set; }
        public int failed { get; set; }
        public int warned { get; set; }
        public long ms { get; set; }
        public List<DiagCheck> checks { get; set; }
    }

    /// <summary>Клиент HTTP-API навыка «Домашняя музыка».</summary>
    public sealed class Api
    {
        private static readonly JsonSerializerOptions JsonOpts = new()
        {
            PropertyNameCaseInsensitive = true
        };

        private readonly HttpClient _http;

        public string BaseUrl { get; }
        public string Token { get; }

        public Api()
        {
            BaseUrl = ReadBaseUrl();
            Token = ReadSetting("apiToken");
            _http = new HttpClient { BaseAddress = new Uri(BaseUrl.TrimEnd('/') + "/") };
            _http.DefaultRequestHeaders.Add("X-Source", "app");
            if (!string.IsNullOrWhiteSpace(Token))
            {
                _http.DefaultRequestHeaders.Add("X-Api-Token", Token);
            }
            _http.Timeout = TimeSpan.FromSeconds(25);
        }

        /// <summary>URL обложки: приложение не знает токен Music Assistant, его подставит навык.</summary>
        public string CoverUrl(string relative)
        {
            if (string.IsNullOrWhiteSpace(relative))
            {
                return null;
            }
            var url = BaseUrl.TrimEnd('/') + relative;
            if (!string.IsNullOrWhiteSpace(Token))
            {
                url += (url.Contains('?') ? "&" : "?") + "token=" + Uri.EscapeDataString(Token);
            }
            return url;
        }

        private static string ReadSetting(string key)
        {
            try
            {
                var path = Path.Combine(AppContext.BaseDirectory, "config.json");
                if (!File.Exists(path))
                {
                    return "";
                }
                using var doc = JsonDocument.Parse(File.ReadAllText(path));
                return doc.RootElement.TryGetProperty(key, out var el) && el.ValueKind == JsonValueKind.String
                    ? (el.GetString() ?? "")
                    : "";
            }
            catch
            {
                return "";
            }
        }

        private static string ReadBaseUrl()
        {
            const string fallback = "http://127.0.0.1:8100";
            try
            {
                var path = Path.Combine(AppContext.BaseDirectory, "config.json");
                if (!File.Exists(path))
                {
                    return fallback;
                }
                using var doc = JsonDocument.Parse(File.ReadAllText(path));
                if (doc.RootElement.TryGetProperty("baseUrl", out var el) && el.ValueKind == JsonValueKind.String)
                {
                    var url = el.GetString();
                    if (!string.IsNullOrWhiteSpace(url))
                    {
                        return url.TrimEnd('/');
                    }
                }
            }
            catch
            {
                // некорректный config.json — работаем с умолчанием
            }
            return fallback;
        }

        public async Task<StatusResponse> GetStatusAsync()
        {
            using var resp = await _http.GetAsync("api/status").ConfigureAwait(false);
            var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            return JsonSerializer.Deserialize<StatusResponse>(text, JsonOpts) ?? new StatusResponse { ok = false, error = "пустой ответ" };
        }

        public async Task<CommandResponse> SendCommandAsync(string command)
        {
            var body = JsonSerializer.Serialize(new { text = command });
            using var content = new StringContent(body, Encoding.UTF8, "application/json");
            using var resp = await _http.PostAsync("api/command", content).ConfigureAwait(false);
            var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            return JsonSerializer.Deserialize<CommandResponse>(text, JsonOpts)
                   ?? new CommandResponse { ok = false, error = "пустой ответ" };
        }

        public async Task<QueueResponse> GetQueueAsync(int offset, int limit, int near = -1)
        {
            var nearArg = near >= 0 ? $"&near={near}" : "";
            using var resp = await _http.GetAsync($"api/queue?offset={offset}&limit={limit}{nearArg}").ConfigureAwait(false);
            var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            return JsonSerializer.Deserialize<QueueResponse>(text, JsonOpts)
                   ?? new QueueResponse { ok = false, error = "пустой ответ" };
        }

        public async Task<DiagnosticsResponse> GetDiagnosticsAsync()
        {
            using var resp = await _http.GetAsync("api/diagnostics").ConfigureAwait(false);
            var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            return JsonSerializer.Deserialize<DiagnosticsResponse>(text, JsonOpts)
                   ?? new DiagnosticsResponse { ok = false, checks = new List<DiagCheck>() };
        }

        public async Task<CommandResponse> PlayIndexAsync(int index)
        {
            var body = JsonSerializer.Serialize(new { index });
            using var content = new StringContent(body, Encoding.UTF8, "application/json");
            using var resp = await _http.PostAsync("api/play-index", content).ConfigureAwait(false);
            var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            return JsonSerializer.Deserialize<CommandResponse>(text, JsonOpts)
                   ?? new CommandResponse { ok = false, error = "пустой ответ" };
        }

        public async Task<EventsResponse> GetEventsAsync(long since, int limit = 200)
        {
            using var resp = await _http.GetAsync($"api/events?since={since}&limit={limit}").ConfigureAwait(false);
            var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            return JsonSerializer.Deserialize<EventsResponse>(text, JsonOpts) ?? new EventsResponse { events = new List<EventRow>() };
        }
    }
}
