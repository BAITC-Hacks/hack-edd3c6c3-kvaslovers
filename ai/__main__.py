"""Example: python3 -m ai E0002 --data ai/data"""

import argparse
import json
from pathlib import Path

from .data import load_dataset
from .recommendation import recommend


def main():
    parser = argparse.ArgumentParser(description="Career Quest recommendations")
    parser.add_argument("employee_id")
    parser.add_argument("--data", type=Path, default=Path(__file__).with_name("data"))
    parser.add_argument("--as-of-date")
    args = parser.parse_args()
    employees, events, skills, history = load_dataset(args.data)
    matches = [x for x in employees["employees"] if x["employee_id"] == args.employee_id]
    if len(matches) != 1:
        parser.error(f"Expected one employee with ID {args.employee_id}, found {len(matches)}")
    result = recommend(matches[0], events, skills, history, as_of_date=args.as_of_date)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
