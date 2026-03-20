import os
import json
import time
import threading
import requests
from flask import Flask, jsonify, request, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='static')

# ─────────────────────────────────────────────
# CONFIG (set via environment variables on Railway)
# ─────────────────────────────────────────────
API_KEY      = os.environ.get('T212_API_KEY', '')
ACCOUNT_TYPE = os.environ.get('T212_ACCOUNT_TYPE', 'demo')  # 'demo' or 'live'

def base_url():
    if ACCOUNT_TYPE == 'live':
        return 'https://live.trading212.com/api/v0'
    return 'https://demo.trading212.com/api/v0'

def t212_headers():
    key = API_KEY or request.headers.get('X-Api-Key', '')
    return {'Authorization': key}

# ─────────────────────────────────────────────
# BOT STATE
# ─────────────────────────────────────────────
bot_state = {
    'running': False,
    'watchlist': ['AAPL', 'TSLA'],
    'price_history': {},   # symbol -> list of prices
    'signals': {},         # symbol -> signal dict
    'positions': [],       # local position tracking
    'closed_trades': [],
    'session_pnl': 0.0,
    'session_trades': 0,
    'session_wins': 0,
    'log': [],
    'settings': {
        'position_size': 50,
        'stop_loss_pct': 2.0,
        'take_profit_pct': 3.0,
        'max_positions': 3,
        'interval_seconds': 60,
    }
}

bot_thread = None
stop_event = threading.Event()

def add_log(msg, level='info'):
    ts = datetime.now().strftime('%H:%M:%S')
    entry = {'ts': ts, 'msg': msg, 'level': level}
    bot_state['log'].insert(0, entry)
    if len(bot_state['log']) > 200:
        bot_state['log'].pop()
    print(f"[{ts}] {msg}")

# ─────────────────────────────────────────────
# T212 API WRAPPERS
# ─────────────────────────────────────────────
def t212_get(path, api_key=None):
    key = api_key or API_KEY
    r = requests.get(
        base_url() + path,
        headers={'Authorization': key},
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def t212_post(path, body, api_key=None):
    key = api_key or API_KEY
    r = requests.post(
        base_url() + path,
        headers={'Authorization': key, 'Content-Type': 'application/json'},
        json=body,
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def get_account_cash(api_key=None):
    return t212_get('/equity/account/cash', api_key)

def get_instruments(api_key=None):
    return t212_get('/equity/metadata/instruments', api_key)

def get_price(symbol, api_key=None):
    instruments = get_instruments(api_key)
    for inst in instruments:
        ticker = inst.get('ticker', '')
        short  = inst.get('shortName', '')
        if ticker == symbol or short == symbol or ticker.startswith(symbol + '_'):
            price = inst.get('currentPrice') or inst.get('buyPrice')
            return float(price) if price else None
    return None

def place_market_order(symbol, qty, api_key=None):
    return t212_post('/equity/orders/market', {
        'ticker': symbol,
        'quantity': qty,
        'timeValidity': 'DAY'
    }, api_key)

# ─────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────
def sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

def ema(data, period):
    if len(data) < period:
        return None
    k = 2 / (period + 1)
    e = sum(data[:period]) / period
    for price in data[period:]:
        e = price * k + e * (1 - k)
    return e

def calc_rsi(data, period=14):
    if len(data) < period + 1:
        return None
    changes = [data[i] - data[i-1] for i in range(1, len(data))]
    recent = changes[-period:]
    gains = sum(c for c in recent if c > 0) / period
    losses = abs(sum(c for c in recent if c < 0)) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))

def calc_macd(data):
    e12 = ema(data, 12)
    e26 = ema(data, 26)
    if e12 is None or e26 is None:
        return None
    return e12 - e26

def calc_bb(data, period=20):
    m = sma(data, period)
    if m is None:
        return None
    sl = data[-period:]
    variance = sum((x - m) ** 2 for x in sl) / period
    std = variance ** 0.5
    return {'upper': m + 2*std, 'middle': m, 'lower': m - 2*std}

