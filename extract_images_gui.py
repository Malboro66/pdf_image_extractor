#!/usr/bin/env python3
"""GUI enterprise para extração de imagens de PDF.

Arquitetura em camadas:
  - AppViewModel   : estado observável, sem dependência de tkinter.
  - JobAdapter     : ponte injetável entre ViewModel e Core pipeline.
  - Panels         : componentes de UI puros, observam o ViewModel.
  - WidgetFactory  : factory centralizada para widgets estilizados.

Compatível com Python 3.10+ / Windows / Linux / macOS.
PEP 8 compliant.
"""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Protocol

# ---------------------------------------------------------------------------
# Constantes de estilo (único ponto de verdade — sem hardcode espalhado)
# ---------------------------------------------------------------------------

SETTINGS_PATH = Path.home() / ".pdf_image_extractor_gui.json"

COLORS = {
    "bg": "#111827",
    "card": "#1f2937",
    "text": "#f9fafb",
    "muted": "#9ca3af",
    "accent": "#2563eb",
    "success": "#16a34a",
    "error": "#dc2626",
    "row_ok": "#052e16",
    "row_err": "#450a0a",
}

FONTS = {
    "title": ("Segoe UI", 15, "bold"),
    "body": ("Segoe UI", 10),
    "mono": ("Consolas", 9),
}


# ---------------------------------------------------------------------------
# SettingsModel — dataclass puro, serializável, sem tkinter
# ---------------------------------------------------------------------------


@dataclass
class SettingsModel:
    """Estado persistível das configurações do job."""

    output_dir: str = str(Path("imagens_extraidas").resolve())
    prefix: str = "imagem"
    engine: str = "auto"
    recursive: bool = False
    continue_on_error: bool = True
    max_workers: int = 4
    report_base: str = "relatorio_extracao"

    @staticmethod
    def _coerce_max_workers(value: object, default: int = 4) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    def save(self, path: Path = SETTINGS_PATH) -> None:
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "SettingsModel":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            if "max_workers" in filtered:
                filtered["max_workers"] = cls._coerce_max_workers(filtered["max_workers"])
            return cls(**filtered)
        except Exception:
            return cls()


# ---------------------------------------------------------------------------
# JobAdapter Protocol — permite injeção de dependência e mock em testes
# ---------------------------------------------------------------------------


