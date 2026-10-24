"""プロ野球順位予想アプリ。

起動:
    python -m pip install -r requirements.txt
    python app.py --self-test
    python app.py --self-test --live
    python app.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import npb_core

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()

app = Flask(__name__)
app.json.ensure_ascii = False


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def data_path() -> Path:
    override = os.environ.get("NPB_DATA_PATH")
    return Path(override) if override else ROOT / "data" / "app_state.json"


def now_parts() -> tuple[str, str]:
    current = datetime.now(npb_core.JST)
    return current.isoformat(timespec="minutes"), current.strftime("%Y/%m/%d %H:%M")


def today_jst() -> str:
    return datetime.now(npb_core.JST).date().isoformat()


USER_DATA_KEYS = ("draft", "confirmed", "locked", "history", "archives", "season_year", "view_year")


def default_state() -> dict:
    return {
        "draft": deepcopy(npb_core.DEFAULT_DRAFT),
        "confirmed": None,
        "locked": False,
        "history": [],
        "standings": None,
        "snapshots": [],
        "season_games": [],
        "season_games_year": None,
        "season_history_complete": False,
        "season_year": None,
        "archives": [],
        "view_year": None,
        "last_error": None,
    }


def load_state() -> dict:
    path = data_path()
    if not path.exists():
        return default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state()
    state = default_state()
    if isinstance(raw, dict):
        for key in state:
            if key in raw:
                state[key] = raw[key]
    _repair(state)
    return state


def _repair(state: dict) -> None:
    draft = state.get("draft")
    if not isinstance(draft, dict):
        state["draft"] = deepcopy(npb_core.DEFAULT_DRAFT)
    for league in ("central", "pacific"):
        if not npb_core.valid_lineup(state["draft"].get(league), league):
            state["draft"][league] = list(npb_core.DEFAULT_DRAFT[league])
    confirmed = state.get("confirmed")
    if confirmed is not None:
        ok = isinstance(confirmed, dict) and all(
            npb_core.valid_lineup(confirmed.get(league), league) for league in ("central", "pacific")
        )
        if not ok:
            state["confirmed"] = None
            state["locked"] = False
    if not isinstance(state.get("history"), list):
        state["history"] = []
    if not isinstance(state.get("snapshots"), list):
        state["snapshots"] = []
    if not isinstance(state.get("season_games"), list):
        state["season_games"] = []
    if not isinstance(state.get("archives"), list):
        state["archives"] = []
    state["season_year"] = _as_year(state.get("season_year"))
    state["view_year"] = _as_year(state.get("view_year"))
    if state["view_year"] is not None and _find_archive(state, state["view_year"]) is None:
        state["view_year"] = None
    state["season_history_complete"] = bool(state.get("season_history_complete"))
    state["locked"] = bool(state.get("locked"))


def _as_year(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _find_archive(state: dict, year: int | None) -> dict | None:
    if year is None:
        return None
    for item in state.get("archives") or []:
        if isinstance(item, dict) and _as_year(item.get("year")) == year:
            return item
    return None


def save_state(state: dict) -> None:
    path = data_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def active_prediction(state: dict) -> tuple[dict, str]:
    confirmed = state.get("confirmed")
    if confirmed:
        return {"central": list(confirmed["central"]), "pacific": list(confirmed["pacific"])}, "confirmed"
    return {"central": list(state["draft"]["central"]), "pacific": list(state["draft"]["pacific"])}, "draft"


def drafts_differ(state: dict) -> bool:
    confirmed = state.get("confirmed")
    if not confirmed:
        return False
    return confirmed["central"] != state["draft"]["central"] or confirmed["pacific"] != state["draft"]["pacific"]


def standings_ready(standings: dict | None) -> bool:
    return bool(
        standings
        and npb_core.valid_lineup(standings.get("central"), "central")
        and npb_core.valid_lineup(standings.get("pacific"), "pacific")
    )


def is_stale(standings: dict | None, state: dict | None = None) -> bool:
    if state and state.get("season_year") and not standings_ready(state.get("standings")):
        return False
    if not standings or not standings.get("fetched_at"):
        return True
    try:
        fetched = datetime.fromisoformat(standings["fetched_at"])
    except ValueError:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=npb_core.JST)
    return datetime.now(npb_core.JST) - fetched > timedelta(hours=6)


def build_score(prediction: dict, standings: dict) -> dict:
    total = 0
    blocks = {}
    for league in ("central", "pacific"):
        rows = []
        for row in npb_core.rank_breakdown(prediction[league], standings[league]):
            info = npb_core.TEAMS[row["team"]]
            rows.append({**row, "name": info["name"], "full_name": info["full_name"], "colors": info["colors"]})
        league_total = sum(row["penalty"] for row in rows)
        total += league_total
        blocks[league] = {"name": npb_core.LEAGUE_LABEL[league], "total": league_total, "rows": rows}
    return {
        "total": total,
        "label": npb_core.closeness_label(total),
        "central": blocks["central"],
        "pacific": blocks["pacific"],
    }


def build_trend(prediction: dict, snapshots: list[dict]) -> list[dict]:
    points = []
    for snap in snapshots:
        if not npb_core.valid_lineup(snap.get("central"), "central"):
            continue
        if not npb_core.valid_lineup(snap.get("pacific"), "pacific"):
            continue
        central = npb_core.calculate_penalty(prediction["central"], snap["central"])
        pacific = npb_core.calculate_penalty(prediction["pacific"], snap["pacific"])
        points.append({"date": snap["date"], "central": central, "pacific": pacific, "total": central + pacific})
    return points


def build_ranks(prediction: dict, snapshots: list[dict]) -> dict:
    payload = {}
    for league in ("central", "pacific"):
        dates = []
        series = {team_id: [] for team_id in prediction[league]}
        for snap in snapshots:
            order = snap.get(league)
            if not npb_core.valid_lineup(order, league):
                continue
            dates.append(snap["date"])
            rank = {team_id: index for index, team_id in enumerate(order, start=1)}
            for team_id in series:
                series[team_id].append(rank[team_id])
        teams = []
        for index, team_id in enumerate(prediction[league], start=1):
            info = npb_core.TEAMS[team_id]
            teams.append(
                {
                    "id": team_id,
                    "name": info["name"],
                    "colors": info["colors"],
                    "predicted_rank": index,
                    "ranks": series[team_id],
                }
            )
        payload[league] = {"dates": dates, "teams": teams}
    return payload


def team_catalog() -> list[dict]:
    return [{"id": team_id, **info} for team_id, info in npb_core.TEAMS.items()]


def _public_games(games: list[dict]) -> list[dict]:
    rows = []
    for game in games or []:
        away = npb_core.TEAMS.get(game.get("a") or "")
        home = npb_core.TEAMS.get(game.get("b") or "")
        if not away or not home:
            continue
        rows.append(
            {
                "date": game.get("date"),
            "away_id": game.get("a"),
            "away": away["name"],
            "away_score": game.get("sa"),
            "home_id": game.get("b"),
            "home": home["name"],
                "home_score": game.get("sb"),
            }
        )
    return rows


def _games_url(year, as_of) -> str:
    if year and as_of and len(str(as_of)) >= 7:
        try:
            month = int(str(as_of)[5:7])
        except ValueError:
            month = 0
        if 1 <= month <= 12:
            return f"https://npb.jp/games/{int(year)}/schedule_{month:02d}_detail.html"
    return "https://npb.jp/"


def _progress_from_games(standings: dict | None, games: list[dict] | None) -> tuple[dict | None, list[dict]]:
    summary = npb_core.season_game_summary(games or [])
    year = (standings or {}).get("season")
    finished = bool((standings or {}).get("season_finished"))
    if not summary["played"] and not finished:
        return None, []
    progress = {
        "year": year,
        "as_of": summary["as_of"],
        "played": summary["played"] or None,
        "total": summary["total"],
        "remaining": summary["remaining"] if summary["played"] else None,
        "finished": finished,
    }
    return progress, _public_games(summary["last_games"])


def _score_bundle(prediction: dict | None, standings: dict | None, snapshots: list[dict], using: str):
    ready = standings_ready(standings) and prediction and all(
        npb_core.valid_lineup(prediction.get(league), league) for league in ("central", "pacific")
    )
    if not ready:
        return None, [], None
    score = build_score(prediction, standings)
    score["provisional"] = using == "draft"
    return score, build_trend(prediction, snapshots), build_ranks(prediction, snapshots)


def _archive_record(state: dict) -> dict:
    standings = state.get("standings") or {}
    prediction, _using = active_prediction(state)
    progress, recent = _progress_from_games(standings, state.get("season_games") or [])
    iso, label = now_parts()
    kept = ("central", "pacific", "season", "season_finished", "source_label", "source_url", "fetched_label")
    return {
        "year": int(standings.get("season")),
        "archived_at": iso,
        "archived_label": label,
        "prediction": prediction,
        "standings": {key: standings.get(key) for key in kept},
        "snapshots": state.get("snapshots") or [],
        "progress": progress,
        "recent_games": recent,
    }


def public_state(state: dict | None = None) -> dict:
    state = state or load_state()
    live_standings = state.get("standings")
    prediction, using = active_prediction(state)
    score, trend, ranks = _score_bundle(prediction, live_standings, state.get("snapshots") or [], using)
    progress, recent = _progress_from_games(live_standings, state.get("season_games") or [])
    archive = _find_archive(state, state.get("view_year"))
    archive_view = None
    if archive:
        archived_standings = archive.get("standings")
        archived_score, archived_trend, archived_ranks = _score_bundle(
            archive.get("prediction"),
            archived_standings,
            archive.get("snapshots") or [],
            "confirmed",
        )
        archived_progress = archive.get("progress")
        archive_view = {
            "year": archive.get("year"),
            "score": archived_score,
            "trend": archived_trend,
            "ranks": archived_ranks,
            "standings": archived_standings,
            "progress": archived_progress,
            "recent_games": archive.get("recent_games") or [],
            "games_url": _games_url((archived_progress or {}).get("year"), (archived_progress or {}).get("as_of")),
        }
    confirmed = state.get("confirmed")
    active_year = state.get("season_year") or (live_standings or {}).get("season")
    can_start = bool(live_standings and live_standings.get("season_finished") and standings_ready(live_standings))
    base_year = (live_standings or {}).get("season") or active_year or datetime.now(npb_core.JST).year
    return {
        "teams": team_catalog(),
        "draft": state["draft"],
        "confirmed": confirmed,
        "confirmed_label": confirmed.get("updated_label") if confirmed else None,
        "first_confirmed_label": confirmed.get("confirmed_label") if confirmed else None,
        "locked": state["locked"],
        "history": list(reversed(state.get("history") or []))[:30],
        "standings": live_standings,
        "using": using,
        "score": score,
        "trend": trend,
        "ranks": ranks,
        "draft_differs": drafts_differ(state),
        "stale": False if archive else is_stale(live_standings, state),
        "last_error": state.get("last_error"),
        "snapshot_count": len(state.get("snapshots") or []),
        "season_year": active_year,
        "viewing_year": archive.get("year") if archive else None,
        "can_start_next": can_start,
        "next_year": int(base_year) + 1 if can_start else None,
        "archives": [
            {"year": item.get("year"), "label": item.get("archived_label")}
            for item in state.get("archives") or []
            if isinstance(item, dict)
        ],
        "progress": progress,
        "recent_games": recent,
        "games_url": _games_url((progress or {}).get("year") or active_year, (progress or {}).get("as_of")),
        "archive_view": archive_view,
        "snapshots": _public_snapshots(state),
        "legacy_user": export_payload(state) if _has_personal_data(state) else None,
    }


def _merge_games(cached: list[dict], fresh: list[dict], start_month: int) -> list[dict]:
    kept = []
    for game in cached or []:
        try:
            month = int(str(game.get("date", ""))[5:7])
        except ValueError:
            continue
        if month < start_month:
            kept.append(game)
    return kept + list(fresh or [])


def _history_ready(state: dict, year: int | None) -> bool:
    return bool(
        year
        and state.get("season_history_complete")
        and state.get("season_games_year") == year
        and state.get("snapshots")
    )


def apply_standings(state: dict, fetched: dict, games: list[dict] | None = None, keep_history: bool = False) -> None:
    fetched_at, fetched_label = now_parts()
    state["standings"] = {
        "central": fetched["central"],
        "pacific": fetched["pacific"],
        "season": fetched.get("season"),
        "season_finished": fetched.get("season_finished"),
        "source_label": fetched.get("source_label"),
        "source_url": fetched.get("source_url"),
        "headings": fetched.get("headings") or {},
        "fallback_used": fetched.get("fallback_used"),
        "primary_error": fetched.get("primary_error"),
        "cross_check": fetched.get("cross_check"),
        "attempts": fetched.get("attempts") or [],
        "warning": fetched.get("warning"),
        "fetched_at": fetched_at,
        "fetched_label": fetched_label,
    }
    state["last_error"] = None
    if keep_history and state.get("snapshots"):
        return
    year = fetched.get("season")
    if games:
        daily = npb_core.build_daily_standings(games)
        last = daily[-1] if daily else None
        if last and npb_core.valid_lineup(last.get("central"), "central") and npb_core.valid_lineup(last.get("pacific"), "pacific"):
            if fetched.get("season_finished") and (
                last["central"] != fetched["central"] or last["pacific"] != fetched["pacific"]
            ):
                last = {**last, "central": fetched["central"], "pacific": fetched["pacific"]}
                daily[-1] = last
            state["snapshots"] = daily
            state["season_games"] = games
            state["season_games_year"] = year
            state["season_history_complete"] = bool(fetched.get("season_finished"))
            return
    item = {
        "date": today_jst(),
        "central": fetched["central"],
        "pacific": fetched["pacific"],
        "source": fetched.get("source_label"),
    }
    if fetched.get("season_finished") and state.get("snapshots"):
        return
    snaps = [snap for snap in state["snapshots"] if snap.get("date") != item["date"]]
    snaps.append(item)
    snaps.sort(key=lambda snap: snap.get("date") or "")
    state["snapshots"] = snaps


def read_lineup(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("並びの形式が正しくありません")
    lineup = {}
    for league in ("central", "pacific"):
        ids = payload.get(league)
        if not npb_core.valid_lineup(ids, league):
            raise ValueError(f"{npb_core.LEAGUE_LABEL[league]}の並びが正しくありません")
        lineup[league] = list(ids)
    return lineup


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/dashboard")
def dashboard():
    with LOCK:
        return jsonify(public_state(load_state()))


@app.post("/api/refresh")
def refresh():
    try:
        fetched = npb_core.fetch_standings()
    except npb_core.StandingsError as exc:
        with LOCK:
            state = load_state()
            state["last_error"] = str(exc)
            if state.get("standings") is None:
                state["standings"] = {"attempts": exc.attempts, "warning": str(exc)}
            else:
                state["standings"]["attempts"] = exc.attempts
                state["standings"]["warning"] = str(exc)
            save_state(state)
            payload = public_state(state)
        payload["error"] = str(exc)
        return jsonify(payload)
    year = fetched.get("season")
    with LOCK:
        state = load_state()
        active_year = state.get("season_year")
        if active_year and year and int(year) != int(active_year):
            payload = public_state(state)
            payload["notice"] = f"{int(active_year)}年の順位表は、まだ公開されていません。"
            payload["notice_code"] = "standingsNotYet"
            return jsonify(payload)
        keep_history = _history_ready(state, year)
        cached = list(state.get("season_games") or []) if state.get("season_games_year") == year else []
        start_month = 3
        if cached:
            try:
                start_month = int(str(cached[-1].get("date", ""))[5:7])
            except ValueError:
                start_month = 3
    games = None
    if not keep_history:
        try:
            fresh = npb_core.fetch_schedule_games(year or datetime.now(npb_core.JST).year, start_month)
            games = _merge_games(cached, fresh, start_month)
        except Exception:
            games = cached or None
    with LOCK:
        state = load_state()
        apply_standings(state, fetched, games, keep_history)
        save_state(state)
        payload = public_state(state)
    return jsonify(payload)


@app.post("/api/draft")
def save_draft():
    try:
        lineup = read_lineup(request.get_json(force=True, silent=False))
    except ValueError as exc:
        with LOCK:
            payload = public_state(load_state())
        payload["error"] = str(exc)
        return jsonify(payload), 400
    with LOCK:
        state = load_state()
        if state["locked"]:
            payload = public_state(state)
            payload["error"] = "確定中は並べ替えできません。修正するを押してください。"
            return jsonify(payload), 400
        state["draft"] = lineup
        save_state(state)
        payload = public_state(state)
    return jsonify(payload)


@app.post("/api/draft/reset")
def reset_draft():
    with LOCK:
        state = load_state()
        if state["locked"]:
            payload = public_state(state)
            payload["error"] = "確定中は初期順に戻せません。"
            return jsonify(payload), 400
        state["draft"] = deepcopy(npb_core.DEFAULT_DRAFT)
        save_state(state)
        payload = public_state(state)
    payload["notice"] = "初期の下書き順に戻しました。"
    return jsonify(payload)


@app.post("/api/confirm")
def confirm():
    with LOCK:
        state = load_state()
        lineup = {
            "central": list(state["draft"]["central"]),
            "pacific": list(state["draft"]["pacific"]),
        }
        previous = state.get("confirmed")
        if previous and previous["central"] == lineup["central"] and previous["pacific"] == lineup["pacific"]:
            state["locked"] = True
            save_state(state)
            payload = public_state(state)
            payload["notice"] = "順位は前回の確定と同じです。"
            return jsonify(payload)
        iso, label = now_parts()
        first_iso = previous["confirmed_at"] if previous else iso
        first_label = previous["confirmed_label"] if previous else label
        action = "確定" if previous is None else "更新"
        state["confirmed"] = {
            "central": lineup["central"],
            "pacific": lineup["pacific"],
            "confirmed_at": first_iso,
            "confirmed_label": first_label,
            "updated_at": iso,
            "updated_label": label,
        }
        state["locked"] = True
        state["history"].append({"at": iso, "label": label, "action": action, "central": lineup["central"], "pacific": lineup["pacific"]})
        save_state(state)
        payload = public_state(state)
    payload["notice"] = f"予想を{action}しました。最終確定: {label}"
    return jsonify(payload)


@app.post("/api/unlock")
def unlock():
    with LOCK:
        state = load_state()
        if not state.get("confirmed"):
            payload = public_state(state)
            payload["error"] = "まだ確定された予想がありません。"
            return jsonify(payload), 400
        state["locked"] = False
        state["draft"] = {
            "central": list(state["confirmed"]["central"]),
            "pacific": list(state["confirmed"]["pacific"]),
        }
        save_state(state)
        payload = public_state(state)
    payload["notice"] = "修正できます。確定すると履歴に残ります。"
    return jsonify(payload)


@app.post("/api/cancel-edit")
def cancel_edit():
    with LOCK:
        state = load_state()
        if not state.get("confirmed"):
            payload = public_state(state)
            payload["error"] = "戻せる確定予想がありません。"
            return jsonify(payload), 400
        state["draft"] = {
            "central": list(state["confirmed"]["central"]),
            "pacific": list(state["confirmed"]["pacific"]),
        }
        state["locked"] = True
        save_state(state)
        payload = public_state(state)
    payload["notice"] = "修正をやめ、確定済みの順位に戻しました。"
    return jsonify(payload)


@app.post("/api/season/start")
def start_next_season():
    with LOCK:
        state = load_state()
        standings = state.get("standings") or {}
        year = _as_year(standings.get("season"))
        if not standings_ready(standings) or not standings.get("season_finished") or year is None:
            payload = public_state(state)
            payload["error"] = "レギュラーシーズンが終わってから、次の年の予想を始められます。"
            return jsonify(payload), 400
        record = _archive_record(state)
        archives = [item for item in state.get("archives") or [] if not (isinstance(item, dict) and _as_year(item.get("year")) == year)]
        archives.append(record)
        state["archives"] = archives
        state["season_year"] = year + 1
        state["view_year"] = None
        state["draft"] = deepcopy(npb_core.DEFAULT_DRAFT)
        state["confirmed"] = None
        state["locked"] = False
        state["history"] = []
        state["standings"] = None
        state["snapshots"] = []
        state["season_games"] = []
        state["season_games_year"] = None
        state["season_history_complete"] = False
        state["last_error"] = None
        save_state(state)
        payload = public_state(state)
    payload["notice"] = f"{year + 1}年の予想を始めました。{year}年の結果は設定から見られます。"
    return jsonify(payload)


@app.post("/api/season/view")
def view_season():
    body = request.get_json(force=True, silent=True) or {}
    year = _as_year(body.get("year")) if body.get("year") not in (None, "") else None
    with LOCK:
        state = load_state()
        if year is None:
            state["view_year"] = None
            notice = "いまの予想に戻りました。"
        elif _find_archive(state, year) is None:
            payload = public_state(state)
            payload["error"] = "その年の結果は残っていません。"
            return jsonify(payload), 400
        else:
            state["view_year"] = year
            notice = f"{year}年の結果を表示しています。"
        save_state(state)
        payload = public_state(state)
    payload["notice"] = notice
    return jsonify(payload)


def _has_personal_data(state: dict) -> bool:
    if state.get("confirmed") or state.get("history") or state.get("archives") or state.get("season_year") or state.get("view_year"):
        return True
    draft = state.get("draft") or {}
    return draft.get("central") != list(npb_core.DEFAULT_DRAFT["central"]) or draft.get("pacific") != list(npb_core.DEFAULT_DRAFT["pacific"])


def _public_snapshots(state: dict) -> list[dict]:
    rows = []
    for snap in state.get("snapshots") or []:
        if not isinstance(snap, dict):
            continue
        rows.append({"date": snap.get("date"), "central": snap.get("central"), "pacific": snap.get("pacific")})
    return rows


def export_payload(state: dict) -> dict:
    payload = {key: state.get(key) for key in USER_DATA_KEYS}
    _iso, label = now_parts()
    payload["format"] = "pennant-penalty"
    payload["version"] = 1
    payload["exported_label"] = label
    return payload


@app.get("/api/export")
def export_data():
    with LOCK:
        return jsonify(export_payload(load_state()))


@app.post("/api/import")
def import_data():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict) or body.get("format") != "pennant-penalty":
        with LOCK:
            payload = public_state(load_state())
        payload["error"] = "このアプリのバックアップファイルではありません。"
        return jsonify(payload), 400
    with LOCK:
        state = load_state()
        for key in USER_DATA_KEYS:
            if key in body:
                state[key] = body[key]
        _repair(state)
        save_state(state)
        payload = public_state(state)
    payload["notice"] = "バックアップを読み込みました。"
    return jsonify(payload)


@app.post("/api/data/delete")
def delete_user_data():
    with LOCK:
        state = load_state()
        fresh = default_state()
        for key in USER_DATA_KEYS:
            state[key] = fresh[key]
        _repair(state)
        save_state(state)
        payload = public_state(state)
    payload["notice"] = "予想データを消しました。順位表はそのままです。"
    return jsonify(payload)


def run_api_tests() -> None:
    import tempfile

    previous = os.environ.get("NPB_DATA_PATH")
    original = npb_core.fetch_standings
    original_games = npb_core.fetch_schedule_games
    actual = {
        "central": ["T", "G", "DB", "C", "S", "D"],
        "pacific": ["H", "L", "F", "Bs", "M", "E"],
        "season": 2026,
        "season_finished": True,
        "source_label": "テスト",
        "source_url": "https://example.test/standings",
        "headings": {"central": "チーム勝敗表", "pacific": "チーム勝敗表"},
        "fallback_used": False,
        "primary_error": None,
        "cross_check": {"status": "match", "with": "テスト", "detail": "一致"},
        "attempts": [{"url": "https://example.test/standings", "label": "テスト", "ok": True, "error": None, "detail": "セ・パ"}],
        "warning": None,
    }

    def fake_fetch():
        return deepcopy(actual)

    try:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["NPB_DATA_PATH"] = str(Path(directory) / "state.json")
            npb_core.fetch_standings = fake_fetch
            npb_core.fetch_schedule_games = lambda year, start_month=3: []
            client = app.test_client()
            page = client.get("/")
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert "プロ野球順位予想　ペナントペナルティ" in html
            assert "予想と現実の差を数える" not in html
            assert "グラフ" in html and "設定" in html

            first = client.get("/api/dashboard").get_json()
            assert first["standings"] is None
            assert first["score"] is None
            early = client.post("/api/season/start")
            assert early.status_code == 400

            refreshed = client.post("/api/refresh").get_json()
            assert refreshed["score"]["provisional"] is True
            assert refreshed["score"]["total"] == npb_core.calculate_penalty(
                npb_core.DEFAULT_DRAFT["central"], actual["central"]
            ) + npb_core.calculate_penalty(npb_core.DEFAULT_DRAFT["pacific"], actual["pacific"])
            assert refreshed["score"]["central"]["rows"][0]["name"] == "阪神"
            assert refreshed["snapshot_count"] == 1

            confirmed = client.post("/api/confirm").get_json()
            assert confirmed["locked"] is True
            assert confirmed["using"] == "confirmed"
            assert len(confirmed["history"]) == 1
            assert confirmed["score"]["provisional"] is False

            changed = {
                "central": ["D", "S", "C", "DB", "G", "T"],
                "pacific": ["E", "M", "Bs", "F", "L", "H"],
            }
            locked_save = client.post("/api/draft", json=changed)
            assert locked_save.status_code == 400

            unlocked = client.post("/api/unlock").get_json()
            assert unlocked["locked"] is False
            saved = client.post("/api/draft", json=changed).get_json()
            assert saved["draft_differs"] is True
            assert saved["score"]["total"] == confirmed["score"]["total"]

            updated = client.post("/api/confirm").get_json()
            expected = npb_core.calculate_penalty(changed["central"], actual["central"]) + npb_core.calculate_penalty(
                changed["pacific"], actual["pacific"]
            )
            assert updated["score"]["total"] == expected
            assert len(updated["history"]) == 2
            assert updated["history"][0]["action"] == "更新"

            same = client.post("/api/unlock").get_json()
            assert same["locked"] is False
            again = client.post("/api/confirm").get_json()
            assert len(again["history"]) == 2
            assert "同じ" in again["notice"]

            started = client.post("/api/season/start").get_json()
            assert started["season_year"] == 2027
            assert started["standings"] is None
            assert started["score"] is None
            assert started["snapshot_count"] == 0
            assert started["can_start_next"] is False
            assert started["stale"] is False
            assert started["archives"][0]["year"] == 2026

            viewed = client.post("/api/season/view", json={"year": 2026}).get_json()
            assert viewed["viewing_year"] == 2026
            assert viewed["archive_view"]["score"]["total"] == again["score"]["total"]
            assert viewed["score"] is None
            assert len(viewed["archive_view"]["trend"]) == 1

            back = client.post("/api/season/view", json={"year": None}).get_json()
            assert back["viewing_year"] is None
            assert back["archive_view"] is None

            refetched = client.post("/api/refresh").get_json()
            assert refetched["standings"] is None
            assert "まだ" in refetched["notice"]

            exported = client.get("/api/export").get_json()
            assert exported["format"] == "pennant-penalty"
            assert exported["season_year"] == 2027
            bad = client.post("/api/import", json={"format": "other"})
            assert bad.status_code == 400
            exported["draft"] = {
                "central": ["D", "S", "C", "DB", "G", "T"],
                "pacific": ["E", "M", "Bs", "F", "L", "H"],
            }
            exported["locked"] = False
            imported = client.post("/api/import", json=exported).get_json()
            assert imported["draft"]["central"][0] == "D"
            assert imported["locked"] is False
            deleted = client.post("/api/data/delete").get_json()
            assert deleted["confirmed"] is None
            assert deleted["archives"] == []
            assert deleted["draft"]["central"] == npb_core.DEFAULT_DRAFT["central"]
        print("APIテスト: OK")
    finally:
        npb_core.fetch_standings = original
        npb_core.fetch_schedule_games = original_games
        if previous is None:
            os.environ.pop("NPB_DATA_PATH", None)
        else:
            os.environ["NPB_DATA_PATH"] = previous


def serve() -> None:
    port = int(os.environ.get("PORT", "5050"))
    print(f"プロ野球順位予想　ペナントペナルティ  http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    _utf8_stdio()
    if "--self-test" in sys.argv:
        npb_core.run_self_tests()
        run_api_tests()
        print("すべてのオフラインテストが通りました")
        if "--live" in sys.argv:
            raise SystemExit(npb_core.live_check())
    else:
        serve()
