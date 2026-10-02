"""Compare Django fixture round-trips without printing employee information."""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def read_fixture(path):
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(record["model"], record["pk"]): record["fields"] for record in records}


def equivalent(field, source, destination):
    if source == destination:
        return True
    if field.endswith("_at") and isinstance(source, str) and isinstance(destination, str):
        try:
            return datetime.fromisoformat(source.replace("Z", "+00:00")) == datetime.fromisoformat(destination.replace("Z", "+00:00"))
        except ValueError:
            return False
    return False


def different_fields(source, destination):
    return [
        field for field in sorted(set(source) | set(destination))
        if not equivalent(field, source.get(field), destination.get(field))
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    source = read_fixture(args.source)
    destination = read_fixture(args.destination)
    missing = source.keys() - destination.keys()
    extra = destination.keys() - source.keys()
    changed = {
        key: different_fields(source[key], destination[key])
        for key in source.keys() & destination.keys()
        if different_fields(source[key], destination[key])
    }
    print("Source models:", dict(sorted(Counter(model for model, _ in source).items())))
    print("Destination models:", dict(sorted(Counter(model for model, _ in destination).items())))
    print("Missing records:", len(missing))
    print("Extra records:", len(extra))
    print("Changed records:", len(changed))
    for key in sorted(changed)[:10]:
        print("Changed model/PK/fields:", key[0], key[1], changed[key])
        for field in changed[key]:
            if field in {"assigned_at", "submitted_at", "completed_at", "created_at", "updated_at"}:
                print("  Datetime source/destination:", source[key].get(field), destination[key].get(field))
    raise SystemExit(bool(missing or extra or changed))


if __name__ == "__main__":
    main()
