import re
from dataclasses import dataclass
from .models import DEFAULT_VERSION_ID, EntryVersion, SourceEntry, SourceFile, SourceSection


# ---------------------------------------------------------------------------
# Version markers
# ---------------------------------------------------------------------------

_ITEM_MARKER_RE = re.compile(r'^\s*%\s*@item:\s*(.+?)\s*$')
_VERSION_MARKER_RE = re.compile(r'^\s*%\s*@version:\s*(.+?)\s*$')


@dataclass
class _Candidate:
    """A single parsed block before grouping into versioned items."""
    display_label: str
    raw_text: str
    item_id: str | None
    version_id: str | None


def _extract_markers(raw_text: str) -> tuple[str | None, str | None]:
    """Scan a block's comment lines for @item / @version markers."""
    item_id: str | None = None
    version_id: str | None = None
    for line in raw_text.splitlines():
        item_match = _ITEM_MARKER_RE.match(line)
        if item_match:
            item_id = item_match.group(1).strip()
            continue
        version_match = _VERSION_MARKER_RE.match(line)
        if version_match:
            version_id = version_match.group(1).strip()
    return item_id or None, version_id or None


def _group_candidates(candidates: list[_Candidate]) -> tuple[list[SourceEntry], list[str]]:
    """Group same-@item blocks into versioned entries; leave unmarked standalone."""
    entries: list[SourceEntry] = []
    warnings: list[str] = []
    index_by_item: dict[str, int] = {}

    for candidate in candidates:
        has_marker = candidate.item_id is not None
        version_id = candidate.version_id or DEFAULT_VERSION_ID
        version = EntryVersion(
            version_id=version_id,
            display_label=candidate.display_label,
            raw_text=candidate.raw_text,
        )

        if has_marker and candidate.item_id in index_by_item:
            entry = entries[index_by_item[candidate.item_id]]
            existing = next(
                (v for v in entry.versions if v.version_id == version_id), None
            )
            if existing is not None:
                warnings.append(
                    f"Duplicate version '{version_id}' for item "
                    f"'{candidate.item_id}'; keeping the later block."
                )
                entry.versions = [
                    version if v.version_id == version_id else v
                    for v in entry.versions
                ]
            else:
                entry.versions.append(version)
            continue

        if has_marker:
            item_id = candidate.item_id
            parent_label = candidate.item_id
        else:
            # Unmarked blocks are standalone items, never grouped, and keyed by
            # display_label to stay compatible with pre-versioning link libraries.
            item_id = candidate.display_label
            parent_label = candidate.display_label

        entry = SourceEntry(
            item_id=item_id,  # type: ignore[arg-type]
            display_label=parent_label,  # type: ignore[arg-type]
            versions=[version],
        )
        if has_marker:
            index_by_item[item_id] = len(entries)  # type: ignore[index]
        entries.append(entry)

    return entries, warnings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_latex(s: str) -> str:
    """Remove common LaTeX commands to produce a readable display label."""
    s = re.sub(r'\\textbf\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\textit\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\href\{[^}]*\}\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\underline\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\smash\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\small\s*', '', s)
    s = re.sub(r'\\\w+\s*', '', s)   # remove remaining commands
    return s.strip()


def extract_brace_groups(text: str, n: int) -> list[str]:
    """
    Extract up to n top-level brace-delimited groups from text.
    text may span multiple lines (concatenated).
    """
    groups = []
    depth = 0
    current = []
    i = 0
    while i < len(text) and len(groups) < n:
        ch = text[i]
        if ch == '{':
            if depth == 0:
                current = []
            else:
                current.append(ch)
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                groups.append(''.join(current))
            elif depth > 0:
                current.append(ch)
        elif depth > 0:
            current.append(ch)
        i += 1
    return groups


def net_braces(text: str) -> int:
    """Return net brace depth of text (opens minus closes)."""
    return text.count('{') - text.count('}')


def _is_blank_or_comment(line: str) -> bool:
    s = line.strip()
    return s == '' or s.startswith('%')


def _find_prefix_start(lines: list[str], trigger_idx: int) -> int:
    """Walk backwards from trigger_idx to include preceding blank/comment lines."""
    ps = trigger_idx
    j = trigger_idx - 1
    while j >= 0 and _is_blank_or_comment(lines[j]):
        ps = j
        j -= 1
    return ps


# ---------------------------------------------------------------------------
# Phase 1 — Zone extraction
# ---------------------------------------------------------------------------

def zone_extract(lines: list[str]) -> tuple[str, str, list[str], str]:
    """
    Split lines into (preamble, header, body_lines, trailing).
    preamble: everything before \\begin{center}
    header:   \\begin{center} ... \\end{center} (inclusive)
    body_lines: list of lines after \\end{center} until \\end{document}
    trailing: \\end{document} and anything after
    """
    PREAMBLE, HEADER, BODY, TRAILING = range(4)
    state = PREAMBLE

    preamble_lines = []
    header_lines = []
    body_lines = []
    trailing_lines = []

    for line in lines:
        if state == PREAMBLE:
            if r'\begin{center}' in line:
                state = HEADER
                header_lines.append(line)
            else:
                preamble_lines.append(line)
        elif state == HEADER:
            header_lines.append(line)
            if r'\end{center}' in line:
                state = BODY
        elif state == BODY:
            if r'\end{document}' in line:
                state = TRAILING
                trailing_lines.append(line)
            else:
                body_lines.append(line)
        else:  # TRAILING
            trailing_lines.append(line)

    return (
        ''.join(preamble_lines),
        ''.join(header_lines),
        body_lines,
        ''.join(trailing_lines),
    )


