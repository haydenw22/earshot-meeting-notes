"""Tests for the onboarding wizard + Earshot Plus UI states.

Covers:
  - wizard page flow (welcome → tour → choice → path pages → finish);
  - the choice split-screen sets the path;
  - the self-host pages write the SAME cfg keys Settings uses, and never clobber
    non-empty existing values (existing-user safety);
  - onboarding_done is set on finish AND on close (never nags twice);
  - Settings hides the Transcription + AI tabs in cloud mode and shows them again
    after sign-out;
  - the Account page renders the Plus pitch (selfhost) vs subscription (cloud).

Run:  QT_QPA_PLATFORM=offscreen python tests/test_onboarding.py
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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from meeting_notes.config import Config  # noqa: E402
from meeting_notes.storage import db as dbmod  # noqa: E402
from meeting_notes.storage.repository import MeetingRepository  # noqa: E402
from meeting_notes.ui.onboarding import OnboardingDialog  # noqa: E402
from meeting_notes.ui.page_account import AccountPage  # noqa: E402
from meeting_notes.ui.page_settings import SettingsPage  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


def new_repo() -> MeetingRepository:
    return MeetingRepository(dbmod.connect(Path(tempfile.mkdtemp()) / "p.db"))


class _Home:
    def refresh(self):
        pass


class _Record:
    overlay = None


class _Shell:
    """Minimal shell stand-in for pages/wizard built standalone."""

    def __init__(self):
        self.account_changed = 0
        self.home = _Home()
        self.record = _Record()

    def on_account_changed(self):
        self.account_changed += 1

    def refresh_account_card(self):
        pass

    def run_onboarding(self):
        self.ran = True


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    theme = ThemeController(Config())
    theme.apply()

    # ---------------------------------------------------------------
    print("== wizard: page flow welcome → tour → choice ==")
    cfg = Config()
    dlg = OnboardingDialog(None, cfg, theme, shell=_Shell())
    check("starts on the welcome page", dlg.stack.currentIndex() == OnboardingDialog.WELCOME)
    check("back disabled on welcome", not dlg.back_btn.isEnabled())
    dlg._on_next()
    check("next → tour 1", dlg.stack.currentIndex() == OnboardingDialog.TOUR1)
    dlg._on_next()
    dlg._on_next()
    check("through the 3 tour slides to tour 3", dlg.stack.currentIndex() == OnboardingDialog.TOUR3)
    dlg._on_next()
    check("after the tour → choice split-screen", dlg.stack.currentIndex() == OnboardingDialog.CHOICE)
    check("Next hidden on the choice page (cards advance it)", dlg.next_btn.isHidden())

    print("== wizard: choosing self-host routes to the transcription page ==")
    dlg._choose("selfhost")
    check("choice sets the path", dlg._path == "selfhost")
    check("self-host → transcription page", dlg.stack.currentIndex() == OnboardingDialog.SELF_TR)

    print("== wizard: self-host pages write the SAME cfg keys Settings uses ==")
    dlg.tr_provider.setCurrentIndex(dlg.tr_provider.findData("online"))
    dlg.tr_online_base.setText("https://api.groq.com/openai/v1")
    dlg.tr_online_key.setText("sk-groq-123")
    dlg._on_next()  # saves transcription, advances to AI
    check("advanced to the AI page", dlg.stack.currentIndex() == OnboardingDialog.SELF_AI)
    check("transcription_provider written", cfg.transcription_provider == "online")
    check("online_base_url written", cfg.online_base_url == "https://api.groq.com/openai/v1")
    check("online_api_key written", cfg.online_api_key == "sk-groq-123")
    check("account_mode set to selfhost", cfg.account_mode == "selfhost")

    dlg.ai_provider.setCurrentIndex(dlg.ai_provider.findData("anthropic"))
    dlg.ai_anthropic_key.setText("sk-ant-xyz")
    dlg._on_next()  # saves AI, advances to finish
    check("advanced to the finish page", dlg.stack.currentIndex() == OnboardingDialog.FINISH)
    check("notes_provider written", cfg.notes_provider == "anthropic")
    check("anthropic_api_key written", cfg.anthropic_api_key == "sk-ant-xyz")

    print("== wizard: finishing sets onboarding_done ==")
    check("onboarding_done not set until finish", cfg.onboarding_done is False)
    dlg._finish()
    check("finish sets onboarding_done", cfg.onboarding_done is True)
    reloaded = Config.load()
    check("onboarding_done persisted to disk", reloaded.onboarding_done is True)

    # ---------------------------------------------------------------
    print("== existing-user safety: blank fields never clobber non-empty cfg values ==")
    ecfg = Config()
    ecfg.whisper_url = "http://192.168.1.50:9000"
    ecfg.anthropic_api_key = "sk-ant-EXISTING"
    ecfg.online_api_key = "sk-groq-EXISTING"
    edlg = OnboardingDialog(None, ecfg, theme, shell=_Shell())
    edlg._choose("selfhost")
    # user just clicks through without retyping — every field is left as prefilled
    check("home URL prefilled from cfg", edlg.tr_url.text() == "http://192.168.1.50:9000")
    check("anthropic key prefilled from cfg", edlg.ai_anthropic_key.text() == "sk-ant-EXISTING")
    # simulate the user clearing a field then advancing — must NOT wipe the saved value
    edlg.tr_url.setText("")
    edlg.tr_online_key.setText("")
    edlg._save_selfhost_transcription()
    check("cleared home URL keeps the existing value", ecfg.whisper_url == "http://192.168.1.50:9000")
    check("cleared online key keeps the existing value", ecfg.online_api_key == "sk-groq-EXISTING")
    edlg.ai_anthropic_key.setText("")
    edlg._save_selfhost_ai()
    check("cleared anthropic key keeps the existing value", ecfg.anthropic_api_key == "sk-ant-EXISTING")

    # ---------------------------------------------------------------
    print("== wizard (Settings re-run, non-mandatory): closing sets onboarding_done ==")
    ccfg = Config()
    check("fresh cfg starts with onboarding_done False", ccfg.onboarding_done is False)
    cdlg = OnboardingDialog(None, ccfg, theme, shell=_Shell())
    cdlg.reject()  # simulates closing / Escape on the re-run
    check("closing the re-run wizard sets onboarding_done", ccfg.onboarding_done is True)

    print("== wizard (first run): MANDATORY — cannot be dismissed until finished ==")
    mcfg = Config()
    mdlg = OnboardingDialog(None, mcfg, theme, shell=_Shell(), mandatory=True)
    check("no title-bar close button in mandatory mode",
          not bool(mdlg.windowFlags() & Qt.WindowType.WindowCloseButtonHint))
    mdlg.reject()  # what Esc triggers
    check("Escape does nothing in mandatory mode", mcfg.onboarding_done is False)
    check("close is refused in mandatory mode",
          mdlg.close() is False and mcfg.onboarding_done is False)
    mdlg._show_page(OnboardingDialog.CHOICE)
    check("Skip is hidden in mandatory mode", mdlg.skip_btn.isHidden())
    mdlg._finish()
    check("finishing still works in mandatory mode", mcfg.onboarding_done is True)
    check("after finish the dialog can close", mdlg.close() is True)

    print("== wizard: the choice screen pushes Earshot Plus ==")
    pcfg = Config()
    pdlg = OnboardingDialog(None, pcfg, theme, shell=_Shell())
    check("Plus card exists and is highlighted", hasattr(pdlg, "plus_choice_card"))
    check("Plus card carries a Recommended badge", pdlg.plus_badge.text() == "Recommended")
    pdlg._choose("plus")
    check("plus path selected", pdlg._path == "plus")
    check("plus → plus page", pdlg.stack.currentIndex() == OnboardingDialog.PLUS)
    check("plus page offers the self-host path, not a bare skip",
          pdlg.plus_skip_btn.text() == "Use my own keys instead")
    pdlg.plus_skip_btn.click()
    check("own-keys routes to the self-host setup",
          pdlg.stack.currentIndex() == OnboardingDialog.SELF_TR)
    check("own-keys switches the path to selfhost", pdlg._path == "selfhost")

    print("== wizard: a successful Plus sign-in advances to Finish (no soft-lock) ==")
    # Regression: on the mandatory run, Next and Skip are hidden on the PLUS
    # page, so a successful sign-in that didn't navigate would strand the user.
    from meeting_notes.ui import cloud_link as _cl

    class _FakeLinkDialog:
        def __init__(self, *a, **k):
            self.linked_ok = True

        def exec(self):
            return 1

    lcfg = Config()
    ldlg = OnboardingDialog(None, lcfg, theme, shell=_Shell(), mandatory=True)
    ldlg._choose("plus")
    orig_dialog = _cl.CloudLinkDialog
    _cl.CloudLinkDialog = _FakeLinkDialog
    try:
        ldlg._plus_sign_in()
    finally:
        _cl.CloudLinkDialog = orig_dialog
    check("Plus sign-in advances to the Finish page",
          ldlg.stack.currentIndex() == OnboardingDialog.FINISH)

    # ---------------------------------------------------------------
    print("== settings: Transcription + AI tabs hidden in cloud mode, shown in selfhost ==")
    set_repo = new_repo()
    scfg = Config()
    scfg.account_mode = "selfhost"
    shell = _Shell()
    spage = SettingsPage(shell, set_repo, scfg, theme)
    app.processEvents()
    tab_texts = spage.section_titles()
    check("selfhost shows the Transcription tab", "Transcription" in tab_texts)
    check("selfhost shows the AI tab", "AI" in tab_texts)

    scfg.account_mode = "cloud"
    spage.refresh_tabs()
    app.processEvents()
    cloud_tabs = spage.section_titles()
    check("cloud HIDES the Transcription tab", "Transcription" not in cloud_tabs)
    check("cloud HIDES the AI tab", "AI" not in cloud_tabs)
    check("cloud keeps the General tab", "General" in cloud_tabs)
    check("cloud keeps the About tab", "About" in cloud_tabs)
    check("Setup guide button still present in cloud mode", hasattr(spage, "run_guide_btn"))

    print("== settings: tabs reappear after sign-out (cloud → selfhost) ==")
    scfg.account_mode = "selfhost"
    spage.refresh_tabs()
    app.processEvents()
    back_tabs = spage.section_titles()
    check("Transcription tab reappears after sign-out", "Transcription" in back_tabs)
    check("AI tab reappears after sign-out", "AI" in back_tabs)

    print("== settings: saving in cloud mode doesn't crash on absent AI/transcription widgets ==")
    scfg.account_mode = "cloud"
    spage.refresh_tabs()
    app.processEvents()
    scfg.online_api_key = "keep-me"
    spage._save()  # must not raise despite the AI/Transcription widgets being gone
    check("cloud save leaves untouched cfg keys intact", scfg.online_api_key == "keep-me")
    set_repo.close()

    # ---------------------------------------------------------------
    print("== account page: selfhost shows the Earshot Plus pitch + subscribe/learn ==")
    acc_repo = new_repo()
    acfg = Config()
    acfg.account_mode = "selfhost"
    apage = AccountPage(_Shell(), acc_repo, acfg, theme)
    app.processEvents()
    check("subscribe button present in selfhost", hasattr(apage, "subscribe_btn"))
    check("learn-more button present in selfhost", hasattr(apage, "learn_btn"))
    check("no subscription/billing card in selfhost", not hasattr(apage, "billing_btn"))

    print("== account page: cloud shows the subscription card (email, billing, sign out) ==")
    # Stub GET /v1/me so the usage worker resolves instantly (no real 10s network
    # connect that would leave a QThread running at interpreter exit).
    from meeting_notes.transcription import earshot_client as _ec

    orig_get_me = _ec.get_me
    _ec.get_me = lambda base, token, **k: {
        "email": "hayden@example.com", "plan": "plus", "sub_status": "active",
        "period_end": "2026-08-06",
        "usage": {"transcribe_seconds": 3600, "cap_seconds": 144000},
        "billing_url": "https://api.tryearshot.app/account",
    }
    try:
        acfg.account_mode = "cloud"
        acfg.cloud_email = "hayden@example.com"
        acfg.cloud_token = "tok"
        apage.refresh()
        app.processEvents()
        check("billing button present in cloud", hasattr(apage, "billing_btn"))
        check("sign-out button present in cloud", hasattr(apage, "signout_btn"))
        check("usage meter present in cloud", hasattr(apage, "usage_bar"))
        check("no Plus pitch subscribe button in cloud", not hasattr(apage, "subscribe_btn"))
        # let the usage worker finish, then verify it filled the meter/renewal
        w = getattr(apage, "_me_worker", None)
        if w is not None:
            w.wait(4000)
        app.processEvents()
        check("usage meter reflects fetched usage (1h of 40h ≈ 2%)", apage.usage_bar.value() == 2)
        check("renewal date shown from period_end", "2026-08-06" in apage.renewal_lbl.text())
    finally:
        _ec.get_me = orig_get_me

    print("== account page: sign-out clears the token instantly + returns to selfhost ==")
    from meeting_notes.transcription import earshot_client as ec

    # Sign-out clears local state SYNCHRONOUSLY and revokes off-thread, so it
    # never blocks the GUI. Point the base at a closed local port so the
    # background revoke fails fast (connection refused) instead of touching the
    # real server, then wait for that worker to finish so no thread leaks.
    acfg.account_mode = "cloud"
    acfg.cloud_token = "tok"
    acfg.cloud_email = "hayden@example.com"
    acfg.cloud_api_base = "http://127.0.0.1:9"  # unreachable — revoke fails fast
    apage._sign_out()
    check("sign-out clears cloud_token immediately", acfg.cloud_token == "")
    check("sign-out returns to selfhost mode", acfg.account_mode == "selfhost")
    check("sign-out re-renders the selfhost pitch", hasattr(apage, "subscribe_btn"))
    rw = getattr(apage, "_revoke_worker", None)
    if rw is not None:
        rw.wait(4000)  # let the best-effort revoke finish (never raises)
    check("revoke ran off the GUI thread", rw is not None)
    acc_repo.close()

    # ---------------------------------------------------------------
    print("== settings/record: scrolling never changes an unfocused control ==")
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QComboBox, QSlider

    def wheel_down(w):
        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(w, ev)

    calm_repo = new_repo()
    ccfg2 = Config()
    cpage = SettingsPage(_Shell(), calm_repo, ccfg2, theme)
    app.processEvents()
    combos = cpage.findChildren(QComboBox)
    sliders = cpage.findChildren(QSlider)
    check("settings page has combos to protect", len(combos) >= 1)
    check("settings page has the opacity slider", len(sliders) >= 1)
    target = next(c for c in combos if c.count() >= 2)
    target.setCurrentIndex(0)
    wheel_down(target)
    check("wheel over an unfocused combo does NOT change it", target.currentIndex() == 0)
    sl = sliders[0]
    sl.setValue(sl.maximum())
    before = sl.value()
    wheel_down(sl)
    check("wheel over an unfocused slider does NOT change it", sl.value() == before)
    check("combos no longer grab focus via the wheel",
          all(c.focusPolicy() == Qt.FocusPolicy.StrongFocus for c in combos))
    calm_repo.close()

    print("== app: one-shot setup-replay flag ==")
    from meeting_notes import app as app_mod

    flag = app_mod._setup_replay_flag()
    check("replay flag lives next to the app, named run_setup_once.flag",
          flag.endswith("run_setup_once.flag"))
    check("no flag -> no replay", app_mod._consume_replay_flag() is False)
    with open(flag, "w", encoding="utf-8") as fh:
        fh.write("1")
    check("flag present -> replay once", app_mod._consume_replay_flag() is True)
    check("flag consumed (deleted) after replay", not os.path.exists(flag))
    check("second launch -> no replay again", app_mod._consume_replay_flag() is False)

    print("\nONBOARDING TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
