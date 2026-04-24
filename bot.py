import matplotlib
matplotlib.use('Agg')
import asyncio, websockets, json, pandas as pd
from datetime import datetime, timezone, timedelta
import matplotlib.pyplot as plt
import os, io, time
from telegram import Bot
from threading import Thread
from flask import Flask, render_template_string

app = Flask('')
dashboard_data = {"pnl":0.0,"trades":0,"wins":0,"losses":0,"last_scan":"","signals":[]}
HTML="""<html><head><meta http-equiv="refresh" content="15"><style>body{background:#0f172a;color:#e2e8f0;font-family:Arial;padding:20px}.card{background:#1e293b;padding:15px;border-radius:10px;margin-bottom:15px}</style></head><body><h1>🧠 DERIV INTEL</h1><div class=card>PnL ${{"%.2f"|format(data.pnl)}} | Trades {{data.trades}} W{{data.wins}} L{{data.losses}}</div></body></html>"""
@app.route('/')
def home(): return render_template_string(HTML, data=dashboard_data)
Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()

DERIV_TOKEN=os.getenv("DERIV_TOKEN"); TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"); CHAT_ID=os.getenv("CHAT_ID")
SYMBOLS=["R_10","R_25","R_50","R_75","R_100","1HZ10V","1HZ25V","1HZ50V","1HZ75V","1HZ100V","JD10","JD25","JD50","JD75","JD100","BOOM500","BOOM1000","CRASH500","CRASH1000","frxEURUSD","frxGBPUSD","frxUSDJPY","frxAUDUSD","frxUSDCAD","frxUSDCHF","frxEURJPY","frxGBPJPY","frxXAUUSD","frxXAGUSD"]
STAKE=1.0; DAILY_PROFIT_TARGET=5.0; DAILY_LOSS_LIMIT=3.0; MAX_CONSEC_LOSS=3; BOT_NAME="🧠 DERIV INTEL SMC BOT"
bot=Bot(token=TELEGRAM_TOKEN)
daily_pnl=0.0; trades_today=0; wins=0; losses=0; consecutive_losses=0; symbol_cooldown={}; equity_curve=[]; trade_log=[]

async def tg(msg,img=None):
    try:
        txt=f"{BOT_NAME}\n{msg}"
        if img: await bot.send_photo(chat_id=CHAT_ID, photo=img, caption=txt)
        else: await bot.send_message(chat_id=CHAT_ID, text=txt)
    except: pass

def chart_mtf(d1,d5,d15,sym,sig):
    fig,ax=plt.subplots(3,1,figsize=(10,7)); ax[0].plot(d15['close'].tail(50)); ax[0].set_title(f"{sym} 15m"); ax[1].plot(d5['close'].tail(50)); ax[1].set_title("5m"); ax[2].plot(d1['close'].tail(50)); ax[2].set_title(f"1m {sig}"); plt.tight_layout(); b=io.BytesIO(); plt.savefig(b,format='png'); b.seek(0); plt.close(); return b

def img_dashboard():
    fig,ax=plt.subplots(figsize=(7,4)); ax.axis('off'); txt=f"HOURLY\nPnL ${daily_pnl:.2f}\nTrades {trades_today}  WR {(wins/trades_today*100 if trades_today else 0):.1f}%"; ax.text(0.1,0.5,txt,fontsize14,color='white',family='monospace'); fig.patch.set_facecolor('#0f172a'); b=io.BytesIO(); plt.savefig(b,format='png',facecolor=fig.get_facecolor()); b.seek(0); plt.close(); return b

def img_equity():
    fig,ax=plt.subplots(figsize=(10,5)); ax.plot(equity_curve,marker='o',color='#38bdf8'); ax.set_title('Daily Equity Curve',color='white'); ax.set_facecolor('#0f172a'); fig.patch.set_facecolor('#0f172a'); ax.tick_params(colors='white'); ax.grid(True,alpha=0.2)
    for i,v in enumerate(equity_curve): ax.text(i,v,f"{v:.2f}",color='white',fontsize=8)
    b=io.BytesIO(); plt.savefig(b,format='png'); b.seek(0); plt.close(); return b

async def get_candles(s,g):
    uri="wss://ws.derivws.com/websockets/v3?app_id=1089"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"ticks_history":s,"end":"latest","count":100,"style":"candles","granularity":g})); return (json.loads(await ws.recv())).get('candles',[])

def to_df(c):
    df=pd.DataFrame(c); 
    if df.empty: return df
    df['epoch']=pd.to_datetime(df['epoch'],unit='s'); df.set_index('epoch',inplace=True); return df.astype(float)

def bos(df): 
    if len(df)<5: return None
    return 'bull' if df['high'].iloc[-2]>df['high'].iloc[-3] and df['close'].iloc[-1]>df['high'].iloc[-2] else 'bear' if df['low'].iloc[-2]<df['low'].iloc[-3] and df['close'].iloc[-1]<df['low'].iloc[-2] else None
def ob(df):
    for i in range(len(df)-3,1,-1):
        if df['close'][i]<df['open'][i] and df['close'][i+1]>df['high'][i]: return 'bull'
        if df['close'][i]>df['open'][i] and df['close'][i+1]<df['low'][i]: return 'bear'
