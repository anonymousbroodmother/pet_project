import os
import requests
from flask import Flask, render_template, request
import sqlite3
from datetime import datetime, timedelta
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
import base64

app = Flask(__name__)
OPEN_DOTA_API = "https://api.opendota.com/api"


HEROES_CACHE = {}
try:
    heroes_resp = requests.get(f"{OPEN_DOTA_API}/heroes", timeout=10)
    if heroes_resp.status_code == 200:
        heroes_list = heroes_resp.json()
        for h in heroes_list:
            hero_name_clean = h['name'].replace('npc_dota_hero_', '')
            icon_url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/heroes/{hero_name_clean}_icon.png"
            HEROES_CACHE[h['id']] = {
                'name': h['localized_name'],
                'icon': icon_url
            }
    else:
        print("⚠️ Не удалось загрузить героев")
except Exception as e:
    print(f"Ошибка загрузки героев: {e}")



def init_db():
    conn = sqlite3.connect('search_history.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            mmr INTEGER,
            winrate REAL
        )
    ''')
    conn.commit()
    conn.close()


def save_search(steam_id, mmr, winrate):
    try:
        conn = sqlite3.connect('search_history.db')
        c = conn.cursor()
        today = datetime.utcnow().date().isoformat()

        c.execute('SELECT 1 FROM search_history WHERE steam_id = ? AND timestamp LIKE ?', (steam_id, f"{today}%"))
        if not c.fetchone():
            c.execute('''
                INSERT INTO search_history (steam_id, timestamp, mmr, winrate)
                VALUES (?, ?, ?, ?)
            ''', (steam_id, datetime.utcnow().isoformat(), mmr, winrate))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка сохранения в БД: {e}")


def get_recent_searches(limit=5):
    try:
        conn = sqlite3.connect('search_history.db')
        c = conn.cursor()
        c.execute('''
            SELECT steam_id, timestamp, mmr, winrate
            FROM search_history
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        rows = c.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"Ошибка чтения истории: {e}")
        return []



def get_daily_winrate(steam_id):
    try:
        resp = requests.get(f"{OPEN_DOTA_API}/players/{steam_id}/matches?limit=500", timeout=10)
        if resp.status_code != 200:
            return None, None

        matches = resp.json()
        today = datetime.utcnow().date()
        date_stats = {}


        for i in range(30):
            day = today - timedelta(days=i)
            date_stats[day.isoformat()] = {"games": 0, "wins": 0}


        for m in matches:
            start_time = m.get('start_time')
            if not start_time:
                continue
            match_date = datetime.utcfromtimestamp(start_time).date()
            if (today - match_date).days > 30 or match_date > today:
                continue

            date_key = match_date.isoformat()
            if date_key not in date_stats:
                continue

            player_slot = m.get('player_slot', 0)
            radiant_win = m.get('radiant_win', False)
            is_radiant = player_slot < 128
            win = (radiant_win and is_radiant) or (not radiant_win and not is_radiant)

            date_stats[date_key]["games"] += 1
            if win:
                date_stats[date_key]["wins"] += 1


        dates = []
        winrates = []
        for i in range(29, -1, -1):
            date = (today - timedelta(days=i)).isoformat()
            data = date_stats[date]
            wr = round(data["wins"] / data["games"] * 100, 1) if data["games"] > 0 else 0
            dates.append(date[5:])  # MM-DD
            winrates.append(wr)

        return dates, winrates
    except Exception as e:
        print(f"Ошибка получения данных для графика: {e}")
        return None, None


def generate_winrate_chart(dates, winrates):
    plt.style.use('seaborn-v0_8-darkgrid')
    plt.figure(figsize=(10, 4))
    plt.plot(dates, winrates, marker='o', color='#27ae60', linewidth=2, markersize=4)
    plt.title("Winrate за последние 30 дней", fontsize=14)
    plt.xlabel("Дата (ММ-ДД)")
    plt.ylabel("Winrate (%)")
    plt.ylim(0, 100)
    plt.xticks(rotation=45, fontsize=9)
    plt.yticks(fontsize=9)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    plt.close()
    return img_base64



@app.route('/', methods=['GET', 'POST'])
def index():
    init_db()
    recent_searches = get_recent_searches(5)
    mmr = "N/A"
    winrate = 0
    top_heroes = []
    chart_url = None
    error = None

    if request.method == 'POST':
        raw_steam_id = request.form.get('steam_id', '').strip()
        if not raw_steam_id.isdigit():
            error = "Steam ID должен содержать только цифры (например: 76561198012345678)"
        else:
            steam_id = raw_steam_id
            try:

                profile_resp = requests.get(f"{OPEN_DOTA_API}/players/{steam_id}", timeout=10)
                if profile_resp.status_code != 200:
                    error = "Игрок не найден. Убедитесь, что Steam ID правильный и профиль публичный."
                else:
                    player = profile_resp.json()


                    matches_resp = requests.get(f"{OPEN_DOTA_API}/players/{steam_id}/matches?limit=20", timeout=10)
                    matches = matches_resp.json() if matches_resp.status_code == 200 else []
                    wins = sum(
                        1 for m in matches
                        if m.get('radiant_win') == (m.get('player_slot', 0) < 128)
                    )
                    winrate = round(wins / len(matches) * 100, 1) if matches else 0


                    mmr_val = player.get('solo_competitive_rank') or player.get('competitive_rank')
                    mmr = mmr_val if mmr_val else "N/A"


                    heroes_resp = requests.get(f"{OPEN_DOTA_API}/players/{steam_id}/heroes", timeout=10)
                    if heroes_resp.status_code == 200:
                        hero_data = heroes_resp.json()
                        top_heroes = sorted(hero_data, key=lambda h: int(h['games']), reverse=True)[:3]


                    dates, winrates = get_daily_winrate(steam_id)
                    if dates and winrates:
                        chart_url = generate_winrate_chart(dates, winrates)


                    save_search(steam_id, mmr_val, winrate)

            except requests.exceptions.Timeout:
                error = "Тайм-аут при запросе к OpenDota API. Попробуйте позже."
            except requests.exceptions.RequestException as e:
                error = f"Ошибка сети: {str(e)}"
            except Exception as e:
                error = f"Неизвестная ошибка: {str(e)}"

    return render_template(
        'index.html',
        mmr=mmr,
        winrate=winrate,
        top_heroes=top_heroes,
        chart_url=chart_url,
        recent_searches=recent_searches,
        HEROES=HEROES_CACHE,
        error=error
    )



if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)