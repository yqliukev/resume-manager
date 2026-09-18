from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_VERSION_ID = "default"


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
class Entry:
    '''
      Item within a section, e.g. "Backend Developer @ Ground News".
      Each "skill line" is an item. An item groups one or more versions;
      only the active version is emitted when generating a file.
    '''
    item_id: str                 # grouping key (explicit @item, else version label)
    display_label: str           # clean parent text for UI tree
    versions: list["EntryVersion"] = field(default_factory=list)
    selected: bool = True        # include/exclude the whole item
    active_version_id: str = DEFAULT_VERSION_ID

    def resolve_active_version_id(self) -> str:
        '''Return a version id that actually exists, falling back gracefully.'''
        ids = [version.version_id for version in self.versions]
        if self.active_version_id in ids:
            return self.active_version_id
        if DEFAULT_VERSION_ID in ids:
            return DEFAULT_VERSION_ID
        return ids[0] if ids else self.active_version_id

    def active_version(self) -> "EntryVersion | None":
        target = self.resolve_active_version_id()
        for version in self.versions:
            if version.version_id == target:
                return version
        return self.versions[0] if self.versions else None

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
    def from_dict(cls, data: dict) -> "Entry | None":
        display_label = data.get("display_label")
        if not isinstance(display_label, str):
            return None

        raw_versions = data.get("versions")
        versions: list[EntryVersion] = []
        if isinstance(raw_versions, list):
            for raw_version in raw_versions:
                if not isinstance(raw_version, dict):
                    continue
                version = EntryVersion.from_dict(raw_version)
                if version is not None:
                    versions.append(version)

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

        active_version_id = data.get("active_version_id")
        if not isinstance(active_version_id, str) or not active_version_id:
            active_version_id = versions[0].version_id

        selected = data.get("selected", True)
        return cls(
            item_id=item_id,
            display_label=display_label,
            versions=versions,
            selected=bool(selected),
            active_version_id=active_version_id,
        )


@dataclass
class Section:
    ''' Document section, e.g. "Work Experience". '''
    name: str            # e.g. "Work Experience"
    section_type: str    # "standard" | "skills"
    raw_header: str      # "\section{...}" line verbatim (may include preceding comment)
    list_prefix: str     # lines between header and first entry
    list_suffix: str     # lines after last entry (before next section)
    entries: list[Entry] = field(default_factory=list)
    selected: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "section_type": self.section_type,
            "raw_header": self.raw_header,
            "list_prefix": self.list_prefix,
            "list_suffix": self.list_suffix,
            "selected": self.selected,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Section | None":
        name = data.get("name")
        section_type = data.get("section_type")
        raw_header = data.get("raw_header")
        list_prefix = data.get("list_prefix")
        list_suffix = data.get("list_suffix")

        if not isinstance(name, str) or not isinstance(section_type, str):
            return None
        if not isinstance(raw_header, str) or not isinstance(list_prefix, str) or not isinstance(list_suffix, str):
            return None

        raw_entries = data.get("entries", [])
        entries: list[Entry] = []
        if isinstance(raw_entries, list):
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    continue
                entry = Entry.from_dict(raw_entry)
                if entry is not None:
                    entries.append(entry)

        selected = data.get("selected", True)
        return cls(
            name=name,
            section_type=section_type,
            raw_header=raw_header,
            list_prefix=list_prefix,
            list_suffix=list_suffix,
            entries=entries,
            selected=bool(selected),
        )


@dataclass
class ResumeDocument(ABC):
    ''' Parameters also called Zones '''
    preamble: str           # everything up to (not including) \begin{center}
    header: str             # \begin{center}...\end{center} block (inclusive)
    sections: list[Section] = field(default_factory=list)
    trailing: str = ""      # \end{document} and any trailing content
    parse_warnings: list[str] = field(default_factory=list, compare=False, repr=False)  # not persisted

    @property
    @abstractmethod
    def document_type(self) -> str:
        """Return the document category used for persistence."""
    
    def to_dict(self) -> dict:
        return {
            "preamble": self.preamble,
            "header": self.header,
            "trailing": self.trailing,
            "sections": [section.to_dict() for section in self.sections],
        }
    
def _normalize_path(path: str) -> str:
    if not path:
        return ""
    return str(Path(path).expanduser().resolve(strict=False))


def _document_fields_from_dict(raw: dict) -> tuple[str | None, str | None, str | None, list[Section]]:
    preamble = raw.get("preamble")
    if not isinstance(preamble, str):
        preamble = None

    header = raw.get("header")
    if not isinstance(header, str):
        header = None

    trailing = raw.get("trailing")
    if not isinstance(trailing, str):
        trailing = None

    raw_sections = raw.get("sections")
    sections: list[Section] = []
    if isinstance(raw_sections, list):
        for raw_section in raw_sections:
            if isinstance(raw_section, dict):
                section = Section.from_dict(raw_section)
                if section is not None:
                    sections.append(section)

    return preamble, header, trailing, sections


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceFile(ResumeDocument):
    path: str = ""

    def __post_init__(self) -> None:
        self.path = _normalize_path(self.path)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["path"] = self.path
        data["document_type"] = self.document_type
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "SourceFile | None":
        if not isinstance(raw, dict):
            return None
        preamble, header, trailing, sections = _document_fields_from_dict(raw)
        path = raw.get("path")
        if not isinstance(path, str):
            path = ""
        return cls(
            path=path,
            preamble=preamble or "",
            header=header or "",
            sections=sections,
            trailing=trailing or "",
        )

    @property
    def document_type(self) -> str:
        return "source"


