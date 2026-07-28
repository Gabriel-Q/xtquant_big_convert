"""Real-time order/trade (execution) event push over Redis.

Big QMT fires ``order_callback(ContextInfo, orderInfo)`` and
``deal_callback(ContextInfo, dealInfo)`` inside the strategy process. We normalize
the QMT order/deal object (ThinkTrader ``m_*`` fields) into a plain dict and
publish it to a Redis channel, so clients receive ``on_stock_order`` /
``on_stock_trade`` callbacks in real time (MiniQMT style) instead of polling.

Channels (also used as capped streams for short replay, xadd + publish):
- ``bigqmt:order_events:{account_id}``
- ``bigqmt:trade_events:{account_id}``

The normalized field names match ``BigQmtXtTrader._order_from_dict`` /
``_trade_from_dict`` so the client can shape them straight into MiniQMT objects.
"""

import json
import time


ORDER_CHANNEL_TEMPLATE = "bigqmt:order_events:{account_id}"
TRADE_CHANNEL_TEMPLATE = "bigqmt:trade_events:{account_id}"

EVENT_ORDER = "order"
EVENT_TRADE = "trade"

# ThinkTrader enum_EEntrustBS (买卖方向, the m_nDirection field), universal across
# 股票/期货/期权. Ref: https://dict.thinktrader.net/innerApi/enum_constants.html
ENTRUST_BUY = 48         # 买入 / 多
ENTRUST_SELL = 49        # 卖出 / 空
ENTRUST_PLEDGE_IN = 81   # 质押入库
ENTRUST_PLEDGE_OUT = 66  # 质押出库

# enum_EOffset_Flag_Type (开平方向, the m_nOffsetFlag field).
# 48=开仓(=买入 for stocks), 49=平仓(=卖出 for stocks), 51=平今, 52=平昨.
# For spot stocks (无做空), offset flag 48/49 coincides exactly with buy/sell,
# and it is the RELIABLE field in live order_callback (m_nDirection can be 0/None
# at certain callback moments). query_orders already uses m_nOffsetFlag for this
# reason. For futures, offset and direction differ — but we only ship stocks here.
OFFSET_OPEN = 48
OFFSET_CLOSE = 49
OFFSET_CLOSE_TODAY = 51
OFFSET_CLOSE_YESTERDAY = 52

# Map direction -> "BUY"/"SELL". Priority:
#   1. m_nOffsetFlag (reliable in live callbacks, matches query_orders)
#   2. m_nDirection (EEntrustBS 48/49)
#   3. order_type (MiniQMT STOCK_BUY=23 / STOCK_SELL=24) and plain text
# Unknown -> "" (the raw value is always preserved so callers can refine).
_BUY_DIRECTIONS = {ENTRUST_BUY, str(ENTRUST_BUY), OFFSET_OPEN, str(OFFSET_OPEN), 23, "23", "BUY", "buy", "B"}
_SELL_DIRECTIONS = {ENTRUST_SELL, str(ENTRUST_SELL), OFFSET_CLOSE, str(OFFSET_CLOSE), OFFSET_CLOSE_TODAY, str(OFFSET_CLOSE_TODAY), OFFSET_CLOSE_YESTERDAY, str(OFFSET_CLOSE_YESTERDAY), 24, "24", "SELL", "sell", "S"}


def order_channel(account_id):
    return ORDER_CHANNEL_TEMPLATE.format(account_id=str(account_id or ""))


def trade_channel(account_id):
    return TRADE_CHANNEL_TEMPLATE.format(account_id=str(account_id or ""))


def _attr(obj, names, default=None):
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return default


def _action_from_direction(direction):
    if direction in _BUY_DIRECTIONS:
        return "BUY"
    if direction in _SELL_DIRECTIONS:
        return "SELL"
    return ""