class JobAdapterProtocol(Protocol):
    """Contrato do adaptador de job — desacopla GUI do Core."""

    def run_async(
        self,
        settings: SettingsModel,
        input_paths: list[Path],
        on_progress: Callable[[int, int, float], None],
        on_record: Callable[[dict], None],
        on_done: Callable[[int, int, int], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Inicia job em thread separada; todos os callbacks são thread-safe."""
        ...


class CoreJobAdapter:
    """Implementação real que delega ao pipeline existente."""

    def run_async(
        self,
        settings: SettingsModel,
        input_paths: list[Path],
        on_progress: Callable[[int, int, float], None],
        on_record: Callable[[dict], None],
        on_done: Callable[[int, int, int], None],
        on_error: Callable[[str], None],
    ) -> None:
        threading.Thread(
            target=self._run,
            args=(settings, input_paths, on_progress, on_record, on_done, on_error),
            daemon=True,
        ).start()

    def _run(
        self,
        settings: SettingsModel,
        input_paths: list[Path],
        on_progress: Callable,
        on_record: Callable,
        on_done: Callable,
        on_error: Callable,
    ) -> None:
        # Import local para não poluir o namespace global da GUI.
        from pdf_image_extractor.core.models import ExtractionConfig, ExtractionRecord
        from pdf_image_extractor.core.pipeline import (
            JobOrchestrator,
            NullProgressEmitter,
            ReportWriter,
            collect_pdfs_from_inputs,
        )

        class _BridgeEmitter(NullProgressEmitter):
            """Emite eventos de progresso de volta para a GUI via callbacks."""

            def __init__(self, total: int, start_ts: float) -> None:
                self._total = total
                self._start = start_ts

            def on_pdf_finished(
                self,
                pdf: Path,
                records: list[ExtractionRecord],
                errors: int,
                index: int,
                total: int,
            ) -> None:
                del pdf, errors
                elapsed = time.perf_counter() - self._start
                avg = elapsed / max(1, index)
                eta = max(0.0, avg * (total - index))
                on_progress(index, total, eta)
                for record in records:
                    on_record(asdict(record))

            def on_error(self, pdf: Path | None, error: Exception | str) -> None:
                on_error(f"Erro em {pdf or '-'}: {error}")

        try:
            cfg = ExtractionConfig(
                input_paths=input_paths,
                output_dir=Path(settings.output_dir),
                prefix=settings.prefix,
                recursive=settings.recursive,
                continue_on_error=settings.continue_on_error,
                engine=settings.engine,
                quiet=True,
                max_workers=max(1, settings.max_workers),
                report=Path(settings.report_base),
            )
            pdfs = collect_pdfs_from_inputs(cfg.input_paths, cfg.recursive)
            if not pdfs:
                on_error("Nenhum PDF encontrado para processar.")
                on_done(0, 0, 0)
                return

            emitter = _BridgeEmitter(len(pdfs), time.perf_counter())
            all_records, _ = JobOrchestrator(
                cfg,
                progress_emitter=emitter,
                report_writer=ReportWriter(),
            ).run()

            extracted = sum(1 for record in all_records if record.status == "ok")
            skipped = sum(1 for record in all_records if record.status.startswith("skipped"))
            errors = sum(
                1
                for record in all_records
                if record.status in {"error", "timeout", "blocked_policy"}
            )
            on_done(extracted, skipped, errors)

        except Exception as exc:
            on_error(f"Erro crítico: {exc}")
            on_done(0, 0, 1)


# ---------------------------------------------------------------------------
# AppViewModel — estado observável sem tkinter
# ---------------------------------------------------------------------------


class AppViewModel:
    """Gerencia o estado da aplicação de forma desacoplada da UI.

    Os Panels se registram como observadores e são notificados via callbacks
    quando o estado muda. Não há import de tkinter aqui.
    """

    def __init__(self, adapter: JobAdapterProtocol) -> None:
        self._adapter = adapter
        self.settings = SettingsModel.load()
        self.input_paths: list[Path] = []
        self._observers: dict[str, list[Callable]] = {}

    def observe(self, event: str, callback: Callable) -> None:
        self._observers.setdefault(event, []).append(callback)

    def _emit(self, event: str, *args) -> None:
        for callback in self._observers.get(event, []):
            callback(*args)

    def add_paths(self, paths: list[Path]) -> None:
        self.input_paths.extend(paths)
        self._emit("queue_changed", self.input_paths)

    def remove_at(self, index: int) -> None:
        if 0 <= index < len(self.input_paths):
            self.input_paths.pop(index)
            self._emit("queue_changed", self.input_paths)

    def move(self, index: int, delta: int) -> int:
        next_index = max(0, min(len(self.input_paths) - 1, index + delta))
        if index != next_index:
            self.input_paths[index], self.input_paths[next_index] = (
                self.input_paths[next_index],
                self.input_paths[index],
            )
            self._emit("queue_changed", self.input_paths)
        return next_index

    def clear_queue(self) -> None:
        self.input_paths.clear()
        self._emit("queue_changed", self.input_paths)

    def swap(self, index_a: int, index_b: int) -> None:
        """Troca dois itens (usado por drag-and-drop)."""
        if (
            index_a != index_b
            and 0 <= index_a < len(self.input_paths)
            and 0 <= index_b < len(self.input_paths)
        ):
            self.input_paths[index_a], self.input_paths[index_b] = (
                self.input_paths[index_b],
                self.input_paths[index_a],
            )
            self._emit("queue_changed", self.input_paths)

    def save_settings(self) -> None:
        self.settings.save()

    def start_job(self) -> None:
        if not self.input_paths:
            self._emit("validation_error", "Adicione ao menos um PDF ou diretório na fila.")
            return

        self.save_settings()
        self._emit("job_started")

        self._adapter.run_async(
            settings=self.settings,
            input_paths=list(self.input_paths),
            on_progress=lambda done, total, eta: self._emit("progress", done, total, eta),
            on_record=lambda record: self._emit("record", record),
            on_done=lambda ok, skipped, err: self._emit("job_done", ok, skipped, err),
            on_error=lambda msg: self._emit("log", msg),
        )


class WidgetFactory:
    """Cria widgets estilizados com uma única chamada."""

    @staticmethod
    def configure_style(master: tk.Tk) -> None:
        style = ttk.Style(master)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        master.configure(bg=COLORS["bg"])
        style.configure("App.TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure(
            "Title.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text"],
            font=FONTS["title"],
        )
        style.configure(
            "Body.TLabel",
            background=COLORS["card"],
            foreground=COLORS["text"],
            font=FONTS["body"],
        )
        style.configure("Hint.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("Ok.TLabel", background=COLORS["row_ok"], foreground=COLORS["text"])
        style.configure("Err.TLabel", background=COLORS["row_err"], foreground=COLORS["text"])
        style.map(
            "Accent.TButton",
            background=[("active", COLORS["accent"]), ("!active", COLORS["accent"])],
            foreground=[("active", COLORS["text"]), ("!active", COLORS["text"])],
        )

    @staticmethod
    def button(
        parent: tk.Widget,
        text: str,
        command: Callable,
        width: int | None = None,
    ) -> ttk.Button:
        kwargs: dict[str, object] = {"text": text, "command": command}
        if width:
            kwargs["width"] = width
        return ttk.Button(parent, **kwargs)

    @staticmethod
    def label(parent: tk.Widget, text: str, style: str = "Body.TLabel") -> ttk.Label:
        return ttk.Label(parent, text=text, style=style)

    @staticmethod
    def scrolled_listbox(parent: tk.Widget) -> tuple[tk.Listbox, ttk.Scrollbar]:
        listbox = tk.Listbox(
            parent,
            bg="#0b1220",
            fg=COLORS["text"],
            selectbackground=COLORS["accent"],
            relief="flat",
            activestyle="none",
        )
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        return listbox, scrollbar

    @staticmethod
    def progress_bar(parent: tk.Widget, variable: tk.DoubleVar) -> ttk.Progressbar:
        return ttk.Progressbar(parent, variable=variable, maximum=100)

    @staticmethod
    def log_area(parent: tk.Widget, height: int = 5) -> tk.Text:
        return tk.Text(
            parent,
            height=height,
            bg="#030712",
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=FONTS["mono"],
            state="disabled",
        )


class QueuePanel(ttk.Frame):
    """Painel de fila: listbox + botões + drag-and-drop."""

    def __init__(self, parent: tk.Widget, vm: AppViewModel) -> None:
        super().__init__(parent, style="Card.TFrame", padding=12)
        self._vm = vm
        self._drag_index: int | None = None
        self._build()
        vm.observe("queue_changed", self._on_queue_changed)
        vm.observe("job_started", lambda: self._set_controls_state("disabled"))
        vm.observe("job_done", lambda *_: self._set_controls_state("normal"))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        WidgetFactory.label(self, "Fila de entradas").grid(row=0, column=0, sticky="w")

        toolbar = ttk.Frame(self, style="Card.TFrame")
        toolbar.grid(row=0, column=0, sticky="e")

        btn_specs = [
            ("PDFs", self._add_files),
            ("Pasta", self._add_folder),
            ("↑", lambda: self._move(-1)),
            ("↓", lambda: self._move(1)),
            ("Remover", self._remove),
            ("Limpar", self._vm.clear_queue),
        ]
        self._buttons: list[ttk.Button] = []
        for column, (text, cmd) in enumerate(btn_specs):
            button = WidgetFactory.button(toolbar, text, cmd)
            button.grid(row=0, column=column, padx=2)
            self._buttons.append(button)

        list_frame = ttk.Frame(self, style="Card.TFrame")
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self._listbox, scrollbar = WidgetFactory.scrolled_listbox(list_frame)
        self._listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._listbox.bind("<Delete>", lambda _: self._remove())
        self._listbox.bind("<Button-1>", self._drag_start)
        self._listbox.bind("<B1-Motion>", self._drag_motion)

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecione PDFs",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")],
        )
        if paths:
            self._vm.add_paths([Path(path) for path in paths])

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Selecione pasta")
        if folder:
            self._vm.add_paths([Path(folder)])

    def _remove(self) -> None:
        for index in reversed(list(self._listbox.curselection())):
            self._vm.remove_at(index)

    def _move(self, delta: int) -> None:
        selection = self._listbox.curselection()
        if not selection:
            return
        new_index = self._vm.move(selection[0], delta)
        self._listbox.selection_set(new_index)

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_index = self._listbox.nearest(event.y)

    def _drag_motion(self, event: tk.Event) -> None:
        if self._drag_index is None:
            return
        target = self._listbox.nearest(event.y)
        if target != self._drag_index:
            self._vm.swap(self._drag_index, target)
            self._drag_index = target

    def _on_queue_changed(self, paths: list[Path]) -> None:
        current = list(self._listbox.get(0, "end"))
        new_paths = [str(path) for path in paths]
        if current == new_paths:
            return
        self._listbox.delete(0, "end")
        for path in new_paths:
            self._listbox.insert("end", path)

    def _set_controls_state(self, state: str) -> None:
        for button in self._buttons:
            button.configure(state=state)


class ConfigPanel(ttk.Frame):
    """Painel de configuração com validação de campos."""

    def __init__(self, parent: tk.Widget, vm: AppViewModel) -> None:
        super().__init__(parent, style="Card.TFrame", padding=12)
        self._vm = vm
        self._build()
        vm.observe("job_started", lambda: self._set_state("disabled"))
        vm.observe("job_done", lambda *_: self._set_state("normal"))

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)

        settings = self._vm.settings
        self._output_var = tk.StringVar(value=settings.output_dir)
        self._prefix_var = tk.StringVar(value=settings.prefix)
        self._engine_var = tk.StringVar(value=settings.engine)
        self._recursive_var = tk.BooleanVar(value=settings.recursive)
        self._continue_var = tk.BooleanVar(value=settings.continue_on_error)
        self._workers_var = tk.StringVar(value=str(settings.max_workers))

        self._output_var.trace_add("write", lambda *_: self._sync())
        self._prefix_var.trace_add("write", lambda *_: self._sync())
        self._engine_var.trace_add("write", lambda *_: self._sync())
        self._recursive_var.trace_add("write", lambda *_: self._sync())
        self._continue_var.trace_add("write", lambda *_: self._sync())
        self._workers_var.trace_add("write", lambda *_: self._sync())

        self._widgets: list[tk.Widget] = []

        self._build_output_row(0)
        self._build_prefix_engine_row(1)
        self._build_options_row(2)

    def _build_output_row(self, row: int) -> None:
        WidgetFactory.label(self, "Saída").grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(self, textvariable=self._output_var)
        entry.grid(row=row, column=1, sticky="ew", padx=6)
        button = WidgetFactory.button(self, "Selecionar", self._pick_output)
        button.grid(row=row, column=2)
        self._widgets += [entry, button]

    def _build_prefix_engine_row(self, row: int) -> None:
        WidgetFactory.label(self, "Prefixo").grid(row=row, column=0, sticky="w", pady=2)
        prefix_entry = ttk.Entry(self, textvariable=self._prefix_var)
        prefix_entry.grid(row=row, column=1, sticky="ew", padx=6)
        combo = ttk.Combobox(
            self,
            textvariable=self._engine_var,
            values=["auto", "pypdf", "fallback"],
            state="readonly",
            width=10,
        )
        combo.grid(row=row, column=2)
        self._widgets += [prefix_entry, combo]

    def _build_options_row(self, row: int) -> None:
        options_frame = ttk.Frame(self, style="Card.TFrame")
        options_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        chk_recursive = ttk.Checkbutton(
            options_frame,
            text="Recursive",
            variable=self._recursive_var,
        )
        chk_recursive.grid(row=0, column=0, padx=(0, 12))

        chk_continue = ttk.Checkbutton(
            options_frame,
            text="Continuar com erro",
            variable=self._continue_var,
        )
        chk_continue.grid(row=0, column=1, padx=(0, 12))

        WidgetFactory.label(options_frame, "Workers").grid(row=0, column=2, padx=(0, 4))
        spinbox = ttk.Spinbox(
            options_frame,
            from_=1,
            to=32,
            textvariable=self._workers_var,
            width=4,
        )
        spinbox.grid(row=0, column=3)

        self._widgets += [chk_recursive, chk_continue, spinbox]

    def _pick_output(self) -> None:
        folder = filedialog.askdirectory(title="Selecione pasta de saída")
        if folder:
            self._output_var.set(folder)

    def _sync(self) -> None:
        settings = self._vm.settings
        settings.output_dir = self._output_var.get()
        settings.prefix = self._prefix_var.get() or "imagem"
        settings.engine = self._engine_var.get()
        settings.recursive = self._recursive_var.get()
        settings.continue_on_error = self._continue_var.get()
        settings.max_workers = settings._coerce_max_workers(
            self._workers_var.get(),
            default=settings.max_workers,
        )

    def _set_state(self, state: str) -> None:
        for widget in self._widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass


class ResultsPanel(ttk.Frame):
    """Exibe resultados em tabela e preview de imagem selecionada."""

    _COLS = [
        ("arquivo", 190),
        ("pag", 40),
        ("fmt", 50),
        ("status", 110),
        ("bytes", 70),
        ("erro", 220),
    ]

    def __init__(self, parent: tk.Widget, vm: AppViewModel) -> None:
        super().__init__(parent, style="Card.TFrame", padding=12)
        self._preview_image: tk.PhotoImage | None = None
        self._build()
        vm.observe("record", self._on_record)
        vm.observe("job_started", self._clear_table)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        WidgetFactory.label(self, "Resultados").grid(row=0, column=0, sticky="w")

        self._table = ttk.Treeview(
            self,
            columns=[column for column, _ in self._COLS],
            show="headings",
            height=12,
        )
        for column, width in self._COLS:
            self._table.heading(column, text=column.upper())
            self._table.column(column, width=width, anchor="w")
        self._table.grid(row=1, column=0, sticky="nsew")
        self._table.tag_configure("ok", background=COLORS["row_ok"])
        self._table.tag_configure("error", background=COLORS["row_err"])
        self._table.bind("<<TreeviewSelect>>", self._on_select)

        self._preview_label = ttk.Label(self, text="Preview indisponível")
        self._preview_label.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

    def _clear_table(self) -> None:
        for item in self._table.get_children():
            self._table.delete(item)

    def _on_record(self, record: dict) -> None:
        output = record.get("output_file") or "-"
        ext = Path(output).suffix.replace(".", "") if output != "-" else "-"
        status = record.get("status", "")
        err = (record.get("error") or "")[:120]
        tag = "ok" if status == "ok" else ("error" if "error" in status else "")
        self._table.insert(
            "",
            "end",
            values=(
                output,
                record.get("page") or "-",
                ext,
                status,
                record.get("output_bytes"),
                err,
            ),
            tags=(tag,),
        )
        children = self._table.get_children()
        if children:
            self._table.see(children[-1])

    def _on_select(self, _event: tk.Event) -> None:
        selection = self._table.selection()
        if not selection:
            return
        row = self._table.item(selection[0], "values")
        output_file = row[0] if row else None
        if not output_file or output_file in {"-", "None"}:
            return
        threading.Thread(
            target=self._load_preview,
            args=(Path(output_file),),
            daemon=True,
        ).start()

    def _load_preview(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            image = tk.PhotoImage(file=str(path))
            while image.width() > 200 or image.height() > 200:
                image = image.subsample(2)
            self.after(0, lambda i=image: self._set_preview(i))
        except Exception:
            self.after(
                0,
                lambda: self._preview_label.configure(
                    image="",
                    text="Preview: formato não suportado pelo Tk",
                ),
            )

    def _set_preview(self, img: tk.PhotoImage) -> None:
        self._preview_image = img
        self._preview_label.configure(image=img, text="")


class StatusBar(ttk.Frame):
    """Barra de status: progresso, ETA, log e botões de ação."""

    def __init__(self, parent: tk.Widget, vm: AppViewModel) -> None:
        super().__init__(parent, style="Card.TFrame", padding=8)
        self._vm = vm
        self._progress_var = tk.DoubleVar(value=0.0)
        self._build()
        vm.observe("progress", self._on_progress)
        vm.observe("job_started", self._on_job_started)
        vm.observe("job_done", self._on_job_done)
        vm.observe("log", self._append_log)
        vm.observe("validation_error", lambda msg: messagebox.showwarning("Validação", msg))

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)

        self._progress_label = WidgetFactory.label(self, "Aguardando...")
        self._progress_label.grid(row=0, column=0, columnspan=3, sticky="w")

        progress = WidgetFactory.progress_bar(self, self._progress_var)
        progress.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 6))

        self._run_btn = WidgetFactory.button(self, "▶ Extrair imagens", self._on_run)
        self._run_btn.grid(row=2, column=2, sticky="e")

        ttk.Button(self, text="Abrir relatório", command=self._open_report).grid(
            row=2,
            column=0,
            sticky="w",
        )

        self._log = WidgetFactory.log_area(self, height=5)
        self._log.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        self._append_log("Pronto. Adicione PDFs e clique em Extrair imagens.")

    def _on_run(self) -> None:
        self._vm.start_job()

    def _on_job_started(self) -> None:
        self._run_btn.configure(state="disabled", text="⏳ Processando...")
        self._progress_var.set(0.0)
        self._progress_label.configure(text="Iniciando...")

    def _on_progress(self, done: int, total: int, eta: float) -> None:
        pct = (done / max(1, total)) * 100.0
        self._progress_var.set(pct)
        self._progress_label.configure(text=f"{done}/{total} ({pct:.1f}%)  —  ETA {eta:.1f}s")

    def _on_job_done(self, extracted: int, skipped: int, errors: int) -> None:
        self._run_btn.configure(state="normal", text="▶ Extrair imagens")
        self._progress_var.set(100.0)
        status_text = (
            f"✔ Concluído: extraídas={extracted}, ignoradas={skipped}, erros={errors}"
            if errors == 0
            else (
                "⚠ Concluído com erros: "
                f"extraídas={extracted}, ignoradas={skipped}, erros={errors}"
            )
        )
        self._progress_label.configure(text=status_text)
        self._append_log(status_text)
        if errors:
            self._append_log(
                "Dica: tente engine=pypdf ou instale Pillow para melhorar correções."
            )

    def _append_log(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _open_report(self) -> None:
        target = Path(self._vm.settings.report_base).with_suffix(".json").resolve()
        if not target.exists():
            self._append_log("Relatório ainda não gerado.")
            return
        try:
            webbrowser.open(target.as_uri())
        except ValueError:
            webbrowser.open(str(target))


class App(ttk.Frame):
    """Compositor raiz: instancia ViewModel, adapter e painéis."""

    def __init__(self, master: tk.Tk, adapter: JobAdapterProtocol | None = None) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self._adapter = adapter or CoreJobAdapter()
        self._vm = AppViewModel(self._adapter)
        WidgetFactory.configure_style(master)
        self._configure_window()
        self._build()
        self._setup_thread_bridge()

    def _configure_window(self) -> None:
        self.master.title("PDF Image Extractor")
        self.master.geometry("1180x740")
        self.master.minsize(980, 640)
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.grid(sticky="nsew")

    def _build(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=1)

        ttk.Label(self, text="PDF Image Extractor", style="Title.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(
            self,
            text="Fila de entradas, progresso em tempo real, preview e relatório acionável.",
            style="Hint.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))

        left = ttk.Frame(self, style="Card.TFrame", padding=12)
        left.grid(row=2, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=0)

        self._queue_panel = QueuePanel(left, self._vm)
        self._queue_panel.grid(row=0, column=0, sticky="nsew")

        self._config_panel = ConfigPanel(left, self._vm)
        self._config_panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self._results_panel = ResultsPanel(self, self._vm)
        self._results_panel.grid(row=2, column=1, sticky="nsew")

        self._status_bar = StatusBar(self, self._vm)
        self._status_bar.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

    def _setup_thread_bridge(self) -> None:
        """Mantém loop de UI responsivo e pronto para eventos de background."""

        def _poll() -> None:
            self.master.after(50, _poll)

        self.master.after(50, _poll)


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
