"""Tests for Phase S3 — three-provider AI config (Anthropic / OpenAI-compatible
cloud / local): notes-service routing, the one-time Ollama migration, key
preservation across provider switches in Settings, the provider-agnostic Ask
implementation, and the prep-brief meeting-selection helper.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_ai_provider.py
"""
from __future__ import annotations

import json
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
from meeting_notes.notes import anthropic_client, openai_llm  # noqa: E402
from meeting_notes.notes import service as notes_service  # noqa: E402
from meeting_notes.notes.schema import MeetingNotes  # noqa: E402
from meeting_notes.paths import config_path  # noqa: E402
from meeting_notes.qa import ask  # noqa: E402
from meeting_notes.storage.repository import Meeting  # noqa: E402
from meeting_notes.ui.page_record import RecordPage  # noqa: E402
from meeting_notes.ui.page_settings import SettingsPage  # noqa: E402
from meeting_notes.ui.theme_controller import ThemeController  # noqa: E402


def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    assert cond, label


class _Shell:
    def show_home(self):
        pass

    def show_record(self):
        pass

    def open_meeting(self, _mid):
        pass

    def notify_data_changed(self):
        self.notified = True

    class record:
        overlay = None

    class home:
        @staticmethod
        def refresh():
            pass


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841

    # =================================================================
    print("== notes/service.py: provider routing (field selection + base_url) ==")
    calls = {}

    def fake_anthropic_generate(transcript, *, api_key, attendees=None, agenda="", human_date="",
                                 model="", extra_instructions=""):
        calls["engine"] = "anthropic"
        calls["api_key"] = api_key
        calls["model"] = model
        return MeetingNotes(title="T", summary="S", attendees=[], action_items=[], sections=[])

    def fake_openai_generate(transcript, *, base_url, api_key="", model, attendees=None, agenda="",
                              human_date="", max_tokens=4000, extra_instructions=""):
        calls["engine"] = "openai_llm"
        calls["base_url"] = base_url
        calls["api_key"] = api_key
        calls["model"] = model
        return MeetingNotes(title="T2", summary="S2", attendees=[], action_items=[], sections=[])

    orig_anthropic = anthropic_client.generate_notes
    orig_openai = openai_llm.generate_notes
    anthropic_client.generate_notes = fake_anthropic_generate
    openai_llm.generate_notes = fake_openai_generate
    try:
        cfg = Config()
        cfg.notes_provider = "anthropic"
        cfg.anthropic_api_key = "sk-ant-x"
        cfg.anthropic_model = "claude-sonnet-4-6"
        calls.clear()
        notes_service.generate_notes("transcript", cfg)
        check("anthropic routes to anthropic_client", calls["engine"] == "anthropic")
        check("anthropic gets its own key", calls["api_key"] == "sk-ant-x")

        cfg.notes_provider = "openai"
        cfg.llm_base_url = "https://api.openai.com/v1"
        cfg.llm_api_key = "sk-oa-x"
        cfg.llm_model = "gpt-4o-mini"
        calls.clear()
        notes_service.generate_notes("transcript", cfg)
        check("openai routes to openai_llm", calls["engine"] == "openai_llm")
        check("openai gets llm_base_url", calls["base_url"] == "https://api.openai.com/v1")
        check("openai gets llm_api_key", calls["api_key"] == "sk-oa-x")
        check("openai gets llm_model", calls["model"] == "gpt-4o-mini")

        cfg.notes_provider = "local"
        cfg.local_llm_base_url = "http://localhost:11434/v1"
        cfg.local_llm_api_key = ""
        cfg.local_llm_model = "llama3.1"
        calls.clear()
        notes_service.generate_notes("transcript", cfg)
        check("local routes to openai_llm", calls["engine"] == "openai_llm")
        check("local gets local_llm_base_url (NOT llm_base_url)", calls["base_url"] == "http://localhost:11434/v1")
        check("local gets local_llm_api_key", calls["api_key"] == "")
        check("local gets local_llm_model (NOT llm_model)", calls["model"] == "llama3.1")

        # run_action must route the same way (same fields, different call shape)
        run_calls = {}
        orig_run = openai_llm.run_action

        def fake_run_action(instruction, *, base_url, api_key="", model, transcript="",
                             notes_text="", title="", max_tokens=4000):
            run_calls["base_url"] = base_url
            run_calls["model"] = model
            return "ok"

        openai_llm.run_action = fake_run_action
        try:
            cfg.notes_provider = "local"
            notes_service.run_action("do it", cfg, notes_text="x")
            check("run_action(local) uses local_llm_base_url", run_calls["base_url"] == "http://localhost:11434/v1")
            run_calls.clear()
            cfg.notes_provider = "openai"
            notes_service.run_action("do it", cfg, notes_text="x")
            check("run_action(openai) uses llm_base_url", run_calls["base_url"] == "https://api.openai.com/v1")
        finally:
            openai_llm.run_action = orig_run
    finally:
        anthropic_client.generate_notes = orig_anthropic
        openai_llm.generate_notes = orig_openai

    # =================================================================
    print("== notes/service.py: ready() / missing_hint() truth table ==")
    cfg = Config()
    os.environ.pop("ANTHROPIC_API_KEY", None)

    cfg.notes_provider = "anthropic"
    cfg.anthropic_api_key = ""
    check("anthropic: not ready without key", not notes_service.ready(cfg))
    check("anthropic: hint is provider-specific", "Anthropic" in notes_service.missing_hint(cfg))
    cfg.anthropic_api_key = "sk-ant-x"
    check("anthropic: ready with key", notes_service.ready(cfg))

    cfg.notes_provider = "openai"
    cfg.llm_base_url, cfg.llm_model, cfg.llm_api_key = "", "", ""
    check("openai: not ready with nothing set", not notes_service.ready(cfg))
    cfg.llm_base_url = "https://api.openai.com/v1"
    check("openai: not ready without model/key", not notes_service.ready(cfg))
    cfg.llm_model = "gpt-4o-mini"
    check("openai: not ready without key", not notes_service.ready(cfg))
    cfg.llm_api_key = "sk-oa-x"
    check("openai: ready with url+model+key", notes_service.ready(cfg))
    check("openai: hint is provider-specific", "OpenAI" in notes_service.missing_hint(cfg))

    cfg.notes_provider = "local"
    cfg.local_llm_base_url, cfg.local_llm_model = "", ""
    check("local: not ready with nothing set", not notes_service.ready(cfg))
    cfg.local_llm_base_url = "http://localhost:11434/v1"
    check("local: not ready without model", not notes_service.ready(cfg))
    cfg.local_llm_model = "llama3.1"
    check("local: ready with url+model, no key needed", notes_service.ready(cfg))
    check("local: hint is provider-specific", "local" in notes_service.missing_hint(cfg).lower())

    # =================================================================
    print("== config migration: pre-S3 Ollama-as-'openai' config becomes 'local' ==")
    mig_cfg = Config()
    mig_cfg.save()  # establishes the config.json path for this isolated LOCALAPPDATA
    p = config_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    data["notes_provider"] = "openai"
    data["llm_base_url"] = "http://localhost:11434/v1"
    data["llm_api_key"] = ""
    data["llm_model"] = "llama3.1"
    p.write_text(json.dumps(data), encoding="utf-8")

    loaded = Config.load()
    check("migrated provider becomes 'local'", loaded.notes_provider == "local")
    check("migrated local_llm_base_url picked up the old llm_base_url",
          loaded.local_llm_base_url == "http://localhost:11434/v1")
    check("migrated local_llm_model picked up the old llm_model", loaded.local_llm_model == "llama3.1")
    check("llm_base_url reset to the new OpenAI-cloud default", loaded.llm_base_url == "https://api.openai.com/v1")
    check("llm_model cleared", loaded.llm_model == "")

    # a 127.0.0.1 URL migrates the same way as localhost
    data2 = dict(data)
    data2["llm_base_url"] = "http://127.0.0.1:1234/v1"
    data2["llm_model"] = "qwen2.5:14b"
    p.write_text(json.dumps(data2), encoding="utf-8")
    loaded2 = Config.load()
    check("127.0.0.1 also migrates to 'local'", loaded2.notes_provider == "local")
    check("127.0.0.1 base url carried over", loaded2.local_llm_base_url == "http://127.0.0.1:1234/v1")

    # a genuinely-cloud "openai" config must NOT be touched by the migration
    data3 = dict(data)
    data3["notes_provider"] = "openai"
    data3["llm_base_url"] = "https://api.openai.com/v1"
    data3["llm_model"] = "gpt-4o-mini"
    data3["llm_api_key"] = "sk-oa-real"
    p.write_text(json.dumps(data3), encoding="utf-8")
    loaded3 = Config.load()
    check("a real cloud 'openai' config is left alone", loaded3.notes_provider == "openai")
    check("its llm_base_url is untouched", loaded3.llm_base_url == "https://api.openai.com/v1")
    check("its llm_model is untouched", loaded3.llm_model == "gpt-4o-mini")

    # =================================================================
    print("== Settings AI tab: keys for all three providers survive a provider switch + save ==")
    theme = ThemeController(Config())
    theme.apply()
    settings_cfg = Config()
    settings = SettingsPage(_Shell(), None, settings_cfg, theme)

    settings.api_key.setText("sk-ant-keep")
    settings.model.setText("claude-sonnet-4-6")
    settings.llm_base_url.setText("https://api.openai.com/v1")
    settings.llm_key.setText("sk-oa-keep")
    settings.llm_model.setText("gpt-4o-mini")
    settings.local_base_url.setText("http://localhost:11434/v1")
    settings.local_key.setText("")
    settings.local_model.setText("llama3.1")

    # start on anthropic, switch to local, switch to openai — mimics a user
    # trying each provider before settling — then save from the openai tab
    idx_anthropic = settings.notes_provider_combo.findData("anthropic")
    idx_local = settings.notes_provider_combo.findData("local")
    idx_openai = settings.notes_provider_combo.findData("openai")
    settings.notes_provider_combo.setCurrentIndex(idx_anthropic)
    check("claude card visible on anthropic", not settings.claude_card.isHidden())
    check("llm card hidden on anthropic", settings.llm_card.isHidden())
    check("local card hidden on anthropic", settings.local_card.isHidden())
    settings.notes_provider_combo.setCurrentIndex(idx_local)
    check("local card visible on local", not settings.local_card.isHidden())
    check("claude card hidden on local", settings.claude_card.isHidden())
    settings.notes_provider_combo.setCurrentIndex(idx_openai)
    check("llm card visible on openai", not settings.llm_card.isHidden())
    check("local card hidden on openai", settings.local_card.isHidden())

    settings._save()
    reloaded_cfg = Config.load()
    check("provider persisted as openai", reloaded_cfg.notes_provider == "openai")
    check("anthropic key survived (not visible when saved)", reloaded_cfg.anthropic_api_key == "sk-ant-keep")
    check("anthropic model survived", reloaded_cfg.anthropic_model == "claude-sonnet-4-6")
    check("openai (llm_*) key persisted", reloaded_cfg.llm_api_key == "sk-oa-keep")
    check("openai (llm_*) base url persisted", reloaded_cfg.llm_base_url == "https://api.openai.com/v1")
    check("openai (llm_*) model persisted", reloaded_cfg.llm_model == "gpt-4o-mini")
    check("local key survived (not visible when saved)", reloaded_cfg.local_llm_api_key == "")
    check("local base url survived", reloaded_cfg.local_llm_base_url == "http://localhost:11434/v1")
    check("local model survived", reloaded_cfg.local_llm_model == "llama3.1")

    # =================================================================
    print("== qa/ask.py: OpenAI-compatible path (stubbed _chat) ==")

    def make_meeting(mid, title, transcript, days_ago):
        return Meeting(
            id=mid, title=title, date_text=f"{days_ago} days ago", date_iso="2026-07-02",
            attendees=["Hayden"], status="Done", transcript=transcript,
            notes_json=json.dumps({"summary": f"summary for {title}"}),
        )

    meetings5 = [
        make_meeting(1, "Budget sync", "[00:01] Hayden: We agreed the Q3 budget is fifty thousand dollars.", 1),
        make_meeting(2, "Standup", "[00:05] Hayden: Nothing blocking today.", 2),
        make_meeting(3, "Sales call", "[00:10] Hayden: The client wants a discount.", 3),
        make_meeting(4, "1:1 with Sam", "[00:02] Sam: I need more time on the report.", 4),
        make_meeting(5, "Retro", "[00:07] Hayden: We shipped the feature on time.", 5),
    ]

    chat_calls = []

    def fake_chat_select_then_answer(base_url, api_key, model, system, user, *, want_json, max_tokens):
        chat_calls.append({"base_url": base_url, "api_key": api_key, "model": model, "system": system})
        if "select_meetings" in system or "meeting_ids" in system:
            return json.dumps({"meeting_ids": [1]})
        # answer pass: one real (verifiable) quote, one fabricated quote
        return json.dumps({
            "answer": "The Q3 budget was agreed at $50k.",
            "citations": [
                {"meeting_id": 1, "timestamp": "00:01", "quote": "the Q3 budget is fifty thousand dollars"},
                {"meeting_id": 1, "timestamp": "00:02", "quote": "this quote was never actually said"},
            ],
        })

    orig_chat = openai_llm._chat
    openai_llm._chat = fake_chat_select_then_answer
    try:
        ask_cfg = Config()
        ask_cfg.notes_provider = "local"
        ask_cfg.local_llm_base_url = "http://localhost:11434/v1"
        ask_cfg.local_llm_api_key = ""
        ask_cfg.local_llm_model = "llama3.1"

        chat_calls.clear()
        result = ask.answer("What was the Q3 budget?", meetings=meetings5, cfg=ask_cfg, today="2026-07-02")
        check("pass A (select) called for >3 meetings", len(chat_calls) == 2)
        check("selection honoured -> only meeting 1 in scope", "Budget sync" in result.scope
              and "Standup" not in result.scope)
        check("real verbatim quote kept", any("fifty thousand" in c["quote"] for c in result.citations))
        check("fabricated quote dropped", not any("never actually said" in c["quote"] for c in result.citations))
        check("answer text passed through", "50k" in result.text or "budget" in result.text.lower())
        check("local provider's base_url used for the chat calls",
              all(c["base_url"] == "http://localhost:11434/v1" for c in chat_calls))

        # <=3 meetings must skip pass A entirely
        chat_calls.clear()
        result3 = ask.answer("What happened?", meetings=meetings5[:3], cfg=ask_cfg, today="2026-07-02")
        check("<=3 meetings: pass A skipped (exactly 1 chat call, the answer pass)", len(chat_calls) == 1)
        check("scope reflects all 3 meetings when unfiltered", result3.scope.startswith("Searched 3 of 3")
              or "Searched" in result3.scope)
    finally:
        openai_llm._chat = orig_chat

    print("== qa/ask.py: malformed JSON triggers one retry then a clear RuntimeError ==")
    garbage_calls = []

    def fake_chat_garbage(base_url, api_key, model, system, user, *, want_json, max_tokens):
        garbage_calls.append(1)
        return "not json at all"

    openai_llm._chat = fake_chat_garbage
    try:
        try:
            ask.answer("anything", meetings=meetings5[:2], cfg=ask_cfg, today="2026-07-02")
            check("garbage JSON raises RuntimeError", False)
        except RuntimeError as e:
            check("garbage JSON raises RuntimeError", True)
            check("error message is not empty", len(str(e)) > 0)
        # <=3 meetings -> only the answer pass runs, and it retries exactly once
        # (2 attempts total) before giving up, mirroring openai_llm.generate_notes.
        check("exactly one corrective retry (2 attempts) before raising", len(garbage_calls) == 2)
    finally:
        openai_llm._chat = orig_chat

    print("== qa/ask.py: no completed meetings -> friendly Answer, no provider call ==")
    empty_result = ask.answer("anything", meetings=[], cfg=ask_cfg, today="2026-07-02")
    check("empty meetings -> scope 'none'", empty_result.scope == "none")
    check("empty meetings -> friendly text", "don't have any completed" in empty_result.text)

    # =================================================================
    print("== page_record.py: resolve_brief_meetings() ==")
    brief_meetings = [
        Meeting(id=1, title="A", date_text="d1", status="Done", folder_id=10),
        Meeting(id=2, title="B", date_text="d2", status="Done", folder_id=10),
        Meeting(id=3, title="C", date_text="d3", status="Done", folder_id=None),
        Meeting(id=4, title="D", date_text="d4", status="Recording", folder_id=10),  # not Done -> excluded from folder mode
    ]
    folder_result = RecordPage.resolve_brief_meetings(brief_meetings, ("folder", 10))
    check("folder mode returns only Done meetings in that folder",
          [m.id for m in folder_result] == [1, 2])
    check("folder mode excludes a non-Done meeting even if in the folder",
          4 not in [m.id for m in folder_result])

    explicit_result = RecordPage.resolve_brief_meetings(brief_meetings, ("meetings", [3, 1]))
    check("explicit mode returns exactly the picked ids, in the given order",
          [m.id for m in explicit_result] == [3, 1])

    many = [Meeting(id=i, title=f"M{i}", date_text="d", status="Done", folder_id=99) for i in range(1, 15)]
    capped = RecordPage.resolve_brief_meetings(many, ("folder", 99))
    check("folder mode is capped at 10", len(capped) == 10)
    capped_explicit = RecordPage.resolve_brief_meetings(many, ("meetings", [m.id for m in many]))
    check("explicit mode is capped at 10", len(capped_explicit) == 10)

    check("no folder selected (None) -> empty list, no crash",
          RecordPage.resolve_brief_meetings(brief_meetings, ("folder", None)) == [])
    check("falsy payload -> empty list", RecordPage.resolve_brief_meetings(brief_meetings, None) == [])
    check("unknown ids in explicit mode are silently dropped",
          [m.id for m in RecordPage.resolve_brief_meetings(brief_meetings, ("meetings", [999, 1]))] == [1])

    # =================================================================
    print("== page_record.py: BriefPickerDialog (folder combo + checkbox cap) ==")
    from PySide6.QtCore import Qt as _Qt

    from meeting_notes.ui.page_record import BriefPickerDialog

    class _FakeRepo:
        def __init__(self, folders, meetings):
            self._folders = folders
            self._meetings = meetings

        def list_folders(self):
            return self._folders

        def list(self, limit=500):
            return self._meetings[:limit]

    class _F:
        def __init__(self, id, name):
            self.id, self.name = id, name

    fake_folders = [_F(10, "Client X")]
    fake_done = [Meeting(id=i, title=f"Meeting {i}", date_text=f"d{i}", status="Done") for i in range(1, 16)]
    picker = BriefPickerDialog(None, _FakeRepo(fake_folders, fake_done), theme)
    check("folder combo lists the fake folder", picker.folder_combo.count() == 1
          and picker.folder_combo.itemText(0) == "Client X")
    check("meeting list capped at the 25-recent limit (only 15 available here)", picker.meeting_list.count() == 15)
    check("folder radio checked by default", picker.folder_radio.isChecked())
    check("default payload is the folder mode", picker.payload()[0] == "folder")

    picker.meetings_radio.setChecked(True)
    check("payload switches to meetings mode", picker.payload()[0] == "meetings")
    for i in range(11):  # check 11 items — one over the cap
        picker.meeting_list.item(i).setCheckState(_Qt.CheckState.Checked)
    _kind, picked_ids = picker.payload()
    check("checking 11 evicts the oldest so at most 10 remain", len(picked_ids) == 10)
    check("the very first checked item (index 0) was evicted",
          fake_done[0].id not in picked_ids)
    check("the most recently checked item (index 10) is kept",
          fake_done[10].id in picked_ids)

    # a repo with no folders disables the folder option entirely
    no_folder_picker = BriefPickerDialog(None, _FakeRepo([], fake_done), theme)
    check("folder radio disabled when there are no folders", not no_folder_picker.folder_radio.isEnabled())

    print("\nAI PROVIDER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