def _extract_direction(obj):
    """Extract buy/sell direction, matching query_orders' reliable logic.

    For spot stocks, m_nDirection (EEntrustBS 48/49) and m_nOffsetFlag
    (EOffset_Flag_Type 48/49) coincide: buy=open=48, sell=close=49.

    In live order_callback/deal_callback, m_nDirection can be 0/None at
    certain callback moments (e.g. early "未报" state), while m_nOffsetFlag
    is reliably populated. query_orders uses m_nOffsetFlag and works in
    production. So we prefer offset_flag when direction is absent/invalid.

    For futures (not shipped here), direction≠offset (e.g. sell+open=short).
    If BOTH fields are present and direction is a valid buy/sell value, we
    trust m_nDirection (the true buy/sell signal), preserving futures correctness.

    Fallback chain: m_nDirection(if valid buy/sell) > m_nOffsetFlag > order_type.
    The raw value is always returned (even pledge=81) so callers can inspect it;
    _action_from_direction maps only known buy/sell values, leaving others "".
    """
    direction = _attr(obj, ["m_nDirection", "direction"])
    offset = _attr(obj, ["m_nOffsetFlag", "offset_flag"])

    # If direction is present and is a valid buy/sell value, use it (covers
    # both stock 48/49 and futures where direction≠offset).
    if direction is not None:
        try:
            d = int(direction)
            if d in _BUY_DIRECTIONS or d in _SELL_DIRECTIONS:
                return direction
            # 0 / other non-buy-sell numeric -> treat as absent (live bug: m_nDirection
            # is 0 at certain callback moments), fall through to offset_flag.
            # Non-zero non-buy-sell (e.g. pledge 81) is preserved below.
            if d != 0:
                return direction
        except (TypeError, ValueError):
            if direction in _BUY_DIRECTIONS or direction in _SELL_DIRECTIONS:
                return direction
            # non-numeric, non-buy-sell (text) — preserve it
            return direction
    # direction absent/None -> fall back to offset_flag (reliable in stocks)
    if offset is not None:
        try:
            o = int(offset)
            if o in _BUY_DIRECTIONS or o in _SELL_DIRECTIONS:
                return offset
        except (TypeError, ValueError):
            if offset in _BUY_DIRECTIONS or offset in _SELL_DIRECTIONS:
                return offset
    # last resort: MiniQMT-style order_type field
    return _attr(obj, ["order_type"])


# Fields we care about when diagnosing a direction misread. Anything starting
# with "m_" is captured automatically; these are the MiniQMT-style names that
# do not match that prefix.
_RAW_SNAPSHOT_EXTRA_FIELDS = (
    "stock_code",
    "order_type",
    "direction",
    "offset_flag",
    "order_status",
    "order_volume",
    "traded_volume",
    "price",
    "order_id",
    "order_sysid",
    "order_sys_id",
    "trade_id",
    "traded_id",
    "strategy_name",
    "order_remark",
)


def raw_field_snapshot(obj, max_repr=120):
    """Capture every readable field of a live QMT callback object.

    Direction extraction rests on an assumption about what ``m_nDirection`` and
    ``m_nOffsetFlag`` actually carry in live order_callback/deal_callback — an
    assumption nothing in this repo has ever observed. This dumps the raw object
    so one live order settles it.

    Returns ``{name: "<type> <value>"}``. Never raises: a callback that dies
    while being diagnosed would be worse than no diagnosis.
    """
    snapshot = {}
    try:
        if isinstance(obj, dict):
            names = list(obj.keys())
        else:
            names = [name for name in dir(obj) if name.startswith("m_")]
            names.extend(_RAW_SNAPSHOT_EXTRA_FIELDS)
    except Exception:
        return {"__error__": "dir() failed"}
    seen = set()
    for name in names:
        key = str(name)
        if key in seen or key.startswith("__"):
            continue
        seen.add(key)
        try:
            if isinstance(obj, dict):
                if key not in obj:
                    continue
                value = obj[key]
            else:
                if not hasattr(obj, key):
                    continue
                value = getattr(obj, key)
            if callable(value):
                continue
            text = repr(value)
            if len(text) > max_repr:
                text = text[:max_repr] + "..."
            snapshot[key] = "%s %s" % (type(value).__name__, text)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not break callbacks
            snapshot[key] = "<unreadable: %s>" % exc.__class__.__name__
    return snapshot