def analyse(prices):
    if len(prices) < 20:
        return {'signal': 'WAIT', 'bull': 0, 'bear': 0, 'reasons': ['Need more data']}

    rsi  = calc_rsi(prices)
    macd = calc_macd(prices)
    e9   = ema(prices, 9)
    e21  = ema(prices, 21)
    bb   = calc_bb(prices)
    price = prices[-1]

    bull = 0.0
    bear = 0.0
    reasons = []

    if rsi is not None:
        if rsi < 28:
            bull += 2.5; reasons.append(f'RSI {rsi:.0f} oversold +2.5 🟢')
        elif rsi < 42:
            bull += 1.2; reasons.append(f'RSI {rsi:.0f} low +1.2 🟢')
        elif rsi > 72:
            bear += 2.5; reasons.append(f'RSI {rsi:.0f} overbought +2.5 🔴')
        elif rsi > 58:
            bear += 1.2; reasons.append(f'RSI {rsi:.0f} high +1.2 🔴')
        else:
            reasons.append(f'RSI {rsi:.0f} neutral')

    if macd is not None:
        if macd > 0.1:
            bull += 1.5; reasons.append(f'MACD +{macd:.2f} bullish +1.5 🟢')
        elif macd > 0:
            bull += 0.6; reasons.append(f'MACD slightly positive +0.6 🟢')
        elif macd < -0.1:
            bear += 1.5; reasons.append(f'MACD {macd:.2f} bearish +1.5 🔴')
        else:
            bear += 0.6; reasons.append(f'MACD slightly negative +0.6 🔴')

    if e9 and e21:
        if e9 > e21 * 1.001:
            bull += 1.2; reasons.append('EMA9>EMA21 golden cross +1.2 🟢')
        elif e9 < e21 * 0.999:
            bear += 1.2; reasons.append('EMA9<EMA21 death cross +1.2 🔴')
        else:
            reasons.append('EMAs converging neutral')

    if bb:
        if price < bb['lower']:
            bull += 1.5; reasons.append('Below lower Bollinger +1.5 🟢')
        elif price < bb['middle']:
            bull += 0.5; reasons.append('Below BB midline +0.5 🟢')
        elif price > bb['upper']:
            bear += 1.5; reasons.append('Above upper Bollinger +1.5 🔴')
        else:
            bear += 0.5; reasons.append('Above BB midline +0.5 🔴')

    signal = 'HOLD'
    if bull >= 3.0 and bull > bear + 0.4:
        signal = 'BUY'
    elif bear >= 3.0 and bear > bull + 0.4:
        signal = 'SELL'

    return {
        'signal': signal,
        'bull': round(bull, 2),
        'bear': round(bear, 2),
        'rsi': round(rsi, 1) if rsi else None,
        'macd': round(macd, 3) if macd else None,
        'e9': round(e9, 2) if e9 else None,
        'e21': round(e21, 2) if e21 else None,
        'bb_width': round((bb['upper']-bb['lower'])/bb['middle']*100, 1) if bb else None,
        'bb_pos': 'below_lower' if bb and price < bb['lower'] else ('above_upper' if bb and price > bb['upper'] else 'mid'),
        'reasons': reasons
    }

# ─────────────────────────────────────────────
# TRADE EXECUTION
# ─────────────────────────────────────────────
def execute_buy(symbol, price):
    s = bot_state['settings']
    size    = s['position_size']
    sl_pct  = s['stop_loss_pct'] / 100
    tp_pct  = s['take_profit_pct'] / 100
    max_pos = s['max_positions']

    # Don't double-buy same symbol
    if any(p['symbol'] == symbol for p in bot_state['positions']):
        add_log(f'Already holding {symbol}, skipping', 'warn')
        return

    if len(bot_state['positions']) >= max_pos:
        add_log(f'Max positions ({max_pos}) reached', 'warn')
        return

    qty = round(size / price, 6)
    sl  = round(price * (1 - sl_pct), 2)
    tp  = round(price * (1 + tp_pct), 2)

    order_id = None
    try:
        order = place_market_order(symbol, qty)
        order_id = order.get('id')
        add_log(f'✅ BUY order placed on T212: {qty} {symbol} @ £{price:.2f} (#{order_id})', 'buy')
    except Exception as e:
        add_log(f'⚠ T212 order error: {e} — tracking locally', 'warn')

    bot_state['positions'].append({
        'symbol': symbol,
        'entry': price,
        'qty': qty,
        'sl': sl,
        'tp': tp,
        'size': size,
        'open_time': datetime.now().strftime('%H:%M:%S'),
        't212_id': order_id
    })
    bot_state['session_trades'] += 1
    add_log(f'BUY {qty} {symbol} @ £{price:.2f} | SL £{sl} TP £{tp}', 'buy')

