"""公開用の順位JSONを作る。予想データは含めない。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "api" / "dashboard.json"


def main() -> None:
    temporary = Path(tempfile.mkdtemp(prefix="pennant-public-")) / "state.json"
    os.environ["NPB_DATA_PATH"] = str(temporary)
    sys.path.insert(0, str(ROOT))
    import app

    client = app.app.test_client()
    response = client.post("/api/refresh")
    payload = response.get_json()
    if response.status_code != 200 or not payload or not payload.get("standings"):
        raise SystemExit("順位を取得できませんでした")
    private = ["T", "G", "DB", "S", "D", "C"]
    draft = (payload.get("draft") or {}).get("central")
    confirmed = (payload.get("confirmed") or {}).get("central")
    legacy = payload.get("legacy_user") or {}
    legacy_draft = ((legacy.get("draft") or {}).get("central"))
    legacy_confirmed = ((legacy.get("confirmed") or {}).get("central"))
    if payload.get("legacy_user") or draft == private or confirmed == private or legacy_draft == private or legacy_confirmed == private:
        raise SystemExit("個人の予想が公開データに含まれたので中止しました")
    if Path(os.environ["NPB_DATA_PATH"]).resolve() == (ROOT / "data" / "app_state.json").resolve():
        raise SystemExit("個人の保存ファイルは使いません")
    payload["legacy_user"] = None
    payload["confirmed"] = None
    payload["history"] = []
    payload["archives"] = []
    payload["locked"] = False
    standings = payload["standings"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"wrote {OUT} season={standings.get('season')} "
        f"snapshots={payload.get('snapshot_count')} fetched={standings.get('fetched_label')}"
    )


if __name__ == "__main__":
    main()
