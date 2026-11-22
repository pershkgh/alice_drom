using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace MusicControl
{
    /// <summary>
    /// Минимальный JSON-парсер: словари, списки, строки, числа, bool, null.
    ///
    /// Зачем свой: DataContractJsonSerializer из .NET Framework привередлив к составу
    /// ответа (лишнее поле, другой порядок), а проверить на Windows мы не можем —
    /// поэтому разбираем сами, предсказуемо. Вес — пара килобайт.
    /// </summary>
    internal static class Json
    {
        public static object Parse(string text)
        {
            if (text == null)
            {
                return null;
            }
            var i = 0;
            var value = ParseValue(text, ref i);
            SkipWs(text, ref i);
            return value;
        }

        // ── доступ к результатам ────────────────────────────────────────────
        public static Dictionary<string, object> Obj(object v) => v as Dictionary<string, object>;

        public static object Get(object node, string key)
        {
            var map = node as Dictionary<string, object>;
            return map != null && map.TryGetValue(key, out var value) ? value : null;
        }

        public static string Str(object node, string key)
        {
            var value = Get(node, key);
            if (value == null)
            {
                return null;
            }
            return value as string ?? Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        public static double Num(object node, string key)
        {
            var value = Get(node, key);
            switch (value)
            {
                case null: return 0;
                case double d: return d;
                case long l: return l;
                case string s: return double.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var r) ? r : 0;
                default: return 0;
            }
        }

        public static int Int(object node, string key) => (int)Math.Round(Num(node, key));

        public static int? IntOrNull(object node, string key)
        {
            var value = Get(node, key);
            return value == null ? (int?)null : Int(node, key);
        }

        public static bool Bool(object node, string key)
        {
            var value = Get(node, key);
            if (value is bool b)
            {
                return b;
            }
            if (value is string s)
            {
                return s.Equals("true", StringComparison.OrdinalIgnoreCase);
            }
            return false;
        }

        public static List<object> Arr(object node, string key) => Get(node, key) as List<object>;

        /// <summary>Экранирование строки для тела запроса.</summary>
        public static string Escape(string value)
        {
            if (value == null)
            {
                return "null";
            }
            var sb = new StringBuilder(value.Length + 8);
            sb.Append('"');
            foreach (var ch in value)
            {
                switch (ch)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (ch < 0x20)
                        {
                            sb.Append("\\u").Append(((int)ch).ToString("x4"));
                        }
                        else
                        {
                            sb.Append(ch);
                        }
                        break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }

        // ── разбор ──────────────────────────────────────────────────────────
        private static void SkipWs(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i]))
            {
                i++;
            }
        }

        private static object ParseValue(string s, ref int i)
        {
            SkipWs(s, ref i);
            if (i >= s.Length)
            {
                return null;
            }
            switch (s[i])
            {
                case '{': return ParseObject(s, ref i);
                case '[': return ParseArray(s, ref i);
                case '"': return ParseString(s, ref i);
                case 't': Expect(s, ref i, "true"); return true;
                case 'f': Expect(s, ref i, "false"); return false;
                case 'n': Expect(s, ref i, "null"); return null;
                default: return ParseNumber(s, ref i);
            }
        }

        private static void Expect(string s, ref int i, string token)
        {
            if (string.CompareOrdinal(s, i, token, 0, token.Length) != 0)
            {
                throw new FormatException("ожидалось " + token + " на позиции " + i);
            }
            i += token.Length;
        }

        private static Dictionary<string, object> ParseObject(string s, ref int i)
        {
            var map = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            i++; // {
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == '}')
            {
                i++;
                return map;
            }
            while (i < s.Length)
            {
                SkipWs(s, ref i);
                var key = ParseString(s, ref i);
                SkipWs(s, ref i);
                if (i < s.Length && s[i] == ':')
                {
                    i++;
                }
                var value = ParseValue(s, ref i);
                map[key] = value;
                SkipWs(s, ref i);
                if (i < s.Length && s[i] == ',')
                {
                    i++;
                    continue;
                }
                if (i < s.Length && s[i] == '}')
                {
                    i++;
                }
                break;
            }
            return map;
        }

        private static List<object> ParseArray(string s, ref int i)
        {
            var list = new List<object>();
            i++; // [
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == ']')
            {
                i++;
                return list;
            }
            while (i < s.Length)
            {
                list.Add(ParseValue(s, ref i));
                SkipWs(s, ref i);
                if (i < s.Length && s[i] == ',')
                {
                    i++;
                    continue;
                }
                if (i < s.Length && s[i] == ']')
                {
                    i++;
                }
                break;
            }
            return list;
        }

        private static string ParseString(string s, ref int i)
        {
            var sb = new StringBuilder();
            i++; // "
            while (i < s.Length)
            {
                var ch = s[i++];
                if (ch == '"')
                {
                    break;
                }
                if (ch != '\\')
                {
                    sb.Append(ch);
                    continue;
                }
                if (i >= s.Length)
                {
                    break;
                }
                var esc = s[i++];
                switch (esc)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (i + 4 <= s.Length)
                        {
                            var code = int.Parse(s.Substring(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                            sb.Append((char)code);
                            i += 4;
                        }
                        break;
                    default: sb.Append(esc); break;
                }
            }
            return sb.ToString();
        }

        private static object ParseNumber(string s, ref int i)
        {
            var start = i;
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0)
            {
                i++;
            }
            var raw = s.Substring(start, i - start);
            if (raw.IndexOf('.') < 0 && raw.IndexOf('e') < 0 && raw.IndexOf('E') < 0
                && long.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l))
            {
                return l;
            }
            return double.TryParse(raw, NumberStyles.Any, CultureInfo.InvariantCulture, out var d) ? d : 0;
        }
    }
}
