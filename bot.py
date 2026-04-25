import asyncio, websockets, json, os, io, time
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from telegram import Bot
from flask import Flask
from threading import Thread

print("[START] Bot initializing...", flush=True)

# --- ENV CHECK ---
TOKEN = os.getenv("DERIV_TOKEN")
TG = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("CHAT_ID")
print(f"[ENV] DERIV_TOKEN={'YES' if TOKEN else 'NO'} TELEGRAM={'YES' if TG else 'NO'} CHAT_ID={'YES' if CHAT else 'NO'}", flush=True)

if not all([TOKEN, TG, CHAT]):
    print("[FATAL] Missing env vars – add them in Render > Environment", flush=True)
    time.sleep(3600)

bot = Bot(token=TG)

# --- FLASK KEEP-ALIVE for Render free ---
app = Flask('')
@app.route('/')
def home(): return "Deriv Intel V4 Running"
def run_flask(): app.run(host='0.0.0.0', port=10000)
Thread(target=run_flask, daemon=True).start()
print("[FLASK] Started on port 10000", flush=True)

# --- TRADING SETUP ---
SYMS = ["R_75", "R_100", "BOOM1000", "CRASH1000", "1HZ75V"]
daily_pnl = 0
trades = 0

async def send(txt, img=None):
    try:
        if img:
            await bot.send_photo(CHAT, photo=img, caption=txt)
        else:
            await bot.send_message(CHAT, txt)
        print(f"[TG] Sent: {txt[:50]}", flush=True)
    except Exception as e:
        print(f"[TG ERROR] {e}", flush=True)

def sma(data, n): return sum(data[-n:])/n if len(data)>=n else data[-1]

async def get_candles(sym):
    try:
        async with websockets.connect("wss://ws.derivws.com/websockets/v3?app_id=1089", ping_interval=20) as ws:
            await ws.send(json.dumps({"ticks_history": sym, "end": "latest", "count": 60, "style": "candles", "granularity": 60}))
            r = json.loads(await ws.recv())
            return r.get('candles', [])
    except Exception as e:
        print(f"[WS ERROR] {e}", flush=True)
        return []

def make_chart(closes, sym):
    fig, ax = plt.subplots(figsize=(5,3))
    ax.plot(closes[-40:])
    ax.set_title(sym)
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    return buf

async def trade(sym, direction, stake=1.0):
    try:
        async with websockets.connect("wss://ws.derivws.com/websockets/v3?app_id=1089") as ws:
            await ws.send(json.dumps({"authorize": TOKEN}))
            await ws.recv()
            await ws.send(json.dumps({
                "buy": 1, "price": stake,
                "parameters": {"amount": stake, "basis": "stake", "contract_type": "CALL" if direction=="BUY" else "PUT",
                               "currency": "USD", "duration": 3, "duration_unit": "m", "symbol": sym}
            }))
            r = json.loads(await ws.recv())
            cid = r.get('buy', {}).get('contract_id')
            if not cid: return 0
            await asyncio.sleep(185)
            await ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid}))
            p = json.loads(await ws.recv())
            return float(p.get('proposal_open_contract', {}).get('profit', 0))
    except Exception as e:
        print(f"[TRADE ERROR] {e}", flush=True)
        return 0

async def main():
    global daily_pnl, trades
    await send("✅ Deriv Intel V4 ONLINE (Render Free)")
    print("[MAIN] Loop started", flush=True)
    
    while True:
        try:
            for sym in SYMS:
                print(f"[SCAN] {sym}", flush=True)
                candles = await get_candles(sym)
                if len(candles) < 30: continue
                closes = [c['close'] for c in candles]
                
                # Simple signal
                if closes[-1] > sma(closes, 20) and closes[-1] > closes[-2]:
                    direction = "BUY"
                elif closes[-1] < sma(closes, 20) and closes[-1] < closes[-2]:
                    direction = "SELL"
                else:
                    continue
                
                chart = make_chart(closes, sym)
                await send(f"🚨 {direction} {sym} @ {closes[-1]:.2f}", chart)
                
                pnl = await trade(sym, direction)
                daily_pnl += pnl
                trades += 1
                await send(f"{'✅ WIN' if pnl>0 else '❌ LOSS'} ${pnl:+.2f} | Daily: ${daily_pnl:.2f} | Trades: {trades}")
                await asyncio.sleep(30)
                break
            await asyncio.sleep(15)
        except Exception as e:
            print(f"[LOOP ERROR] {e}", flush=True)
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
