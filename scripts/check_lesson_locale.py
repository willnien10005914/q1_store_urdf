#!/usr/bin/env python3
"""Verify English locale coverage for Q1 School lessons."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LESSONS_DIR = ROOT / "tutorials" / "lessons"
CATEGORIES_PATH = ROOT / "tutorials" / "categories.json"


def main() -> int:
    errors: list[str] = []
    ok_lessons = 0
    ok_quiz = 0
    ok_params = 0

    cat = json.loads(CATEGORIES_PATH.read_text(encoding="utf-8"))
    for c in cat.get("categories", []):
        if not c.get("description_en"):
            errors.append(f"category {c.get('id')}: missing description_en")

    lesson_dirs = sorted(p for p in LESSONS_DIR.iterdir() if p.is_dir())
    for d in lesson_dirs:
        en_md = d / "lesson.en.md"
        if not en_md.is_file():
            errors.append(f"{d.name}: missing lesson.en.md")
            continue
        if not en_md.read_text(encoding="utf-8").strip():
            errors.append(f"{d.name}: empty lesson.en.md")

        lj = d / "lesson.json"
        if not lj.is_file():
            errors.append(f"{d.name}: missing lesson.json")
            continue
        data = json.loads(lj.read_text(encoding="utf-8"))
        ok_lessons += 1

        for i, p in enumerate(data.get("params", [])):
            if not p.get("label_en"):
                errors.append(f"{d.name} param[{i}] key={p.get('key')}: missing label_en")
            else:
                ok_params += 1
            if "help_en" not in p:
                errors.append(f"{d.name} param[{i}] key={p.get('key')}: missing help_en")

        for q in data.get("quiz", []):
            qid = q.get("id", "?")
            for field in ("question_en", "choices_en", "explain_en"):
                if field not in q or (field != "choices_en" and not q.get(field) and field != "explain_en"):
                    if field == "explain_en" and not q.get(field):
                        errors.append(f"{d.name} quiz {qid}: missing explain_en")
                    elif field != "explain_en":
                        errors.append(f"{d.name} quiz {qid}: missing {field}")
            if "choices_en" in q:
                if len(q["choices_en"]) != len(q.get("choices", [])):
                    errors.append(
                        f"{d.name} quiz {qid}: choices_en len {len(q['choices_en'])} "
                        f"!= choices len {len(q.get('choices', []))}"
                    )
                else:
                    ok_quiz += 1
            if q.get("explain_en") is None:
                errors.append(f"{d.name} quiz {qid}: explain_en is null")

        run = data.get("run") or {}
        if run.get("note") and not run.get("note_en"):
            errors.append(f"{d.name}: run.note exists but note_en missing")

    print("=== Q1 School locale check (en) ===")
    print(f"Lessons checked: {ok_lessons}")
    print(f"Params with label_en: {ok_params}")
    print(f"Quiz items with matching choices_en: {ok_quiz}")
    print(f"Categories: {len(cat.get('categories', []))}")
    if errors:
        print(f"FAIL: {len(errors)} issue(s)")
        for e in errors[:50]:
            print(f"  - {e}")
        if len(errors) > 50:
            print(f"  ... and {len(errors) - 50} more")
        return 1
    print("PASS: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
