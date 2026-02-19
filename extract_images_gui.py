#!/usr/bin/env python3
"""Interface gráfica moderna e minimalista para extração de imagens de PDF."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from extract_images import run_extraction_job


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=18)
        self.master = master
        self.grid(sticky="nsew")
        self._configure_style()
        self._build()

    def _configure_style(self) -> None:
        style = ttk.Style(self.master)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        bg = "#111827"
        card = "#1f2937"
        text = "#f9fafb"
        accent = "#10b981"

        self.master.configure(bg=bg)
        style.configure("App.TFrame", background=bg)
        style.configure("Card.TFrame", background=card)
        style.configure("Title.TLabel", background=bg, foreground=text, font=("Segoe UI", 16, "bold"))
        style.configure("Label.TLabel", background=card, foreground=text, font=("Segoe UI", 10))
        style.configure("Hint.TLabel", background=bg, foreground="#9ca3af", font=("Segoe UI", 9))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))
        style.map("Primary.TButton", background=[("!disabled", accent)])
        style.configure("Status.TLabel", background=bg, foreground="#d1d5db", font=("Consolas", 9))

    def _build(self) -> None:
        self.master.title("PDF Image Extractor")
        self.master.geometry("760x480")
        self.master.minsize(700, 420)
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(self, text="PDF Image Extractor", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            self,
            text="Interface minimalista para extrair imagens de um PDF ou de uma pasta inteira.",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 14))

        card = ttk.Frame(self, style="Card.TFrame", padding=16)
        card.grid(row=2, column=0, sticky="nsew")
        card.columnconfigure(1, weight=1)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path("imagens_extraidas").resolve()))
        self.prefix_var = tk.StringVar(value="imagem")
        self.engine_var = tk.StringVar(value="auto")
        self.recursive_var = tk.BooleanVar(value=False)
        self.continue_var = tk.BooleanVar(value=True)

        self._row_path(card, 0, "Entrada (PDF ou pasta)", self.input_var, self._pick_input)
        self._row_path(card, 1, "Saída das imagens", self.output_var, self._pick_output)
        self._row_entry(card, 2, "Prefixo", self.prefix_var)
        ttk.Label(card, text="Engine", style="Label.TLabel").grid(row=3, column=0, sticky="w", pady=8)
        engine = ttk.Combobox(card, textvariable=self.engine_var, values=["auto", "pypdf", "fallback"], state="readonly")
        engine.grid(row=3, column=1, sticky="ew", padx=(8, 8), pady=8)

        ttk.Checkbutton(card, text="Processar subpastas (recursive)", variable=self.recursive_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Checkbutton(card, text="Continuar em caso de erro", variable=self.continue_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))

        actions = ttk.Frame(self, style="App.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        actions.columnconfigure(0, weight=1)

        self.run_btn = ttk.Button(actions, text="Extrair imagens", style="Primary.TButton", command=self._start)
        self.run_btn.grid(row=0, column=1, sticky="e")

        self.status = tk.Text(self, height=8, bg="#030712", fg="#e5e7eb", insertbackground="#e5e7eb", relief="flat")
        self.status.grid(row=4, column=0, sticky="nsew")
        self.status.insert("end", "Pronto. Configure os caminhos e clique em 'Extrair imagens'.\n")
        self.status.configure(state="disabled")

    def _row_path(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, cmd) -> None:
        ttk.Label(parent, text=label, style="Label.TLabel").grid(row=row, column=0, sticky="w", pady=8)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=8)
        ttk.Button(parent, text="Selecionar", command=cmd).grid(row=row, column=2, sticky="e", pady=8)

    def _row_entry(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(parent, text=label, style="Label.TLabel").grid(row=row, column=0, sticky="w", pady=8)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=8)

    def _pick_input(self) -> None:
        selected = filedialog.askopenfilename(title="Selecione um PDF", filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        if selected:
            self.input_var.set(selected)
            return
        selected_dir = filedialog.askdirectory(title="Ou selecione uma pasta com PDFs")
        if selected_dir:
            self.input_var.set(selected_dir)

    def _pick_output(self) -> None:
        selected = filedialog.askdirectory(title="Selecione a pasta de saída")
        if selected:
            self.output_var.set(selected)

    def _append_status(self, text: str) -> None:
        self.status.configure(state="normal")
        self.status.insert("end", text + "\n")
        self.status.see("end")
        self.status.configure(state="disabled")

    def _start(self) -> None:
        if not self.input_var.get().strip():
            messagebox.showwarning("Campo obrigatório", "Informe um arquivo PDF ou diretório de entrada.")
            return
        self.run_btn.configure(state="disabled")
        threading.Thread(target=self._run_job, daemon=True).start()

    def _run_job(self) -> None:
        try:
            input_path = Path(self.input_var.get().strip())
            output_dir = Path(self.output_var.get().strip())
            self.master.after(0, lambda: self._append_status(f"Iniciando processamento de: {input_path}"))
            records, code = run_extraction_job(
                input_path=input_path,
                output_dir=output_dir,
                prefix=self.prefix_var.get().strip() or "imagem",
                recursive=self.recursive_var.get(),
                continue_on_error=self.continue_var.get(),
                engine=self.engine_var.get(),
                quiet=True,
            )

            extracted = sum(1 for r in records if r.status == "ok")
            errors = sum(1 for r in records if r.status == "error")
            skipped = sum(1 for r in records if r.status.startswith("skipped"))

            if code == 2:
                self.master.after(0, lambda: self._append_status("Nenhum PDF encontrado para processar."))
            else:
                self.master.after(0, lambda: self._append_status(f"Concluído: extraídas={extracted}, ignoradas={skipped}, erros={errors}"))

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
