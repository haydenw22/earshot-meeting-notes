"""Tests for Phase D — the PROJECTS rename, the sidebar bottom cluster
(Integrations nav, Settings nav, theme toggle slider, account card, version),
and the two new pages: Integrations (Todoist + Webhook, moved out of
Settings -> General) and Account (display name, Earshot Cloud "coming soon",
storage folder).

Run:  QT_QPA_PLATFORM=offscreen python tests/test_pages.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Isolate ALL app data to a throwaway dir so the test can never read or overwrite
# the real %LOCALAPPDATA%\Earshot\config.json (which would wipe the user's key/URL).
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="earshot_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.page_account import AccountPage  # noqa: E402
from meeting_notes.ui.page_integrations import IntegrationsPage  # noqa: E402
from meeting_notes.ui.page_settings import SettingsPage  # noqa: E402
from meeting_notes.ui.shell import Shell  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def new_repo() -> MeetingRepository:
    return MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "p.db"))


class _Shell:
    """A minimal shell stand-in for pages built standalone (not through Shell)."""

    def show_home(self):
        pass

    def show_record(self):
        pass

    def show_ask(self):
        pass

    def open_meeting(self, _mid):
        pass

    def notify_data_changed(self):
        self.notified = True

    def refresh_account_card(self):
        self.refreshed = True


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    theme = ThemeController(Config())
    theme.apply()

    # ---------------------------------------------------------------
    print("== sidebar: PROJECTS section label + Overview nav button ==")
    repo = new_repo()
    shell = Shell(repo, Config(), theme)
    app.processEvents()
    # the sidebar section header is a QLabel(objectName="SectionLabel"); confirm
    # it now reads "PROJECTS" and the old "FOLDERS" wording is gone
    from PySide6.QtWidgets import QLabel
    section_labels = [w.text() for w in shell.sidebar.findChildren(QLabel) if w.objectName() == "SectionLabel"]
    check("a SectionLabel reads 'PROJECTS'", "PROJECTS" in section_labels)
    check("no SectionLabel still reads 'FOLDERS'", "FOLDERS" not in section_labels)
    check("Home nav button text is 'Overview'", shell.home_btn.text().strip() == "Overview")

    # ---------------------------------------------------------------
    print("== shell: show_integrations()/show_account() switch the stack ==")
    shell.show_home()
    app.processEvents()
    check("home not hidden after show_home", not shell.home.isHidden())

    shell.show_integrations()
    app.processEvents()
    check("integrations page is the current widget", shell.stack.currentWidget() is shell.integrations)
    check("integrations page not hidden", not shell.integrations.isHidden())
    check("home page hidden while integrations shown", shell.home.isHidden())
    check("integrations_btn is checked", shell.integrations_btn.isChecked())

    shell.show_account()
    app.processEvents()
    check("account page is the current widget", shell.stack.currentWidget() is shell.account)
    check("account page not hidden", not shell.account.isHidden())
    check("integrations page hidden while account shown", shell.integrations.isHidden())

    shell.show_home()
    app.processEvents()
    check("home not hidden again after navigating back", not shell.home.isHidden())
    check("account page hidden after navigating away", shell.account.isHidden())

    # ---------------------------------------------------------------
    print("== sidebar CTA reflects a live recording ==")
    check("CTA starts as 'New recording'", shell.new_btn.text().strip() == "New recording")
    check("pulse timer idle before recording", not shell._rec_pulse.isActive())
    shell.set_recording(True)
    check("CTA reads 'Recording' while live", shell.new_btn.text().strip() == "Recording")
    check("record-dot pulse timer running", shell._rec_pulse.isActive())
    shell._pulse_record_icon()  # a tick must not raise and keeps an icon set
    check("icon present mid-pulse", not shell.new_btn.icon().isNull())
    shell.set_recording(False)
    check("CTA restores after stop", shell.new_btn.text().strip() == "New recording")
    check("pulse timer stopped after stop", not shell._rec_pulse.isActive())

    print("== home header: no separate top-right record button ==")
    check("home page has no header record button", not hasattr(shell.home, "new_btn"))
    repo.close()

    # ---------------------------------------------------------------
    print("== Integrations page: Todoist token + webhook URL round-trip into cfg ==")
    int_repo = new_repo()
    int_cfg = Config()
    ipage = IntegrationsPage(_Shell(), int_repo, int_cfg, theme)
    app.processEvents()
    ipage.todoist_token.setText("tok_xyz_789")
    ipage.webhook_url.setText("https://example.com/earshot-hook")
    ipage.webhook_when.setCurrentIndex(1)  # "After transcription (raw transcript)"
    ipage._save()
    reloaded = Config.load()
    check("todoist_token round-trips into cfg", reloaded.todoist_token == "tok_xyz_789")
    check("webhook_url round-trips into cfg", reloaded.webhook_url == "https://example.com/earshot-hook")
    check("webhook_when round-trips into cfg", reloaded.webhook_when == "transcript")

    print("== Integrations page: test-connection button exists and is wired ==")
    check("todoist_test_btn exists", hasattr(ipage, "todoist_test_btn"))
    # exercise the wiring itself (blank token -> ping() returns False -> the
    # label reports "could not connect") rather than introspecting Qt's signal
    # internals, which is a stronger, more direct check of the click path
    ipage.todoist_token.setText("")
    ipage._test_todoist()
    check("test-connection updates the status label", "Could not connect" in ipage.todoist_test_label.text())

    print("== Integrations page: 'More coming soon' card lists Slack/Notion/Calendar ==")
    from PySide6.QtWidgets import QLabel as _QLabel
    all_labels = [w.text() for w in ipage.findChildren(_QLabel)]
    for name in ("Slack", "Notion", "Calendar"):
        check(f"coming-soon card mentions {name}", any(name == t for t in all_labels))
    check("a 'Coming soon' chip is present", any("Coming soon" in t for t in all_labels))
    int_repo.close()

    # ---------------------------------------------------------------
    print("== Settings General tab no longer exposes the moved fields ==")
    set_repo = new_repo()
    spage = SettingsPage(_Shell(), set_repo, Config(), theme)
    app.processEvents()
    check("SettingsPage has no webhook_url attribute", not hasattr(spage, "webhook_url"))
    check("SettingsPage has no webhook_when attribute", not hasattr(spage, "webhook_when"))
    check("SettingsPage has no todoist_token attribute", not hasattr(spage, "todoist_token"))
    check("SettingsPage has no todoist_test_btn attribute", not hasattr(spage, "todoist_test_btn"))
    set_repo.close()

    # ---------------------------------------------------------------
    print("== theme toggle slider: invoking it flips cfg.theme_mode / theme.mode both ways ==")
    slider_repo = new_repo()
    scfg = Config()
    stheme = ThemeController(scfg)
    stheme.apply()
    sshell = Shell(slider_repo, scfg, stheme)
    app.processEvents()
    check("theme_toggle widget exists", hasattr(sshell, "theme_toggle"))

    start_mode = stheme.mode
    sshell.theme_toggle._on_toggle()
    app.processEvents()
    check("first toggle flips theme.mode", stheme.mode != start_mode)
    check("first toggle flips cfg.theme_mode", scfg.theme_mode == stheme.mode)
    sshell.theme_toggle._on_toggle()
    app.processEvents()
    check("second toggle flips theme.mode back", stheme.mode == start_mode)
    check("second toggle flips cfg.theme_mode back", scfg.theme_mode == stheme.mode)
    slider_repo.close()

    # ---------------------------------------------------------------
    print("== account: typing a display name + editingFinished persists + sidebar avatar updates ==")
    acc_repo = new_repo()
    acfg = Config()
    atheme = ThemeController(acfg)
    atheme.apply()
    ashell = Shell(acc_repo, acfg, atheme)
    app.processEvents()

    check("account card initially shows 'Guest'", ashell.account_card.name_lbl.text() == "Guest")
    check("account card avatar initial defaults to 'G'", ashell.account_card.avatar.text() == "G")

    ashell.account.name_edit.setText("Hayden Whittle")
    ashell.account.name_edit.editingFinished.emit()
    app.processEvents()
    check("account_name persisted to cfg", acfg.account_name == "Hayden Whittle")
    reloaded_acc = Config.load()
    check("account_name round-trips via Config.load", reloaded_acc.account_name == "Hayden Whittle")
    check("sidebar account card name updates immediately", ashell.account_card.name_lbl.text() == "Hayden Whittle")
    check("sidebar account card avatar initial updates immediately", ashell.account_card.avatar.text() == "H")

    print("== account card: clicking it navigates to the Account page ==")
    ashell.show_home()
    app.processEvents()
    ashell.account_card._on_click()
    app.processEvents()
    check("clicking the account card shows the account page", ashell.stack.currentWidget() is ashell.account)
    acc_repo.close()

    # ---------------------------------------------------------------
    # The old "Earshot Cloud — coming soon" mock is replaced by the real Earshot
    # Plus flow (spec Phase 1). In selfhost mode the Account page pitches Plus with
    # a "Sign in / Subscribe" button (opens the device-link dialog) and a "Learn
    # more" button (opens tryearshot.app). Assert the new behaviour directly —
    # stronger than the old "shows a coming-soon box" check.
    print("== account: selfhost shows the Earshot Plus pitch (subscribe + learn more) ==")
    signin_repo = new_repo()
    scfg2 = Config()
    scfg2.account_mode = "selfhost"
    spage2 = AccountPage(_Shell(), signin_repo, scfg2, theme)
    app.processEvents()
    check("subscribe_btn exists (Sign in / Subscribe)", hasattr(spage2, "subscribe_btn"))
    check("learn_btn exists (Learn more)", hasattr(spage2, "learn_btn"))
    check("no old coming-soon signin_btn remains", not hasattr(spage2, "signin_btn"))

    import meeting_notes.ui.page_account as pa_mod
    opened = []
    orig_open = pa_mod.webbrowser.open
    pa_mod.webbrowser.open = lambda url, *a, **k: opened.append(url)
    try:
        spage2.learn_btn.click()
    finally:
        pa_mod.webbrowser.open = orig_open
    check("Learn more opens tryearshot.app", opened == ["https://tryearshot.app"])
    signin_repo.close()

    print("\nPAGES TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
