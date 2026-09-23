"""Reproduce three factual demo cases: python3 -m ai.demo [--output ai/demo]."""

import argparse
import json
from pathlib import Path

from .data import load_dataset
from .recommendation import recommend

CASES = (
    ("promotion", "E0002", "Повышение: Backend Middle → Senior"),
    ("career_change", "E0004", "Смена профессии: Data Analyst → Product Manager"),
    ("repeated_no_show", "E0151", "Повторные пропуски: штраф без запрета"),
)


def build_demo():
    employees, events, skills, history = load_dataset(Path(__file__).with_name("data"))
    by_id = {e["employee_id"]: e for e in employees["employees"]}
    cases = []
    for key, employee_id, title in CASES:
        employee = by_id[employee_id]
        results = recommend(employee, events, skills, history)
        cases.append({"case": key, "title": title, "employee": employee,
                      "recommendations": results})
    # Controlled comparison: remove ONLY the two real no-show records from an
    # in-memory copy. Keep all completions and skill estimates unchanged.
    omitted = [r for r in history if r["employee_id"] == "E0151"
               and r["event_id"] == "EV_036" and r["status"] == "no_show"]
    omitted_ids = {r["record_id"] for r in omitted}
    comparison = recommend(by_id["E0151"], events, skills,
                           [r for r in history if r["record_id"] not in omitted_ids])
    cases[-1]["comparison"] = {
        "label": "Моделирование: только две неявки EV_036 исключены из расчёта",
        "removed_records": omitted, "recommendations": comparison,
    }
    # Check the actual demonstration claims; fail visibly if source data changes.
    assert [r["event_id"] for r in cases[0]["recommendations"]] == ["EV_005", "EV_011", "EV_036"]
    assert [r["event_id"] for r in cases[1]["recommendations"]] == ["EV_026", "EV_021", "EV_027"]
    assert len(omitted) == 2
    actual = cases[-1]["recommendations"]
    assert [r["event_id"] for r in actual] == ["EV_036"]
    assert [r["event_id"] for r in comparison] == ["EV_036"]
    assert actual[0]["factors"]["skills"] == comparison[0]["factors"]["skills"]
    assert actual[0]["factors"]["history"]["counts"]["no_show"] == 2
    assert abs(actual[0]["score"] - 0.3425) < 1e-12
    assert abs(comparison[0]["score"] - 0.38) < 1e-12
    return {"as_of_date": skills["meta"]["as_of_date"], "cases": cases}


