#!/usr/bin/env python3
"""Interface gráfica moderna e minimalista para extração de imagens de PDF."""

from __future__ import annotations

import json
import threading
import time
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pdf_image_extractor.core.models import ExtractionConfig
from pdf_image_extractor.core.pipeline import collect_pdfs_from_inputs, extract_from_pdf, resolve_engine, write_report

SETTINGS_PATH = Path.home() / ".pdf_image_extractor_gui.json"


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.grid(sticky="nsew")
        self.input_paths: list[Path] = []
        self.preview_image: tk.PhotoImage | None = None
        self.drag_index: int | None = None
        self.last_report_base = Path("relatorio_extracao")
        self._configure_style()
        self._build()
        self._load_settings()

    def _configure_style(self) -> None:
        style = ttk.Style(self.master)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        bg, card, text = "#111827", "#1f2937", "#f9fafb"
        self.master.configure(bg=bg)
        style.configure("App.TFrame", background=bg)
        style.configure("Card.TFrame", background=card)
        style.configure("Title.TLabel", background=bg, foreground=text, font=("Segoe UI", 15, "bold"))
        style.configure("Label.TLabel", background=card, foreground=text, font=("Segoe UI", 10))
        style.configure("Hint.TLabel", background=bg, foreground="#9ca3af")

    def _build(self) -> None:
        self.master.title("PDF Image Extractor")
        self.master.geometry("1120x720")
        self.master.minsize(980, 640)
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=1)

        ttk.Label(self, text="PDF Image Extractor", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self, text="Fila de entradas, progresso em tempo real, preview e relatório acionável.", style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))

        left = ttk.Frame(self, style="Card.TFrame", padding=12)
        left.grid(row=2, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        ttk.Label(left, text="Fila de entradas", style="Label.TLabel").grid(row=0, column=0, sticky="w")
        btns = ttk.Frame(left, style="Card.TFrame")
        btns.grid(row=1, column=0, sticky="ew", pady=(6, 8))
        for i in range(6):
            btns.columnconfigure(i, weight=1)
        ttk.Button(btns, text="Adicionar PDFs", command=self._add_files).grid(row=0, column=0, padx=2, sticky="ew")
        ttk.Button(btns, text="Adicionar pasta", command=self._add_folder).grid(row=0, column=1, padx=2, sticky="ew")
        ttk.Button(btns, text="Remover", command=self._remove_selected).grid(row=0, column=2, padx=2, sticky="ew")
        ttk.Button(btns, text="↑", width=3, command=lambda: self._move_selected(-1)).grid(row=0, column=3, padx=2)
        ttk.Button(btns, text="↓", width=3, command=lambda: self._move_selected(1)).grid(row=0, column=4, padx=2)
        ttk.Button(btns, text="Limpar", command=self._clear_queue).grid(row=0, column=5, padx=2, sticky="ew")

        self.queue = tk.Listbox(left, bg="#0b1220", fg="#e5e7eb", selectbackground="#2563eb", relief="flat")
        self.queue.grid(row=2, column=0, sticky="nsew")
        self.queue.bind("<Button-1>", self._drag_start)
        self.queue.bind("<B1-Motion>", self._drag_motion)

        cfg = ttk.Frame(left, style="Card.TFrame")
        cfg.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        for i in range(4):
            cfg.columnconfigure(i, weight=1)

        self.output_var = tk.StringVar(value=str(Path("imagens_extraidas").resolve()))
        self.prefix_var = tk.StringVar(value="imagem")
        self.engine_var = tk.StringVar(value="auto")
        self.recursive_var = tk.BooleanVar(value=False)
        self.continue_var = tk.BooleanVar(value=True)
        self.max_workers_var = tk.StringVar(value="4")

        ttk.Label(cfg, text="Saída", style="Label.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(cfg, textvariable=self.output_var).grid(row=0, column=1, columnspan=2, sticky="ew", padx=6)
        ttk.Button(cfg, text="Selecionar", command=self._pick_output).grid(row=0, column=3, sticky="ew")
        ttk.Label(cfg, text="Prefixo", style="Label.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(cfg, textvariable=self.prefix_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Label(cfg, text="Engine", style="Label.TLabel").grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Combobox(cfg, textvariable=self.engine_var, values=["auto", "pypdf", "fallback"], state="readonly").grid(row=1, column=3, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(cfg, text="Recursive", variable=self.recursive_var).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(cfg, text="Continuar com erro", variable=self.continue_var).grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(cfg, text="Workers", style="Label.TLabel").grid(row=2, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(cfg, textvariable=self.max_workers_var, width=5).grid(row=2, column=3, sticky="w", pady=(6, 0))

        right_top = ttk.Frame(self, style="Card.TFrame", padding=12)
        right_top.grid(row=2, column=1, sticky="nsew")
        right_top.columnconfigure(0, weight=1)
        right_top.rowconfigure(1, weight=1)

        ttk.Label(right_top, text="Resultados", style="Label.TLabel").grid(row=0, column=0, sticky="w")
        cols = ("arquivo", "pag", "fmt", "status", "bytes", "erro")
        self.table = ttk.Treeview(right_top, columns=cols, show="headings", height=10)
        for c, w in [("arquivo", 190), ("pag", 40), ("fmt", 50), ("status", 110), ("bytes", 70), ("erro", 220)]:
            self.table.heading(c, text=c.upper())
            self.table.column(c, width=w, anchor="w")
        self.table.grid(row=1, column=0, sticky="nsew")
        self.table.bind("<<TreeviewSelect>>", self._on_table_select)

        right_bottom = ttk.Frame(self, style="Card.TFrame", padding=12)
        right_bottom.grid(row=3, column=1, sticky="nsew", pady=(8, 0))
        right_bottom.columnconfigure(0, weight=1)
        right_bottom.rowconfigure(2, weight=1)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_lbl = ttk.Label(right_bottom, text="0/0")
        self.progress_lbl.grid(row=0, column=0, sticky="w")
        ttk.Progressbar(right_bottom, variable=self.progress_var, maximum=100).grid(row=1, column=0, sticky="ew", pady=(4, 6))

        self.preview = ttk.Label(right_bottom, text="Preview indisponível")
        self.preview.grid(row=2, column=0, sticky="nsew")

        actions = ttk.Frame(right_bottom, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        self.run_btn = ttk.Button(actions, text="Extrair imagens", command=self._start)
        self.run_btn.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="Abrir relatório", command=self._open_report).grid(row=0, column=0, sticky="w")

        self.status = tk.Text(self, height=5, bg="#030712", fg="#e5e7eb", insertbackground="#e5e7eb", relief="flat")
        self.status.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.status.insert("end", "Pronto. Adicione PDFs e clique em Extrair imagens.\n")
        self.status.configure(state="disabled")

    def _append_status(self, text: str) -> None:
        self.status.configure(state="normal")
        self.status.insert("end", text + "\n")
        self.status.see("end")
        self.status.configure(state="disabled")

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames(title="Selecione PDFs", filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        for p in selected:
            self.input_paths.append(Path(p))
        self._refresh_queue()

    def _add_folder(self) -> None:
        selected = filedialog.askdirectory(title="Selecione pasta")
        if selected:
            self.input_paths.append(Path(selected))
            self._refresh_queue()

    def _remove_selected(self) -> None:
        idxs = list(self.queue.curselection())
        for i in reversed(idxs):
            self.input_paths.pop(i)
        self._refresh_queue()

    def _move_selected(self, delta: int) -> None:
        sel = self.queue.curselection()
        if not sel:
            return
        i = sel[0]
        j = max(0, min(len(self.input_paths) - 1, i + delta))
        if i == j:
            return
        self.input_paths[i], self.input_paths[j] = self.input_paths[j], self.input_paths[i]
        self._refresh_queue(select=j)

    def _clear_queue(self) -> None:
        self.input_paths = []
        self._refresh_queue()

    def _refresh_queue(self, select: int | None = None) -> None:
        self.queue.delete(0, "end")
        for p in self.input_paths:
            self.queue.insert("end", str(p))
        if select is not None and 0 <= select < self.queue.size():
            self.queue.selection_set(select)

    def _drag_start(self, event):
        self.drag_index = self.queue.nearest(event.y)

    def _drag_motion(self, event):
        if self.drag_index is None:
            return
        i = self.queue.nearest(event.y)
        if i == self.drag_index or i < 0 or i >= len(self.input_paths):
            return
        self.input_paths[self.drag_index], self.input_paths[i] = self.input_paths[i], self.input_paths[self.drag_index]
        self.drag_index = i
        self._refresh_queue(select=i)

    def _pick_output(self) -> None:
        selected = filedialog.askdirectory(title="Selecione pasta de saída")
        if selected:
            self.output_var.set(selected)

    def _on_table_select(self, _evt) -> None:
        sel = self.table.selection()
        if not sel:
            return
        row = self.table.item(sel[0], "values")
        output_file = row[0]
        if not output_file or output_file in {"-", "None"}:
            self.preview.configure(text="Sem preview")
            return
        p = Path(output_file)
        if not p.exists():
            self.preview.configure(text="Arquivo não encontrado")
            return
        try:
            self.preview_image = tk.PhotoImage(file=str(p))
            self.preview.configure(image=self.preview_image, text="")
        except Exception:
            self.preview.configure(text="Preview disponível para PNG/GIF no Tk padrão")

    def _settings_payload(self) -> dict:
        return {
            "output_dir": self.output_var.get(),
            "prefix": self.prefix_var.get(),
            "engine": self.engine_var.get(),
            "recursive": self.recursive_var.get(),
            "continue_on_error": self.continue_var.get(),
            "max_workers": self.max_workers_var.get(),
            "report_base": str(self.last_report_base),
        }

    def _save_settings(self) -> None:
        SETTINGS_PATH.write_text(json.dumps(self._settings_payload(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            self.output_var.set(data.get("output_dir", self.output_var.get()))
            self.prefix_var.set(data.get("prefix", self.prefix_var.get()))
            self.engine_var.set(data.get("engine", self.engine_var.get()))
            self.recursive_var.set(bool(data.get("recursive", False)))
            self.continue_var.set(bool(data.get("continue_on_error", True)))
            self.max_workers_var.set(str(data.get("max_workers", "4")))
            self.last_report_base = Path(data.get("report_base", "relatorio_extracao"))
        except Exception:
            pass

    def _start(self) -> None:
        if not self.input_paths:
            messagebox.showwarning("Campo obrigatório", "Adicione ao menos um PDF ou diretório na fila.")
            return
        self._save_settings()
        self.run_btn.configure(state="disabled")
        for i in self.table.get_children():
            self.table.delete(i)
        threading.Thread(target=self._run_job, daemon=True).start()

    def _insert_record(self, record) -> None:
        out = record.output_file or "-"
        ext = Path(out).suffix.replace(".", "") if out != "-" else "-"
        err = record.error or ("Instale Pillow para correção direta" if "unhandled" in record.correction_status else "")
        self.table.insert("", "end", values=(out, record.page or "-", ext, record.status, record.output_bytes, err[:120]))

    def _open_report(self) -> None:
        target = self.last_report_base.with_suffix(".json")
        if target.exists():
            webbrowser.open(target.as_uri())
        else:
            self._append_status("Relatório ainda não gerado.")

    def _run_job(self) -> None:
        try:
            output_dir = Path(self.output_var.get().strip())
            max_workers = max(1, int(self.max_workers_var.get().strip() or "4"))
            report_base = Path("relatorio_extracao")
            self.last_report_base = report_base

            cfg = ExtractionConfig(
                input_paths=self.input_paths,
                output_dir=output_dir,
                prefix=self.prefix_var.get().strip() or "imagem",
                recursive=self.recursive_var.get(),
                continue_on_error=self.continue_var.get(),
                engine=self.engine_var.get(),
                quiet=True,
                max_workers=max_workers,
                report=report_base,
            )
            pdfs = collect_pdfs_from_inputs(cfg.input_paths, cfg.recursive)
            if not pdfs:
                self.master.after(0, lambda: self._append_status("Nenhum PDF encontrado para processar."))
                return

            engine = resolve_engine(cfg.engine)
            start = time.perf_counter()
            all_records = []
            total_errors = 0
            total = len(pdfs)

            for idx, pdf in enumerate(pdfs, start=1):
                records, errors = extract_from_pdf(pdf, cfg, engine)
                all_records.extend(records)
                total_errors += errors
                elapsed = time.perf_counter() - start
                avg = elapsed / idx
                eta = max(0.0, avg * (total - idx))
                progress = (idx / total) * 100.0

                def _ui_update(local_records=records, i=idx, p=progress, eta_s=eta):
                    self.progress_var.set(p)
                    self.progress_lbl.configure(text=f"{i}/{total} ({p:.1f}%) - ETA {eta_s:.1f}s")
                    for r in local_records:
                        self._insert_record(r)

                self.master.after(0, _ui_update)
                if errors and cfg.fail_fast:
                    break

            write_report(all_records, cfg.report, cfg.report_formats)
            extracted = sum(1 for r in all_records if r.status == "ok")
            skipped = sum(1 for r in all_records if r.status.startswith("skipped"))
            self.master.after(0, lambda: self._append_status(f"Concluído: extraídas={extracted}, ignoradas={skipped}, erros={total_errors}"))
            if total_errors:
                self.master.after(0, lambda: self._append_status("Dica: tente engine pypdf ou instale Pillow para melhorar correções."))

        except Exception as exc:
            self.master.after(0, lambda: self._append_status(f"Erro: {exc}"))
        finally:
            self.master.after(0, lambda: self.run_btn.configure(state="normal"))


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
