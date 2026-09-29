from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_VERSION_ID = "default"


def _parse_list[T](raw: object, parse: Callable[[dict], T | None]) -> list[T]:
    if not isinstance(raw, list):
        return []
    items: list[T] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed = parse(item)
        if parsed is not None:
            items.append(parsed)
    return items


@dataclass
class EntryVersion:
    '''
      A single writeup of an item. An item may have several versions
      (e.g. an "swe" and an "ml" version of the same job); exactly one is
      emitted when generating a file.
    '''
    version_id: str      # radio option name, e.g. "swe" (unique within an item)
    display_label: str   # clean text for UI (e.g. "ML Engineer @ Ground News")
    raw_text: str        # verbatim source lines, preserved for output

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "display_label": self.display_label,
            "raw_text": self.raw_text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EntryVersion | None":
        display_label = data.get("display_label")
        raw_text = data.get("raw_text")
        if not isinstance(display_label, str) or not isinstance(raw_text, str):
            return None
        version_id = data.get("version_id")
        if not isinstance(version_id, str) or not version_id:
            version_id = DEFAULT_VERSION_ID
        return cls(version_id=version_id, display_label=display_label, raw_text=raw_text)


@dataclass
class SourceEntry:
    '''
      Item within a source section, e.g. "Backend Developer @ Ground News".
      Each "skill line" is an item. An item groups one or more versions in
      source order; the first version is the default for new generated files.
    '''
    item_id: str                 # grouping key (explicit @item, else version label)
    display_label: str           # clean parent text for UI tree
    versions: list[EntryVersion]

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "display_label": self.display_label,
            "versions": [version.to_dict() for version in self.versions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SourceEntry | None":
        display_label = data.get("display_label")
        if not isinstance(display_label, str):
            return None

        versions = _parse_list(data.get("versions"), EntryVersion.from_dict)
        if not versions:
            # Backward compatibility: old schema stored a single raw_text.
            raw_text = data.get("raw_text")
            if not isinstance(raw_text, str):
                return None
            versions = [
                EntryVersion(
                    version_id=DEFAULT_VERSION_ID,
                    display_label=display_label,
                    raw_text=raw_text,
                )
            ]

        item_id = data.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            item_id = display_label

        return cls(item_id=item_id, display_label=display_label, versions=versions)


@dataclass
class GeneratedEntry(SourceEntry):
    '''
      A source item plus the choices made for one generated file: whether it
      is included, and which version is emitted.
    '''
    active_version_id: str
    selected: bool = True        # include/exclude the whole item

    @classmethod
    def from_source(
        cls,
        entry: SourceEntry,
        preferred_version: str | None = None,
        selected: bool = True,
    ) -> "GeneratedEntry":
        return cls(
            item_id=entry.item_id,
            display_label=entry.display_label,
            versions=_clone_versions(entry.versions),
            active_version_id=_pick_active_version_id(entry.versions, preferred_version),
            selected=selected,
        )

    def active_version(self) -> EntryVersion | None:
        for version in self.versions:
            if version.version_id == self.active_version_id:
                return version
        return self.versions[0] if self.versions else None

    def resolve_active_version_id(self) -> str:
        '''Return a version id that actually exists, falling back to the first.'''
        version = self.active_version()
        return version.version_id if version is not None else self.active_version_id

    @property
    def active_raw_text(self) -> str:
        version = self.active_version()
        return version.raw_text if version is not None else ""

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "display_label": self.display_label,
            "selected": self.selected,
            "active_version_id": self.active_version_id,
            "versions": [version.to_dict() for version in self.versions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GeneratedEntry | None":
        entry = SourceEntry.from_dict(data)
        if entry is None:
            return None
        active_version_id = data.get("active_version_id")
        return cls.from_source(
            entry,
            preferred_version=active_version_id if isinstance(active_version_id, str) else None,
            selected=bool(data.get("selected", True)),
        )


@dataclass
class SectionLayout:
    ''' Document section, e.g. "Work Experience". '''
    name: str            # e.g. "Work Experience"
    section_type: str    # "standard" | "skills"
    raw_header: str      # "\section{...}" line verbatim (may include preceding comment)
    list_prefix: str     # lines between header and first entry
    list_suffix: str     # lines after last entry (before next section)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "section_type": self.section_type,
            "raw_header": self.raw_header,
            "list_prefix": self.list_prefix,
            "list_suffix": self.list_suffix,
        }


def _section_layout_from_dict(data: dict) -> dict[str, str] | None:
    keys = ("name", "section_type", "raw_header", "list_prefix", "list_suffix")
    layout = {key: data.get(key) for key in keys}
    if not all(isinstance(value, str) for value in layout.values()):
        return None
    return layout  # type: ignore[return-value]


@dataclass
class SourceSection(SectionLayout):
    entries: list[SourceEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["entries"] = [entry.to_dict() for entry in self.entries]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SourceSection | None":
        layout = _section_layout_from_dict(data)
        if layout is None:
            return None
        return cls(**layout, entries=_parse_list(data.get("entries"), SourceEntry.from_dict))


@dataclass
class GeneratedSection(SectionLayout):
    entries: list[GeneratedEntry] = field(default_factory=list)
    selected: bool = True

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["selected"] = self.selected
        data["entries"] = [entry.to_dict() for entry in self.entries]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "GeneratedSection | None":
        layout = _section_layout_from_dict(data)
        if layout is None:
            return None
        return cls(
            **layout,
            entries=_parse_list(data.get("entries"), GeneratedEntry.from_dict),
            selected=bool(data.get("selected", True)),
        )


@dataclass
class ResumeDocument(ABC):
    ''' Parameters also called Zones '''
    preamble: str           # everything up to (not including) \begin{center}
    header: str             # \begin{center}...\end{center} block (inclusive)
    trailing: str = ""      # \end{document} and any trailing content

    @property
    @abstractmethod
    def document_type(self) -> str:
        """Return the document category used for persistence."""
    
    def to_dict(self) -> dict:
        return {
            "preamble": self.preamble,
            "header": self.header,
            "trailing": self.trailing,
        }
    
def _normalize_path(path: str) -> str:
    if not path:
        return ""
    return str(Path(path).expanduser().resolve(strict=False))


def _document_fields_from_dict(raw: dict) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("preamble", "header", "trailing"):
        value = raw.get(key)
        fields[key] = value if isinstance(value, str) else ""
    return fields


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceFile(ResumeDocument):
    sections: list[SourceSection] = field(default_factory=list)
    path: str = ""
    parse_warnings: list[str] = field(default_factory=list, compare=False, repr=False)  # not persisted

    def __post_init__(self) -> None:
        self.path = _normalize_path(self.path)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["sections"] = [section.to_dict() for section in self.sections]
        data["path"] = self.path
        data["document_type"] = self.document_type
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "SourceFile | None":
        if not isinstance(raw, dict):
            return None
        path = raw.get("path")
        if not isinstance(path, str):
            path = ""
        return cls(
            **_document_fields_from_dict(raw),
            sections=_parse_list(raw.get("sections"), SourceSection.from_dict),
            path=path,
        )

    @property
    def document_type(self) -> str:
        return "source"


@dataclass
class GeneratedFile(ResumeDocument):
    sections: list[GeneratedSection] = field(default_factory=list)
    path: str = ""
    pdf_path: str = ""

    def __post_init__(self) -> None:
        self.path = _normalize_path(self.path)
        self.pdf_path = _normalize_path(self.pdf_path)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["sections"] = [section.to_dict() for section in self.sections]
        data["path"] = self.path
        data["pdf_path"] = self.pdf_path
        data["document_type"] = self.document_type
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "GeneratedFile | None":
        if not isinstance(raw, dict):
            return None
        path = raw.get("path")
        if not isinstance(path, str):
            path = ""
        pdf_path = raw.get("pdf_path")
        if not isinstance(pdf_path, str):
            pdf_path = ""
        return cls(
            **_document_fields_from_dict(raw),
            sections=_parse_list(raw.get("sections"), GeneratedSection.from_dict),
            path=path,
            pdf_path=pdf_path,
        )

    @property
    def document_type(self) -> str:
        return "generated"


def _section_entries_map(document: SourceFile | GeneratedFile) -> dict[str, set[str]]:
    return {
        section.name: {entry.item_id for entry in section.entries}
        for section in document.sections
    }


def _clone_versions(versions: list[EntryVersion]) -> list[EntryVersion]:
    return [
        EntryVersion(
            version_id=version.version_id,
            display_label=version.display_label,
            raw_text=version.raw_text,
        )
        for version in versions
    ]


def _pick_active_version_id(versions: list[EntryVersion], preferred: str | None) -> str:
    ids = [version.version_id for version in versions]
    if preferred is not None and preferred in ids:
        return preferred
    return ids[0] if ids else DEFAULT_VERSION_ID


def validate_generated_subset(source: SourceFile, generated: GeneratedFile) -> None:
    """Ensure generated sections/entries are strict subsets of the source file."""
    source_sections = _section_entries_map(source)
    source_section_types = {section.name: section.section_type for section in source.sections}

    for target_section in generated.sections:
        if target_section.name not in source_sections:
            raise ValueError(f"Generated section is not present in source: {target_section.name}")

        source_section_type = source_section_types.get(target_section.name)
        if target_section.section_type != source_section_type:
            raise ValueError(
                f"Generated section type mismatch for {target_section.name}: "
                f"{target_section.section_type} != {source_section_type}"
            )

        source_entries = source_sections[target_section.name]
        for target_entry in target_section.entries:
            if target_entry.item_id not in source_entries:
                raise ValueError(
                    "Generated entry is not present in source section "
                    f"{target_section.name}: {target_entry.item_id}"
                )


def default_link_library_path(source_path: str) -> str:
    source_abs = _normalize_path(source_path)
    if not source_abs:
        return ""
    source_file = Path(source_abs)
    return str(source_file.with_name(f"{source_file.stem}.resume-links.json"))


def build_generated_file(
    source: SourceFile,
    template: GeneratedFile | None = None,
    path: str = "",
) -> GeneratedFile:
    """Lay the choices from ``template`` over the current source structure.

    Nothing is written to disk. Sections and entries that ``template`` does not
    know about (all of them when there is no template) start selected, with
    the first version of each item active.
    """
    template_sections = {section.name: section for section in template.sections} if template else {}
    sections: list[GeneratedSection] = []

    for source_section in source.sections:
        template_section = template_sections.get(source_section.name)
        template_entries = {
            entry.item_id: entry for entry in template_section.entries
        } if template_section is not None else {}

        entries: list[GeneratedEntry] = []
        for source_entry in source_section.entries:
            template_entry = template_entries.get(source_entry.item_id)
            if template_entry is None:
                entries.append(GeneratedEntry.from_source(source_entry))
            else:
                entries.append(
                    GeneratedEntry.from_source(
                        source_entry,
                        preferred_version=template_entry.active_version_id,
                        selected=template_entry.selected,
                    )
                )

        sections.append(
            GeneratedSection(
                name=source_section.name,
                section_type=source_section.section_type,
                raw_header=source_section.raw_header,
                list_prefix=source_section.list_prefix,
                list_suffix=source_section.list_suffix,
                entries=entries,
                selected=template_section.selected if template_section is not None else True,
            )
        )

    return GeneratedFile(
        path=path,
        preamble=source.preamble,
        header=source.header,
        sections=sections,
        trailing=source.trailing,
    )


@dataclass
class LinkLibrary:
    """Library of LinkRecords"""

    source_path: str
    source_file: SourceFile
    links: dict[str, GeneratedFile] = field(default_factory=dict) # keyed by generated file path

    def to_dict(self) -> dict: 
        return {
            "source_path": self.source_path,
            "source_file": self.source_file.to_dict(),
            "links": {gen_path: gen_file.to_dict() for gen_path, gen_file in self.links.items()},
        }
    
    @classmethod
    def from_dict(cls, raw: dict) -> "LinkLibrary | None":
        if not isinstance(raw, dict):
            return None

        raw_file = raw.get("source_file")
        if not isinstance(raw_file, dict):
            return None

        source_file = SourceFile.from_dict(raw_file)
        if source_file is None:
            return None

        source_path = raw.get("source_path")
        if not isinstance(source_path, str):
            source_path = source_file.path

        links: dict[str, GeneratedFile] = {}
        raw_links = raw.get("links")
        if isinstance(raw_links, dict):
            for gen_path, raw_gen_file in raw_links.items():
                if not isinstance(gen_path, str) or not isinstance(raw_gen_file, dict):
                    continue
                gen_file = GeneratedFile.from_dict({**raw_gen_file, "path": gen_path})
                if gen_file is not None:
                    links[gen_path] = gen_file

        return cls(
            source_path=source_path,
            source_file=source_file,
            links=links,
        )

    @classmethod
    def empty_for_source(cls, source_file: SourceFile) -> "LinkLibrary":
        return cls(
            source_path=source_file.path,
            source_file=source_file,
            links={},
        )

    def update_source_file(self, source_file: SourceFile) -> None:
        self.source_file = source_file
        self.source_path = self.source_file.path

    def add_generated_file(self, generated_file: GeneratedFile) -> None:
        validate_generated_subset(self.source_file, generated_file)
        self.links[generated_file.path] = generated_file

    def remove_generated_file(self, path: str, delete_from_disk: bool = True) -> None:
        """Drop a generated file from the library and optionally delete it on disk.

        The library entry is always cleared, even when the underlying files are
        already missing (orphaned rows). If ``delete_from_disk`` is set and a file
        exists but cannot be removed, an ``OSError`` is raised before the library
        entry is dropped so callers can surface the failure.
        """
        normalized = _normalize_path(path)
        generated_file = self.links.get(normalized) or self.links.get(path)

        if delete_from_disk and generated_file is not None:
            for target in (generated_file.path, generated_file.pdf_path):
                if not target:
                    continue
                Path(target).unlink(missing_ok=True)

        self.links.pop(normalized, None)
        self.links.pop(path, None)

    def create_generated_file(
        self,
        output_path: str,
        template: GeneratedFile | None = None,
        generate_pdf: bool = False,
    ) -> GeneratedFile:
        from .assembler import assemble, compile_pdf, write_tex

        generated_file = build_generated_file(self.source_file, template, output_path)

        write_tex(assemble(generated_file), output_path)

        if generate_pdf:
            output_dir = str(Path(output_path).parent)
            success, log = compile_pdf(output_path, output_dir)
            if not success:
                raise RuntimeError(f"PDF generation failed for {output_path}\n{log}")
            write_tex(assemble(generated_file), output_path)
            generated_file.pdf_path = str(Path(output_path).with_suffix(".pdf"))

        return generated_file

    def write_source_file(self, output_path: str) -> str:
        """Write the full source snapshot to ``output_path`` and retarget the library.

        Every section and every version is emitted. The previous source file is
        left in place.
        """
        from .assembler import assemble_source, write_tex

        destination = _normalize_path(output_path)
        if not destination:
            raise ValueError("Choose a destination path for the source file.")
        parent = Path(destination).parent
        if not parent.is_dir():
            raise ValueError(f"The destination folder does not exist: {parent}")
        if any(
            _normalize_path(key) == destination or generated.path == destination
            for key, generated in self.links.items()
        ):
            raise ValueError("The source destination is already a generated file in this library.")

        write_tex(assemble_source(self.source_file), destination)
        rewritten = SourceFile.from_dict(self.source_file.to_dict())
        if rewritten is None:
            raise ValueError("Could not copy the source snapshot.")
        rewritten.path = destination
        self.update_source_file(rewritten)
        return destination

    def _lookup_link(self, path: str) -> tuple[str, GeneratedFile] | None:
        normalized = _normalize_path(path)
        if path in self.links:
            return path, self.links[path]
        if normalized in self.links:
            return normalized, self.links[normalized]
        for key, generated in self.links.items():
            if _normalize_path(key) == normalized or generated.path == normalized:
                return key, generated
        return None

    def relocate_generated_file(
        self, old_path: str, new_path: str
    ) -> tuple[GeneratedFile, str | None]:
        """Rewrite one generated file at ``new_path`` and rekey the library.

        The file at ``old_path`` is not deleted. When PDF compilation fails, the
        ``.tex`` is kept, ``pdf_path`` is left empty, and the error text is
        returned as the second value after the library has been updated.
        """
        found = self._lookup_link(old_path)
        if found is None:
            raise KeyError(f"Generated file is not in the library: {old_path}")
        old_key, existing = found

        destination = _normalize_path(new_path)
        if not destination:
            raise ValueError("Choose a destination path for the generated file.")
        parent = Path(destination).parent
        if not parent.is_dir():
            raise ValueError(f"The destination folder does not exist: {parent}")
        if destination == _normalize_path(self.source_path):
            raise ValueError("A generated file cannot use the source file path.")

        for key, other in self.links.items():
            if key == old_key:
                continue
            if _normalize_path(key) == destination or other.path == destination:
                raise ValueError(f"Another generated file is already stored at {destination}")

        want_pdf = bool(existing.pdf_path)
        pdf_error: str | None = None
        try:
            generated = self.create_generated_file(
                destination,
                template=existing,
                generate_pdf=want_pdf,
            )
        except (RuntimeError, FileNotFoundError) as exc:
            if not want_pdf:
                raise
            pdf_error = str(exc)
            generated = self.create_generated_file(
                destination,
                template=existing,
                generate_pdf=False,
            )

        if old_key != generated.path:
            self.remove_generated_file(old_key, delete_from_disk=False)
        self.add_generated_file(generated)
        return generated, pdf_error

    def refresh_generated_files(self) -> None:
        refreshed: dict[str, GeneratedFile] = {}
        for gen_path, generated_file in self.links.items():
            refreshed[gen_path] = self.create_generated_file(
                gen_path,
                template=generated_file,
                generate_pdf=bool(generated_file.pdf_path),
            )
        self.links = refreshed
        
        
