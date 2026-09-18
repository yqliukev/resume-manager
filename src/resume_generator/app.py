import os
import tkinter
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .models import (
    SourceFile,
    LinkLibrary,
    merge_source_document,
    default_link_library_path,
)
from .parser import parse_file
from .persistence_v2 import (
    load_link_library,
    save_link_library,
    update_library_source_file,
)

VERSION_COLOR = "#5AB0F0"  # distinct accent so versions read differently


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Resume Generator")
        self.geometry("960x680")
        self.minsize(720, 520)

        # Canonical parsed source document (never mutated by an edit session).
        self.source_doc: SourceFile | None = None
        # Document currently bound to the tree. Equals source_doc while creating
        # a new file, or a working clone with a generated file's selections while
        # editing an existing one.
        self.doc: SourceFile | None = None
        self.file_path: str | None = None
        self.link_library: LinkLibrary | None = None
        self.library_path: str | None = None

        # Editor session state.
        self.editing_path: str | None = None  # None => creating a new file
        self.current_view: str = "manage"

        # State maps: keyed by section index / (section_idx, entry_idx)
        self.section_vars: dict[int, tkinter.IntVar] = {}
        self.entry_vars: dict[tuple, tkinter.IntVar] = {}
        self.version_vars: dict[tuple, tkinter.StringVar] = {}

        # Keep references so GC doesn't collect them
        self._section_cb_refs: list = []
        self._entry_cb_refs: list[list] = []
        self._version_radio_refs: list = []

        self._pending_warnings: list[str] = []

        self._build_ui()
        self._show_manage_view()
        self._refresh_file_list()
        self._update_control_states()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Top bar ──────────────────────────────────────────────────
        top = ctk.CTkFrame(self, corner_radius=0)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            top, text="Upload File", width=150, command=self._choose_upload_target
        ).grid(row=0, column=0, padx=(8, 8), pady=6, sticky="w")

        self.refresh_btn = ctk.CTkButton(
            top, text="↻", width=42, command=self._refresh_source_file, state="disabled"
        )
        self.refresh_btn.grid(row=0, column=1, padx=(0, 8), pady=6, sticky="w")

        self.file_label = ctk.CTkLabel(top, text="Source: none", anchor="w")
        self.file_label.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 2))

        self.library_label = ctk.CTkLabel(top, text="Library: none", anchor="w")
        self.library_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))

        # ── View container (manage view + editor view share this cell) ─
        self._build_manage_view()
        self._build_editor_view()

        # ── Status footer (shared by both views) ──────────────────────
        footer = ctk.CTkFrame(self, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))
        footer.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            footer, text="Status: Ready", anchor="w", font=ctk.CTkFont(size=12)
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=10, pady=6)

    def _build_manage_view(self):
        self.manage_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.manage_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self.manage_frame.grid_columnconfigure(0, weight=1)
        self.manage_frame.grid_rowconfigure(0, weight=1)

        self.file_list_frame = ctk.CTkScrollableFrame(
            self.manage_frame,
            label_text="Generated files",
            label_font=ctk.CTkFont(weight="bold"),
        )
        self.file_list_frame.grid(row=0, column=0, sticky="nsew")
        self.file_list_frame.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(self.manage_frame, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.generate_new_btn = ctk.CTkButton(
            actions, text="Generate New", width=140, command=self._enter_create_mode
        )
        self.generate_new_btn.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.update_links_btn = ctk.CTkButton(
            actions, text="Update Links", width=140, command=self._update_links
        )
        self.update_links_btn.grid(row=0, column=1, padx=(0, 8), sticky="w")

    def _build_editor_view(self):
        self.editor_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.editor_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self.editor_frame.grid_columnconfigure(0, weight=1)
        self.editor_frame.grid_rowconfigure(1, weight=1)

        # Header: Back + mode label
        header = ctk.CTkFrame(self.editor_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            header, text="← Back", width=90, command=self._back_to_list
        ).grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.mode_label = ctk.CTkLabel(
            header, text="New file", anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.mode_label.grid(row=0, column=1, sticky="ew")

        # Main content (left tree | right preview)
        content = ctk.CTkFrame(self.editor_frame, corner_radius=0, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=55)
        content.grid_columnconfigure(1, weight=45)
        content.grid_rowconfigure(0, weight=1)

        self.tree_frame = ctk.CTkScrollableFrame(
            content, label_text="Sections & Entries", label_font=ctk.CTkFont(weight="bold")
        )
        self.tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.tree_frame.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(content)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.stats_label = ctk.CTkLabel(
            right, text="Open a .tex file to begin",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w"
        )
        self.stats_label.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))

        self.preview_frame = ctk.CTkScrollableFrame(
            right, label_text="Selected entries", label_font=ctk.CTkFont(weight="bold")
        )
        self.preview_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.preview_frame.grid_columnconfigure(0, weight=1)

        # Bottom bar: output fields + action button
        bottom = ctk.CTkFrame(self.editor_frame, corner_radius=0)
        bottom.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        bottom.grid_columnconfigure(1, weight=1)

        # Output fields are only shown when creating a new file.
        self.output_fields_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        self.output_fields_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.output_fields_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.output_fields_frame, text="Output folder:").grid(
            row=0, column=0, padx=(10, 6), pady=(8, 4), sticky="w"
        )
        self.output_dir_entry = ctk.CTkEntry(
            self.output_fields_frame, placeholder_text="/path/to/output/folder"
        )
        self.output_dir_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=(8, 4))
        ctk.CTkButton(
            self.output_fields_frame, text="Browse", width=90, command=self._browse_output_dir
        ).grid(row=0, column=2, padx=(4, 10), pady=(8, 4))

        ctk.CTkLabel(self.output_fields_frame, text="Output file name:").grid(
            row=1, column=0, padx=(10, 6), pady=4, sticky="w"
        )
        self.output_name_entry = ctk.CTkEntry(
            self.output_fields_frame, placeholder_text="generated.tex"
        )
        self.output_name_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=4)

        self.generate_pdf_var = tkinter.IntVar(value=0)
        ctk.CTkCheckBox(
            bottom,
            text="Generate PDF",
            variable=self.generate_pdf_var,
        ).grid(row=1, column=0, columnspan=2, padx=(10, 4), pady=(4, 8), sticky="w")

        self.action_btn = ctk.CTkButton(
            bottom, text="Generate", width=120, command=self._generate
        )
        self.action_btn.grid(row=1, column=2, padx=(4, 10), pady=(4, 8), sticky="e")

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------

    def _show_manage_view(self):
        self.current_view = "manage"
        self.editor_frame.grid_remove()
        self.manage_frame.grid()

    def _show_editor_view(self):
        self.current_view = "editor"
        self.manage_frame.grid_remove()
        self.editor_frame.grid()

    # ------------------------------------------------------------------
    # File open
    # ------------------------------------------------------------------

    def _choose_upload_target(self):
        popup = ctk.CTkToplevel(self)
        popup.title("Choose upload type")
        popup.geometry("360x170")
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()

        popup.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            popup,
            text="Choose File Type",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        button_row = ctk.CTkFrame(popup, fg_color="transparent")
        button_row.grid(row=1, column=0, padx=20, pady=(8, 8), sticky="ew")
        button_row.grid_columnconfigure(0, weight=1)
        button_row.grid_columnconfigure(1, weight=1)

        def choose_source():
            popup.destroy()
            self.after(0, self._open_source_file)

        def choose_library():
            popup.destroy()
            self.after(0, self._open_link_library)

        ctk.CTkButton(
            button_row,
            text="Source File",
            command=choose_source,
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            button_row,
            text="Link Library",
            command=choose_library,
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        ctk.CTkButton(
            popup,
            text="Cancel",
            width=90,
            command=popup.destroy,
        ).grid(row=2, column=0, padx=20, pady=(4, 16), sticky="e")

        popup.bind("<Escape>", lambda _event: popup.destroy())

    def _load_source_file(self, path: str, *, reset_output_fields: bool):
        self._set_status("Parsing source file…")
        try:
            parsed_source = parse_file(path)
        except Exception as exc:
            messagebox.showerror("Parse error", str(exc))
            self._set_status(f"Error: {exc}")
            return False

        self._pending_warnings = list(getattr(parsed_source, "parse_warnings", []))
        source_path = os.path.abspath(path)

        # Resolve which link library (if any) belongs to this source.
        library = self._resolve_library_for_source(source_path)

        if library is not None:
            self.link_library = library
            self.source_doc = merge_source_document(parsed_source, library.source_file)
            if self.library_path:
                self.library_label.configure(text=f"Library: {self.library_path}")
        else:
            self.source_doc = parsed_source
            self.link_library = None
            self.library_path = None
            self.library_label.configure(text="Library: none")

        self.doc = self.source_doc
        self.editing_path = None
        self.file_path = source_path
        self.file_label.configure(text=f"Source: {source_path}")

        if reset_output_fields:
            self._set_default_output_fields(source_path)

        self._refresh_file_list()
        self._show_manage_view()
        self._update_control_states()
        return True

    def _resolve_library_for_source(self, source_path: str) -> LinkLibrary | None:
        """Return the link library associated with source_path, if available.

        Prefers an already-loaded library pointing at the same source, otherwise
        auto-loads the sibling ``{stem}.resume-links.json`` when it exists and
        references this source file.
        """
        if self.link_library is not None and self._same_source(
            self.link_library, source_path
        ):
            return self.link_library

        default_lib = default_link_library_path(source_path)
        if default_lib and os.path.exists(default_lib):
            try:
                candidate = load_link_library(default_lib)
            except Exception:
                return None
            if self._same_source(candidate, source_path):
                self.library_path = os.path.abspath(default_lib)
                return candidate
        return None

    @staticmethod
    def _same_source(library: LinkLibrary, source_path: str) -> bool:
        target = os.path.abspath(source_path)
        candidates = {library.source_path, library.source_file.path}
        return any(os.path.abspath(c) == target for c in candidates if c)

    def _open_source_file(self):
        path = filedialog.askopenfilename(
            title="Open LaTeX Source File",
            filetypes=[("LaTeX files", "*.tex"), ("All files", "*.*")],
        )
        if not path:
            return

        if not self._load_source_file(path, reset_output_fields=True):
            return
        self._announce_load("Source loaded")

    def _refresh_source_file(self):
        if not self.file_path:
            messagebox.showwarning("No source file", "Please upload a source file first.")
            return

        in_editor = self.current_view == "editor"
        editing_path = self.editing_path
        # Capture any in-progress selections before we discard the old structure.
        if in_editor:
            self._sync_model()
        prev_working = self.doc

        self._set_status("Refreshing source file…")
        try:
            parsed_source = parse_file(self.file_path)
        except Exception as exc:
            messagebox.showerror("Parse error", str(exc))
            self._set_status(f"Error: {exc}")
            return

        self._pending_warnings = list(getattr(parsed_source, "parse_warnings", []))

        source_template = (
            self.link_library.source_file if self.link_library is not None else None
        )
        new_source = merge_source_document(parsed_source, source_template)
        self.source_doc = new_source
        self.file_label.configure(text=f"Source: {self.file_path}")

        if in_editor:
            # Re-apply the in-progress UI selections onto the new structure.
            self.doc = merge_source_document(new_source, prev_working)
            if editing_path is None:
                self.source_doc = self.doc
            self._build_tree()
        else:
            self.doc = self.source_doc
            self._refresh_file_list()

        self._update_control_states()
        self._announce_load("Source refreshed")

    def _open_link_library(self):
        path = filedialog.askopenfilename(
            title="Open Link Library",
            filetypes=[("Link libraries", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        self._set_status("Loading link library…")
        try:
            library = load_link_library(path)
        except Exception as exc:
            messagebox.showerror("Library error", str(exc))
            self._set_status(f"Error: {exc}")
            return

        source_path = library.source_path or library.source_file.path
        loaded_source: SourceFile | None = None
        self._pending_warnings = []
        if source_path and os.path.exists(source_path):
            try:
                parsed_source = parse_file(source_path)
                self._pending_warnings = list(getattr(parsed_source, "parse_warnings", []))
                loaded_source = merge_source_document(parsed_source, library.source_file)
            except Exception:
                loaded_source = None
                self._pending_warnings = []

        if loaded_source is None:
            loaded_source = SourceFile.from_dict(library.source_file.to_dict()) or library.source_file

        self.link_library = library
        self.library_path = os.path.abspath(path)
        self.source_doc = loaded_source
        self.doc = loaded_source
        self.editing_path = None

        if source_path:
            self.file_path = self.source_doc.path or os.path.abspath(source_path)
        else:
            self.file_path = self.source_doc.path or None

        if self.file_path:
            self.file_label.configure(text=f"Source: {self.file_path}")
        else:
            self.file_label.configure(text="Source: unavailable")
        self.library_label.configure(text=f"Library: {self.library_path}")

        self._set_default_output_fields(source_path)

        self._refresh_file_list()
        self._show_manage_view()
        self._update_control_states()
        self._announce_load("Link library loaded")

    # ------------------------------------------------------------------
    # Manage view: generated file list
    # ------------------------------------------------------------------

    def _refresh_file_list(self):
        for widget in self.file_list_frame.winfo_children():
            widget.destroy()

        if self.source_doc is None:
            ctk.CTkLabel(
                self.file_list_frame,
                text="Upload a source file or link library to begin.",
                anchor="w",
                font=ctk.CTkFont(size=12),
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=8)
            return

        links = self.link_library.links if self.link_library is not None else {}
        if not links:
            ctk.CTkLabel(
                self.file_list_frame,
                text="No generated files yet. Use \"Generate New\" to create one.",
                anchor="w",
                font=ctk.CTkFont(size=12),
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=8)
            return

        for row, (path, gen_file) in enumerate(sorted(links.items())):
            self._add_file_row(row, path, gen_file)

    def _add_file_row(self, row: int, path: str, gen_file):
        row_frame = ctk.CTkFrame(self.file_list_frame)
        row_frame.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
        row_frame.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(row_frame, fg_color="transparent")
        info.grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=6)
        info.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            info,
            text=os.path.basename(path),
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            info,
            text=os.path.dirname(path) or ".",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).grid(row=1, column=0, sticky="ew")

        badges = []
        if gen_file.pdf_path:
            badges.append("PDF")
        if not os.path.exists(path):
            badges.append("missing on disk")
        if badges:
            ctk.CTkLabel(
                info,
                text="  •  ".join(badges),
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=VERSION_COLOR,
            ).grid(row=2, column=0, sticky="ew")

        ctk.CTkButton(
            row_frame, text="Edit", width=80,
            command=lambda p=path: self._enter_edit_mode(p),
        ).grid(row=0, column=1, padx=4, pady=6)

        ctk.CTkButton(
            row_frame, text="Delete", width=80, fg_color="#B0413E", hover_color="#8F3230",
            command=lambda p=path: self._delete_file(p),
        ).grid(row=0, column=2, padx=(4, 8), pady=6)

    def _delete_file(self, path: str):
        if self.link_library is None:
            return

        gen_file = self.link_library.links.get(path)
        name = os.path.basename(path)
        detail = f"Delete generated file \"{name}\"?"
        if gen_file is not None and gen_file.pdf_path:
            detail += "\n\nThe linked PDF will also be removed."
        detail += "\n\nThis deletes the file(s) from disk and cannot be undone."

        if not messagebox.askyesno("Delete file", detail):
            return

        # If this file is open in the editor, drop that edit session first.
        if self.editing_path is not None and os.path.abspath(self.editing_path) == os.path.abspath(path):
            self.editing_path = None
            self.doc = self.source_doc
            self._show_manage_view()

        try:
            self.link_library.remove_generated_file(path, delete_from_disk=True)
        except OSError as exc:
            messagebox.showerror("Delete error", str(exc))
            self._set_status(f"Error: {exc}")
            return

        saved_path = save_link_library(self.link_library, self.library_path)
        self.library_path = saved_path
        self.library_label.configure(text=f"Library: {saved_path}")
        self._refresh_file_list()
        self._update_control_states()
        self._set_status(f"Deleted: {path}")

    # ------------------------------------------------------------------
    # Editor entry points
    # ------------------------------------------------------------------

    def _enter_create_mode(self):
        if self.source_doc is None:
            messagebox.showwarning("No source file", "Please upload a source file first.")
            return

        self.editing_path = None
        self.doc = self.source_doc
        self.mode_label.configure(text="New file")
        self.output_fields_frame.grid()
        self._set_default_output_fields(self.file_path)
        self.generate_pdf_var.set(0)
        self.action_btn.configure(text="Generate", command=self._generate)

        self._build_tree()
        self._show_editor_view()
        self._set_status("Creating a new generated file")

    def _enter_edit_mode(self, path: str):
        if self.link_library is None or self.source_doc is None:
            return
        gen_file = self.link_library.links.get(path)
        if gen_file is None:
            messagebox.showwarning("Missing entry", "That generated file is no longer in the library.")
            self._refresh_file_list()
            return

        # Build a working source clone carrying this file's saved selections.
        self.doc = merge_source_document(self.source_doc, gen_file)
        self.editing_path = path
        self.mode_label.configure(text=f"Editing: {os.path.basename(path)}")
        self.output_fields_frame.grid_remove()
        self.generate_pdf_var.set(1 if gen_file.pdf_path else 0)
        self.action_btn.configure(text="Rebuild", command=self._rebuild)

        self._build_tree()
        self._show_editor_view()
        self._set_status(f"Editing {os.path.basename(path)}")

    def _back_to_list(self):
        # Discard unsaved checkbox changes by pointing back at the source doc.
        self.editing_path = None
        self.doc = self.source_doc
        self._show_manage_view()
        self._set_status("Ready")

    # ------------------------------------------------------------------
    # Tree building
    # ------------------------------------------------------------------

    def _build_tree(self):
        # Clear old widgets
        for widget in self.tree_frame.winfo_children():
            widget.destroy()
        self.section_vars.clear()
        self.entry_vars.clear()
        self.version_vars.clear()
        self._section_cb_refs.clear()
        self._entry_cb_refs.clear()
        self._version_radio_refs.clear()

        if not self.doc:
            return

        SECTION_FONT = ctk.CTkFont(size=13, weight="bold")
        ENTRY_FONT = ctk.CTkFont(size=12)
        VERSION_FONT = ctk.CTkFont(size=11)

        row = 0
        for si, section in enumerate(self.doc.sections):
            self._section_cb_refs.append(None)
            self._entry_cb_refs.append([])

            # Section row
            sec_var = tkinter.IntVar(value=1 if section.selected else 0)
            self.section_vars[si] = sec_var

            sec_cb = ctk.CTkCheckBox(
                self.tree_frame,
                text=section.name,
                variable=sec_var,
                font=SECTION_FONT,
                command=lambda i=si: self._on_section_toggle(i),
            )
            sec_cb.grid(
                row=row, column=0, sticky="w",
                padx=8, pady=(10 if si > 0 else 4, 2)
            )
            self._section_cb_refs[si] = sec_cb
            row += 1

            # Entry rows (indented)
            for ei, entry in enumerate(section.entries):
                entry_var = tkinter.IntVar(value=1 if entry.selected else 0)
                self.entry_vars[(si, ei)] = entry_var

                label = entry.display_label
                if len(label) > 52:
                    label = label[:50] + "…"

                ent_cb = ctk.CTkCheckBox(
                    self.tree_frame,
                    text=label,
                    variable=entry_var,
                    font=ENTRY_FONT,
                    command=lambda i=si, j=ei: self._on_entry_toggle(i, j),
                )
                ent_cb.grid(
                    row=row, column=0, sticky="w",
                    padx=(30, 8), pady=1
                )
                self._entry_cb_refs[si].append(ent_cb)
                row += 1

                # Version radios (only when an item has more than one version)
                if len(entry.versions) > 1:
                    version_var = tkinter.StringVar(
                        value=entry.resolve_active_version_id()
                    )
                    self.version_vars[(si, ei)] = version_var

                    for version in entry.versions:
                        radio_label = self._version_radio_label(entry, version)
                        radio = ctk.CTkRadioButton(
                            self.tree_frame,
                            text=radio_label,
                            variable=version_var,
                            value=version.version_id,
                            font=VERSION_FONT,
                            text_color=VERSION_COLOR,
                            radiobutton_width=16,
                            radiobutton_height=16,
                            command=lambda i=si, j=ei: self._on_version_change(i, j),
                        )
                        radio.grid(
                            row=row, column=0, sticky="w",
                            padx=(56, 8), pady=(0, 1)
                        )
                        self._version_radio_refs.append(radio)
                        row += 1

        self._update_preview()

    @staticmethod
    def _version_radio_label(entry, version) -> str:
        title = version.display_label
        if len(title) > 44:
            title = title[:42] + "…"
        if version.display_label and version.display_label != entry.display_label:
            return f"{version.version_id} — {title}"
        return version.version_id

    # ------------------------------------------------------------------
    # Checkbox event handlers
    # ------------------------------------------------------------------

    def _on_section_toggle(self, si: int):
        val = self.section_vars[si].get()
        # Cascade to all child entries
        n_entries = len(self.doc.sections[si].entries)
        for ei in range(n_entries):
            self.entry_vars[(si, ei)].set(val)
        self._update_preview()

    def _on_entry_toggle(self, si: int, _ei: int):
        n_entries = len(self.doc.sections[si].entries)
        selected_count = sum(
            self.entry_vars[(si, j)].get() for j in range(n_entries)
        )
        # Update section checkbox: checked if any entry is selected
        if selected_count == 0:
            self.section_vars[si].set(0)
        else:
            self.section_vars[si].set(1)
        self._update_preview()

    def _on_version_change(self, _si: int, _ei: int):
        # Choosing a version does not change include/exclude state.
        self._update_preview()

    # ------------------------------------------------------------------
    # Preview panel
    # ------------------------------------------------------------------

    def _update_preview(self):
        for w in self.preview_frame.winfo_children():
            w.destroy()

        if not self.doc:
            return

        total = 0
        selected = 0
        row = 0

        for si, section in enumerate(self.doc.sections):
            sec_selected = bool(self.section_vars.get(si, tkinter.IntVar()).get())
            entries = section.entries

            for ei, entry in enumerate(entries):
                total += 1
                entry_on = bool(self.entry_vars.get((si, ei), tkinter.IntVar()).get())
                if sec_selected and entry_on:
                    selected += 1
                    text = f"[{section.name}]  {entry.display_label[:55]}"
                    if len(entry.versions) > 1:
                        version_var = self.version_vars.get((si, ei))
                        active_id = (
                            version_var.get() if version_var is not None
                            else entry.resolve_active_version_id()
                        )
                        text += f"  [{active_id}]"
                    sec_label = ctk.CTkLabel(
                        self.preview_frame,
                        text=text,
                        anchor="w",
                        font=ctk.CTkFont(size=11),
                        wraplength=320,
                    )
                    sec_label.grid(row=row, column=0, sticky="ew", padx=6, pady=1)
                    row += 1

        self.stats_label.configure(
            text=f"{selected} of {total} entries selected"
        )

    # ------------------------------------------------------------------
    # Generate / rebuild / update
    # ------------------------------------------------------------------

    def _update_control_states(self):
        has_source = self.source_doc is not None
        self.refresh_btn.configure(state="normal" if self.file_path else "disabled")
        self.generate_new_btn.configure(state="normal" if has_source else "disabled")
        has_links = bool(self.link_library.links) if self.link_library is not None else False
        self.update_links_btn.configure(
            state="normal" if (has_source and has_links) else "disabled"
        )

    def _set_default_output_fields(self, source_path: str | None):
        if source_path:
            source_dir = os.path.dirname(source_path)
            source_name = os.path.splitext(os.path.basename(source_path))[0]
        else:
            source_dir = os.getcwd()
            source_name = "generated"

        self.output_dir_entry.delete(0, "end")
        self.output_dir_entry.insert(0, source_dir)

        self.output_name_entry.delete(0, "end")
        self.output_name_entry.insert(0, f"{source_name}.tex")

    def _browse_output_dir(self):
        initial_dir = self.output_dir_entry.get().strip() or (
            os.path.dirname(self.file_path) if self.file_path else os.getcwd()
        )
        path = filedialog.askdirectory(
            title="Select output folder",
            initialdir=initial_dir,
        )
        if path:
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, path)

    def _sync_model(self):
        """Push UI checkbox states back into the document model."""
        if not self.doc:
            return
        for si, section in enumerate(self.doc.sections):
            section.selected = bool(self.section_vars.get(si, tkinter.IntVar(value=1)).get())
            for ei, entry in enumerate(section.entries):
                entry.selected = bool(
                    self.entry_vars.get((si, ei), tkinter.IntVar(value=1)).get()
                )
                version_var = self.version_vars.get((si, ei))
                if version_var is not None:
                    entry.active_version_id = version_var.get()

    def _ensure_library(self) -> LinkLibrary | None:
        if self.source_doc is None:
            return None
        if self.link_library is None:
            self.link_library = LinkLibrary.empty_for_source(self.source_doc)
            self.library_path = default_link_library_path(self.source_doc.path)
        return self.link_library

    def _update_links(self):
        if self.source_doc is None:
            messagebox.showwarning("No source file", "Please upload a source file first.")
            return

        library = self._ensure_library()
        if library is None:
            return

        try:
            update_library_source_file(library, self.source_doc)
        except Exception as exc:
            messagebox.showerror("Update error", str(exc))
            self._set_status(f"Error: {exc}")
            return

        saved_path = save_link_library(library, self.library_path)
        self.library_path = saved_path
        self.link_library = library
        self.library_label.configure(text=f"Library: {saved_path}")
        self._refresh_file_list()
        self._update_control_states()
        self._set_status(f"Links updated: {saved_path}")

    def _generate(self):
        if self.source_doc is None:
            messagebox.showwarning("No source file", "Please upload a source file first.")
            return

        # In create mode self.doc is the source doc, so syncing stores the
        # current selections as the source default snapshot.
        self._sync_model()
        library = self._ensure_library()
        if library is None:
            return

        output_dir = self.output_dir_entry.get().strip()
        output_name = self.output_name_entry.get().strip()
        generate_pdf = bool(self.generate_pdf_var.get())

        if not output_dir:
            messagebox.showwarning("No output folder", "Please choose an output folder.")
            return
        if not output_name:
            messagebox.showwarning("No output file name", "Please choose an output file name.")
            return
        if os.path.basename(output_name) != output_name:
            messagebox.showwarning("Invalid file name", "Output file name must not include folder separators.")
            return
        if not output_name.lower().endswith(".tex"):
            output_name += ".tex"
        if not os.path.isdir(output_dir):
            messagebox.showwarning("Invalid output folder", "The selected output folder does not exist.")
            return

        # Store the current source snapshot (no sibling rebuild on create).
        library.update_source_file(self.source_doc)

        output_path = os.path.join(output_dir, output_name)
        try:
            generated_file = library.create_generated_file(output_path, generate_pdf=generate_pdf)
        except Exception as exc:
            messagebox.showerror("Write error", str(exc))
            self._set_status(f"Error: {exc}")
            return
        library.add_generated_file(generated_file)

        saved_path = save_link_library(library, self.library_path)
        self.library_path = saved_path
        self.link_library = library
        self.library_label.configure(text=f"Library: {saved_path}")

        self._refresh_file_list()
        self._update_control_states()
        self._back_to_list()

        if generate_pdf and generated_file.pdf_path:
            self._set_status(f"Generated: {output_path} and {generated_file.pdf_path}")
        else:
            self._set_status(f"Generated: {output_path}")

    def _rebuild(self):
        if self.link_library is None or self.editing_path is None:
            return

        self._sync_model()
        generate_pdf = bool(self.generate_pdf_var.get())
        output_path = self.editing_path

        try:
            generated_file = self.link_library.create_generated_file(
                output_path, template=self.doc, generate_pdf=generate_pdf
            )
        except Exception as exc:
            messagebox.showerror("Rebuild error", str(exc))
            self._set_status(f"Error: {exc}")
            return
        # Replace the existing entry (validates subset against the source).
        self.link_library.add_generated_file(generated_file)

        saved_path = save_link_library(self.link_library, self.library_path)
        self.library_path = saved_path
        self.library_label.configure(text=f"Library: {saved_path}")

        self._refresh_file_list()
        self._update_control_states()
        self._back_to_list()

        if generate_pdf and generated_file.pdf_path:
            self._set_status(f"Rebuilt: {output_path} and {generated_file.pdf_path}")
        else:
            self._set_status(f"Rebuilt: {output_path}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str):
        self.status_label.configure(text=f"Status: {msg}")

    def _announce_load(self, default_msg: str):
        if self._pending_warnings:
            count = len(self._pending_warnings)
            self._set_status(
                f"{default_msg} ({count} version warning(s)): {self._pending_warnings[0]}"
            )
        else:
            self._set_status(default_msg)
