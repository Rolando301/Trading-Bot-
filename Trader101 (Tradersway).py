import time
import csv
from datetime import datetime
import numpy as np
import MetaTrader5 as mt5

# ---------------------------
# User configuration
# ---------------------------
MT5_LOGIN = 12345678        # replace or set USE_LOGIN_PARAMS=False to rely on logged-in terminal
MT5_PASSWORD = "password"
MT5_SERVER = "TradersWay-Demo"
USE_LOGIN_PARAMS = False
TRADE_SYMBOL = None         # example: "BTCUSD_i" or "BTCUSD" — set to None to input at runtime
TIMEFRAME_MAP = {
    "1m": mt5.TIMEFRAME_M1,
    "5m": mt5.TIMEFRAME_M5,
    "15m": mt5.TIMEFRAME_M15,
    "1h": mt5.TIMEFRAME_H1,
    "4h": mt5.TIMEFRAME_H4
}
TRADE_LOG_FILE = "mt5_trades_log.csv"

# Risk / sizing
RISK_PERCENT = 1.00   # used only when USE_RISK_SIZE = True
USE_RISK_SIZE = False
FIXED_LOT_SIZE = 1.00

# Order parameters
DEVIATION = 500  # increased default deviation (points) — adjust for your broker/instrument
MAGIC = 234000

# Defaults will be overridden by symbol_info where possible
MIN_LOT = 1.00
MAX_LOT = 10.00

# Minimum SL/TP distance in POINTS (will be compared to symbol.point)
MIN_DISTANCE_POINTS = 10  # 10 * point (will be multiplied by symbol.point)

# ---------------------------
# Helper functions
# ---------------------------
def init_mt5():
    if USE_LOGIN_PARAMS:
        ok = mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    else:
        ok = mt5.initialize()
    if not ok:
        raise RuntimeError(f"mt5.initialize() failed, error = {mt5.last_error()}")
    print("✅ MT5 initialized")

def shutdown_mt5():
    mt5.shutdown()
    print("ℹ MT5 shutdown")

def print_symbol_info(symbol):
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    print("=== SYMBOL INFO ===")
    print("symbol:", symbol)
    print("symbol_info:", info)
    print("symbol_tick:", tick)
    print("===================")

def get_candles(symbol, timeframe_str="1m", limit=200):
    timeframe = TIMEFRAME_MAP.get(timeframe_str, mt5.TIMEFRAME_M1)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, limit)
    if rates is None or len(rates) == 0:
        print(f"⚠ No rates for {symbol} / {timeframe_str}. mt5.last_error(): {mt5.last_error()}")
        return None
    candles = []
    for r in rates:
        candles.append({
            "time": datetime.fromtimestamp(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4])
        })
    return candles

def detect_zones(candles):
    highs = np.array([c["high"] for c in candles])
    lows = np.array([c["low"] for c in candles])
    supply_zone = float(np.max(highs))
    demand_zone = float(np.min(lows))
    return supply_zone, demand_zone

def log_trade(symbol, direction, entry, exit_price, profit_loss):
    with open(TRADE_LOG_FILE, mode="a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            direction,
            f"{entry:.8f}",
            f"{exit_price:.8f}",
            f"{profit_loss:.8f}"
        ])

def clamp_and_round_lot(symbol, lot):
    info = mt5.symbol_info(symbol)
    if info is None:
        return round(lot, 2)
    vol_min = info.volume_min if info.volume_min else MIN_LOT
    vol_max = info.volume_max if info.volume_max else MAX_LOT
    step = info.volume_step if info.volume_step and info.volume_step > 0 else 0.01
    lot = max(vol_min, min(vol_max, lot))
    # round to nearest step
    rounded = round(lot / step) * step
    # clamp again to avoid floating rounding pushing it over limits
    rounded = max(vol_min, min(vol_max, rounded))
    return rounded

