using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace MusicControl
{
    /// <summary>Строка журнала.</summary>
    public sealed class LogRow
    {
        public string Time { get; set; }
        public string Source { get; set; }
        public string Text { get; set; }
        public string Level { get; set; }
        public Brush BadgeBrush { get; set; }
        public Brush TextBrush { get; set; }
    }

    /// <summary>Строка очереди.</summary>
    public sealed class QueueRowVm : INotifyPropertyChanged
    {
        private ImageSource _cover;

        public event PropertyChangedEventHandler PropertyChanged;

        public int Index { get; set; }
        public string Number => (Index + 1).ToString(CultureInfo.InvariantCulture);
        public string Title { get; set; }
        public string Sub { get; set; }
        public string DurationText { get; set; }
        public string CoverUrl { get; set; }
        public bool IsCurrent { get; set; }
        public Brush TitleBrush { get; set; }

        public ImageSource Cover
        {
            get => _cover;
            set
            {
                _cover = value;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Cover)));
            }
        }
    }

    /// <summary>Строка диагностики.</summary>
    public sealed class DiagRowVm
    {
        public string Title { get; set; }
        public string Detail { get; set; }
        public string Hint { get; set; }
        public Brush DotBrush { get; set; }
        public Visibility HintVisibility =>
            string.IsNullOrWhiteSpace(Hint) ? Visibility.Collapsed : Visibility.Visible;
    }

    public partial class MainWindow : Window
    {
        private Api _api = new();
        private string _skillName = "";
        private string _coverUrl;
        private string _coverTrack = "";
        private readonly Dictionary<string, BitmapImage> _nowPlayingCovers = new Dictionary<string, BitmapImage>();
        private readonly HttpClient _img = new();
        private readonly ObservableCollection<LogRow> _rows = new();
        private readonly List<LogRow> _all = new();
        private readonly ObservableCollection<QueueRowVm> _queueRows = new();
        private readonly Dictionary<string, ImageSource> _coverCache = new();
        private readonly DispatcherTimer _statusTimer = new();
        private readonly DispatcherTimer _eventsTimer = new();
        private readonly DispatcherTimer _progressTimer = new();
        private readonly DispatcherTimer _volumeTimer = new();

        private long _lastEventId;
        private bool _statusBusy;
        private bool _eventsBusy;
        private bool _suppressVolume;
        private string _state = "idle";
        private string _filter = "";
        private string _page = "home";
        private int _queueOffset;
        private int _queueTotal;
        private int? _queueCurrent;
        private int _lastQueueIndex = -1;
        private double _duration;
        private double _lastElapsed;
        private DateTime _statusAt = DateTime.UtcNow;

        private static readonly SolidColorBrush BadgeVoice = new(Color.FromRgb(0xA7, 0x8B, 0xFA));
        private static readonly SolidColorBrush BadgeApp = new(Color.FromRgb(0x3F, 0xA9, 0xF5));
        private static readonly SolidColorBrush BadgeSkill = new(Color.FromRgb(0x3D, 0xDC, 0x97));
        private static readonly SolidColorBrush BadgeWarn = new(Color.FromRgb(0xFF, 0xC7, 0x5F));
        private static readonly SolidColorBrush BadgeErr = new(Color.FromRgb(0xFF, 0x6B, 0x6B));
        private static readonly SolidColorBrush TextNormal = new(Color.FromRgb(0xED, 0xF0, 0xFF));
        private static readonly SolidColorBrush TextAccent = new(Color.FromRgb(0xA7, 0x8B, 0xFA));
        private static readonly SolidColorBrush TextRed = new(Color.FromRgb(0xFF, 0x9A, 0x9A));
        private static readonly SolidColorBrush TextAmber = new(Color.FromRgb(0xFF, 0xD9, 0x8A));

        public MainWindow()
        {
            InitializeComponent();
            LogItems.ItemsSource = _rows;
            QueueItems.ItemsSource = _queueRows;
            DiagItems.ItemsSource = new ObservableCollection<DiagRowVm>();

            _statusTimer.Interval = TimeSpan.FromSeconds(2.5);
            _statusTimer.Tick += async (_, __) => await RefreshStatusAsync();
            _eventsTimer.Interval = TimeSpan.FromSeconds(1.2);
            _eventsTimer.Tick += async (_, __) => await RefreshEventsAsync();

            _progressTimer.Interval = TimeSpan.FromMilliseconds(200);
            _progressTimer.Tick += (_, __) => TickProgress();
            _progressTimer.Start();

            _volumeTimer.Interval = TimeSpan.FromMilliseconds(650);
            _volumeTimer.Tick += async (_, __) =>
            {
                _volumeTimer.Stop();
                var level = (int)Math.Round(SldVolume.Value);
                await SendAsync("громкость " + level.ToString(CultureInfo.InvariantCulture));
            };

            Loaded += async (_, __) =>
            {
                TxtSettingsUrl.Text = _api.BaseUrl;
                TxtSettingsToken.Text = _api.Token;
                if (!_api.IsConfigured)
                {
                    ShowPage("settings");
                    TxtSettingsResult.Text = "Укажите адрес сервера и токен — это всё, что нужно для подключения.";
                    SetServer(false, "не настроен");
                    return;
                }
                SetServer(false, "подключение…");
                await RefreshStatusAsync();
                await RefreshEventsAsync();
                _statusTimer.Start();
                _eventsTimer.Start();
                TxtPhrase.Focus();
            };
        }

        // ---------- навигация ----------

        private void ShowPage(string page)
        {
            _page = page;
            PageHome.Visibility = page == "home" ? Visibility.Visible : Visibility.Collapsed;
            PageDiag.Visibility = page == "diag" ? Visibility.Visible : Visibility.Collapsed;
            PageQueue.Visibility = page == "queue" ? Visibility.Visible : Visibility.Collapsed;
            PageSettings.Visibility = page == "settings" ? Visibility.Visible : Visibility.Collapsed;

            NavHome.Style = (Style)FindResource(page == "home" ? "NavButtonActive" : "NavButton");
            NavDiag.Style = (Style)FindResource(page == "diag" ? "NavButtonActive" : "NavButton");
            NavQueue.Style = (Style)FindResource(page == "queue" ? "NavButtonActive" : "NavButton");
            NavSettings.Style = (Style)FindResource(page == "settings" ? "NavButtonActive" : "NavButton");

            // иконки навигации: активная — светлая
            var iconFill = (Brush)FindResource(page == "home" ? "TextBrush" : "MutedBrush");
            if (NavHome.Content is StackPanel homeStack && homeStack.Children[0] is System.Windows.Shapes.Path homePath)
            {
                homePath.Fill = iconFill;
            }
            if (NavQueue.Content is StackPanel queueStack && queueStack.Children[0] is System.Windows.Shapes.Path queuePath)
            {
                queuePath.Fill = (Brush)FindResource(page == "queue" ? "TextBrush" : "MutedBrush");
            }
            if (NavDiag.Content is StackPanel diagStack && diagStack.Children[0] is System.Windows.Shapes.Path diagPath)
            {
                diagPath.Stroke = (Brush)FindResource(page == "diag" ? "TextBrush" : "MutedBrush");
            }
        }

        private const string ProjectUrl = "https://github.com/pershkgh/alice_drom";

        private void OnOpenGithub(object sender, RoutedEventArgs e)
        {
            try
            {
                Process.Start(new ProcessStartInfo(ProjectUrl) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                AddLocalRow("WARNING", "app", "не удалось открыть браузер: " + ex.Message);
            }
        }

        private async void OnNavHome(object sender, RoutedEventArgs e)
        {
            ShowPage("home");
            await RefreshStatusAsync();
        }

        private async void OnNavDiag(object sender, RoutedEventArgs e)
        {
            ShowPage("diag");
            await RefreshDiagnosticsAsync();
        }

        private void OnNavSettings(object sender, RoutedEventArgs e)
        {
            TxtSettingsUrl.Text = _api.BaseUrl;
            TxtSettingsToken.Text = _api.Token;
            ShowPage("settings");
        }

        private async void OnSettingsTest(object sender, RoutedEventArgs e)
        {
            var probe = new Api(TxtSettingsUrl.Text, TxtSettingsToken.Text);
            if (!probe.IsConfigured)
            {
                TxtSettingsResult.Text = "Укажите адрес сервера, например http://192.168.1.10:8100";
                return;
            }
            TxtSettingsResult.Text = "Проверяю " + probe.BaseUrl + " …";
            try
            {
                var st = await probe.GetStatusAsync();
                if (st == null)
                {
                    TxtSettingsResult.Text = "Нет ответа от " + probe.BaseUrl +
                        ". Проверьте: сервер включён, навык слушает порт 8100, сеть доступна.";
                    return;
                }
                if (!st.ok)
                {
                    TxtSettingsResult.Text = "Сервер ответил отказом: " + (st.error ?? "неизвестная причина") +
                        ". Скорее всего неверный токен.";
                    return;
                }
                var station = st.station != null && st.station.available ? "станция на связи" : "станция не в сети";
                TxtSettingsResult.Text = "Сервер отвечает · " + station + " · громкость " +
                    (st.station != null ? st.station.volume_1_10 : 0) + "/10" +
                    (string.IsNullOrWhiteSpace(st.title) ? "" : " · сейчас: " + st.title);
            }
            catch (Exception ex)
            {
                TxtSettingsResult.Text = "Ошибка проверки: " + ex.Message;
            }
        }

        private async void OnSettingsSave(object sender, RoutedEventArgs e)
        {
            try
            {
                Api.SaveConfig(TxtSettingsUrl.Text, TxtSettingsToken.Text);
                _api = new Api(TxtSettingsUrl.Text, TxtSettingsToken.Text);
                TxtSettingsResult.Text = "Сохранено в " + Api.ConfigPath;
                await RefreshStatusAsync();
                if (_api.IsConfigured)
                {
                    ShowPage("home");
                }
            }
            catch (Exception ex)
            {
                TxtSettingsResult.Text = "Не удалось сохранить: " + ex.Message + " (нет прав на папку?)";
            }
        }

        private void OnSettingsOpenConfig(object sender, RoutedEventArgs e)
        {
            try
            {
                if (System.IO.File.Exists(Api.ConfigPath))
                {
                    Process.Start("explorer.exe", "/select,\"" + Api.ConfigPath + "\"");
                }
                else
                {
                    Process.Start("explorer.exe", "\"" + Api.ConfigFolder + "\"");
                }
            }
            catch (Exception ex)
            {
                TxtSettingsResult.Text = "Не удалось открыть проводник: " + ex.Message;
            }
        }

        private async void OnNavQueue(object sender, RoutedEventArgs e)
        {
            ShowPage("queue");
            await RefreshQueueAsync(reset: true);
        }

        // ---------- HTTP ----------

        private async Task RefreshStatusAsync()
        {
            if (_statusBusy)
            {
                return;
            }
            _statusBusy = true;
            try
            {
                var st = await _api.GetStatusAsync();
                if (st == null || !st.ok)
                {
                    SetServer(false, st?.error == null ? "нет ответа" : "отказ доступа");
                    SetStation("—", false, null);
                    _state = "idle";
                    return;
                }
                SetServer(true, "на связи");
                _state = st.station?.state ?? "idle";
                SetStation(_state, st.station?.available ?? false, st.station?.name);
                ShowTrack(st);
                if (!SldVolume.IsMouseCaptureWithin)
                {
                    ShowVolume(st.station?.volume_1_10 ?? 3);
                }
            }
            catch (Exception)
            {
                SetServer(false, "нет связи");
                SetStation("—", false, null);
            }
            finally
            {
                _statusBusy = false;
            }
        }

        private async Task RefreshEventsAsync()
        {
            if (_eventsBusy)
            {
                return;
            }
            _eventsBusy = true;
            try
            {
                var res = await _api.GetEventsAsync(_lastEventId);
                if (res?.events == null)
                {
                    return;
                }
                foreach (var ev in res.events)
                {
                    _lastEventId = Math.Max(_lastEventId, ev.id);
                    AddRow(ev);
                }
                if (res.last > _lastEventId)
                {
                    _lastEventId = res.last;
                }
                if (res.events.Count > 0 && IsScrolledToBottom())
                {
                    LogScroll.ScrollToEnd();
                }
            }
            catch
            {
                // сервер недоступен — статус покажет состояние
            }
            finally
            {
                _eventsBusy = false;
            }
        }

        private async Task SendAsync(string command)
        {
            if (string.IsNullOrWhiteSpace(command))
            {
                return;
            }
            try
            {
                var res = await _api.SendCommandAsync(command);
                if (res != null && !res.ok && !string.IsNullOrEmpty(res.error))
                {
                    AddLocalRow("WARNING", "app", "ошибка: " + res.error);
                }
            }
            catch (Exception ex)
            {
                AddLocalRow("ERROR", "app", "не смогла отправить: " + ex.Message);
            }
            await RefreshEventsAsync();
            await RefreshStatusAsync();
        }

        // ---------- диагностика ----------

        private async void OnDiagRefresh(object sender, RoutedEventArgs e) => await RefreshDiagnosticsAsync();

        private async Task RefreshDiagnosticsAsync()
        {
            TxtDiagSummary.Text = "проверяю…";
            DotDiag.Fill = (Brush)FindResource("AmberBrush");
            var rows = (ObservableCollection<DiagRowVm>)DiagItems.ItemsSource;
            rows.Clear();

            DiagnosticsResponse res;
            try
            {
                res = await _api.GetDiagnosticsAsync();
            }
            catch (Exception ex)
            {
                DotDiag.Fill = (Brush)FindResource("RedBrush");
                TxtDiagSummary.Text = "нет связи";
                rows.Add(new DiagRowVm
                {
                    Title = "Сервер навыка",
                    Detail = "не удалось получить диагностику: " + ex.Message,
                    DotBrush = (Brush)FindResource("RedBrush")
                });
                TxtDiagTime.Text = "проверка не удалась";
                return;
            }

            if (res?.checks == null)
            {
                DotDiag.Fill = (Brush)FindResource("RedBrush");
                TxtDiagSummary.Text = res?.error ?? "нет данных";
                return;
            }

            foreach (var chk in res.checks)
            {
                rows.Add(new DiagRowVm
                {
                    Title = chk.title,
                    Detail = chk.detail,
                    Hint = chk.hint,
                    DotBrush = (Brush)FindResource(chk.state switch
                    {
                        "ok" => "GreenBrush",
                        "warn" => "AmberBrush",
                        _ => "RedBrush"
                    })
                });
            }

            var failed = res.checks.Count(c => c.state == "fail");
            var warned = res.checks.Count(c => c.state == "warn");
            DotDiag.Fill = (Brush)FindResource(failed == 0 ? (warned == 0 ? "GreenBrush" : "AmberBrush") : "RedBrush");
            TxtDiagSummary.Text = failed == 0
                ? (warned == 0 ? $"всё в порядке · {res.ms} мс" : $"замечаний: {warned}")
                : $"сломано: {failed}";
            TxtDiagTime.Text = $"проверено в {DateTime.Now:HH:mm:ss} · вся цепочка за {res.ms} мс";
        }

        // ---------- очередь ----------

        private async void OnQueueRefresh(object sender, RoutedEventArgs e) => await RefreshQueueAsync(reset: true);

        private async void OnQueueMore(object sender, RoutedEventArgs e) => await RefreshQueueAsync(reset: false);

        private async void OnQueueItemClick(object sender, RoutedEventArgs e)
        {
            if (sender is Button b && b.Tag is int index)
            {
                try
                {
                    var res = await _api.PlayIndexAsync(index);
                    if (res == null || !res.ok)
                    {
                        AddLocalRow("WARNING", "app", "не смогла включить трек: " + (res?.error ?? "неизвестная ошибка"));
                    }
                }
                catch (Exception ex)
                {
                    AddLocalRow("ERROR", "app", ex.Message);
                }
                await Task.Delay(900);
                await RefreshQueueAsync(reset: true);
                await RefreshStatusAsync();
            }
        }

        private async Task RefreshQueueAsync(bool reset)
        {
            if (reset)
            {
                _queueOffset = 0;
                _queueRows.Clear();
                _coverCache.Clear();
                TxtQueueStatus.Text = "загружаю…";
            }
            try
            {
                // при первой загрузке просим окно вокруг играющего трека
                var res = await _api.GetQueueAsync(_queueOffset, 60, reset ? _lastQueueIndex : -1);
                if (res == null || !res.ok)
                {
                    TxtQueueStatus.Text = "очередь недоступна: " + (res?.error ?? "нет ответа");
                    TxtQueueInfo.Text = "—";
                    return;
                }

                _queueTotal = res.total;
                _queueCurrent = res.current;
                TxtQueueInfo.Text = $"треков: {res.total}";
                TxtQueueStatus.Text = res.total == 0
                    ? "очередь пуста — включи что-нибудь на главной"
                    : $"показано {_queueRows.Count + res.items.Count} из {res.total}" +
                      (res.current.HasValue ? $" · играет №{res.current + 1}" : "");

                foreach (var item in res.items)
                {
                    var isCurrent = res.current.HasValue && item.index == res.current.Value;
                    var row = new QueueRowVm
                    {
                        Index = item.index,
                        Title = string.IsNullOrWhiteSpace(item.title) ? "—" : item.title,
                        Sub = BuildQueueSub(item),
                        DurationText = FormatTime(item.duration),
                        CoverUrl = _api.CoverUrl(item.cover),
                        IsCurrent = isCurrent,
                        TitleBrush = isCurrent ? TextAccent : TextNormal
                    };
                    _queueRows.Add(row);
                    _ = LoadCoverAsync(row);
                }

                if (reset && res.items.Count > 0)
                {
                    _queueOffset = res.offset;
                }
                _queueOffset += res.items.Count;
                BtnQueueMore.IsEnabled = _queueOffset < res.total;
                BtnQueueMore.Content = _queueOffset < res.total
                    ? $"Показать ещё ({res.total - _queueOffset})"
                    : "Это всё";
            }
            catch (Exception ex)
            {
                TxtQueueStatus.Text = "ошибка: " + ex.Message;
            }
        }

        private static string BuildQueueSub(QueueItem item)
        {
            var parts = new List<string>();
            if (!string.IsNullOrWhiteSpace(item.artist))
            {
                parts.Add(item.artist);
            }
            if (!string.IsNullOrWhiteSpace(item.album))
            {
                parts.Add(item.album);
            }
            return parts.Count > 0 ? string.Join(" · ", parts) : "—";
        }

        private async Task LoadCoverAsync(QueueRowVm row)
        {
            if (string.IsNullOrWhiteSpace(row.CoverUrl))
            {
                return;
            }
            if (_coverCache.TryGetValue(row.CoverUrl, out var cached))
            {
                row.Cover = cached;
                return;
            }
            try
            {
                var bytes = await _img.GetByteArrayAsync(row.CoverUrl).ConfigureAwait(true);
                using var stream = new MemoryStream(bytes);
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.StreamSource = stream;
                bmp.DecodePixelWidth = 92;
                bmp.EndInit();
                bmp.Freeze();
                _coverCache[row.CoverUrl] = bmp;
                row.Cover = bmp;
            }
            catch
            {
                // нет обложки — останется заглушка с нотой
            }
        }

        // ---------- отображение ----------

        private void SetServer(bool ok, string text)
        {
            if (!_api.IsConfigured)
            {
                DotServer.Fill = (Brush)FindResource("AmberBrush");
                TxtServer.Text = "сервер: не настроен";
                return;
            }
            var brush = (Brush)FindResource(ok ? "GreenBrush" : "RedBrush");
            DotServer.Fill = brush;
            TxtServer.Text = "сервер: " + text;
        }

        private void SetStation(string state, bool available, string name)
        {
            // имя навыка — из настроек сервера (SKILL_NAME), по умолчанию остаётся своё
            if (!string.IsNullOrWhiteSpace(_skillName))
            {
                TxtSkillTitle.Text = _skillName;
            }

            var map = new Dictionary<string, string>
            {
                ["playing"] = "играет",
                ["paused"] = "пауза",
                ["idle"] = "молчит",
                ["off"] = "выключена"
            };
            var human = map.TryGetValue(state ?? "", out var h) ? h : state ?? "—";
            TxtStation.Text = "колонка: " + human + (available ? "" : " · нет связи");
            DotStation.Fill = (Brush)FindResource(available ? "GreenBrush" : "AmberBrush");

            var playing = state == "playing";
            // иконки — векторные фигуры: символы ▶/⏸ на Windows в Segoe UI
            // отсутствуют и рисовались квадратами
            var icon = (Geometry)FindResource(playing ? "IconPause" : "IconPlay");
            if (BtnPlayIcon != null)
            {
                BtnPlayIcon.Data = icon;
            }
        }

        /// <summary>
        /// Обложка текущего трека.
        ///
        /// Правила, чтобы картинка не «моргала» на каждом опросе статуса:
        ///  · один и тот же URL — ничего не делаем;
        ///  · пока играет тот же трек, а сервер на миг не отдал cover — держим текущую;
        ///  · уже скачанные обложки лежат в кэше (назад/перемешивание не качают заново).
        /// </summary>
        private async void ShowCover(StatusResponse st)
        {
            var key = (st.title ?? "") + "|" + (st.artist ?? "");
            var url = _api.CoverUrl(st.cover);

            if (string.IsNullOrWhiteSpace(url))
            {
                // у трека нет обложки: заглушку ставим только если трек действительно другой
                if (key != _coverTrack || ImgCover.Source == null)
                {
                    ResetCover();
                    _coverTrack = key;
                }
                return;
            }

            if (url == _coverUrl)
            {
                ShowCoverImage();
                return;
            }

            if (_nowPlayingCovers.TryGetValue(url, out var cached))
            {
                _coverUrl = url;
                _coverTrack = key;
                ImgCover.Source = cached;
                ShowCoverImage();
                return;
            }

            _coverUrl = url;
            _coverTrack = key;
            try
            {
                // качаем байты в фоне, BitmapImage собираем в UI-потоке
                var bytes = await Task.Run(() =>
                {
                    using (var client = new System.Net.WebClient())
                    {
                        client.Headers["X-Api-Token"] = _api.Token ?? "";
                        return client.DownloadData(url);
                    }
                });
                if (_coverUrl != url)
                {
                    return; // пока грузили, трек сменился
                }
                var bitmap = new BitmapImage();
                using (var stream = new MemoryStream(bytes))
                {
                    bitmap.BeginInit();
                    bitmap.CacheOption = BitmapCacheOption.OnLoad;
                    bitmap.StreamSource = stream;
                    bitmap.EndInit();
                }
                bitmap.Freeze();
                if (_nowPlayingCovers.Count > 40)
                {
                    _nowPlayingCovers.Clear(); // без фанатизма: при смене трека кэш не нужен
                }
                _nowPlayingCovers[url] = bitmap;
                ImgCover.Source = bitmap;
                ShowCoverImage();
            }
            catch (Exception ex)
            {
                // не гасим уже показанную обложку из-за сетевого сбоя
                if (ImgCover.Source == null)
                {
                    ResetCover();
                }
                AddLocalRow("WARNING", "app", "обложка не загрузилась: " + ex.Message);
            }
        }

        private void ShowCoverImage()
        {
            ImgCover.Visibility = Visibility.Visible;
            CoverPlaceholder.Visibility = Visibility.Collapsed;
        }

        private void ResetCover()
        {
            _coverUrl = null;
            ImgCover.Source = null;
            ImgCover.Visibility = Visibility.Collapsed;
            CoverPlaceholder.Visibility = Visibility.Visible;
        }

        private void ShowTrack(StatusResponse st)
        {
            if (!string.IsNullOrWhiteSpace(st.skill_name))
            {
                _skillName = st.skill_name;
                TxtSkillTitle.Text = st.skill_name;
            }

            var title = !string.IsNullOrWhiteSpace(st.title)
                ? st.title
                : (string.IsNullOrWhiteSpace(st.track) ? "ничего не играет" : st.track);

            TxtTrack.Text = title;
            TxtSub.Text = BuildSubtitle(st);
            ShowCover(st);

            var total = st.duration > 0 ? st.duration : 0;
            var done = Math.Max(0, st.elapsed);
            _duration = total;
            _lastElapsed = done;
            _statusAt = DateTime.UtcNow;

            PrgTrack.Value = total > 0 ? Math.Min(100, done / total * 100.0) : 0;
            TxtElapsed.Text = FormatTime(done);
            TxtDuration.Text = total > 0 ? FormatTime(total) : "—";

            var q = st.queue;
            if (q != null)
            {
                _lastQueueIndex = q.index;
                TxtQueue.Text = $"очередь {q.index + 1}/{q.items} · перемешивание {(q.shuffle ? "вкл" : "выкл")} · повтор {RepeatText(q.repeat)}";
            }
        }

        private static string RepeatText(string mode) => mode switch
        {
            "one" => "трек",
            "all" => "всё",
            _ => "выкл"
        };

        private static string BuildSubtitle(StatusResponse st)
        {
            var parts = new List<string>();
            if (!string.IsNullOrWhiteSpace(st.artist))
            {
                parts.Add(st.artist);
            }
            if (!string.IsNullOrWhiteSpace(st.album))
            {
                parts.Add(st.album);
            }
            if (parts.Count == 0 && !string.IsNullOrWhiteSpace(st.track))
            {
                parts.Add(st.track);
            }
            return parts.Count > 0 ? string.Join(" · ", parts) : "—";
        }

        private static string FormatTime(double seconds)
        {
            if (seconds <= 0)
            {
                return "0:00";
            }
            var span = TimeSpan.FromSeconds(seconds);
            return span.TotalHours >= 1
                ? $"{(int)span.TotalHours}:{span.Minutes:00}:{span.Seconds:00}"
                : $"{span.Minutes}:{span.Seconds:00}";
        }

        /// <summary>Плавный таймлайн: между опросами статуса сами двигаем позицию.</summary>
        private void TickProgress()
        {
            if (_duration <= 0)
            {
                return;
            }
            var elapsed = _lastElapsed;
            if (_state == "playing")
            {
                elapsed += (DateTime.UtcNow - _statusAt).TotalSeconds;
            }
            elapsed = Math.Min(Math.Max(elapsed, 0), _duration);
            PrgTrack.Value = Math.Min(100, elapsed / _duration * 100.0);
            TxtElapsed.Text = FormatTime(elapsed);
        }

        private void ShowVolume(int level)
        {
            _suppressVolume = true;
            SldVolume.Value = Math.Min(Math.Max(level, 0), 10);
            TxtVolume.Text = level + "/10";
            _suppressVolume = false;
        }

        // ---------- журнал ----------

        private void AddRow(EventRow ev)
        {
            var badge = (ev.source ?? "") switch
            {
                "колонка" => BadgeVoice,
                "app" => BadgeApp,
                _ => ev.level switch
                {
                    "ERROR" => BadgeErr,
                    "WARNING" => BadgeWarn,
                    _ => BadgeSkill
                }
            };

            var textBrush = ev.level switch
            {
                "ERROR" => (Brush)TextRed,
                "WARNING" => (Brush)TextAmber,
                _ => TextNormal
            };

            var time = DateTimeOffset.FromUnixTimeMilliseconds((long)(ev.ts * 1000)).ToLocalTime().ToString("HH:mm:ss");

            Insert(new LogRow
            {
                Time = time,
                Source = string.IsNullOrWhiteSpace(ev.source) ? "навык" : ev.source,
                Text = ev.text ?? "",
                Level = ev.level ?? "INFO",
                BadgeBrush = badge,
                TextBrush = textBrush
            });
        }

        private void AddLocalRow(string level, string source, string text)
        {
            Insert(new LogRow
            {
                Time = DateTime.Now.ToString("HH:mm:ss"),
                Source = source,
                Text = text,
                Level = level,
                BadgeBrush = level == "ERROR" ? BadgeErr : BadgeWarn,
                TextBrush = level == "ERROR" ? (Brush)TextRed : TextAmber
            });
        }

        private void Insert(LogRow row)
        {
            _all.Add(row);
            if (_all.Count > 2000)
            {
                _all.RemoveRange(0, _all.Count - 2000);
            }
            if (Matches(row))
            {
                _rows.Add(row);
                if (_rows.Count > 1200)
                {
                    _rows.RemoveAt(0);
                }
            }
        }

        private bool Matches(LogRow row)
        {
            if (string.IsNullOrWhiteSpace(_filter))
            {
                return true;
            }
            return (row.Text ?? "").IndexOf(_filter, StringComparison.OrdinalIgnoreCase) >= 0
                || (row.Source ?? "").IndexOf(_filter, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private bool IsScrolledToBottom()
        {
            return LogScroll.VerticalOffset >= LogScroll.ScrollableHeight - 40;
        }

        // ---------- обработчики ----------

        private async void OnPrev(object sender, RoutedEventArgs e) => await SendAsync("назад");

        private async void OnNext(object sender, RoutedEventArgs e) => await SendAsync("дальше");

        private async void OnPlayPause(object sender, RoutedEventArgs e)
        {
            await SendAsync(_state == "playing" ? "пауза" : "продолжи");
        }

        private async void OnStop(object sender, RoutedEventArgs e) => await SendAsync("стоп");

        private async void OnQuickAction(object sender, RoutedEventArgs e)
        {
            if (sender is Button b && b.Tag is string cmd)
            {
                await SendAsync(cmd);
            }
        }

        private async void OnPlayPhrase(object sender, RoutedEventArgs e) => await PlayPhraseAsync();

        private static readonly string[] CommandVerbs =
        {
            "включи", "включить", "поставь", "запусти", "сыграй", "вруби", "играй",
            "перемешай", "перемешать", "по порядку", "дальше", "далее", "назад",
            "пауза", "продолжи", "продолжай", "стоп", "что играет", "кто играет",
            "избранное", "похожее", "лайк", "тише", "громче", "громкость",
            "повтори", "перемотай"
        };

        private static bool LooksLikeCommand(string text)
        {
            var t = text.Trim().ToLowerInvariant();
            foreach (var verb in CommandVerbs)
            {
                if (t == verb || t.StartsWith(verb + " ") || t.StartsWith(verb + ","))
                {
                    return true;
                }
            }
            return false;
        }

        private async Task PlayPhraseAsync()
        {
            var q = TxtPhrase.Text?.Trim() ?? "";
            if (q.Length == 0)
            {
                TxtPhrase.Focus();
                return;
            }
            // «включи evanescence» в поле — отправляем как есть, иначе получится
            // «включи включи evanescence» и навык не находит исполнителя
            await SendAsync(LooksLikeCommand(q) ? q : "включи " + q);
            TxtPhrase.Clear();
        }

        private async void OnSendRaw(object sender, RoutedEventArgs e)
        {
            var q = TxtPhrase.Text?.Trim() ?? "";
            if (q.Length == 0)
            {
                TxtPhrase.Focus();
                return;
            }
            await SendAsync(q);
            TxtPhrase.Clear();
        }

        private async void OnPhraseKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                e.Handled = true;
                await PlayPhraseAsync();
            }
        }

        private void OnFilterChanged(object sender, TextChangedEventArgs e)
        {
            _filter = TxtFilter.Text?.Trim() ?? "";
            TxtFilterHint.Visibility = string.IsNullOrEmpty(_filter) ? Visibility.Visible : Visibility.Collapsed;

            _rows.Clear();
            foreach (var row in _all.Where(Matches))
            {
                _rows.Add(row);
            }
            LogScroll.ScrollToEnd();
        }

        private void OnClearLog(object sender, RoutedEventArgs e)
        {
            _rows.Clear();
            _all.Clear();
        }

        private void OnVolumeChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (_suppressVolume || TxtVolume == null)
            {
                return;
            }
            var level = (int)Math.Round(e.NewValue);
            TxtVolume.Text = level + "/10";
            _volumeTimer.Stop();
            _volumeTimer.Start();
        }
    }
}
