"""プロ野球順位予想の採点と、公開順位表の取得。

採点はチームごとの |予想順位 - 実際の順位| を合計する。
0 に近いほどドンピシャ。6チームを完全に逆順にすると 18 ズレになる。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))

PRIMARY_URL = "https://npb.jp/standings/"

CENTRAL_IDS = ("T", "G", "DB", "C", "S", "D")
PACIFIC_IDS = ("H", "L", "F", "Bs", "M", "E")

# colors は旗の2色。stroke は矢印とグラフの線。指定の色をそのまま使う。
TEAMS: dict[str, dict] = {
    "T": {"name": "阪神", "full_name": "阪神タイガース", "league": "central", "colors": ["#FEE200", "#000000"], "stroke": "#FEE200"},
    "G": {"name": "巨人", "full_name": "読売ジャイアンツ", "league": "central", "colors": ["#F97709", "#000000"], "stroke": "#F97709"},
    "DB": {"name": "DeNA", "full_name": "横浜DeNAベイスターズ", "league": "central", "colors": ["#0091E1", "#FFFFFF"], "stroke": "#0091E1"},
    "C": {"name": "広島", "full_name": "広島東洋カープ", "league": "central", "colors": ["#FF0000", "#FFFFFF"], "stroke": "#FF0000"},
    "S": {"name": "ヤクルト", "full_name": "東京ヤクルトスワローズ", "league": "central", "colors": ["#0F1350", "#98C145"], "stroke": "#98C145"},
    "D": {"name": "中日", "full_name": "中日ドラゴンズ", "league": "central", "colors": ["#002569", "#FFFFFF"], "stroke": "#002569"},
    "H": {"name": "ソフトバンク", "full_name": "福岡ソフトバンクホークス", "league": "pacific", "colors": ["#FCC700", "#000000"], "stroke": "#FCC700"},
    "L": {"name": "西武", "full_name": "埼玉西武ライオンズ", "league": "pacific", "colors": ["#051E46", "#FEFEFE"], "stroke": "#051E46"},
    "F": {"name": "日本ハム", "full_name": "北海道日本ハムファイターズ", "league": "pacific", "colors": ["#01609A", "#FFFFFF"], "stroke": "#01609A"},
    "Bs": {"name": "オリックス", "full_name": "オリックス・バファローズ", "league": "pacific", "colors": ["#A47B01", "#000000"], "stroke": "#A47B01"},
    "M": {"name": "ロッテ", "full_name": "千葉ロッテマリーンズ", "league": "pacific", "colors": ["#A12760", "#C0C0C0"], "stroke": "#A12760"},
    "E": {"name": "楽天", "full_name": "東北楽天ゴールデンイーグルス", "league": "pacific", "colors": ["#870010", "#FFFFFF"], "stroke": "#870010"},
}

LEAGUE_LABEL = {"central": "セ・リーグ", "pacific": "パ・リーグ"}

DEFAULT_DRAFT = {
    "central": ["T", "G", "C", "DB", "D", "S"],
    "pacific": ["H", "F", "M", "Bs", "L", "E"],
}

_RAW_ALIASES = [
    ("横浜DeNAベイスターズ", "DB"),
    ("横浜DeNA", "DB"),
    ("ベイスターズ", "DB"),
    ("DeNA", "DB"),
    ("横浜", "DB"),
    ("読売ジャイアンツ", "G"),
    ("ジャイアンツ", "G"),
    ("読売", "G"),
    ("巨人", "G"),
    ("阪神タイガース", "T"),
    ("タイガース", "T"),
    ("阪神", "T"),
    ("中日ドラゴンズ", "D"),
    ("ドラゴンズ", "D"),
    ("中日", "D"),
    ("広島東洋カープ", "C"),
    ("カープ", "C"),
    ("広島", "C"),
    ("東京ヤクルトスワローズ", "S"),
    ("ヤクルトスワローズ", "S"),
    ("ヤクルト", "S"),
    ("スワローズ", "S"),
    ("福岡ソフトバンクホークス", "H"),
    ("ソフトバンクホークス", "H"),
    ("ソフトバンク", "H"),
    ("ホークス", "H"),
    ("北海道日本ハムファイターズ", "F"),
    ("日本ハムファイターズ", "F"),
    ("日本ハム", "F"),
    ("ファイターズ", "F"),
    ("千葉ロッテマリーンズ", "M"),
    ("ロッテマリーンズ", "M"),
    ("マリーンズ", "M"),
    ("ロッテ", "M"),
    ("埼玉西武ライオンズ", "L"),
    ("西武ライオンズ", "L"),
    ("ライオンズ", "L"),
    ("西武", "L"),
    ("東北楽天ゴールデンイーグルス", "E"),
    ("楽天ゴールデンイーグルス", "E"),
    ("ゴールデンイーグルス", "E"),
    ("イーグルス", "E"),
    ("楽天", "E"),
    ("オリックス・バファローズ", "Bs"),
    ("オリックスバファローズ", "Bs"),
    ("バファローズ", "Bs"),
    ("オリックス", "Bs"),
    ("Hanshin Tigers", "T"),
    ("Yomiuri Giants", "G"),
    ("YOKOHAMA DeNA BAYSTARS", "DB"),
    ("Hiroshima Toyo Carp", "C"),
    ("Tokyo Yakult Swallows", "S"),
    ("Chunichi Dragons", "D"),
    ("Fukuoka SoftBank Hawks", "H"),
    ("Saitama Seibu Lions", "L"),
    ("Hokkaido Nippon-Ham Fighters", "F"),
    ("ORIX Buffaloes", "Bs"),
    ("Chiba Lotte Marines", "M"),
    ("Tohoku Rakuten Golden Eagles", "E"),
]

_SHORT = {
    "神": "T",
    "巨": "G",
    "デ": "DB",
    "横": "DB",
    "広": "C",
    "ヤ": "S",
    "中": "D",
    "ソ": "H",
    "西": "L",
    "日": "F",
    "オ": "Bs",
    "ロ": "M",
    "楽": "E",
    "bs": "Bs",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
}

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "caption", "p", "div", "span", "strong", "b", "font")
_STANDING_KEYS = ("勝率", "勝利", "敗北", "引分", "pct", "gb", "差", "順位", "勝敗", "standings", "試合")
_INTERLEAGUE_KEYS = ("交流戦", "インターリーグ", "interleague")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("・", "").replace("･", "").replace(" ", "").replace("\u3000", "")
    return text.casefold()


ALIASES = tuple(sorted(((normalize(alias), team_id) for alias, team_id in _RAW_ALIASES), key=lambda item: len(item[0]), reverse=True))
SHORT = {normalize(key): value for key, value in _SHORT.items()}


class StandingsError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


def calculate_penalty(prediction_list: list[str], actual_list: list[str]) -> int:
    """予想順と実際の順位の絶対差を合計して返す。

    prediction_list も actual_list も、先頭が1位のチームID並び。
    例: 1位予想が実際6位なら、そのチームは 5 点。
    """
    return sum(row["penalty"] for row in rank_breakdown(prediction_list, actual_list))


def rank_breakdown(prediction_list: list[str], actual_list: list[str]) -> list[dict]:
    _validate_lists(prediction_list, actual_list)
    actual_rank = {team: index for index, team in enumerate(actual_list, start=1)}
    rows = []
    for predicted_rank, team in enumerate(prediction_list, start=1):
        actual = actual_rank[team]
        delta = actual - predicted_rank
        rows.append(
            {
                "team": team,
                "predicted_rank": predicted_rank,
                "actual_rank": actual,
                "delta": delta,
                "penalty": abs(delta),
            }
        )
    return rows


def _validate_lists(prediction_list: list[str], actual_list: list[str]) -> None:
    if not isinstance(prediction_list, list) or not isinstance(actual_list, list):
        raise ValueError("順位リストの形式が正しくありません")
    if len(prediction_list) != len(actual_list):
        raise ValueError("予想と実際のチーム数が一致しません")
    if not prediction_list:
        raise ValueError("順位リストが空です")
    if len(set(prediction_list)) != len(prediction_list) or len(set(actual_list)) != len(actual_list):
        raise ValueError("順位リストに重複したチームがあります")
    if set(prediction_list) != set(actual_list):
        raise ValueError("予想と実際でチームの構成が一致しません")


def valid_lineup(ids: object, league: str) -> bool:
    expected = set(CENTRAL_IDS if league == "central" else PACIFIC_IDS)
    return isinstance(ids, list) and len(ids) == 6 and set(ids) == expected


def closeness_label(total: int) -> str:
    if total == 0:
        return "ドンピシャ"
    if total <= 4:
        return "かなり近い"
    if total <= 10:
        return "まずまず"
    if total <= 18:
        return "まだズレている"
    return "大きくズレている"


def identify_team(text: str) -> str | None:
    norm = normalize(text)
    if not norm:
        return None
    for alias, team_id in ALIASES:
        if alias and alias in norm:
            return team_id
    return SHORT.get(norm)


def parse_standings_html(content: bytes | str) -> dict:
    soup = BeautifulSoup(content, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    page_text = soup.get_text(" ", strip=True)
    year = _detect_year(title) or _detect_year(page_text)
    finished = "全日程終了" in page_text or "regular season final" in page_text.casefold()

    strict: dict[str, list[str]] = {}
    loose: dict[str, list[str]] = {}
    strict_heading: dict[str, str] = {}
    loose_heading: dict[str, str] = {}

    for table in soup.find_all("table"):
        if table.find_parent("table"):
            continue
        labels = _nearby_labels(table)
        if _is_interleague(labels):
            continue
        orders = _extract_orders(table)
        if not orders:
            continue
        heading = _pick_heading(labels)
        bucket = strict if _looks_like_standings(table, labels) else loose
        headings = strict_heading if bucket is strict else loose_heading
        for league, order in orders.items():
            if league not in bucket:
                bucket[league] = order
                headings[league] = heading

    return {
        "central": strict.get("central") or loose.get("central"),
        "pacific": strict.get("pacific") or loose.get("pacific"),
        "headings": {
            "central": strict_heading.get("central") or loose_heading.get("central") or "",
            "pacific": strict_heading.get("pacific") or loose_heading.get("pacific") or "",
        },
        "season": year,
        "season_finished": finished,
    }


def _detect_year(text: str) -> int | None:
    match = re.search(r"(20\d{2})\s*年", text)
    if not match:
        match = re.search(r"(20\d{2})", text)
    return int(match.group(1)) if match else None


def _nearby_labels(table) -> list[str]:
    labels: list[str] = []
    caption = table.find("caption")
    if caption:
        text = " ".join(caption.get_text(" ", strip=True).split())
        if text:
            labels.append(text[:80])
    for element in table.find_all_previous(True):
        if element.name == "table":
            break
        if element.find_parent("table") or element.find("table"):
            continue
        if element.name not in _HEADING_TAGS:
            continue
        text = " ".join(element.get_text(" ", strip=True).split())
        if not text or len(text) > 48:
            continue
        labels.append(text)
        if len(labels) >= 5:
            break
    return labels


def _section_title(labels: list[str]) -> str:
    """直前の見出しだけを見る。ページ上部のナビにある「交流戦」では判定しない。"""
    for text in labels:
        folded = text.casefold()
        if any(key in folded for key in ("勝敗", "順位", "成績", "交流戦", "インター", "standings", "interleague")):
            return text
    return labels[0] if labels else ""


def _is_interleague(labels: Iterable[str]) -> bool:
    title = _section_title(list(labels)).casefold()
    return any(key in title for key in _INTERLEAGUE_KEYS)


def _looks_like_standings(table, labels: list[str]) -> bool:
    header = table.find("tr")
    header_text = header.get_text(" ", strip=True) if header else ""
    blob = normalize(" ".join(labels) + " " + header_text)
    return any(key in blob for key in _STANDING_KEYS)


def _pick_heading(labels: list[str]) -> str:
    return _section_title(labels)


def _extract_orders(table) -> dict[str, list[str]]:
    found: list[str] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        team_id = None
        for cell in cells[:3]:
            team_id = identify_team(cell.get_text(" ", strip=True))
            if team_id:
                break
        if team_id and team_id not in found:
            found.append(team_id)
    orders = {}
    central = [team_id for team_id in found if team_id in CENTRAL_IDS]
    pacific = [team_id for team_id in found if team_id in PACIFIC_IDS]
    if valid_lineup(central, "central"):
        orders["central"] = central
    if valid_lineup(pacific, "pacific"):
        orders["pacific"] = pacific
    return orders


def http_get(url: str) -> bytes:
    try:
        response = requests.get(url, headers=HEADERS, timeout=(5, 12), allow_redirects=True)
    except requests.RequestException as exc:
        raise StandingsError(f"通信エラー: {exc.__class__.__name__}", []) from exc
    if response.status_code != 200:
        raise StandingsError(f"HTTP {response.status_code}", [])
    if not response.content:
        raise StandingsError("空の応答", [])
    return response.content


def _season_years() -> list[int]:
    year = datetime.now(JST).year
    return [year, year - 1]


def _source_plan(year: int) -> list[tuple[str, str]]:
    return [
        (PRIMARY_URL, "NPB公式 順位表"),
        (f"https://npb.jp/bis/{year}/stats/", "NPB公式 成績"),
        (f"https://npb.jp/bis/{year}/stats/std_c.html", "NPB公式 セ勝敗表"),
        (f"https://npb.jp/bis/{year}/stats/std_p.html", "NPB公式 パ勝敗表"),
        ("https://npb.jp/cl/", "NPB公式 セ・リーグ"),
        ("https://npb.jp/pl/", "NPB公式 パ・リーグ"),
    ]


def _fallback_plan() -> list[tuple[str, str]]:
    return [
        ("https://baseball.yahoo.co.jp/npb/standings/", "スポーツナビ（Yahoo!野球）"),
        ("https://baseball.yahoo.co.jp/npb/standings/central", "スポーツナビ セ"),
        ("https://baseball.yahoo.co.jp/npb/standings/pacific", "スポーツナビ パ"),
        ("https://sports.yahoo.co.jp/npb/standings/", "スポーツナビ"),
    ]


def fetch_standings() -> dict:
    """NPB公式を優先し、失敗時は公式内の別ページ、その次にスポーツナビへ切り替える。

    両方取れたときは順位を突き合わせ、不一致ならNPBを採用して警告を残す。
    """
    attempts: list[dict] = []
    collected = _empty_collected()
    primary_error = None

    for year in _season_years():
        for url, label in _source_plan(year):
            if _complete(collected) and url != PRIMARY_URL:
                continue
            parsed = _try_source(attempts, url, label)
            if url == PRIMARY_URL and parsed is None:
                primary_error = next((item["error"] for item in attempts if item["url"] == url), "取得できませんでした")
            if parsed:
                _absorb(collected, parsed, url, label)
            if _complete(collected) and url != PRIMARY_URL:
                break
        if _complete(collected):
            break

    fallback = _empty_collected()
    for url, label in _fallback_plan():
        parsed = _try_source(attempts, url, label)
        if parsed:
            _absorb(fallback, parsed, url, label)
        if _complete(fallback):
            break

    if _complete(collected):
        return _package(collected, attempts, primary_error, fallback if _complete(fallback) else None)
    if _complete(fallback):
        return _package(fallback, attempts, primary_error, None, external=True)
    detail = " / ".join(f"{item['label']}: {item['error'] or '順位を解釈できませんでした'}" for item in attempts[:6])
    raise StandingsError(f"順位表を取得できませんでした。{detail}", attempts)


def _empty_collected() -> dict:
    return {
        "central": None,
        "pacific": None,
        "headings": {"central": "", "pacific": ""},
        "urls": {"central": "", "pacific": ""},
        "labels": {"central": "", "pacific": ""},
        "season": None,
        "season_finished": False,
    }


def _complete(collected: dict) -> bool:
    return bool(collected["central"] and collected["pacific"])


def _try_source(attempts: list[dict], url: str, label: str) -> dict | None:
    try:
        parsed = parse_standings_html(http_get(url))
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        attempts.append({"url": url, "label": label, "ok": False, "error": message[:180], "detail": ""})
        return None
    got = []
    if parsed.get("central"):
        got.append("セ")
    if parsed.get("pacific"):
        got.append("パ")
    ok = bool(got)
    attempts.append(
        {
            "url": url,
            "label": label,
            "ok": ok,
            "error": None if ok else "順位表を解釈できませんでした",
            "detail": "・".join(got),
        }
    )
    return parsed if ok else None


def _absorb(into: dict, parsed: dict, url: str, label: str) -> None:
    for league in ("central", "pacific"):
        if into[league] is None and parsed.get(league):
            into[league] = parsed[league]
            into["headings"][league] = parsed.get("headings", {}).get(league, "")
            into["urls"][league] = url
            into["labels"][league] = label
    if parsed.get("season") and not into["season"]:
        into["season"] = parsed["season"]
    if parsed.get("season_finished"):
        into["season_finished"] = True


def _package(collected: dict, attempts: list[dict], primary_error: str | None, other: dict | None, external: bool = False) -> dict:
    urls = [collected["urls"]["central"], collected["urls"]["pacific"]]
    same_page = urls[0] == urls[1]
    source_url = urls[0] if same_page else urls[0]
    if external:
        source_label = "スポーツナビ（Yahoo!野球）"
    elif collected["labels"]["central"] == collected["labels"]["pacific"]:
        source_label = collected["labels"]["central"]
    else:
        source_label = "NPB公式 チーム勝敗表"
    fallback_used = external or source_url.rstrip("/") != PRIMARY_URL.rstrip("/")
    cross = _cross_check(collected, other)
    warning = _warning(fallback_used, external, primary_error, cross)
    return {
        "central": collected["central"],
        "pacific": collected["pacific"],
        "season": collected["season"],
        "season_finished": collected["season_finished"],
        "source_label": source_label,
        "source_url": source_url,
        "source_urls": urls,
        "headings": collected["headings"],
        "fallback_used": fallback_used,
        "primary_error": primary_error,
        "cross_check": cross,
        "attempts": attempts,
        "warning": warning,
    }


def _cross_check(primary: dict, other: dict | None) -> dict:
    if not other or not _complete(other):
        return {"status": "unavailable", "with": "スポーツナビ（Yahoo!野球）", "detail": "予備ソースは取得できませんでした"}
    same_central = primary["central"] == other["central"]
    same_pacific = primary["pacific"] == other["pacific"]
    if same_central and same_pacific:
        return {"status": "match", "with": "スポーツナビ（Yahoo!野球）", "detail": "セ・パともに一致"}
    parts = []
    if not same_central:
        parts.append("セ・リーグ")
    if not same_pacific:
        parts.append("パ・リーグ")
    return {"status": "mismatch", "with": "スポーツナビ（Yahoo!野球）", "detail": f"{'と'.join(parts)}の順位が一致しません"}


def _warning(fallback_used: bool, external: bool, primary_error: str | None, cross: dict) -> str | None:
    notes = []
    if external:
        notes.append("NPB公式から順位を取得できなかったため、スポーツナビの順位を表示しています。")
    elif fallback_used:
        notes.append("npb.jp/standings/ からは取得できなかったため、NPB公式の成績ページを表示しています。")
    if cross["status"] == "match":
        notes.append("スポーツナビの順位と一致しています。")
    elif cross["status"] == "mismatch":
        notes.append("ソース間で順位が異なるため、優先ソースの順位を表示しています。")
    if not notes:
        return None
    return " ".join(notes)


def live_check() -> int:
    print("順位表を取得しています…")
    try:
        result = fetch_standings()
    except StandingsError as exc:
        print(exc)
        for attempt in exc.attempts:
            status = "OK" if attempt["ok"] else "NG"
            print(f"  [{status}] {attempt['label']} {attempt['url']} {attempt['error'] or ''}")
        return 1
    for league in ("central", "pacific"):
        names = [f"{index}.{TEAMS[team_id]['name']}" for index, team_id in enumerate(result[league], start=1)]
        print(f"{LEAGUE_LABEL[league]}: {' '.join(names)}")
        print(f"  見出し: {result['headings'].get(league) or '（なし）'}")
    print(f"出典: {result['source_label']} {result['source_url']}")
    print(f"突合: {result['cross_check']['status']} / {result['cross_check']['detail']}")
    if result.get("warning"):
        print(f"注意: {result['warning']}")
    central_penalty = calculate_penalty(DEFAULT_DRAFT["central"], result["central"])
    pacific_penalty = calculate_penalty(DEFAULT_DRAFT["pacific"], result["pacific"])
    print(f"初期下書きとのズレ: 合計 {central_penalty + pacific_penalty}（セ {central_penalty} / パ {pacific_penalty}）")
    return 0


# 2026年の公式戦は143試合。同じページの続きに出るクライマックスシリーズは数に入れない。
REGULAR_SEASON_GAMES = 143


def regular_season_game_total(game_cap: int = REGULAR_SEASON_GAMES) -> int:
    """12球団の規定試合を、1試合につき1回だけ数えた総数。143試合なら858。"""
    return len(TEAMS) * game_cap // 2


def season_game_summary(games: list[dict], game_cap: int = REGULAR_SEASON_GAMES) -> dict:
    """規定試合までの消化数と、最後に数えた日の試合を返す。"""
    played_by = {team_id: 0 for team_id in TEAMS}
    counted: list[dict] = []
    for game in sorted(games or [], key=lambda item: item.get("date") or ""):
        away, home = game.get("a"), game.get("b")
        if away not in played_by or home not in played_by or away == home:
            continue
        if played_by[away] >= game_cap or played_by[home] >= game_cap:
            continue
        played_by[away] += 1
        played_by[home] += 1
        counted.append(game)
    total = regular_season_game_total(game_cap)
    last_date = counted[-1]["date"] if counted else None
    last_games = [game for game in counted if game.get("date") == last_date] if last_date else []
    played = len(counted)
    return {
        "played": played,
        "total": total,
        "remaining": max(0, total - played),
        "as_of": last_date,
        "last_games": last_games,
    }


def parse_schedule_games(content: bytes | str, year: int) -> list[dict]:
    """月間の試合日程から、スコアの付いた試合だけを取り出す。"""
    soup = BeautifulSoup(content, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    games: list[dict] = []
    current: date | None = None
    for row in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if not cells or cells[0] == "月日":
            continue
        found_date = re.match(r"(\d{1,2})/(\d{1,2})", cells[0])
        if found_date:
            current = date(year, int(found_date.group(1)), int(found_date.group(2)))
            matchup = cells[1] if len(cells) > 1 else ""
        elif not cells[0] and len(cells) > 1:
            matchup = cells[1]
        else:
            matchup = cells[0]
        if current is None:
            continue
        parsed = _parse_matchup(matchup)
        if parsed is None:
            continue
        away, away_score, home, home_score = parsed
        games.append(
            {
                "date": current.isoformat(),
                "a": away,
                "sa": away_score,
                "b": home,
                "sb": home_score,
            }
        )
    return games


def _parse_matchup(matchup: str) -> tuple[str, int, str, int] | None:
    text = normalize(matchup)
    if any(word in text for word in ("中止", "延期", "ノーゲーム")):
        return None
    found = re.search(r"(\d+)\s*[-－−]\s*(\d+)", text)
    if not found:
        return None
    away = identify_team(text[: found.start()])
    home = identify_team(text[found.end() :])
    if not away or not home or away == home:
        return None
    return away, int(found.group(1)), home, int(found.group(2))


def fetch_schedule_games(year: int, start_month: int = 3) -> list[dict]:
    """公式の月間日程を読む。無い月（404）で打ち切る。"""
    games: list[dict] = []
    for month in range(max(1, start_month), 12):
        url = f"https://npb.jp/games/{year}/schedule_{month:02d}_detail.html"
        try:
            content = http_get(url)
        except StandingsError as exc:
            if "404" in str(exc):
                break
            raise
        games.extend(parse_schedule_games(content, year))
    return games


def build_daily_standings(games: list[dict], game_cap: int = REGULAR_SEASON_GAMES) -> list[dict]:
    """試合結果から、開幕日から公式戦最終日までの毎日の順位を作る。

    各チームの公式戦は game_cap 試合まで。それを超える試合はポストシーズンとして除外する。
    試合のない日は、直前の順位をそのまま置く。
    """
    records = {team_id: _blank_record() for team_id in TEAMS}
    by_date: dict[str, list[dict]] = {}
    for game in sorted(games, key=lambda item: item.get("date") or ""):
        away, home = game.get("a"), game.get("b")
        if away not in records or home not in records or away == home:
            continue
        by_date.setdefault(str(game["date"]), []).append(game)
    if not by_date:
        return []

    orders: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str] | None] = {"central": None, "pacific": None}
    for day in sorted(by_date):
        counted = False
        for game in by_date[day]:
            away, home = game["a"], game["b"]
            if records[away]["g"] >= game_cap or records[home]["g"] >= game_cap:
                continue
            _apply_game(records, away, int(game["sa"]), home, int(game["sb"]))
            counted = True
        if not counted:
            continue
        if _league_started(records, CENTRAL_IDS):
            current["central"] = _rank_teams(CENTRAL_IDS, records)
        if _league_started(records, PACIFIC_IDS):
            current["pacific"] = _rank_teams(PACIFIC_IDS, records)
        if current["central"] and current["pacific"]:
            orders[day] = {"central": list(current["central"]), "pacific": list(current["pacific"])}
    if not orders:
        return []

    start = date.fromisoformat(min(orders))
    end = date.fromisoformat(max(orders))
    points: list[dict] = []
    cursor = None
    day = start
    while day <= end:
        key = day.isoformat()
        if key in orders:
            cursor = orders[key]
        if cursor:
            points.append({"date": key, "central": list(cursor["central"]), "pacific": list(cursor["pacific"]), "source": "日程"})
        day += timedelta(days=1)
    return points


def _blank_record() -> dict:
    return {"w": 0, "l": 0, "t": 0, "rf": 0, "ra": 0, "g": 0, "vs": {}}


def _apply_game(records: dict, away: str, away_score: int, home: str, home_score: int) -> None:
    for team_id, scored, allowed, opponent in (
        (away, away_score, home_score, home),
        (home, home_score, away_score, away),
    ):
        record = records[team_id]
        record["g"] += 1
        record["rf"] += scored
        record["ra"] += allowed
        versus = record["vs"].setdefault(opponent, {"w": 0, "l": 0, "rf": 0, "ra": 0})
        versus["rf"] += scored
        versus["ra"] += allowed
        if away_score == home_score:
            record["t"] += 1
        elif scored > allowed:
            record["w"] += 1
            versus["w"] += 1
        else:
            record["l"] += 1
            versus["l"] += 1


def _league_started(records: dict, team_ids: tuple[str, ...]) -> bool:
    return all(records[team_id]["g"] > 0 for team_id in team_ids)


def _win_pct(record: dict) -> float:
    decided = record["w"] + record["l"]
    if not decided:
        return 0.0
    return record["w"] / decided


def _rank_teams(team_ids: tuple[str, ...], records: dict) -> list[str]:
    ordered = sorted(team_ids, key=lambda team_id: (-_win_pct(records[team_id]), team_id))
    groups: list[list[str]] = []
    for team_id in ordered:
        if groups and abs(_win_pct(records[team_id]) - _win_pct(records[groups[-1][0]])) < 1e-12:
            groups[-1].append(team_id)
        else:
            groups.append([team_id])
    result: list[str] = []
    for group in groups:
        result.extend(_break_ties(group, records))
    return result


def _break_ties(group: list[str], records: dict) -> list[str]:
    if len(group) == 1:
        return group

    def key(team_id: str) -> tuple:
        wins = losses = scored = allowed = 0
        for other in group:
            if other == team_id:
                continue
            versus = records[team_id]["vs"].get(other)
            if not versus:
                continue
            wins += versus["w"]
            losses += versus["l"]
            scored += versus["rf"]
            allowed += versus["ra"]
        decided = wins + losses
        head_pct = wins / decided if decided else 0.0
        record = records[team_id]
        return (-head_pct, -(scored - allowed), -(record["rf"] - record["ra"]), -record["w"], team_id)

    return sorted(group, key=key)


def run_self_tests() -> None:
    _test_penalty_examples()
    _test_parser_ignores_interleague()
    _test_short_names()
    _test_daily_standings()
    print("計算テスト: OK")
    print("パーサーテスト: OK")


def _test_penalty_examples() -> None:
    base = ["T", "G", "DB", "C", "S", "D"]
    assert calculate_penalty(base, base) == 0

    first_is_last = ["G", "DB", "C", "S", "D", "T"]
    rows = {row["team"]: row for row in rank_breakdown(base, first_is_last)}
    assert rows["T"]["predicted_rank"] == 1
    assert rows["T"]["actual_rank"] == 6
    assert rows["T"]["penalty"] == 5
    assert rows["T"]["delta"] == 5

    third_is_second = ["T", "DB", "G", "C", "S", "D"]
    rows = {row["team"]: row for row in rank_breakdown(base, third_is_second)}
    assert rows["DB"]["predicted_rank"] == 3
    assert rows["DB"]["actual_rank"] == 2
    assert rows["DB"]["penalty"] == 1
    assert rows["DB"]["delta"] == -1
    assert calculate_penalty(base, third_is_second) == 2

    assert calculate_penalty(base, list(reversed(base))) == 18

    try:
        calculate_penalty(["T"], ["G"])
    except ValueError:
        pass
    else:
        raise AssertionError("チーム不一致を検出できませんでした")
    print("例: 1位予想が実際6位 → 5ズレ")
    print("例: 3位予想が実際2位 → 1ズレ")
    print("例: 完全逆順 → 18ズレ")


def _test_parser_ignores_interleague() -> None:
    html = """
    <html><head><meta charset="utf-8"><title>2026年度 公式戦成績 全日程終了</title></head><body>
      <table>
        <tr><th>名前</th></tr>
        <tr><td>中日ドラゴンズ</td></tr>
        <tr><td>東京ヤクルトスワローズ</td></tr>
        <tr><td>広島東洋カープ</td></tr>
        <tr><td>横浜DeNAベイスターズ</td></tr>
        <tr><td>読売ジャイアンツ</td></tr>
        <tr><td>阪神タイガース</td></tr>
      </table>
      <h3>交流戦チーム勝敗表</h3>
      <table>
        <tr><th>チーム</th><th>勝利</th><th>勝率</th></tr>
        <tr><td>読売ジャイアンツ</td><td>10</td><td>.625</td></tr>
        <tr><td>中日ドラゴンズ</td><td>7</td><td>.389</td></tr>
        <tr><td>阪神タイガース</td><td>6</td><td>.333</td></tr>
        <tr><td>広島東洋カープ</td><td>5</td><td>.294</td></tr>
        <tr><td>東京ヤクルトスワローズ</td><td>6</td><td>.353</td></tr>
        <tr><td>横浜DeNAベイスターズ</td><td>5</td><td>.278</td></tr>
      </table>
      <h3>チーム勝敗表</h3>
      <table>
        <tr><th>チーム</th><th>勝利</th><th>勝率</th><th>交流戦</th></tr>
        <tr><td>阪神タイガース</td><td>79</td><td>.560</td><td>6-12</td></tr>
        <tr><td>読売ジャイアンツ</td><td>76</td><td>.543</td><td>10-6</td></tr>
        <tr><td>横浜DeNAベイスターズ</td><td>71</td><td>.507</td><td>5-13</td></tr>
        <tr><td>広島東洋カープ</td><td>61</td><td>.439</td><td>5-12</td></tr>
        <tr><td>東京ヤクルトスワローズ</td><td>61</td><td>.433</td><td>6-11</td></tr>
        <tr><td>中日ドラゴンズ</td><td>60</td><td>.426</td><td>7-11</td></tr>
      </table>
      <h3>交流戦チーム勝敗表</h3>
      <table>
        <tr><th>チーム</th><th>勝利</th><th>勝率</th></tr>
        <tr><td>埼玉西武ライオンズ</td><td>14</td><td>.824</td></tr>
        <tr><td>福岡ソフトバンクホークス</td><td>14</td><td>.778</td></tr>
        <tr><td>北海道日本ハムファイターズ</td><td>14</td><td>.778</td></tr>
        <tr><td>千葉ロッテマリーンズ</td><td>10</td><td>.625</td></tr>
        <tr><td>オリックス・バファローズ</td><td>9</td><td>.529</td></tr>
        <tr><td>東北楽天ゴールデンイーグルス</td><td>4</td><td>.222</td></tr>
      </table>
      <h3>チーム勝敗表</h3>
      <table>
        <tr><th>チーム</th><th>勝利</th><th>勝率</th><th>交流戦</th></tr>
        <tr><td>福岡ソフトバンクホークス</td><td>91</td><td>.650</td><td>14-4</td></tr>
        <tr><td>埼玉西武ライオンズ</td><td>79</td><td>.568</td><td>14-3</td></tr>
        <tr><td>北海道日本ハムファイターズ</td><td>78</td><td>.557</td><td>14-4</td></tr>
        <tr><td>オリックス・バファローズ</td><td>65</td><td>.461</td><td>9-8</td></tr>
        <tr><td>千葉ロッテマリーンズ</td><td>63</td><td>.450</td><td>10-6</td></tr>
        <tr><td>東北楽天ゴールデンイーグルス</td><td>58</td><td>.408</td><td>4-14</td></tr>
      </table>
    </body></html>
    """
    parsed = parse_standings_html(html)
    assert parsed["central"] == ["T", "G", "DB", "C", "S", "D"]
    assert parsed["pacific"] == ["H", "L", "F", "Bs", "M", "E"]
    assert parsed["season"] == 2026
    assert parsed["season_finished"] is True
    assert "交流戦" not in parsed["headings"]["central"]
    assert "交流戦" not in parsed["headings"]["pacific"]
    print("例: 交流戦の表と、交流戦の列を分けて勝敗表を採用")


def _test_daily_standings() -> None:
    html = """
    <table>
      <tr><th>月日</th><th>対戦カード</th></tr>
      <tr><td>3/27（金）</td><td>巨人 1 - 0 阪神</td></tr>
      <tr><td></td><td>DeNA 0 - 2 ヤクルト</td></tr>
      <tr><td></td><td>広島 3 - 1 中日</td></tr>
      <tr><td></td><td>ソフトバンク 4 - 2 日本ハム</td></tr>
      <tr><td></td><td>西武 0 - 1 ロッテ</td></tr>
      <tr><td></td><td>オリックス 中止 楽天</td></tr>
      <tr><td>3/28（土）</td><td>オリックス 2 - 5 楽天</td></tr>
      <tr><td>3/29（日）</td><td>セ・リーグ 1 - 2 パ・リーグ</td></tr>
      <tr><td>3/30（月）</td><td>阪神 1 - 0 巨人</td></tr>
    </table>
    """
    games = parse_schedule_games(html, 2026)
    assert [game["date"] for game in games] == [
        "2026-03-27", "2026-03-27", "2026-03-27", "2026-03-27", "2026-03-27", "2026-03-28", "2026-03-30"
    ]
    assert games[0] == {"date": "2026-03-27", "a": "G", "sa": 1, "b": "T", "sb": 0}
    season = [
        {"date": "2026-03-27", "a": "G", "sa": 1, "b": "T", "sb": 0},
        {"date": "2026-03-27", "a": "S", "sa": 1, "b": "DB", "sb": 0},
        {"date": "2026-03-27", "a": "C", "sa": 1, "b": "D", "sb": 0},
        {"date": "2026-03-27", "a": "H", "sa": 1, "b": "F", "sb": 0},
        {"date": "2026-03-27", "a": "M", "sa": 1, "b": "L", "sb": 0},
        {"date": "2026-03-27", "a": "E", "sa": 1, "b": "Bs", "sb": 0},
        {"date": "2026-03-29", "a": "T", "sa": 1, "b": "G", "sb": 0},
    ]
    daily = build_daily_standings(season, game_cap=2)
    assert [point["date"] for point in daily] == ["2026-03-27", "2026-03-28", "2026-03-29"]
    assert daily[1]["central"] == daily[0]["central"]
    assert daily[1]["pacific"] == daily[0]["pacific"]
    assert daily[2]["central"] != daily[0]["central"]
    capped = build_daily_standings(season, game_cap=1)
    assert [point["date"] for point in capped] == ["2026-03-27"]
    summary = season_game_summary(season, game_cap=2)
    assert summary["played"] == 7
    assert summary["total"] == 12
    assert summary["remaining"] == 5
    assert summary["as_of"] == "2026-03-29"
    assert len(summary["last_games"]) == 1
    assert regular_season_game_total() == 858
    print("例: 試合のない日は前日順位、規定試合を超える分は入れない")


def _test_short_names() -> None:
    html = """
    <h3>チーム勝敗表</h3>
    <table>
      <tr><th>順位</th><th>チーム</th><th>勝率</th></tr>
      <tr><td>1</td><td>神</td><td>.5</td></tr>
      <tr><td>2</td><td>巨</td><td>.4</td></tr>
      <tr><td>3</td><td>デ</td><td>.3</td></tr>
      <tr><td>4</td><td>広</td><td>.2</td></tr>
      <tr><td>5</td><td>ヤ</td><td>.1</td></tr>
      <tr><td>6</td><td>中</td><td>.0</td></tr>
    </table>
    """
    parsed = parse_standings_html(html)
    assert parsed["central"] == ["T", "G", "DB", "C", "S", "D"]


if __name__ == "__main__":
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    run_self_tests()
    if "--live" in sys.argv:
        raise SystemExit(live_check())