def execute_sell(pos_idx, reason, current_price):
    if pos_idx >= len(bot_state['positions']):
        return
    pos = bot_state['positions'][pos_idx]
    pnl = round((current_price - pos['entry']) * pos['qty'], 2)

    try:
        place_market_order(pos['symbol'], -pos['qty'])
        add_log(f'✅ SELL order placed on T212: {pos["symbol"]}', 'sell')
    except Exception as e:
        add_log(f'⚠ T212 sell error: {e}', 'warn')

    bot_state['session_pnl'] += pnl
    if pnl > 0:
        bot_state['session_wins'] += 1

    bot_state['closed_trades'].insert(0, {
        **pos,
        'exit': current_price,
        'pnl': pnl,
        'reason': reason,
        'close_time': datetime.now().strftime('%H:%M:%S')
    })
    bot_state['positions'].pop(pos_idx)
    add_log(f'{reason} {pos["symbol"]} @ £{current_price:.2f} | P&L: {"+" if pnl>=0 else ""}£{pnl}', 'buy' if pnl >= 0 else 'sell')

def check_stops():
    for i in range(len(bot_state['positions']) - 1, -1, -1):
        pos = bot_state['positions'][i]
        sym = pos['symbol']
        prices = bot_state['price_history'].get(sym, [])
        if not prices:
            continue
        p = prices[-1]
        if p <= pos['sl']:
            add_log(f'⛔ Stop Loss hit: {sym} @ £{p:.2f}', 'warn')
            execute_sell(i, 'SL', p)
        elif p >= pos['tp']:
            add_log(f'✅ Take Profit hit: {sym} @ £{p:.2f}', 'buy')
            execute_sell(i, 'TP', p)

# ─────────────────────────────────────────────
# BOT LOOP (runs in background thread)
# ─────────────────────────────────────────────
def bot_loop():
    add_log('Bot loop started', 'info')
    while not stop_event.is_set():
        if bot_state['running']:
            try:
                run_scan()
            except Exception as e:
                add_log(f'Bot error: {e}', 'warn')

        interval = bot_state['settings']['interval_seconds']
        stop_event.wait(timeout=interval)

    add_log('Bot loop stopped', 'info')

def run_scan():
    add_log(f'Scanning {len(bot_state["watchlist"])} symbol(s)…')
    for symbol in bot_state['watchlist']:
        try:
            price = get_price(symbol)
            if not price:
                add_log(f'No price for {symbol}', 'warn')
                continue

            if symbol not in bot_state['price_history']:
                bot_state['price_history'][symbol] = []
            bot_state['price_history'][symbol].append(price)
            if len(bot_state['price_history'][symbol]) > 300:
                bot_state['price_history'][symbol].pop(0)

            prices = bot_state['price_history'][symbol]
            a = analyse(prices)
            bot_state['signals'][symbol] = {**a, 'price': price}

            add_log(f'{symbol}: £{price:.2f} | Bull {a["bull"]} Bear {a["bear"]} → {a["signal"]}')

            if a['signal'] == 'BUY':
                execute_buy(symbol, price)
            elif a['signal'] == 'SELL':
                for i in range(len(bot_state['positions']) - 1, -1, -1):
                    if bot_state['positions'][i]['symbol'] == symbol:
                        execute_sell(i, 'SELL', price)

            time.sleep(0.5)  # rate limit between symbols

        except Exception as e:
            add_log(f'{symbol} error: {e}', 'warn')

    check_stops()

