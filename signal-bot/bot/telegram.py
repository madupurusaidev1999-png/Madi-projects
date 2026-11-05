"""Telegram delivery + message formatting."""
import html
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .http import post

log = logging.getLogger(__name__)


def send(text):
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        print("\n----- (dry run: Telegram not configured) -----\n" + text)
        return True
    try:
        res = post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
        )
        return bool(res.get("ok"))
    except Exception as e:
        log.error("telegram send failed: %s", e)
        return False


def fmt_price(p):
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.8f}".rstrip("0")


def fmt_dist(d):
    return f"{d:,.2f}" if d >= 1 else fmt_price(d)


def local_time(dt=None):
    dt = dt or datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(config.TIMEZONE)
    except Exception:
        tz = timezone.utc
    return dt.astimezone(tz).strftime("%d %b %H:%M %Z")


METAL_NAMES = {"GOLD": "XAU/USD (Gold)", "SILVER": "XAG/USD (Silver)", "COPPER": "Copper (XCU/USD)",
               "PLATINUM": "XPT/USD (Platinum)", "PALLADIUM": "XPD/USD (Palladium)"}


def display_name(asset):
    if asset.kind == "metal":
        return METAL_NAMES.get(asset.sentiment_key, asset.symbol)
    return asset.symbol


def format_signal(sig, asset, urgent=False):
    """Levels are shown in Kraken Prop terms: crypto = Kraken price,
    metals = live spot price (the bot shifts its levels onto spot)."""
    long_ = sig["direction"] == "LONG"
    shift = sig.get("shift", 0.0)
    e, sl, tp1, tp2 = (sig[k] + shift for k in ("entry", "sl", "tp1", "tp2"))
    chase = e + (1 if long_ else -1) * sig["risk"] * config.ENTRY_ZONE_R

    def pct(x):
        return f"{(x - e) / e * 100:+.2f}%"

    now = datetime.now(timezone.utc)
    entry_until = local_time(now + timedelta(minutes=config.ENTRY_VALID_MINUTES))
    close_by = local_time(now + timedelta(hours=config.SIGNAL_EXPIRY_HOURS))
    head = "🟢 BUY / LONG" if long_ else "🔴 SELL / SHORT"
    badge = {"A": "⭐ Grade A", "B": "Grade B", "C": "Grade C"}[sig["grade"]]
    if asset.kind == "metal":
        src = "spot price" if "shift" in sig else "Kraken futures price (spot unavailable)"
    else:
        src = "Kraken price"
    lines = [
        f"{'⚡ ' if urgent else ''}<b>{head}  {html.escape(display_name(asset))}</b>",
        f"{badge} · score {sig['score']:.0f}/100",
        "",
        f"Entry: <code>{fmt_price(e)}</code>",
        f"SL:    <code>{fmt_price(sl)}</code>  ({pct(sl)})",
        f"TP1:   <code>{fmt_price(tp1)}</code>  ({pct(tp1)})",
        f"TP2:   <code>{fmt_price(tp2)}</code>  ({pct(tp2)})",
        f"<i>Levels use the {src}. If your chart differs a little, keep the same distances: "
        f"SL {fmt_dist(sig['risk'])} · TP1 {fmt_dist(sig['risk'] * config.TP1_R)} · "
        f"TP2 {fmt_dist(sig['risk'] * config.TP2_R)}</i>",
        "",
        "<b>⏳ Valid</b>",
        f"• Enter before <b>{entry_until}</b>",
        f"• Skip if price is already {'above' if long_ else 'below'} <code>{fmt_price(chase)}</code>",
        f"• Close by <b>{close_by}</b> if no TP/SL hit",
        "",
        "<b>Why:</b>",
    ]
    lines += [f"• {html.escape(r)}" for r in sig["reasons"]]
    top = (sig.get("sentiment") or {}).get("top")
    if top:
        lines.append(f"📰 <i>{html.escape(top[:140])}</i>")
    lines += ["", f"🕒 {local_time()}"]
    return "\n".join(lines)


def format_outcome(rec, event, r_value):
    icon = {"tp1": "✅", "tp2": "🏆", "sl": "❌", "expired": "⌛"}[event]
    label = {"tp1": "hit TP1", "tp2": "hit TP2", "sl": "hit stop-loss",
             "expired": f"expired after {config.SIGNAL_EXPIRY_HOURS}h"}[event]
    entry = rec["entry"] + rec.get("shift", 0.0)
    return (f"{icon} {rec['direction']} <b>{html.escape(rec.get('name', rec['symbol']))}</b> {label} "
            f"({r_value:+.1f}R)\nEntry {fmt_price(entry)} · sent {local_time(datetime.fromisoformat(rec['sent_at']))}")