def fvg(df):
    for i in range(len(df)-1,2,-1):
        if df['low'].iloc[i]>df['high'].iloc[i-2]: return 'bull'
        if df['high'].iloc[i]<df['low'].iloc[i-2]: return 'bear'
def sweep(df): return len(df)>3 and df['low'].iloc[-2]<df['low'].iloc[-3] and df['close'].iloc[-1]>df['low'].iloc[-2]
def intel(df): return {"atr":round((df['high']-df['low']).rolling(14).mean().iloc[-1],4),"trend":'up' if df['close'].iloc[-1]>df['close'].rolling(50).mean().iloc[-1] else 'down'}

async def analyze(sym):
    try:
        c1,c5,c15=await asyncio.gather(get_candles(sym,60),get_candles(sym,300),get_candles(sym,900))
        d1,d5,d15=to_df(c1),to_df(c5),to_df(c15)
        if d1.empty: return None
        b15=bos(d15); i=intel(d1); score=0; direction=None
        if b15=='bull' and i['trend']=='up': direction='BUY'; score+=2
        if b15=='bear' and i['trend']=='down': direction='SELL'; score+=2
        if bos(d5)==direction.lower()[:4] if direction else False: score+=1
        if ob(d1)==direction.lower()[:4] if direction else False: score+=1
        if fvg(d1)==direction.lower()[:4] if direction else False: score+=1
        if sweep(d1): score+=1
        if direction and score>=3: return {"sym":sym,"sig":direction,"score":score,"d1":d1,"d5":d5,"d15":d15,"i":i}
    except: return None

async def place(sym,dir):
    uri="wss://ws.derivws.com/websockets/v3?app_id=1089"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"authorize":DERIV_TOKEN})); await ws.recv()
        await ws.send(json.dumps({"buy":1,"price":STAKE,"parameters":{"amount":STAKE,"basis":"stake","contract_type":"CALL" if dir=="BUY" else "PUT","currency":"USD","duration":2,"duration_unit":"m","symbol":sym}}))
        r=json.loads(await ws.recv()); cid=r.get('buy',{}).get('contract_id')
        if not cid: return 0
        await asyncio.sleep(125); await ws.send(json.dumps({"proposal_open_contract":1,"contract_id":cid})); return float((json.loads(await ws.recv())).get('proposal_open_contract',{}).get('profit',0))

async def hourly():
    while True:
        await asyncio.sleep(3600); await tg(f"Hourly Update\nPnL ${daily_pnl:.2f} | {wins}W-{losses}L", img_dashboard())

async def daily():
    while True:
        now=datetime.now(timezone(timedelta(hours=3)))  # Kenya time
        if now.hour==23 and now.minute==59:
            img=img_equity()
            summary=f"DAILY CLOSE 23:59 EAT\nPnL ${daily_pnl:.2f}\nTrades {trades_today} | Win rate {(wins/trades_today*100 if trades_today else 0):.1f}%\nBest: {max(equity_curve) if equity_curve else 0:.2f} Worst: {min(equity_curve) if equity_curve else 0:.2f}"
            await tg(summary, img)
            await asyncio.sleep(70)
        await asyncio.sleep(30)

async def scanner():
    global daily_pnl,trades_today,wins,losses,consecutive_losses
    await tg("ONLINE - Hourly + Daily reports active")
    asyncio.create_task(hourly()); asyncio.create_task(daily())
    while True:
        try:
            if daily_pnl>=DAILY_PROFIT_TARGET or daily_pnl<=-DAILY_LOSS_LIMIT: await asyncio.sleep(300); continue
            if consecutive_losses>=MAX_CONSEC_LOSS: await tg("Risk pause"); await asyncio.sleep(1800); consecutive_losses=0; continue
            dashboard_data["last_scan"]=datetime.utcnow().strftime("%H:%M")
            for sym in SYMBOLS:
                if time.time()-symbol_cooldown.get(sym,0)<300: continue
                res=await analyze(sym)
                if res and res['score']>=4:
                    symbol_cooldown[sym]=time.time(); trades_today+=1
                    chart=chart_mtf(res['d1'],res['d5'],res['d15'],sym,res['sig'])
                    await tg(f"🚨 TRADE {res['sig']} {sym}\nScore {res['score']}/5", chart)
                    pnl=await place(sym,res['sig']); daily_pnl+=pnl; equity_curve.append(round(daily_pnl,2)); trade_log.append(pnl)
                    if pnl>0: wins+=1; consecutive_losses=0; res_txt="WIN ✅"
                    else: losses+=1; consecutive_losses+=1; res_txt="LOSS ❌"
                    await tg(f"{res_txt} {pnl:+.2f}\nDaily ${daily_pnl:.2f}", chart_mtf(res['d1'],res['d5'],res['d15'],sym,res_txt))
                    dashboard_data.update({"pnl":daily_pnl,"trades":trades_today,"wins":wins,"losses":losses})
                    break
            await asyncio.sleep(30)
        except Exception as e: print(e); await asyncio.sleep(10)

if __name__=="__main__": asyncio.run(scanner())