def format_raw_snapshot(kind, obj):
    """One-line, GBK-safe rendering of :func:`raw_field_snapshot` for the QMT panel."""
    snapshot = raw_field_snapshot(obj)
    parts = ["%s=%s" % (name, snapshot[name]) for name in sorted(snapshot)]
    return "[bigqmt_exec_raw] %s type=%s %s" % (
        kind,
        type(obj).__name__,
        " | ".join(parts) or "<no fields>",
    )


def normalize_order_event(order, account_id=""):
    """Build a JSON-able order event dict from a Big QMT orderInfo object."""
    direction = _extract_direction(order)
    return {
        "event_type": EVENT_ORDER,
        "account_id": str(_attr(order, ["m_strAccountID", "account_id"], account_id) or account_id or ""),
        "stock_code": str(_attr(order, ["m_strInstrumentID", "stock_code", "m_strInstrument"], "") or ""),
        "order_sys_id": str(_attr(order, ["m_strOrderSysID", "order_sys_id", "order_sysid", "order_id"], "") or ""),
        "order_volume": _attr(order, ["m_nVolumeTotal", "order_volume", "volume"]),
        "traded_volume": _attr(order, ["m_nVolumeTraded", "traded_volume"]),
        "price": _attr(order, ["m_dLimitPrice", "price", "limit_price"]),
        "status": _attr(order, ["m_nOrderStatus", "order_status", "status"]),
        "direction": direction,
        "action": _action_from_direction(direction),
        "offset_flag": _attr(order, ["m_nOffsetFlag", "offset_flag"]),
        "strategy_name": str(_attr(order, ["m_strOptName", "strategy_name", "order_remark", "remark"], "") or ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at_ts": time.time(),
    }


def normalize_trade_event(trade, account_id=""):
    """Build a JSON-able trade (成交) event dict from a Big QMT dealInfo object."""
    direction = _extract_direction(trade)
    return {
        "event_type": EVENT_TRADE,
        "account_id": str(_attr(trade, ["m_strAccountID", "account_id"], account_id) or account_id or ""),
        "stock_code": str(_attr(trade, ["m_strInstrumentID", "stock_code"], "") or ""),
        "order_sys_id": str(_attr(trade, ["m_strOrderSysID", "order_sys_id", "order_sysid", "order_id"], "") or ""),
        "trade_id": str(_attr(trade, ["m_strTradeID", "trade_id"], "") or ""),
        "volume": _attr(trade, ["m_nVolume", "volume", "traded_volume"]),
        "price": _attr(trade, ["m_dPrice", "price", "traded_price"]),
        "amount": _attr(trade, ["m_dTradeAmount", "amount"]),
        "commission": _attr(trade, ["m_dComssion", "m_dCommission", "commission"]),
        "direction": direction,
        "action": _action_from_direction(direction),
        "offset_flag": _attr(trade, ["m_nOffsetFlag", "offset_flag"]),
        "traded_at": str(_attr(trade, ["m_strTradeTime", "traded_at", "trade_time"], "") or ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at_ts": time.time(),
    }


def _publish(redis_client, channel, event, maxlen=2000):
    raw = json.dumps(event, ensure_ascii=False, default=str)
    try:
        redis_client.xadd(channel, {"payload": raw}, maxlen=maxlen, approximate=True)
    except Exception:
        pass
    redis_client.publish(channel, raw)
    return event


def publish_order_event(redis_client, account_id, event):
    return _publish(redis_client, order_channel(account_id), event)


def publish_trade_event(redis_client, account_id, event):
    return _publish(redis_client, trade_channel(account_id), event)
