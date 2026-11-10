"""Earshot Plus free trial in the app: plan cache + prompt timing (cloud_plan),
the trial-ended / ending-soon dialog, the sidebar card, and the plan-block
path from a failed transcription to the prompt.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_trial_prompt.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data so the test can never touch the real config.json.
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from meeting_notes import cloud_plan  # noqa: E402
from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.transcription import earshot_client  # noqa: E402
from meeting_notes.ui import trial_dialog  # noqa: E402
from meeting_notes.ui.shell import Shell  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def new_repo() -> MeetingRepository:
    return MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "t.db"))


def me(state="active", ends=None, started=None, cap=False, annual=True) -> dict:
    ends = ends or NOW + dt.timedelta(days=5)
    started = started or ends - dt.timedelta(days=7)
    return {
        "sub_status": "expired" if state == "ended" else "trialing",
        "period_end": ends.date().isoformat(),
        "billing_url": "https://api.example/account",
        "trial": {"state": state, "started_at": started.isoformat(), "ends_at": ends.isoformat(),
                  "days": 7, "cap_reached": cap},
        "checkout_url": "https://tryearshot.app/account?checkout=monthly",
        "checkout_annual_url": "https://tryearshot.app/account?checkout=annual" if annual else None,
        "price_monthly": 9,
        "price_annual": 90 if annual else None,
        "usage": {"transcribe_seconds": 0, "cap_seconds": 36000, "llm_calls": 0},
    }


def cloud_cfg(data: dict | None = None) -> Config:
    cfg = Config()
    cfg.account_mode = "cloud"
    cfg.cloud_token = "tok"
    cfg.cloud_api_base = "http://127.0.0.1:9"  # any /v1/me a page fires fails fast, offline
    if data is not None:
        cloud_plan.cache_me(cfg, data)
    return cfg


def add_meeting(repo, *, created: dt.datetime, secs: float, items: int) -> None:
    m = repo.create(date_text="x", date_iso=created.date().isoformat(), attendees=[])
    notes = {"title": "t", "summary": "s",
             "action_items": [{"text": f"do {i}", "confirmed": i % 2 == 0} for i in range(items)]}
    repo.update(m.id, duration_secs=secs, notes_json=json.dumps(notes))
    repo.conn.execute("UPDATE meetings SET created_at=? WHERE id=?",
                      (created.strftime("%Y-%m-%d %H:%M:%S"), m.id))
    repo.conn.commit()


def test_plan_logic() -> None:
    print("== cloud_plan ==")
    cfg = cloud_cfg(me())
    t = cloud_plan.trial(cfg)
    check("trial parsed from the snapshot", t is not None and t.state == "active" and t.ends is not None)
    check("5 days left: no prompt yet", cloud_plan.prompt_kind(cfg) is None)
    check("ends phrase counts days", cloud_plan.ends_phrase(t, NOW) == "in 5 days")
    check("within 48 h: ending prompt",
          cloud_plan.prompt_kind(cloud_cfg(me(ends=NOW + dt.timedelta(hours=30)))) == "ending")
    check("allowance used: cap prompt", cloud_plan.prompt_kind(cloud_cfg(me(cap=True))) == "cap")
    check("over: ended prompt",
          cloud_plan.prompt_kind(cloud_cfg(me(state="ended", ends=NOW - dt.timedelta(hours=1)))) == "ended")
    selfhost = cloud_cfg(me(state="ended"))
    selfhost.account_mode = "selfhost"
    check("signed out: no trial, no prompt",
          cloud_plan.trial(selfhost) is None and cloud_plan.prompt_kind(selfhost) is None)
    paid = cloud_cfg({**me(), "trial": None, "sub_status": "active"})
    check("paid account: no trial", cloud_plan.trial(paid) is None)

    print("== reminders ==")
    cfg = cloud_cfg(me(state="ended"))
    check("first time: prompt", cloud_plan.should_prompt(cfg, "ended", NOW))
    cloud_plan.mark_prompted(cfg, "ended", NOW)
    check("again within a day: no", not cloud_plan.should_prompt(cfg, "ended", NOW + dt.timedelta(hours=3)))
    check("next day: yes", cloud_plan.should_prompt(cfg, "ended", NOW + dt.timedelta(hours=21)))
    check("kinds are independent", cloud_plan.should_prompt(cfg, "ending", NOW))

    print("== checkout + prices ==")
    check("cached checkout url", cloud_plan.checkout_url(cfg).endswith("checkout=monthly"))
    check("annual checkout url", cloud_plan.checkout_url(cfg, annual=True).endswith("checkout=annual"))
    check("default checkout url when never fetched",
          cloud_plan.checkout_url(Config()) == "https://tryearshot.app/account?checkout=monthly")
    check("prices from the server", cloud_plan.prices(cfg) == (9, 90))
    check("no yearly plan when the server has none", cloud_plan.prices(cloud_cfg(me(annual=False))) == (9, None))
    check("default prices when never fetched", cloud_plan.prices(Config()) == (9, 90))

    print("== what the trial did ==")
    repo = new_repo()
    start = NOW - dt.timedelta(days=6)
    add_meeting(repo, created=start - dt.timedelta(days=2), secs=3600, items=9)  # before the trial
    add_meeting(repo, created=start + dt.timedelta(days=1), secs=2700, items=3)
    add_meeting(repo, created=start + dt.timedelta(days=2), secs=4500, items=4)
    st = cloud_plan.trial_stats(repo, start)
    check("only meetings since the trial began", st.meetings == 2)
    check("time captured adds up", st.seconds == 7200 and st.hours_text == "2")
    check("kept + suggested action items count", st.action_items == 7)
    check("meetings with notes counted", st.with_notes == 2)


def test_client_errors() -> None:
    print("== plan-block errors ==")
    err = earshot_client._friendly_body({"error": {"code": "trial_ended", "message": "x"}}, 402)
    check("trial_ended gets friendly copy and a code",
          err.code == "trial_ended" and "free trial has ended" in str(err))
    check("trial_ended is a plan block", earshot_client.is_plan_block(str(err)))
    cap = earshot_client._friendly_body({"error": {"code": "trial_cap_reached"}}, 402)
    check("trial cap is a plan block", earshot_client.is_plan_block(str(cap)))
    sub = earshot_client._friendly_body({"error": {"code": "sub_inactive"}}, 402)
    check("sub_inactive is a plan block", earshot_client.is_plan_block(str(sub)))
    other = earshot_client._friendly_body({"error": {"code": "upstream_error"}}, 502)
    check("an upstream error is not", not earshot_client.is_plan_block(str(other)))
    check("em-dash free new copy", "—" not in str(err) and "—" not in str(cap))


def test_dialog(theme) -> None:
    print("== trial dialog ==")
    repo = new_repo()
    ends = NOW - dt.timedelta(hours=1)
    cfg = cloud_cfg(me(state="ended", ends=ends))
    add_meeting(repo, created=ends - dt.timedelta(days=3), secs=5400, items=5)
    add_meeting(repo, created=ends - dt.timedelta(days=2), secs=1800, items=2)
    opened = []
    trial_dialog.webbrowser.open = lambda url: opened.append(url)
    subscribed = []
    dlg = trial_dialog.TrialDialog(None, cfg, theme, repo, "ended", on_subscribed=lambda: subscribed.append(1))
    check("ended headline", dlg.headline_lbl.text() == "Your free trial has ended")
    check("stats from the trial", dlg.stats.meetings == 2 and dlg.stats.action_items == 7)
    check("CTA defaults to monthly", dlg.cta.text() == "Keep Earshot Plus  ·  $9/month")
    dlg.opt_year.clicked.emit()
    check("picking yearly updates the CTA", dlg.cta.text() == "Keep Earshot Plus  ·  $90/year")
    dlg.cta.click()
    check("CTA opens the yearly checkout", opened and opened[-1].endswith("checkout=annual"))
    check("then says to finish in the browser",
          not dlg.waiting.isHidden() and "browser" in dlg.waiting.text())
    check("CTA becomes I've subscribed", dlg.cta.text() == "I've subscribed")
    dlg.cta.click()
    check("I've subscribed re-checks the plan", subscribed == [1])
    dlg.deleteLater()

    soon = cloud_cfg(me(ends=NOW + dt.timedelta(hours=20)))
    dlg = trial_dialog.TrialDialog(None, soon, theme, new_repo(), "ending")
    check("ending headline", dlg.headline_lbl.text().startswith("Your free trial ends"))
    check("ending CTA", dlg.cta.text().startswith("Subscribe now"))
    check("ending dismiss is a reminder", dlg.later_btn.text() == "Remind me tomorrow")
    check("no meetings yet: no stats", dlg.stats.meetings == 0)
    dlg.deleteLater()

    dlg = trial_dialog.TrialDialog(None, cloud_cfg(me(cap=True, annual=False)), theme, new_repo(), "cap")
    check("cap headline", dlg.headline_lbl.text() == "You've used all your trial hours")
    check("no yearly option without a yearly price", dlg.opt_year is None)
    dlg.selfhost_btn.click()
    check("own-keys link opens the self-hosting guide", opened[-1] == cloud_plan.SELF_HOST_GUIDE)
    dlg.deleteLater()


def test_shell(theme_cfg) -> None:
    print("== sidebar + watcher ==")
    repo = new_repo()
    cfg = cloud_cfg(me(state="ended", ends=NOW - dt.timedelta(hours=2)))
    theme = ThemeController(cfg)
    shell = Shell(repo, cfg, theme)
    shell.refresh_plan_state()
    check("plan chip says the trial ended", shell.plan_chip.text() == "Trial ended")
    check("sidebar card shown", not shell.status_card.isHidden())
    check("sidebar card copy", shell.status_title.text() == "Your free trial has ended"
          and shell.status_btn.text() == "Keep Earshot Plus")
    shown = []
    shell.trial_watcher.show_prompt = lambda kind=None, from_user=False: shown.append((kind, from_user))
    shell.status_btn.click()
    check("sidebar button opens the prompt", shown == [(None, True)])

    shown.clear()
    shell.trial_watcher._on_me(me(state="ended", ends=NOW - dt.timedelta(hours=2)), False, "")
    check("watcher prompts when the trial is over", shown == [("ended", False)])

    errors = []
    QMessageBox.critical = staticmethod(lambda *a, **k: errors.append(a[-1]))
    shown.clear()
    shell.trial_watcher._on_me({**me(), "trial": None, "sub_status": "canceled"}, True, "Plan error")
    check("not on a trial: the plain error instead", shown == [] and errors == ["Plan error"])

    print("== a refused transcription opens the prompt ==")
    blocked = []
    shell.trial_watcher.blocked = lambda msg: blocked.append(msg)
    errors.clear()
    msg = earshot_client._SLUG_MESSAGES["trial_ended"]
    shell.record._on_process_failed(1, msg)
    check("record page hands plan blocks to the watcher", blocked == [msg] and errors == [])
    shell.record._on_process_failed(2, "Earshot Plus had a temporary problem")
    check("other failures still show the error box", errors == ["Earshot Plus had a temporary problem"])

    cfg.extra["cloud_trial"] = None
    cfg.extra["cloud_sub_status"] = "active"
    shell.refresh_plan_state()
    check("paid account: plain Plus chip, no trial card",
          shell.plan_chip.text() == "Plus" and shell.status_card.isHidden())
    from meeting_notes.ui import workers
    workers.join_all()  # pages' /v1/me fetches: never exit with a live QThread
    shell.sync.shutdown()


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    cfg = Config()
    theme = ThemeController(cfg)
    test_plan_logic()
    test_client_errors()
    test_dialog(theme)
    test_shell(cfg)
    print("all trial prompt checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
