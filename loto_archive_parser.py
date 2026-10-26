"""Strict, dependency-free parsing and normalization of Loto 6/49 archives.

Source URL (when supplied), game heading and six-ball column header must agree.
Date cells also delimit source rows whose <tr> tags are missing. Exact duplicate
rows are removed; different supplementary draws on the same date are preserved.
"""
from datetime import date, datetime
from html import unescape
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

ARCHIVE_URLS_649 = (
    "https://www.loto49.ro/arhiva-loto49-1993-2000.php",
    "https://www.loto49.ro/arhiva-loto49-2001-2005.php",
    "https://www.loto49.ro/arhiva-loto49-2006-2010.php",
    "https://www.loto49.ro/arhiva-loto49-2011-2015.php",
    "https://www.loto49.ro/arhiva-loto-6-49-din-perioada-2016-2019.php",
    "https://www.loto49.ro/arhiva-loto-6-49-din-perioada-2020-2023.php",
    "https://www.loto49.ro/arhiva-loto49.php",
)


class LotoDataExtractionError(RuntimeError):
    pass


def valid_draw(numbers):
    return (isinstance(numbers, (list, tuple)) and len(numbers) == 6
            and all(type(number) is int and 1 <= number <= 49 for number in numbers)
            and len(set(numbers)) == 6)


def _normalize_date(value):
    """The source also writes dates such as 2020-03-1; return ISO or None."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})", value)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


def _has_other_game(draw):
    if any("joker" in str(key).lower() for key in draw):
        return True
    for key in ("game", "game_key", "gameKey", "lottery", "lottery_game"):
        if key in draw:
            marker = re.sub(r"[^a-z0-9]", "", str(draw[key]).lower())
            if marker not in {"649", "loto649", "6din49", "loto6din49"}:
                return True
    return False


def normalize_draws(draws):
    """Validate, normalize and deduplicate exact draws; retain special draws."""
    unique = {}
    today = datetime.now(ZoneInfo("Europe/Bucharest")).date().isoformat()
    for draw in draws:
        if not isinstance(draw, dict) or _has_other_game(draw):
            raise LotoDataExtractionError("Istoricul trebuie sa contina numai extrageri Loto 6/49, separate de Joker.")
        day = _normalize_date(draw.get("date"))
        if day is None:
            raise LotoDataExtractionError("Data invalida in istoric.")
        if not valid_draw(draw.get("numbers")):
            raise LotoDataExtractionError("Extragere invalida in istoric.")
        if day > today:
            raise LotoDataExtractionError("Arhiva contine date viitoare invalide.")
        numbers = sorted(draw["numbers"])
        # Match the Site's lexicographic number-string tie break on the same day.
        unique[(day, ",".join(map(str, numbers)))] = {"date": day, "numbers": numbers}
    return [unique[key] for key in sorted(unique)]


def _decode_cell(html):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _heading_game(html):
    text = _decode_cell(html).lower()
    games = [game for pattern, game in (
        (r"\b(?:loto\s*)?6\s*(?:/|din|-)\s*49\b", "649"),
        (r"\b(?:loto\s*)?5\s*(?:/|din|-)\s*40\b", "540"),
        (r"\bjoker\b", "joker"),
    ) if re.search(pattern, text)]
    return games[0] if len(games) == 1 else None


def _is_expected_source(source_url):
    try:
        def location(url):
            parsed = urlparse(url)
            default_port = 443 if parsed.scheme.lower() == "https" else 80
            return parsed.scheme.lower(), parsed.hostname, parsed.port or default_port, parsed.path
        return location(source_url) in {location(url) for url in ARCHIVE_URLS_649}
    except (TypeError, ValueError, AttributeError):
        return False


def _header_game(cells, game):
    if len(cells) != 7 or not re.match(r"data (?:tragerii|extragerii)", cells[0], re.IGNORECASE):
        return None
    labels = [re.sub(r"[^a-z0-9]", "", cell.lower()) for cell in cells[1:]]

    def numbered(count):
        return all(label in {f"nr{index}", f"n{index}"}
                   for index, label in enumerate(labels[:count], 1))

    if numbered(5) and labels[5] in {"j", "joker"}:
        return "joker"
    if numbered(6) and game in {"649", "540"}:
        return game
    return None


def _table_rows(html):
    """Respect existing rows; dates also delimit consecutive orphan cells."""
    rows, cells, markup = [], [], ""
    tokens = re.finditer(r"</?tr\b[^>]*>|<t([dh])\b[^>]*>([\s\S]*?)</t\1\s*>", html, re.IGNORECASE)
    for token in tokens:
        if re.match(r"</?tr\b", token[0], re.IGNORECASE):
            if cells:
                rows.append((cells, markup))
            cells, markup = [], token[0]
        else:
            text = _decode_cell(token[2])
            # A date cannot replace a missing ball from the previous draw.
            if _normalize_date(text) and cells:
                rows.append((cells, markup))
                cells, markup = [], ""
            cells.append(text)
            markup += token[0]
    if cells:
        rows.append((cells, markup))
    return rows


def extract_draws_from_html(html, source_url=None):
    """Read identified 6/49 tables; validate a final response URL if supplied.

    Without a URL, a 6/49 heading/caption and six-ball header are still required.
    Missing row tags and unpadded dates are supported without borrowing cells
    from a subsequent row or importing any Joker ball as a sixth main number.
    """
    if source_url is not None and not _is_expected_source(source_url):
        return []
    content = re.sub(r"<!--[\s\S]*?-->", "", html)
    content = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1\s*>", "", content, flags=re.IGNORECASE)
    sections = re.finditer(
        r"<h[1-6]\b[^>]*>[\s\S]*?</h[1-6]\s*>|<table\b[^>]*>[\s\S]*?</table\s*>",
        content, re.IGNORECASE)
    rows, seen, heading = [], set(), None
    for section in sections:
        text = section[0]
        if re.match(r"<h", text, re.IGNORECASE):
            heading = _heading_game(text)
            continue
        if len(re.findall(r"<table\b", text, re.IGNORECASE)) != 1:
            continue
        caption = re.search(r"<caption\b[^>]*>([\s\S]*?)</caption\s*>", text, re.IGNORECASE)
        game = _heading_game(caption[1]) if caption else heading
        if game != "649":
            continue
        columns_game = None
        for cells, markup in _table_rows(text):
            day = _normalize_date(cells[0])
            if day is None:
                if re.match(r"data (?:tragerii|extragerii)", cells[0], re.IGNORECASE):
                    columns_game = _header_game(cells, game)
                continue
            if columns_game != "649" or len(cells) != 7:
                continue
            if re.search(r"\bjoker\b|data-game\s*=\s*[\"']?joker", markup, re.IGNORECASE):
                continue
            if not all(re.fullmatch(r"[0-9]{1,2}", value) for value in cells[1:]):
                continue
            numbers = sorted(map(int, cells[1:]))
            key = day, tuple(numbers)
            if valid_draw(numbers) and key not in seen:
                seen.add(key)
                rows.append({"date": day, "numbers": numbers})
    return rows


def archive_request_url(url, cache_buster):
    """Refresh the current archive; closed historical pages remain cacheable."""
    if not _is_expected_source(url):
        raise LotoDataExtractionError("Sursa arhivei Loto 6/49 nu este permisa.")
    parsed = urlparse(url)
    if parsed.path == "/arhiva-loto49.php":
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if key != "_history_refresh"]
        query.append(("_history_refresh", str(cache_buster)))
        parsed = parsed._replace(query=urlencode(query))
    return urlunparse(parsed)