def calc_lot_size(symbol, entry, stop_loss):
    """Simple risk-based lot calculation.
       If USE_RISK_SIZE is False, return fixed lot (rounded to symbol step).
    """
    if not USE_RISK_SIZE:
        return clamp_and_round_lot(symbol, FIXED_LOT_SIZE)

    account_info = mt5.account_info()
    if account_info is None:
        print("⚠ Could not fetch account info for sizing, using fixed lot.")
        return clamp_and_round_lot(symbol, FIXED_LOT_SIZE)

    balance = account_info.balance
    risk_money = balance * RISK_PERCENT

    info = mt5.symbol_info(symbol)
    if info is None:
        print("⚠ Could not fetch symbol info for sizing, using fixed lot.")
        return clamp_and_round_lot(symbol, FIXED_LOT_SIZE)

    contract_size = info.trade_contract_size if info.trade_contract_size else 1.0
    distance = abs(entry - stop_loss)
    if distance == 0:
        return clamp_and_round_lot(symbol, FIXED_LOT_SIZE)

    # Simplified calculation (approximate). Validate on demo.
    lot = risk_money / (distance * contract_size)
    return clamp_and_round_lot(symbol, lot)

def place_order(symbol, direction, volume, sl, tp):
    """
    Place a market order using current tick price (ask/bid).
    direction: "BUY" or "SELL"
    volume: float lots (already rounded)
    sl, tp: prices (floats) or None
    """
    symbol_tick = mt5.symbol_info_tick(symbol)
    if symbol_tick is None:
        print(f"⚠ Could not get tick for {symbol}")
        return None

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    order_price = symbol_tick.ask if order_type == mt5.ORDER_TYPE_BUY else symbol_tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": order_price,
        "sl": float(sl) if sl is not None else 0.0,
        "tp": float(tp) if tp is not None else 0.0,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": "SupplyDemandBot",
        "type_filling": mt5.ORDER_FILLING_IOC
    }
    result = mt5.order_send(request)
    # detailed debug printing
    print("---- order_send debug ----")
    print("requested:", request)
    print("result:", result)
    if result is not None:
        try:
            print("retcode:", result.retcode)
            print("comment:", result.comment)
            # retcode description is not always textual; show dict if available
            if hasattr(result, "request"):
                print("server_request:", result.request)
        except Exception:
            pass
    print("--------------------------")
    return result

