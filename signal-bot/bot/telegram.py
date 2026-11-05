"""Telegram delivery + message formatting."""
import html
import logging
from datetime import datetime, timezone
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


def local_time(dt=None):
    dt = dt or datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(config.TIMEZONE)
    except Exception:
        tz = timezone.utc
    return dt.astimezone(tz).strftime("%d %b %H:%M %Z")


def format_signal(sig, asset, urgent=False):
    long_ = sig["direction"] == "LONG"
    e = sig["entry"]

    def pct(x):
        return f"{(x - e) / e * 100:+.2f}%"

    head = "🟢 LONG / BUY" if long_ else "🔴 SHORT / SELL"
    badge = {"A": "⭐ Grade A", "B": "Grade B", "C": "Grade C (lower confidence)"}[sig["grade"]]
    lines = [
        f"{'⚡ URGENT · ' if urgent else ''}<b>{head}  {html.escape(asset.symbol)}</b>",
        f"{badge} · score {sig['score']:.0f}/100 · {asset.kind.title()}",
        f"Where: {html.escape(asset.venue)}",
        "",
        f"Entry: <code>{fmt_price(e)}</code>",
        f"Stop:  <code>{fmt_price(sig['sl'])}</code>  ({pct(sig['sl'])})",
        f"TP1:   <code>{fmt_price(sig['tp1'])}</code>  ({pct(sig['tp1'])})",
        f"TP2:   <code>{fmt_price(sig['tp2'])}</code>  ({pct(sig['tp2'])})",
        f"R:R 1:{config.TP1_R} / 1:{config.TP2_R} · hold: a few hours, max {config.SIGNAL_EXPIRY_HOURS}h",
        "",
        "<b>Why:</b>",
    ]
    lines += [f"• {html.escape(r)}" for r in sig["reasons"]]
    top = (sig.get("sentiment") or {}).get("top")
    if top:
        lines.append(f"📰 <i>{html.escape(top[:140])}</i>")
    lines += ["", f"🕒 {local_time()} · Move SL to entry after TP1 · Not financial advice"]
    return "\n".join(lines)


def format_outcome(rec, event, r_value):
    icon = {"tp1": "✅", "tp2": "🏆", "sl": "❌", "expired": "⌛"}[event]
    label = {"tp1": "hit TP1", "tp2": "hit TP2", "sl": "hit stop-loss",
             "expired": f"expired after {config.SIGNAL_EXPIRY_HOURS}h"}[event]
    return (f"{icon} {rec['direction']} <b>{html.escape(rec['symbol'])}</b> {label} "
            f"({r_value:+.1f}R)\nEntry {fmt_price(rec['entry'])} · sent {local_time(datetime.fromisoformat(rec['sent_at']))}")