# ─────────────────────────────────────────────
# API ROUTES (called by the frontend)
# ─────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/test-connection', methods=['POST'])
def test_connection():
    data = request.json or {}
    key  = data.get('apiKey', API_KEY)
    acct = data.get('accountType', ACCOUNT_TYPE)
    global ACCOUNT_TYPE
    ACCOUNT_TYPE = acct
    try:
        r = requests.get(
            ('https://demo.trading212.com' if acct=='demo' else 'https://live.trading212.com') + '/api/v0/equity/account/cash',
            headers={'Authorization': key}, timeout=10
        )
        r.raise_for_status()
        cash = r.json()
        bal = cash.get('free', cash.get('total', 0))
        return jsonify({'ok': True, 'balance': bal, 'message': f'Connected! Free cash: £{bal:.2f}'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 400

@app.route('/api/start', methods=['POST'])
def start_bot():
    global bot_thread
    data = request.json or {}

    key  = data.get('apiKey', '')
    acct = data.get('accountType', 'demo')
    if key:
        global API_KEY, ACCOUNT_TYPE
        API_KEY = key
        ACCOUNT_TYPE = acct

    bot_state['settings'].update({
        'position_size':    float(data.get('positionSize', 50)),
        'stop_loss_pct':    float(data.get('stopLoss', 2)),
        'take_profit_pct':  float(data.get('takeProfit', 3)),
        'max_positions':    int(data.get('maxPositions', 3)),
        'interval_seconds': int(data.get('interval', 60)),
    })
    if data.get('watchlist'):
        bot_state['watchlist'] = data['watchlist']

    bot_state['running'] = True
    stop_event.clear()

    if bot_thread is None or not bot_thread.is_alive():
        bot_thread = threading.Thread(target=bot_loop, daemon=True)
        bot_thread.start()

    add_log(f'▶ Bot started ({acct.upper()}) | {", ".join(bot_state["watchlist"])}', 'buy')
    return jsonify({'ok': True})

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    bot_state['running'] = False
    add_log('■ Bot stopped', 'warn')
    return jsonify({'ok': True})

@app.route('/api/state', methods=['GET'])
def get_state():
    return jsonify({
        'running':       bot_state['running'],
        'watchlist':     bot_state['watchlist'],
        'signals':       bot_state['signals'],
        'positions':     bot_state['positions'],
        'closed_trades': bot_state['closed_trades'][:30],
        'session_pnl':   bot_state['session_pnl'],
        'session_trades':bot_state['session_trades'],
        'session_wins':  bot_state['session_wins'],
        'log':           bot_state['log'][:50],
        'price_history': {k: v[-80:] for k, v in bot_state['price_history'].items()},
    })

@app.route('/api/close-position', methods=['POST'])
def close_position():
    data = request.json or {}
    idx  = data.get('index', 0)
    if idx < len(bot_state['positions']):
        pos = bot_state['positions'][idx]
        sym = pos['symbol']
        prices = bot_state['price_history'].get(sym, [])
        price  = prices[-1] if prices else pos['entry']
        execute_sell(idx, 'SELL', price)
    return jsonify({'ok': True})

@app.route('/api/watchlist', methods=['POST'])
def update_watchlist():
    data = request.json or {}
    action = data.get('action')
    symbol = data.get('symbol', '').upper().strip()
    if action == 'add' and symbol:
        if symbol not in bot_state['watchlist']:
            bot_state['watchlist'].append(symbol)
            bot_state['price_history'][symbol] = []
            add_log(f'Added {symbol} to watchlist')
    elif action == 'remove' and symbol:
        if symbol in bot_state['watchlist']:
            bot_state['watchlist'].remove(symbol)
            add_log(f'Removed {symbol} from watchlist', 'warn')
    return jsonify({'ok': True, 'watchlist': bot_state['watchlist']})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    data = request.json or {}
    bot_state['settings'].update(data)
    return jsonify({'ok': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Start bot thread immediately (it waits until bot_state['running'] = True)
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()
    app.run(host='0.0.0.0', port=port, debug=False)