# ---------------------------
# Bot main loop
# ---------------------------
def trading_bot():
    try:
        init_mt5()
    except RuntimeError as e:
        print("Initialization error:", e)
        return

    symbol = TRADE_SYMBOL or input("Enter market symbol (as in MT5 Market Watch, e.g., BTCUSD_i): ").strip()
    interval = input("Enter candle interval (1m, 5m, 15m, 1h, 4h) [default 1m]: ").strip() or "1m"

    # ensure symbol is available and get info
    if not mt5.symbol_select(symbol, True):
        print(f"⚠ Failed to select symbol {symbol}. Check symbol name with your broker.")
        shutdown_mt5()
        return

    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"⚠ symbol_info({symbol}) returned None. Can't trade.")
        shutdown_mt5()
        return

    # derive symbol parameters
    point = info.point if info.point else 10**-info.digits if info.digits else 0.0001
    min_distance = max(MIN_DISTANCE_POINTS * point, point)  # minimum price distance for SL/TP
    global MIN_LOT, MAX_LOT
    MIN_LOT = info.volume_min if info.volume_min else MIN_LOT
    MAX_LOT = info.volume_max if info.volume_max else MAX_LOT

    print_symbol_info(symbol)
    print(f"Using point={point}, min_distance={min_distance}, volume_step={info.volume_step}, volume_min={MIN_LOT}, volume_max={MAX_LOT}")

    print(f"\n✨ Starting MT5 bot for {symbol} on {interval} timeframe...\n")

    in_trade = False
    direction = None
    entry = None
    take_profit = None
    stop_loss = None
    position_ticket = None

    try:
        while True:
            candles = get_candles(symbol, interval, limit=200)
            if not candles:
                time.sleep(3)
                continue

            supply, demand = detect_zones(candles)
            candle_close = candles[-1]["close"]

            # For prints, show candle close, but use tick price for execution
            print(f"{datetime.now().strftime('%H:%M:%S')} | CandleClose: {candle_close:.6f} | Supply: {supply:.6f} | Demand: {demand:.6f}")

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                print("⚠ No tick info right now.")
                time.sleep(1)
                continue
            bid = tick.bid
            ask = tick.ask
            last = tick.last if hasattr(tick, "last") else (ask if ask else bid)

            # ENTRY logic uses candle-based zone detection but executes at tick price instantly
            if not in_trade:
                # BUY condition (candle close <= demand)
                if candle_close <= demand:
                    # compute entry price from tick
                    direction = "BUY"
                    entry_price = ask  # immediate market entry price
                    # prefer tp/sl distances based on supply/demand width but anchored to executed entry price
                    raw_tp = entry_price + (supply - demand) * 0.5
                    raw_sl = entry_price - (supply - demand) * 0.3
                    # ensure min distance
                    if abs(raw_tp - entry_price) < min_distance:
                        raw_tp = entry_price + min_distance
                    if abs(entry_price - raw_sl) < min_distance:
                        raw_sl = entry_price - min_distance
                    lot = calc_lot_size(symbol, entry_price, raw_sl)
                    lot = clamp_and_round_lot(symbol, lot)
                    print(f"Attempting BUY -> tick.ask={ask:.6f}, entry={entry_price:.6f}, tp={raw_tp:.6f}, sl={raw_sl:.6f}, lot={lot}")
                    res = place_order(symbol, direction, lot, raw_sl, raw_tp)
                    if res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                        entry = entry_price
                        take_profit = raw_tp
                        stop_loss = raw_sl
                        in_trade = True
                        print(f"🟢 BUY ORDER placed (ticket info in result). entry used: {entry:.6f}")
                    else:
                        print("⚠ BUY order failed or was rejected. See above debug for retcode/comment.")
                # SELL condition
                elif candle_close >= supply:
                    direction = "SELL"
                    entry_price = bid
                    raw_tp = entry_price - (supply - demand) * 0.5
                    raw_sl = entry_price + (supply - demand) * 0.3
                    if abs(entry_price - raw_tp) < min_distance:
                        raw_tp = entry_price - min_distance
                    if abs(raw_sl - entry_price) < min_distance:
                        raw_sl = entry_price + min_distance
                    lot = calc_lot_size(symbol, entry_price, raw_sl)
                    lot = clamp_and_round_lot(symbol, lot)
                    print(f"Attempting SELL -> tick.bid={bid:.6f}, entry={entry_price:.6f}, tp={raw_tp:.6f}, sl={raw_sl:.6f}, lot={lot}")
                    res = place_order(symbol, direction, lot, raw_sl, raw_tp)
                    if res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                        entry = entry_price
                        take_profit = raw_tp
                        stop_loss = raw_sl
                        in_trade = True
                        print(f"🔴 SELL ORDER placed (ticket info in result). entry used: {entry:.6f}")
                    else:
                        print("⚠ SELL order failed or was rejected. See above debug for retcode/comment.")
            else:
                # monitor positions for our magic and symbol
                positions = mt5.positions_get(symbol=symbol)
                my_pos = None
                if positions:
                    for p in positions:
                        # try to match by magic and comment too
                        try:
                            if p.magic == MAGIC and p.comment == "SupplyDemandBot":
                                my_pos = p
                                break
                        except Exception:
                            # fallback if magic isn't present
                            my_pos = p
                            break

                if my_pos is None:
                    # position not found in MT5 -> probably closed by broker or manually
                    print("ℹ Position not found in MT5 positions (closed?). Logging and resetting.")
                    if entry is not None:
                        pnl = (last - entry) if direction == "BUY" else (entry - last)
                        log_trade(symbol, direction, entry, last, pnl)
                    in_trade = False
                    direction = None
                    entry = None
                    take_profit = None
                    stop_loss = None
                else:
                    # Use latest tick to check if TP/SL reached (server often closes automatically)
                    price_now = ask if direction == "BUY" else bid
                    if direction == "BUY":
                        if price_now >= take_profit or price_now <= stop_loss:
                            pnl = (price_now - entry) * my_pos.volume * (my_pos.price_open if my_pos.price_open else 1)
                            print(f"🔔 BUY exit triggered by price_now={price_now:.6f}. Logging pnl approx: {pnl}")
                            log_trade(symbol, direction, entry, price_now, pnl)
                            in_trade = False
                            direction = None
                            entry = None
                    else:  # SELL
                        if price_now <= take_profit or price_now >= stop_loss:
                            pnl = (entry - price_now) * my_pos.volume * (my_pos.price_open if my_pos.price_open else 1)
                            print(f"🔔 SELL exit triggered by price_now={price_now:.6f}. Logging pnl approx: {pnl}")
                            log_trade(symbol, direction, entry, price_now, pnl)
                            in_trade = False
                            direction = None
                            entry = None

            time.sleep(1)

    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        shutdown_mt5()