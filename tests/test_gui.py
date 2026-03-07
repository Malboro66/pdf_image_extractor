"""Plano de testes unitários para os componentes de UI refatorados.

Estratégia: todos os testes de lógica rodam sem instanciar widgets tkinter
(testando AppViewModel e SettingsModel diretamente). Apenas testes de
integração criam raiz Tk, e são marcados com @pytest.mark.gui para
exclusão fácil em pipelines headless (CI sem display).

Executar apenas testes sem GUI:
  pytest tests/test_gui.py -m "not gui"

Executar tudo (requer display):
  pytest tests/test_gui.py
"""

from __future__ import annotations

import json
import tkinter as tk
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

import pytest

from extract_images_gui import (
    AppViewModel,
    ConfigPanel,
    CoreJobAdapter,
    QueuePanel,
    ResultsPanel,
    SettingsModel,
    StatusBar,
    WidgetFactory,
)


class _FakeAdapter:
    """Adaptador falso que captura chamadas sem executar job real."""

    def __init__(self) -> None:
        self.called = False
        self.last_settings: SettingsModel | None = None
        self.last_paths: list[Path] = []
        self._on_done: Callable | None = None

    def run_async(
        self,
        settings,
        input_paths,
        on_progress,
        on_record,
        on_done,
        on_error,
    ):
        del on_progress, on_record, on_error
        self.called = True
        self.last_settings = settings
        self.last_paths = list(input_paths)
        self._on_done = on_done

    def finish(self, extracted=1, skipped=0, errors=0) -> None:
        if self._on_done:
            self._on_done(extracted, skipped, errors)


class TestSettingsModel(unittest.TestCase):
    def test_default_values(self) -> None:
        settings = SettingsModel()
        self.assertEqual(settings.engine, "auto")
        self.assertEqual(settings.max_workers, 4)
        self.assertFalse(settings.recursive)
        self.assertTrue(settings.continue_on_error)

    def test_save_and_load_roundtrip(self) -> None:
        with mock.patch("pathlib.Path.write_text") as write_text_mock, mock.patch(
            "pathlib.Path.exists", return_value=True
        ), mock.patch("pathlib.Path.read_text") as read_text_mock:
            original = SettingsModel(prefix="test_prefix", max_workers=8)
            original.save(Path("/fake/settings.json"))

            payload = json.dumps(
                {
                    "prefix": "test_prefix",
                    "max_workers": 8,
                    "output_dir": original.output_dir,
                    "engine": "auto",
                    "recursive": False,
                    "continue_on_error": True,
                    "report_base": "relatorio_extracao",
                }
            )
            read_text_mock.return_value = payload
            loaded = SettingsModel.load(Path("/fake/settings.json"))

            write_text_mock.assert_called_once()
            self.assertEqual(loaded.prefix, "test_prefix")
            self.assertEqual(loaded.max_workers, 8)

    def test_load_returns_defaults_when_file_missing(self) -> None:
        with mock.patch("pathlib.Path.exists", return_value=False):
            settings = SettingsModel.load(Path("/nonexistent.json"))
        self.assertEqual(settings.engine, "auto")

    def test_load_returns_defaults_on_corrupt_json(self) -> None:
        with mock.patch("pathlib.Path.exists", return_value=True), mock.patch(
            "pathlib.Path.read_text", return_value="{invalid json}"
        ):
            settings = SettingsModel.load(Path("/corrupt.json"))
        self.assertEqual(settings.engine, "auto")


