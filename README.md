# waze-eta

שירות קטן שנותן זמני נסיעה חיים מ-Waze, לפי כתובת או מיקום. הוא נבנה בשביל [בוט המאמן בטלגרם](https://github.com/BlinkyBill182/coach-bot): הבוט שואל אותו כמה זמן לוקחת הנסיעה, ומזיז לפי זה את זמן היציאה ואת תזכורת האריזה.

**לא רשמי.** ל-Waze אין API ציבורי. השירות משתמש ב-[pywaze](https://github.com/eifinger/pywaze), הספרייה שמאחורי האינטגרציה של Waze ב-Home Assistant, והיא פונה לאותן כתובות כמו המפה החיה של Waze. זה יכול להפסיק לעבוד בכל רגע, ואתם בודקים בעצמכם שזה מתאים לתנאי השימוש של Waze. אם Waze משתנה, התיקון אמור להיות רק ב-`waze_eta/waze.py`.

## התקנה ליד בוט המאמן

רץ על אותו שרת כמו הבוט, מאחורי אותו Traefik, בכתובת `https://<השרת>/waze`. צריך שהבוט כבר מותקן לפי ה-[SETUP.md שלו](https://github.com/BlinkyBill182/coach-bot/blob/main/bot/SETUP.md).

1. בעמוד הזה לוחצים **Use this template**, ובוחרים **Private**.
2. מורידים למחשב, ליד תיקיית הבוט ולא בתוכה:

```bash
git clone https://github.com/<המשתמש-שלכם>/waze-eta.git
cd waze-eta
```

3. העלאה ראשונה, עם שם השרת שלכם במקום `srv123456.hstgr.cloud`:

```bash
bash deploy/deploy.sh root@srv123456.hstgr.cloud --first srv123456.hstgr.cloud
```

הפקודה יוצרת על השרת `/docker/waze-eta/.env` עם מפתח חדש (`ETA_KEY`), ולוקחת את הגדרות Traefik מהבוט.

4. מעתיקים את המפתח לבוט. מתחברים לשרת:

```bash
ssh root@srv123456.hstgr.cloud
```

```bash
grep ETA_KEY /docker/waze-eta/.env
```

ב-`nano /docker/coach/.env` ממלאים `ETA_KEY` באותו ערך, ו-`ETA_URL=https://srv123456.hstgr.cloud/waze`. יוצאים עם `exit`, ומתיקיית הבוט מריצים שוב את פקודת ההעלאה של הבוט.

5. בודקים:

```bash
curl -s https://srv123456.hstgr.cloud/waze/health
```

אמור לחזור `{"ok": true}`. מעכשיו הבוט מחשב יציאות לפי Waze. כשמשתפים איתו מיקום חי בטלגרם, הוא מחשב מהמקום שאתם נמצאים בו.

אזור ברירת המחדל הוא ישראל (`WAZE_REGION=IL`, `TZ_NAME=Asia/Jerusalem`). מחוץ לישראל משנים אותם ב-`/docker/waze-eta/.env` ל-`EU`, `US` או `AU`.

## API

Every call except `/health` needs the header `X-API-Key: <ETA_KEY>`.

### `GET /eta`

| param | required | meaning |
|---|---|---|
| `from` | yes | address (Hebrew or English), `lat,lon`, or `me` (the owner's latest reported location, see below) |
| `to` | yes | address, `lat,lon`, or `me` |
| `depart_at` | no | departure, ISO (`2026-10-26T08:30`). No offset = `TZ_NAME` time. Default: now. Allowed range: an hour ago to 7 days ahead |

Leaving later uses Waze's predicted traffic for that hour.

`200`:
```json
{"minutes": 29, "km": 11.3, "route": "20 איילון צפון",
 "depart_at": "2026-10-25T11:11:00+02:00", "arrive_at": "2026-10-25T11:40:00+02:00",
 "from": {"lat": 32.794, "lon": 34.9896}, "to": {"lat": 32.0853, "lon": 34.781768}, "cached": false}
```
`minutes` is the fastest route, rounded. `from` and `to` are the points Waze matched, so you can check a geocode.
When `from` (or `to`) is `me`, the response also has
`"from_location": {"source": "telegram", "at": "...", "age_min": 3, "accuracy": 15}`.

Errors are always `{"error": "..."}`:
- `400` missing `from`/`to`, or bad `depart_at`
- `401` missing or wrong key
- `409` `me` was used but no device reported a location in the last 24 h (`LOCATION_MAX_AGE_H`)
- `422` Waze found no place for an address
- `502` Waze failed or timed out. Callers should fall back to their own estimate

### `POST /location`
A device reports where the owner is. Body (JSON):
`{"lat": 32.794, "lon": 34.9896, "source": "telegram", "accuracy": 15, "at": "2026-10-25T11:20:00+02:00"}`.
`source` names the device (`telegram`, `mac`, ...). `accuracy` (metres) and `at` (default now) are optional.
Returns `{"saved": true}`, or `{"saved": false}` when that device already sent a newer report
(Telegram live-location edits can arrive out of order).

The coach bot forwards each Telegram live-location update here as `telegram`.

### `GET /location`
The freshest report from any device in the last 24 h, with `age_min`; `404` if none. This is what `me` means.

### `GET /health`
`{"ok": true}`, no key.

### Example
```bash
curl -G -H "X-API-Key: $ETA_KEY" https://srv123456.hstgr.cloud/waze/eta \
  --data-urlencode "from=חיפה" --data-urlencode "to=תל אביב" --data-urlencode "depart_at=2026-10-26T08:00"
```

## Behaviour
- Caches: geocodes for 7 days; routes for 10 minutes per origin, destination and 15-minute departure slot.
- At most 2 calls to Waze at a time, so a burst from a caller doesn't get the server's IP blocked.
- Logs show minutes and status only, not addresses.
- Locations live only on the server, in `/docker/waze-eta/data/location.json` (latest per device, no history).

## Development
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest                       # Waze is stubbed
ETA_KEY=dev PORT=8765 .venv/bin/python -m waze_eta   # live, local
```

Updates: `bash deploy/deploy.sh root@<server>`. Logs: `ssh root@<server> 'cd /docker/waze-eta && docker compose logs --tail 50'`.

MIT licence. Not affiliated with Waze or Google.