@dataclass
class GeneratedFile(ResumeDocument):
    path: str = ""
    pdf_path: str = ""

    def __post_init__(self) -> None:
        self.path = _normalize_path(self.path)
        self.pdf_path = _normalize_path(self.pdf_path)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["path"] = self.path
        data["pdf_path"] = self.pdf_path
        data["document_type"] = self.document_type
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "GeneratedFile | None":
        if not isinstance(raw, dict):
            return None
        preamble, header, trailing, sections = _document_fields_from_dict(raw)
        path = raw.get("path")
        if not isinstance(path, str):
            path = ""
        pdf_path = raw.get("pdf_path")
        if not isinstance(pdf_path, str):
            pdf_path = ""
        return cls(
            path=path,
            pdf_path=pdf_path,
            preamble=preamble or "",
            header=header or "",
            sections=sections,
            trailing=trailing or "",
        )

    @property
    def document_type(self) -> str:
        return "generated"


def _section_entries_map(document: ResumeDocument) -> dict[str, set[str]]:
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
    if preferred in ids:
        return preferred  # type: ignore[return-value]
    if DEFAULT_VERSION_ID in ids:
        return DEFAULT_VERSION_ID
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


def merge_source_document(current: SourceFile, template: "ResumeDocument | None" = None) -> SourceFile:
    if template is None:
        return SourceFile.from_dict(current.to_dict()) or current

    template_sections = {section.name: section for section in template.sections}
    merged_sections: list[Section] = []

    for current_section in current.sections:
        template_section = template_sections.get(current_section.name)
        selected = template_section.selected if template_section is not None else True

        template_entries = {
            entry.item_id: entry for entry in template_section.entries
        } if template_section is not None else {}
        merged_entries: list[Entry] = []

        for current_entry in current_section.entries:
            template_entry = template_entries.get(current_entry.item_id)
            entry_selected = template_entry.selected if template_entry is not None else True
            preferred_version = (
                template_entry.active_version_id if template_entry is not None
                else current_entry.active_version_id
            )
            versions = _clone_versions(current_entry.versions)
            merged_entries.append(
                Entry(
                    item_id=current_entry.item_id,
                    display_label=current_entry.display_label,
                    versions=versions,
                    selected=entry_selected,
                    active_version_id=_pick_active_version_id(versions, preferred_version),
                )
            )

        merged_sections.append(
            Section(
                name=current_section.name,
                section_type=current_section.section_type,
                raw_header=current_section.raw_header,
                list_prefix=current_section.list_prefix,
                list_suffix=current_section.list_suffix,
                entries=merged_entries,
                selected=selected,
            )
        )

    return SourceFile(
        path=current.path,
        preamble=current.preamble,
        header=current.header,
        sections=merged_sections,
        trailing=current.trailing,
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

        source = self.source_file
        template_sections = {section.name: section for section in template.sections} if template else {}
        sections: list[Section] = []

        for source_section in source.sections:
            template_section = template_sections.get(source_section.name)
            selected = template_section.selected if template_section is not None else source_section.selected
            if template is not None and template_section is None:
                selected = True

            template_entries = {
                entry.item_id: entry for entry in template_section.entries
            } if template_section is not None else {}
            entries: list[Entry] = []

            for source_entry in source_section.entries:
                template_entry = template_entries.get(source_entry.item_id)
                entry_selected = template_entry.selected if template_entry is not None else source_entry.selected
                preferred_version = (
                    template_entry.active_version_id if template_entry is not None
                    else source_entry.active_version_id
                )
                if template is not None and template_entry is None:
                    entry_selected = True

                versions = _clone_versions(source_entry.versions)
                entries.append(
                    Entry(
                        item_id=source_entry.item_id,
                        display_label=source_entry.display_label,
                        versions=versions,
                        selected=entry_selected,
                        active_version_id=_pick_active_version_id(versions, preferred_version),
                    )
                )

            sections.append(
                Section(
                    name=source_section.name,
                    section_type=source_section.section_type,
                    raw_header=source_section.raw_header,
                    list_prefix=source_section.list_prefix,
                    list_suffix=source_section.list_suffix,
                    entries=entries,
                    selected=selected,
                )
            )

        generated_file = GeneratedFile(
            path=output_path,
            preamble=source.preamble,
            header=source.header,
            sections=sections,
            trailing=source.trailing,
        )

        write_tex(assemble(generated_file), output_path)

        if generate_pdf:
            output_dir = str(Path(output_path).parent)
            success, log = compile_pdf(output_path, output_dir)
            if not success:
                raise RuntimeError(f"PDF generation failed for {output_path}\n{log}")
            write_tex(assemble(generated_file), output_path)
            generated_file.pdf_path = str(Path(output_path).with_suffix(".pdf"))

        return generated_file

    def refresh_generated_files(self) -> None:
        refreshed: dict[str, GeneratedFile] = {}
        for gen_path, generated_file in self.links.items():
            refreshed[gen_path] = self.create_generated_file(
                gen_path,
                template=generated_file,
                generate_pdf=bool(generated_file.pdf_path),
            )
        self.links = refreshed
        
        