class TestAppViewModel(unittest.TestCase):
    def _make_vm(self) -> tuple[AppViewModel, _FakeAdapter]:
        adapter = _FakeAdapter()
        with mock.patch("pathlib.Path.exists", return_value=False):
            vm = AppViewModel(adapter)
        return vm, adapter

    def test_add_paths_emits_queue_changed(self) -> None:
        vm, _ = self._make_vm()
        received: list = []
        vm.observe("queue_changed", received.append)

        vm.add_paths([Path("a.pdf"), Path("b.pdf")])

        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0]), 2)

    def test_remove_at_valid_index(self) -> None:
        vm, _ = self._make_vm()
        vm.input_paths = [Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]

        vm.remove_at(1)

        self.assertEqual(vm.input_paths, [Path("a.pdf"), Path("c.pdf")])

    def test_remove_at_invalid_index_no_crash(self) -> None:
        vm, _ = self._make_vm()
        vm.input_paths = [Path("a.pdf")]

        vm.remove_at(99)

        self.assertEqual(len(vm.input_paths), 1)

    def test_move_up(self) -> None:
        vm, _ = self._make_vm()
        vm.input_paths = [Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]

        new_idx = vm.move(2, -1)

        self.assertEqual(new_idx, 1)
        self.assertEqual(vm.input_paths[1], Path("c.pdf"))
        self.assertEqual(vm.input_paths[2], Path("b.pdf"))

    def test_move_clamps_at_boundaries(self) -> None:
        vm, _ = self._make_vm()
        vm.input_paths = [Path("a.pdf"), Path("b.pdf")]

        new_idx = vm.move(0, -1)

        self.assertEqual(new_idx, 0)
        self.assertEqual(vm.input_paths[0], Path("a.pdf"))

    def test_swap_two_items(self) -> None:
        vm, _ = self._make_vm()
        vm.input_paths = [Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]

        vm.swap(0, 2)

        self.assertEqual(vm.input_paths[0], Path("c.pdf"))
        self.assertEqual(vm.input_paths[2], Path("a.pdf"))

    def test_clear_queue(self) -> None:
        vm, _ = self._make_vm()
        vm.input_paths = [Path("a.pdf"), Path("b.pdf")]

        vm.clear_queue()

        self.assertEqual(vm.input_paths, [])

    def test_start_job_emits_job_started_and_calls_adapter(self) -> None:
        vm, adapter = self._make_vm()
        vm.input_paths = [Path("a.pdf")]
        events: list[str] = []
        vm.observe("job_started", lambda: events.append("started"))

        with mock.patch.object(vm, "save_settings"):
            vm.start_job()

        self.assertIn("started", events)
        self.assertTrue(adapter.called)
        self.assertEqual(adapter.last_paths, [Path("a.pdf")])

    def test_start_job_with_empty_queue_emits_validation_error(self) -> None:
        vm, adapter = self._make_vm()
        errors: list[str] = []
        vm.observe("validation_error", errors.append)

        vm.start_job()

        self.assertFalse(adapter.called)
        self.assertEqual(len(errors), 1)

    def test_multiple_observers_all_notified(self) -> None:
        vm, _ = self._make_vm()
        calls: list[str] = []
        vm.observe("queue_changed", lambda _: calls.append("obs1"))
        vm.observe("queue_changed", lambda _: calls.append("obs2"))

        vm.add_paths([Path("x.pdf")])

        self.assertEqual(calls, ["obs1", "obs2"])

    def test_settings_synced_to_adapter_on_start(self) -> None:
        vm, adapter = self._make_vm()
        vm.settings.prefix = "custom_prefix"
        vm.input_paths = [Path("a.pdf")]

        with mock.patch.object(vm, "save_settings"):
            vm.start_job()

        assert adapter.last_settings is not None
        self.assertEqual(adapter.last_settings.prefix, "custom_prefix")