def render_demo(bundle):
    lines = [
        "# Три сценария демо Career Quest",
        "",
        f'Дата расчёта: **{bundle["as_of_date"]}**. Все три профиля взяты из исходного синтетического kit.',
        "Оценки рассчитаны текущим движком; это не вероятность повышения. LLM не используется.",
        "",
        "## Как провести демо за 3 минуты",
        "",
        "Открой профиль по ID в приложении, если интеграция готова. Иначе покажи результат команды",
        "из корня репозитория. В каждом случае сначала назови цель, затем первую активность и причину выбора.",
        "Последний пример дополнительно покажи по таблице сравнения ниже.",
        "",
        "```sh",
        "python3 -m ai E0002",
        "python3 -m ai E0004",
        "python3 -m ai E0151",
        "```",
    ]
    speech = {
        "promotion": (
            "Сотрудник — Backend Engineer уровня Middle, его цель — Senior. "
            "System Design и API Design сейчас на уровне 1, а для Senior требуется 4. "
            "Оба навыка критические, поэтому первым идёт System Design Fundamentals. "
            "Он поднимает оба навыка до 2. Это конкретный шаг к цели, а не обещание немедленного повышения."
        ),
        "career_change": (
            "Никита работает аналитиком, но хочет стать Product Manager уровня Middle. "
            "Движок берёт требования целевой профессии. Product Discovery отсутствует в профиле "
            "и считается равным нулю; для цели нужен уровень 3, это критический навык. "
            "Product Discovery Lab развивает его и ещё два нужных навыка. "
            "Вторым идёт A/B Testing Workshop: полезен именно его вклад в Product Analytics с 2 до 3."
        ),
        "repeated_no_show": (
            "У сотрудника две неявки в Public Speaking Club. Навык выступлений всё ещё нужен: "
            "сейчас 0, для следующего грейда требуется 2. История уменьшает оценку клуба, "
            "но не запрещает его навсегда. После одной встречи прогноз — уровень 1. "
            "Подходящая активность здесь одна, поэтому система не заполняет список лишними курсами."
        ),
    }
    for number, case in enumerate(bundle["cases"], 1):
        employee = case["employee"]
        lines += ["", f'## {number}. {case["title"]}', "",
                  f'**{employee["employee_id"]} — {employee["full_name"]}**, {employee["role"]}, {employee["grade"]}.',
                  "", "| Место | Активность | Score | Следующая сессия |",
                  "| --- | --- | --- | --- |"]
        for rank, item in enumerate(case["recommendations"], 1):
            lines.append(f'| {rank} | {item["event_id"]} — {item["title"]} | {item["score"]:.4f} | {item["next_session"] or "В любое время"} |')
        first = case["recommendations"][0]
        lines += ["", "**Что раскрыть в первой карточке:**", "",
                  "| Навык | Сейчас → после активности | Требуется | Критический |",
                  "| --- | --- | --- | --- |"]
        for effect in first["factors"]["skills"]:
            lines.append(f'| {effect["name"]} | {effect["current_level"]:g} → {effect["projected_level"]:g} | {effect["required_level"]:g} | {"Да" if effect["critical"] else "Нет"} |')
        lines += ["", "**Что сказать жюри:**", "", f'> {speech[case["case"]]}', ""]
        if case["case"] == "promotion":
            lines += ["Для первой активности вклад факторов: `0.3500 + 0.1800 + 0.0320 + 0.0500 = 0.6120`.",
                      "В истории связанной активности есть одно прекращение (`dropped`), оно уже снижает history_fit до 1/3.",
                      "Secure Coding Workshop и Public Speaking Club имеют равные оценки; порядок определяет event_id."]
        elif case["case"] == "career_change":
            lines += ["Подчеркни: цель — **Product Manager / Middle**, а не следующий грейд Data Analyst.",
                      "Курсы целевой роли разрешены политикой движка; требования к текущему грейду и prerequisites продолжают проверяться."]
        else:
            comparison = case["comparison"]
            lines += ["**Доказательство влияния истории:**", "",
                      "| Условие | history_fit | Score клуба |",
                      "| --- | --- | --- |",
                      f'| Исходная история: две неявки | {first["factors"]["history"]["fit"]:.2f} | {first["score"]:.4f} |',
                      f'| Моделирование: исключены только две неявки | {comparison["recommendations"][0]["factors"]["history"]["fit"]:.2f} | {comparison["recommendations"][0]["score"]:.4f} |',
                      "", "Реальные записи истории:", ""]
            for row in comparison["removed_records"]:
                lines.append(f'- `{row["record_id"]}` — {row["date"]}, `{row["event_id"]}`, `{row["status"]}`.')
            lines += ["", "Сравнение — явно обозначенное моделирование, а не второй реальный профиль.",
                      "Скрипт убирает только эти строки из копии в памяти; файлы датасета не меняются.",
                      "Уровни, завершённые активности и остальные факторы остаются прежними.",
                      "Штраф: `0.15 × (0.50 − 0.25) = 0.0375`. Позиция не меняется: кандидат всего один."]
    lines += ["", "## Завершение демо", "",
              '> «Рекомендация учитывает цель, критичность навыков, реальный прирост и историю участия. '
              'Каждый вывод можно проверить по структурированным факторам; система работает без LLM».',
              "", "## Воспроизведение и файлы", "",
              "`python3 -m ai.demo` печатает этот сценарий и проверяет ключевые утверждения.",
              "`python3 -m ai.demo --output ai/demo` пересоздаёт README и `results.json` с полными результатами и исходными профилями.",
              "`python3 -m unittest ai.test_recommendation -v` запускает тесты движка.",
              "", "После изменения алгоритма пересоздай материалы: сохранённые оценки — снимок, а не источник расчёта."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bundle = build_demo()
    text = render_demo(bundle)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "README.md").write_text(text, encoding="utf-8")
        (args.output / "results.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Verified 3 demo cases; saved to {args.output}")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