# ---------------------------------------------------------------------------
# Phase 2 — Section splitting
# ---------------------------------------------------------------------------

def section_split(body_lines: list[str]) -> list[tuple[list[str], list[str]]]:
    """
    Split body_lines into (header_lines, content_lines) pairs per section.
    header_lines: comment/blank lines leading up to \\section{} + the \\section line itself.
    content_lines: lines following the \\section line until the next section header starts.
    """
    section_re = re.compile(r'\\section\*?\{')

    # Find indices of \\section lines
    section_indices = [i for i, l in enumerate(body_lines) if section_re.search(l)]

    if not section_indices:
        return []

    # For each section, walk backwards to absorb leading blank/comment lines
    prefix_starts = [] # starting indices before {section}
    for si in section_indices:
        ps = _find_prefix_start(body_lines, si)
        prefix_starts.append(ps)

    # Build (header_lines, content_lines) pairs
    chunks = []
    for k, si in enumerate(section_indices):
        chunk_start = prefix_starts[k]
        # Content starts after the \\section line
        content_start = si + 1
        # Content ends at the start of the next section's prefix (or end of body)
        if k + 1 < len(section_indices):
            content_end = prefix_starts[k + 1]
        else:
            content_end = len(body_lines)

        header_lines = body_lines[chunk_start: si + 1]
        content_lines = body_lines[content_start: content_end]
        chunks.append((header_lines, content_lines))

    return chunks


# ---------------------------------------------------------------------------
# Phase 3 — Entry extraction
# ---------------------------------------------------------------------------

def parse_standard_entries(
    content_lines: list[str],
) -> tuple[str, list[_Candidate], str]:
    """
    Parse entries from a standard section (Work Experience / Projects / Education).
    Returns (list_prefix, candidates, list_suffix).
    """
    trigger_re = re.compile(r'\\resumeSubheading\b|\\resumeProjectHeading\b|\\resumeHeading\b')

    # Find trigger positions
    trigger_indices = [i for i, l in enumerate(content_lines) if trigger_re.search(l)]

    if not trigger_indices:
        # No entries — everything is list_prefix
        return (''.join(content_lines), [], '')

    # Compute entry_start_list for each trigger
    entry_start_list = [_find_prefix_start(content_lines, ti) for ti in trigger_indices]

    list_prefix_end = entry_start_list[0]
    list_prefix = ''.join(content_lines[:list_prefix_end])

    candidates: list[_Candidate] = []
    for k, ti in enumerate(trigger_indices):
        entry_start = entry_start_list[k]

        # Walk forward until \\resumeItemListEnd (inclusive)
        entry_end = ti
        for j in range(ti, len(content_lines)):
            entry_end = j
            if r'\resumeItemListEnd' in content_lines[j]:
                break

        raw_text = ''.join(content_lines[entry_start: entry_end + 1])

        # Build display label by extracting brace groups from the trigger + next lines
        label = _build_standard_label(
            content_lines, ti, trigger_re.search(content_lines[ti]).group()
        )
        item_id, version_id = _extract_markers(raw_text)
        candidates.append(
            _Candidate(
                display_label=label,
                raw_text=raw_text,
                item_id=item_id,
                version_id=version_id,
            )
        )

    # list_suffix: everything after last entry's \\resumeItemListEnd
    last_entry_end_in_content = _find_last_resumeitemlistend(content_lines, trigger_indices[-1])
    list_suffix = ''.join(content_lines[last_entry_end_in_content + 1:])

    return list_prefix, candidates, list_suffix


def _find_last_resumeitemlistend(lines: list[str], from_idx: int) -> int:
    """Return index of \\resumeItemListEnd scanning forward from from_idx."""
    for j in range(from_idx, len(lines)):
        if r'\resumeItemListEnd' in lines[j]:
            return j
    return len(lines) - 1

def _build_standard_label(lines: list[str], trigger_idx: int, trigger: str) -> str:
    """
    Extract display label for a standard entry.
    \\resumeSubheading → "{title} @ {org}"
    \\resumeProjectHeading → "{project}"
    """
    # Concatenate trigger line + next few lines to capture brace groups
    lookahead = ''.join(lines[trigger_idx: trigger_idx + 5])

    # Remove the trigger command itself, then extract groups from remaining text
    after_trigger = lookahead[lookahead.find(trigger) + len(trigger):]

    if r'\resumeSubheading' in trigger:
        groups = extract_brace_groups(after_trigger, 4)
        if len(groups) >= 3:
            title = strip_latex(groups[0])
            org = strip_latex(groups[2])
            return f"{title} @ {org}"
        elif groups:
            return strip_latex(groups[0])
    else:  
        groups = extract_brace_groups(after_trigger, 2)
        if groups:
            return strip_latex(groups[0])

    return "(unknown)"


