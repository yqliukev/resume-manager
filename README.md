# Resume Generator

Resume Generator is a desktop GUI for turning a master LaTeX resume into tailored output files. It is designed for the common workflow of keeping one source resume and producing multiple generated variants by selecting different sections or entries.

The application focuses on two persistent artifacts:

- A source LaTeX resume file.
- A link library that stores the source snapshot and every generated file derived from it.

## What The App Does

The GUI lets you start a new link library from a source resume, or open an existing library, inspect the parsed sections and entries, choose what should stay in the output, and then write a new generated `.tex` file. Each generated file is recorded in the link library so the source and its outputs stay connected over time.

## Assumptions

This project assumes:

- The source file is a LaTeX resume that matches the structure in [this resume template](https://www.overleaf.com/latex/templates/cs-slash-it-slash-swe-resume-template/ncxgzcgknkmf)
- The resume contains recognizable section and entry commands that the parser can extract.
- The source file has all content, generated files are derived snapshots.

## How To Use The GUI

1. Launch the app.
2. To start a new library, click `Upload File`, choose `Source File`, and open a `.tex` resume. This drops any library already open in the app.
3. Review the parsed sections in the left panel.
4. Toggle section and entry checkboxes to choose what should appear in the output.
5. Click `Generate` to create a new generated `.tex` file, register it in a new link library, and save that library beside the source.
6. Click `Update Links` to refresh an existing library's source snapshot without creating a new output file.
7. To continue an existing library, click `Upload File`, choose `Link Library`, and open its `.resume-links.json`. This is the only way to restore generated files.
8. After loading a link library, the app restores the saved source snapshot and generated-file history. If those paths are missing, use `Remake Files` to write them somewhere on this machine.

### UI Behavior

- `Upload File` → `Source File` parses a source resume and starts a new library session. It does not open an existing library, including a sibling `.resume-links.json`.
- `Upload File` → `Link Library` loads a saved link library and reconnects it to its source file when that file exists.
- `Update Links` saves the current source snapshot into the open library without creating a new output file.
- `Generate` creates a new output file using the current selections and stores a matching generated-file entry in the library.
- Every new file starts with all sections and entries selected, using the first version of each item as written in the source. Selections are saved only on generated files; the source snapshot holds no selection state.
- The first Generate on a new session writes `{stem}.resume-links.json` next to the source. If that file already exists, the app asks before replacing it.
- `Remake Files` rewrites the open library's source and generated files to new locations and updates that same JSON file.
- The app shows the currently loaded source file and library file in the top bar.

### Where The File Is Stored

A new library is saved beside the source file. For a source file like `resume.tex`, that path is `resume.resume-links.json` in the same folder. Opening the source again does not load this file; open the JSON itself to continue the library.

#### resume-links.json

The JSON snapshot is the source of truth for the relationship between one master resume and many generated variants. It makes it possible to reopen the project later, see the previously generated outputs, and rebuild new variants from the same source structure.

### How Updates Work

- Loading a source file creates an in-memory `SourceFile` and clears any open library.
- Clicking `Update Links` writes the current source snapshot into the open link library JSON file.
- Clicking `Generate` creates a new `GeneratedFile`, writes it to disk as `.tex`, adds it to the link library, and saves the JSON file.
- Clicking `Rebuild` on the edit page stores the current (possibly refreshed) source snapshot in the library and rewrites only the file being edited.
- The first save of a new library uses the sibling `.resume-links.json` path. Replacing an existing file there requires confirmation, and that is when the previous library becomes obsolete.
