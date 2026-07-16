"""SMTP notifications for the daily autorun."""

import os
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Dict

from bidmatch.config import AutorunConfig


def _redact(text: str) -> str:
    token = os.environ.get("BIDMATCH_SUB", "").strip()
    return text.replace(token, "<redacted>") if token else text


def _send(cfg: AutorunConfig, msg: EmailMessage) -> None:
    msg["From"] = cfg.smtp_user or f"bidmatch@{cfg.smtp_host}"
    msg["To"] = ", ".join(cfg.notify_to)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as s:
        s.starttls()
        if cfg.smtp_user:
            s.login(cfg.smtp_user, cfg.smtp_password)
        s.send_message(msg)


def send_success(cfg: AutorunConfig, xlsx_path: Path, delta: Dict, summary: Dict) -> None:
    today = date.today().isoformat()
    msg = EmailMessage()
    msg["Subject"] = (
        f"BidMatch weekly — {delta['new']} new, {delta['repriced']} repriced | "
        f"Bid: {summary['bid']} | Investigate: {summary['investigate']} ({today})"
    )
    msg.set_content(
        f"Daily BidMatch run complete ({today}).\n\n"
        f"Delta: {delta['new']} new, {delta['repriced']} repriced, "
        f"{delta['unchanged']} unchanged, {delta['frozen']} frozen (past due).\n\n"
        f"Sheets: Bid {summary['bid']} | No Bid {summary['no_bid']} | "
        f"Investigate {summary['investigate']}\n"
        f"High-confidence Bid pipeline: ${summary['high_pipeline']:,.2f}\n\n"
        f"Workbook attached: {xlsx_path.name}"
    )
    msg.add_attachment(
        xlsx_path.read_bytes(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=xlsx_path.name,
    )
    _send(cfg, msg)


def send_failure(cfg: AutorunConfig, stage: str, error_text: str) -> None:
    today = date.today().isoformat()
    msg = EmailMessage()
    msg["Subject"] = f"BidMatch daily FAILED ({today})"
    msg.set_content(
        f"The daily BidMatch run failed at stage: {stage}\n\n{_redact(error_text)}"
    )
    _send(cfg, msg)