@pytest.mark.gui
class TestQueuePanelIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        adapter = _FakeAdapter()
        with mock.patch("pathlib.Path.exists", return_value=False):
            self.vm = AppViewModel(adapter)
        self.panel = QueuePanel(self.root, self.vm)

    def tearDown(self) -> None:
        self.root.destroy()

    def test_listbox_updates_on_queue_changed(self) -> None:
        self.vm.add_paths([Path("x.pdf"), Path("y.pdf")])
        self.root.update()

        items = list(self.panel._listbox.get(0, "end"))
        self.assertEqual(items, ["x.pdf", "y.pdf"])

    def test_listbox_cleared_when_queue_cleared(self) -> None:
        self.vm.add_paths([Path("x.pdf")])
        self.vm.clear_queue()
        self.root.update()

        self.assertEqual(self.panel._listbox.size(), 0)

    def test_buttons_disabled_on_job_started(self) -> None:
        self.vm._emit("job_started")
        self.root.update()

        for button in self.panel._buttons:
            self.assertEqual(str(button.cget("state")), "disabled")

    def test_buttons_reenabled_on_job_done(self) -> None:
        self.vm._emit("job_started")
        self.vm._emit("job_done", 1, 0, 0)
        self.root.update()

        for button in self.panel._buttons:
            self.assertNotEqual(str(button.cget("state")), "disabled")


@pytest.mark.gui
class TestStatusBarIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.adapter = _FakeAdapter()
        with mock.patch("pathlib.Path.exists", return_value=False):
            self.vm = AppViewModel(self.adapter)
        self.bar = StatusBar(self.root, self.vm)

    def tearDown(self) -> None:
        self.root.destroy()

    def test_progress_label_updates(self) -> None:
        self.vm._emit("progress", 3, 10, 7.5)
        self.root.update()

        text = self.bar._progress_label.cget("text")
        self.assertIn("3/10", text)
        self.assertIn("30.0%", text)

    def test_run_button_disabled_during_job(self) -> None:
        self.vm._emit("job_started")
        self.root.update()

        self.assertEqual(str(self.bar._run_btn.cget("state")), "disabled")

    def test_run_button_reenabled_after_job(self) -> None:
        self.vm._emit("job_started")
        self.vm._emit("job_done", 5, 1, 0)
        self.root.update()

        self.assertEqual(str(self.bar._run_btn.cget("state")), "normal")

    def test_log_appends_messages(self) -> None:
        self.vm._emit("log", "Mensagem de teste")
        self.root.update()

        content = self.bar._log.get("1.0", "end")
        self.assertIn("Mensagem de teste", content)


@pytest.mark.gui
class TestResultsPanelIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        with mock.patch("pathlib.Path.exists", return_value=False):
            self.vm = AppViewModel(_FakeAdapter())
        self.panel = ResultsPanel(self.root, self.vm)

    def tearDown(self) -> None:
        self.root.destroy()

    def test_record_inserted_in_table(self) -> None:
        record = {
            "output_file": "/out/img_0001.jpg",
            "page": 1,
            "status": "ok",
            "output_bytes": 1024,
            "error": None,
        }
        self.vm._emit("record", record)
        self.root.update()

        rows = self.panel._table.get_children()
        self.assertEqual(len(rows), 1)
        values = self.panel._table.item(rows[0], "values")
        self.assertIn("ok", values)

    def test_table_cleared_on_job_started(self) -> None:
        for i in range(3):
            self.vm._emit(
                "record",
                {
                    "output_file": f"/out/img_{i}.jpg",
                    "page": i,
                    "status": "ok",
                    "output_bytes": 0,
                    "error": None,
                },
            )
        self.vm._emit("job_started")
        self.root.update()

        self.assertEqual(len(self.panel._table.get_children()), 0)

    def test_error_record_gets_error_tag(self) -> None:
        record = {
            "output_file": None,
            "page": 1,
            "status": "error",
            "output_bytes": 0,
            "error": "parse failed",
        }
        self.vm._emit("record", record)
        self.root.update()

        rows = self.panel._table.get_children()
        tags = self.panel._table.item(rows[0], "tags")
        self.assertIn("error", tags)


class TestModuleSymbols(unittest.TestCase):
    def test_expected_exports_are_importable(self) -> None:
        self.assertIsNotNone(CoreJobAdapter)
        self.assertIsNotNone(ConfigPanel)
        self.assertIsNotNone(WidgetFactory)


if __name__ == "__main__":
    unittest.main()
