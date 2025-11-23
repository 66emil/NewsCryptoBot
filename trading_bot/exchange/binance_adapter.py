import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

from trading_bot.config import get_config
from trading_bot.exchange.base import ExchangeAdapter

logger = logging.getLogger(__name__)


class BinanceExchange(ExchangeAdapter):
    """
    Адаптер Binance Futures (USDT-M).
    """

    name = "binance"

    def __init__(self) -> None:
        config = get_config()
        self._base_url = config.BINANCE_BASE_URL.rstrip("/")
        self._api_key = config.BINANCE_API_KEY
        self._api_secret = config.BINANCE_API_SECRET

    def _sign(self, params: Dict[str, Any]) -> str:
        query_string = urlencode(params)
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        url = f"{self._base_url}{path}"
        params = params or {}
        headers = {
            "Content-Type": "application/json",
        }

        if signed:
            if not self._api_key or not self._api_secret:
                logger.error("Binance: API key/secret not set for private request")
                return None
            
            headers["X-MBX-APIKEY"] = self._api_key
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    timeout=10
                ) as resp:
                    try:
                        data = await resp.json()
                    except Exception:
                        text = await resp.text()
                        logger.error("Binance: failed to parse JSON: %s", text)
                        return None
                    
                    if resp.status >= 400:
                        logger.error("Binance API error (%s): %s", resp.status, data)
                        return None
                    
                    return data
            except Exception as e:
                logger.error("Binance request error: %s", e)
                return None

    # ---- Реализация интерфейса ----

    async def has_market(self, symbol: str) -> bool:
        # GET /fapi/v1/exchangeInfo
        data = await self._request("GET", "/fapi/v1/exchangeInfo")
        if not data or "symbols" not in data:
            return False
        
        for s in data["symbols"]:
            if s["symbol"] == symbol and s["status"] == "TRADING":
                return True
        return False

    async def get_klines(self, symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
        # Binance kline intervals: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
        # Map timeframe if needed. Assuming standard format like '15m', '1h'.
        # If timeframe is just a number (minutes), map it.
        interval = str(timeframe)
        if interval.isdigit():
            tf_map = {
                "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
                "60": "1h", "120": "2h", "240": "4h", "360": "6h", "480": "8h",
                "720": "12h", "1440": "1d"
            }
            interval = tf_map.get(interval, "1h")
        
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        
        data = await self._request("GET", "/fapi/v1/klines", params=params)
        if not isinstance(data, list):
            return []
        
        # [Open time, Open, High, Low, Close, Volume, ...]
        result = []
        for candle in data:
            try:
                result.append({
                    "openTime": int(candle[0]),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]),
                })
            except (IndexError, ValueError):
                continue
        return result

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        params = {"symbol": symbol}
        data = await self._request("GET", "/fapi/v1/ticker/24hr", params=params)
        if not data or "lastPrice" not in data:
            return {}
        
        return {
            "lastPrice": float(data.get("lastPrice", 0.0)),
            "volume": float(data.get("volume", 0.0)),
            # Binance doesn't always return OI in 24hr ticker, need separate call usually,
            # but interface only asks for Dict.
        }

    async def get_open_interest(self, symbol: str, timeframe: str, limit: int = 50) -> List[Dict[str, Any]]:
        # GET /fapi/v1/openInterestHist
        # period: "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"
        interval = str(timeframe)
        if interval.isdigit():
             tf_map = {
                "5": "5m", "15": "15m", "30": "30m",
                "60": "1h", "120": "2h", "240": "4h", "360": "6h", "720": "12h", "1440": "1d"
            }
             interval = tf_map.get(interval, "1h")

        params = {
            "symbol": symbol,
            "period": interval,
            "limit": limit
        }
        data = await self._request("GET", "/futures/data/openInterestHist", params=params)
        # Note: /futures/data/ is a different base endpoint usually (https://fapi.binance.com/futures/data/openInterestHist)
        # Actually standard fapi base url + /futures/data/... works? 
        # No, usually it's https://fapi.binance.com/futures/data/openInterestHist
        
        # Let's try /fapi/v1/openInterest for CURRENT OI, but method asks for history.
        # If history fails, return current.
        
        if not isinstance(data, list):
             # Fallback to current OI
             curr_params = {"symbol": symbol}
             curr = await self._request("GET", "/fapi/v1/openInterest", params=curr_params)
             if curr and "openInterest" in curr:
                 return [{"timestamp": int(curr["time"]), "openInterest": float(curr["openInterest"])}]
             return []

        result = []
        for item in data:
            result.append({
                "timestamp": int(item["timestamp"]),
                "openInterest": float(item["sumOpenInterest"]), # or sumOpenInterestValue
            })
        return result

    async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        data = await self._request("GET", "/fapi/v1/exchangeInfo")
        if not data or "symbols" not in data:
            return {}
        
        target = None
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                target = s
                break
        
        if not target:
            return {}

        # Filters
        price_filter = next((f for f in target["filters"] if f["filterType"] == "PRICE_FILTER"), {})
        lot_filter = next((f for f in target["filters"] if f["filterType"] == "LOT_SIZE"), {})

        return {
            "name": target["symbol"],
            "lotSizeFilter": {
                "qtyStep": lot_filter.get("stepSize", "0.001"),
                "minOrderQty": lot_filter.get("minQty", "0.001"),
            },
            "priceFilter": {
                "tickSize": price_filter.get("tickSize", "0.01"),
            },
        }

    async def get_wallet_balance(self, asset: str = "USDT") -> float:
        # GET /fapi/v2/balance
        data = await self._request("GET", "/fapi/v2/balance", signed=True)
        if not isinstance(data, list):
            return 0.0
        
        for item in data:
            if item.get("asset") == asset:
                return float(item.get("availableBalance", 0.0) or item.get("balance", 0.0))
        
        return 0.0

    async def create_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        time_in_force: str = "GoodTillCancel",
    ) -> Dict[str, Any]:
        # POST /fapi/v1/order
        # side: BUY, SELL
        
        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
             # Map from user terms if needed, but base usually expects Buy/Sell capitalized?
             # Standard Binance: BUY, SELL.
             # My adapter interface might send "Buy"/"Sell" or "Long"/"Short" -> handled in upper logic?
             # Base logic sends "Buy" or "Sell" (Capitalized). Binance needs UPPER.
             side_upper = side_upper
        
        params = {
            "symbol": symbol,
            "side": side_upper,
            "type": order_type.upper(),
            "quantity": str(qty),
        }
        
        if order_type.upper() == "LIMIT":
            if price is None:
                return {"success": False, "message": "Price required for Limit order"}
            params["price"] = str(price)
            params["timeInForce"] = "GTC" if time_in_force == "GoodTillCancel" else "IOC"
        
        logger.info("Binance create_order: %s", params)
        data = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
        
        if not data or "orderId" not in data:
            return {"success": False, "raw": data}
        
        return {"success": True, "raw": data}

    async def set_tp_sl(
        self,
        symbol: str,
        position_side: str,
        tp: Optional[float],
        sl: Optional[float],
    ) -> Dict[str, Any]:
        # Binance Futures TP/SL are usually separate orders:
        # STOP_MARKET (SL) and TAKE_PROFIT_MARKET (TP)
        # with closePosition=True or reduceOnly=True
        
        # position_side: "Buy" (Long) or "Sell" (Short)
        # TP/SL side is opposite.
        
        side = "SELL" if position_side.upper() == "BUY" else "BUY"
        
        orders_res = []
        
        if tp is not None:
            params = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": str(tp),
                "closePosition": "true", # Closes entire position
            }
            res = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
            orders_res.append({"type": "tp", "raw": res, "success": bool(res and "orderId" in res)})

        if sl is not None:
            params = {
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "stopPrice": str(sl),
                "closePosition": "true",
            }
            res = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
            orders_res.append({"type": "sl", "raw": res, "success": bool(res and "orderId" in res)})
            
        all_ok = all(o["success"] for o in orders_res)
        return {"success": all_ok, "orders": orders_res}

