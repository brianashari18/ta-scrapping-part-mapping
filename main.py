import csv
import ast
import glob
import os
import re

# ── constants ────────────────────────────────────────────────────────────────
INPUT_PATTERN = "*_scrapping_result.csv"
OUTPUT_FILE = "cleaned_chords.csv"
OUTPUT_COLS = [
    "title",
    "artist",
    "part_song",
    "chord_absolute",
    "roman_numeral",
    "start_time",
    "end_time",
]

# Valid part-song tags (case-insensitive)
VALID_PARTS = {"intro", "verse", "pre-chorus", "chorus", "interlude", "bridge", "outro"}

# Regex to detect a section tag: [Intro], [Verse], [Pre-Chorus], etc.
SECTION_RE = re.compile(r"^\[([^\]]+)\]$", re.IGNORECASE)

# Regex to detect chord tokens.
# Covers: C, Cm, C#, C#m, Cb, Cmaj7, Cm7, C7, Csus2, Csus4, Cadd9, C/E, etc.
CHORD_TOKEN_RE = re.compile(
    r"\b([A-G][#b]?(?:maj7|maj|min7|min|m7|m|7|sus2|sus4|add9|dim7|dim|aug|6|9)?(?:/[A-G][#b]?)?)\b"
)

# Tokens that look like chords but are actually lyrics/notation noise
NOISE_TOKENS = {
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
}  # single letters in lyrics — handled carefully below


# ── helpers ──────────────────────────────────────────────────────────────────


def parse_harmonic_map(raw: str) -> dict:
    """Parse the harmonic_map column (Python-dict-like string) into a real dict."""
    if not raw or raw.strip() == "":
        return {}
    try:
        # The stored string uses Python bool literals (True/False); ast.literal_eval handles that.
        return ast.literal_eval(raw)
    except Exception:
        return {}


def extract_chords_from_line(line: str) -> list[str]:
    """
    Extract chord tokens from a single line of chord_content.

    Strategy:
    - A chord line typically has chords separated by spaces, optionally with
      lyric text.  We detect it by checking whether ANY token matches the
      chord pattern.
    - Single bare letters (A-G) are only accepted as chords when the whole
      line consists *only* of chord tokens (no lowercase letters besides
      chord suffixes), preventing false positives from lyrics like "Dm G C".
    """
    tokens = CHORD_TOKEN_RE.findall(line)
    if not tokens:
        return []

    # Heuristic: if the line contains lowercase letters that are not part of
    # recognised chord suffixes, it's a lyric line → discard.
    # Remove all recognised chord tokens from the line, then check what's left.
    remainder = CHORD_TOKEN_RE.sub("", line).strip()
    # If the remainder has actual word characters (letters), it's lyrics.
    if re.search(r"[a-zA-Z]", remainder):
        return []

    return tokens


def parse_chord_content(chord_content: str) -> list[tuple[str, str]]:
    """
    Parse a chord_content string into a flat list of (part_song, chord_absolute) tuples.

    Rules:
    - Lines matching [SectionName] update the current part.
    - Other lines are scanned for chord tokens.
    - [2x], [3x] … repeat markers cause the preceding *section block* to be duplicated.
    """
    lines = chord_content.splitlines()
    current_part = "unknown"

    # We'll accumulate (part, chord) pairs; handle repeat markers inline.
    result: list[tuple[str, str]] = []

    # For repeat-marker support we track blocks per section occurrence.
    # Each element: {"part": str, "chords": [(part, chord), ...]}
    current_block: list[tuple[str, str]] = []
    block_part = "unknown"

    def flush_block():
        nonlocal current_block, block_part
        result.extend(current_block)
        current_block = []

    for raw_line in lines:
        line = raw_line.strip()

        # ── section tag ──────────────────────────────────────────────────
        section_match = SECTION_RE.match(line)
        if section_match:
            flush_block()
            tag = section_match.group(1).lower()
            current_part = tag if tag in VALID_PARTS else tag  # keep unknown tags too
            block_part = current_part
            continue

        # ── repeat marker at end of line, e.g. "F Em Am [2x]" ───────────
        repeat_match = re.search(r"\[(\d+)x\]", line, re.IGNORECASE)
        times = int(repeat_match.group(1)) if repeat_match else 1
        if repeat_match:
            line = line[: repeat_match.start()].strip()

        # ── extract chords ───────────────────────────────────────────────
        chords = extract_chords_from_line(line)
        for chord in chords:
            entry = (current_part, chord)
            current_block.append(entry)

        # Apply repeat (duplicate the chord entries just added for this line)
        if repeat_match and chords and times > 1:
            extra = [(current_part, c) for c in chords] * (times - 1)
            current_block.extend(extra)

    flush_block()
    return result


def dedupe_consecutive(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Remove consecutive duplicate chord entries — keep the LAST (next) one.

    e.g. [(chorus, C), (chorus, C), (chorus, G)]
      →  [(chorus, C), (chorus, G)]

    Only dedupes when both part_song AND chord_absolute are identical back-to-back.
    """
    if not pairs:
        return pairs
    result = []
    for i, current in enumerate(pairs):
        if i == 0:
            result.append(current)
            continue
        prev = result[-1]
        # If same chord as previous (regardless of part) → replace prev with current (keep next)
        if current[1] == prev[1]:
            result[-1] = current
        else:
            result.append(current)
    return result


def process_file(filepath: str) -> list[dict]:
    """Read one *_scrapping_result.csv and return cleaned rows."""
    rows = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for record in reader:
            title = record.get("title", "").strip()
            artist = record.get("artist", "").strip()
            chord_content = record.get("chord_content", "")
            harmonic_map = parse_harmonic_map(record.get("harmonic_map", ""))

            pairs = dedupe_consecutive(parse_chord_content(chord_content))

            for part, chord in pairs:
                roman = harmonic_map.get(chord, {}).get("roman", "")
                rows.append(
                    {
                        "title": title,
                        "artist": artist,
                        "part_song": part,
                        "chord_absolute": chord,
                        "roman_numeral": roman,
                        "start_time": "",
                        "end_time": "",
                    }
                )
    return rows


# ── main ─────────────────────────────────────────────────────────────────────


def main():
    workspace = os.path.dirname(os.path.abspath(__file__))
    input_files = glob.glob(os.path.join(workspace, INPUT_PATTERN))

    if not input_files:
        print(f"[!] No files matching '{INPUT_PATTERN}' found in {workspace}")
        return

    all_rows: list[dict] = []
    for fp in sorted(input_files):
        print(f"[+] Processing: {os.path.basename(fp)}")
        file_rows = process_file(fp)
        print(f"    → {len(file_rows)} chord entries extracted")
        all_rows.extend(file_rows)

    output_path = os.path.join(workspace, OUTPUT_FILE)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n[✓] Done. {len(all_rows)} total rows → {output_path}")


if __name__ == "__main__":
    main()
