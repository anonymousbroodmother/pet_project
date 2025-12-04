import requests
from flask import Flask, render_template, request
import sqlite3
from datetime import datetime, timedelta
import matplotlib

matplotlib.use('Agg')  # Важно для сервера без GUI
import matplotlib.pyplot as plt
from io import BytesIO
import base64

app = Flask(__name__)
OPEN_DOTA_API = "https://api.opendota.com/api"

# === Загрузка героев при старте ===
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
except Exception as e:
    print(f"Ошибка загрузки героев: {e}")


# === База данных ===
def init_db():
    conn = sqlite3.connect('search_history.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            winrate REAL
        )
    ''')
    conn.commit()
    conn.close()


def save_search(steam_id, winrate):
    try:
        conn = sqlite3.connect('search_history.db')
        c = conn.cursor()
        today = datetime.utcnow().date().isoformat()
        c.execute('SELECT 1 FROM search_history WHERE steam_id = ? AND timestamp LIKE ?', (steam_id, f"{today}%"))
        if not c.fetchone():
            c.execute('''
                INSERT INTO search_history (steam_id, timestamp, winrate)
                VALUES (?, ?, ?)
            ''', (steam_id, datetime.utcnow().isoformat(), winrate))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка сохранения: {e}")


def get_recent_searches(limit=5):
    try:
        conn = sqlite3.connect('search_history.db')
        c = conn.cursor()
        c.execute('''
            SELECT steam_id, timestamp, winrate
            FROM search_history
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        return c.fetchall()
    except Exception as e:
        print(f"Ошибка чтения: {e}")
        return []


# === График winrate за 30 дней ===
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
        print(f"Ошибка обработки матчей для графика: {e}")
        return None, None


def generate_winrate_chart(dates, winrates):
    try:
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
    except Exception as e:
        print(f"Ошибка генерации графика: {e}")
        return None


# === Главная страница ===
@app.route('/', methods=['GET', 'POST'])
def index():
    init_db()
    recent_searches = get_recent_searches(5)

    player_name = "Неизвестен"
    player_avatar = "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/avatars/fe/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg"
    wins_total = 0
    losses_total = 0
    winrate_total = 0
    winrate = 0
    top_heroes = []
    chart_url = None
    error = None
    steam_id = None
    profile_private = False

    if request.method == 'POST':
        raw_steam_id = request.form.get('steam_id', '').strip()
        if not raw_steam_id.isdigit():
            error = "Steam ID должен содержать только цифры"
        else:
            steam_id = raw_steam_id
            try:
                profile_resp = requests.get(f"{OPEN_DOTA_API}/players/{steam_id}", timeout=10)
                if profile_resp.status_code != 200:
                    error = "Игрок не найден. Проверьте Steam ID."
                else:
                    player = profile_resp.json()

                    profile_data = player.get('profile', {})
                    player_name = profile_data.get('personaname', 'Аноним')
                    player_avatar = profile_data.get('avatarfull', player_avatar)

                    wins_total = player.get('win', 0)
                    losses_total = player.get('lose', 0)
                    total_games = wins_total + losses_total

                    if total_games == 0 and player.get('games', 0) > 0:
                        profile_private = True
                        error = "⚠️ Профиль приватный — данные о победах и поражениях недоступны"
                    elif total_games == 0:
                        error = "Игрок не имеет рейтинговых матчей или профиль пустой"

                    winrate_total = round(wins_total / total_games * 100, 1) if total_games > 0 else 0

                    if not profile_private:
                        matches_resp = requests.get(f"{OPEN_DOTA_API}/players/{steam_id}/matches?limit=20", timeout=10)
                        if matches_resp.status_code == 200:
                            matches = matches_resp.json()
                            wins = sum(1 for m in matches if m.get('radiant_win') == (m.get('player_slot', 0) < 128))
                            winrate = round(wins / len(matches) * 100, 1) if matches else 0

                        heroes_resp = requests.get(f"{OPEN_DOTA_API}/players/{steam_id}/heroes", timeout=10)
                        if heroes_resp.status_code == 200:
                            hero_data = heroes_resp.json()
                            top_heroes = sorted(hero_data, key=lambda h: int(h['games']), reverse=True)[:3]

                        dates, winrates = get_daily_winrate(steam_id)
                        if dates and winrates:
                            chart_url = generate_winrate_chart(dates, winrates)

                        save_search(steam_id, winrate)

            except Exception as e:
                error = f"Ошибка: {str(e)}"

    return render_template(
        'index.html',
        player_name=player_name,
        player_avatar=player_avatar,
        wins_total=wins_total,
        losses_total=losses_total,
        winrate_total=winrate_total,
        winrate=winrate,
        top_heroes=top_heroes,
        chart_url=chart_url,
        recent_searches=recent_searches,
        HEROES=HEROES_CACHE,
        error=error,
        steam_id=steam_id,
        profile_private=profile_private
    )


# === Сравнение игроков ===
@app.route('/compare', methods=['GET', 'POST'])
def compare_players():
    init_db()
    player1 = None
    player2 = None
    error = None

    if request.method == 'POST':
        steam_id1 = request.form.get('steam_id1', '').strip()
        steam_id2 = request.form.get('steam_id2', '').strip()

        if not steam_id1.isdigit() or not steam_id2.isdigit():
            error = "Оба Steam ID должны содержать только цифры"
        elif steam_id1 == steam_id2:
            error = "Введите разные Steam ID"
        else:
            try:
                def fetch_player_data(sid):
                    profile_resp = requests.get(f"{OPEN_DOTA_API}/players/{sid}", timeout=10)
                    if profile_resp.status_code != 200:
                        return None
                    profile = profile_resp.json()

                    profile_data = profile.get('profile', {})
                    name = profile_data.get('personaname', 'Аноним')
                    avatar = profile_data.get('avatarfull', '')
                    wins_total = profile.get('win', 0)
                    losses_total = profile.get('lose', 0)
                    total_games = wins_total + losses_total
                    winrate_total = round(wins_total / total_games * 100, 1) if total_games > 0 else 0

                    matches_resp = requests.get(f"{OPEN_DOTA_API}/players/{sid}/matches?limit=20", timeout=10)
                    winrate = 0
                    if matches_resp.status_code == 200:
                        matches = matches_resp.json()
                        wins = sum(1 for m in matches if m.get('radiant_win') == (m.get('player_slot', 0) < 128))
                        winrate = round(wins / len(matches) * 100, 1) if matches else 0

                    heroes_resp = requests.get(f"{OPEN_DOTA_API}/players/{sid}/heroes", timeout=10)
                    top_hero = None
                    if heroes_resp.status_code == 200:
                        hero_data = heroes_resp.json()
                        top_list = sorted(hero_data, key=lambda h: int(h['games']), reverse=True)[:1]
                        top_hero = top_list[0] if top_list else None

                    return {
                        'steam_id': sid,
                        'name': name,
                        'avatar': avatar,
                        'wins_total': wins_total,
                        'losses_total': losses_total,
                        'winrate_total': winrate_total,
                        'winrate': winrate,
                        'top_hero': top_hero
                    }

                p1 = fetch_player_data(steam_id1)
                p2 = fetch_player_data(steam_id2)

                if not p1 or not p2:
                    error = "Не удалось загрузить данные одного из игроков"
                else:
                    player1 = p1
                    player2 = p2

            except Exception as e:
                error = f"Ошибка: {str(e)}"

    return render_template(
        'compare.html',
        player1=player1,
        player2=player2,
        error=error,
        HEROES=HEROES_CACHE
    )


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)