def parse_skills_entries(
    content_lines: list[str],
) -> tuple[str, list[_Candidate], str]:
    """
    Parse entries from a skills section (\\resumeItem per entry).
    Returns (list_prefix, candidates, list_suffix).
    """
    item_re = re.compile(r'\\resumeItem\{')

    # Find first \\resumeItem line
    first_item = next((i for i, l in enumerate(content_lines) if item_re.search(l)), None)

    if first_item is None:
        return ''.join(content_lines), [], ''

    list_prefix = ''.join(content_lines[:first_item])

    candidates: list[_Candidate] = []
    i = first_item
    last_item_end = first_item

    while i < len(content_lines):
        line = content_lines[i]
        if not item_re.search(line):
            i += 1
            continue

        # Collect this resumeItem (handles multi-line via brace depth)
        pos = line.find(r'\resumeItem{')
        after_cmd = line[pos + len(r'\resumeItem'):]  # includes the opening {
        depth = 0
        raw_line_accum = [line]

        # Count depth in this first line
        for ch in after_cmd:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break

        # Accumulate subsequent lines if argument spans multiple lines
        entry_end = i
        if depth > 0:
            j = i + 1
            while depth > 0 and j < len(content_lines):
                next_line = content_lines[j]
                raw_line_accum.append(next_line)
                for ch in next_line:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            break
                entry_end = j
                j += 1

        raw_text = ''.join(raw_line_accum)

        # Build display label: text inside the braces
        label = _build_skills_label(raw_text)
        # Skills raw_text excludes preceding comments, so scan them for markers.
        preceding = _preceding_comment_block(content_lines, i)
        item_id, version_id = _extract_markers(preceding + raw_text)
        candidates.append(
            _Candidate(
                display_label=label,
                raw_text=raw_text,
                item_id=item_id,
                version_id=version_id,
            )
        )

        last_item_end = entry_end
        i = entry_end + 1  # advance past this entry

    list_suffix = ''.join(content_lines[last_item_end + 1:])
    return list_prefix, candidates, list_suffix


def _preceding_comment_block(lines: list[str], idx: int) -> str:
    """Return contiguous blank/comment lines immediately before idx."""
    j = idx - 1
    collected: list[str] = []
    while j >= 0 and _is_blank_or_comment(lines[j]):
        collected.append(lines[j])
        j -= 1
    collected.reverse()
    return ''.join(collected)


def _build_skills_label(raw_text: str) -> str:
    """Extract display label from a \\resumeItem{...} raw text."""
    pos = raw_text.find(r'\resumeItem{')
    if pos == -1:
        return raw_text.strip()[:60]
    after = raw_text[pos + len(r'\resumeItem'):]
    groups = extract_brace_groups(after, 1)
    if groups:
        label = strip_latex(groups[0])
        # Append some text after the closing brace (e.g., " C++, Python...")
        after_brace = after
        depth = 0
        end_idx = 0
        for ci, ch in enumerate(after_brace):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end_idx = ci + 1
                    break
        remainder = after_brace[end_idx:].strip().rstrip('\n')
        if remainder:
            label = label + ' ' + remainder if label else remainder
        return label[:80]
    return raw_text.strip()[:60]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_section_chunk(
    header_lines: list[str], content_lines: list[str]
) -> tuple[SourceSection, list[str]]:
    raw_header = ''.join(header_lines)

    # Extract section name
    for line in header_lines:
        m = re.search(r'\\section\*?\{([^}]+)\}', line)
        if m:
            name = m.group(1)
            break
    else:
        name = "Unknown"

    # Determine section type
    joined = ''.join(content_lines)
    if r'\resumeSubHeadingListStart' in joined:
        section_type = 'standard'
        list_prefix, candidates, list_suffix = parse_standard_entries(content_lines)
    else:
        section_type = 'skills'
        list_prefix, candidates, list_suffix = parse_skills_entries(content_lines)

    entries, warnings = _group_candidates(candidates)
    warnings = [f"[{name}] {message}" for message in warnings]

    section = SourceSection(
        name=name,
        section_type=section_type,
        raw_header=raw_header,
        list_prefix=list_prefix,
        list_suffix=list_suffix,
        entries=entries,
    )
    return section, warnings


def parse_file(path: str) -> SourceFile:
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()

    preamble, header, body_lines, trailing = zone_extract(lines)
    chunks = section_split(body_lines)

    sections: list[SourceSection] = []
    warnings: list[str] = []
    for header_lines, content_lines in chunks:
        section, section_warnings = parse_section_chunk(header_lines, content_lines)
        sections.append(section)
        warnings.extend(section_warnings)

    source = SourceFile(
        path=path,
        preamble=preamble,
        header=header,
        sections=sections,
        trailing=trailing,
    )
    # Non-persisted diagnostics for the GUI to surface.
    source.parse_warnings = warnings
    return source
