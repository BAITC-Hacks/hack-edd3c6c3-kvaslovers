import copy
import json
import unittest
from pathlib import Path

from ai.data import load_dataset
from ai.explain import with_llm_explanations
from ai.recommendation import recommend


def event(eid="design", sid="design", **changes):
    result = {"event_id": eid, "title": eid, "mandatory": False,
              "format": "online", "duration_hours": 4,
              "target_roles": ["Backend"], "target_grades": ["Middle"],
              "prerequisites": {}, "upcoming_sessions": ["2026-10-02"],
              "develops_skills": [{"skill_id": sid, "gain": 1, "max_level": 5}]}
    result.update(changes)
    return result


def participation(eid, status="completed", when="2026-08-01", employee_id="E", record_id="R1"):
    return {"record_id": record_id, "employee_id": employee_id,
            "event_id": eid, "status": status, "date": when}


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.employee = {"employee_id": "E", "role": "Backend", "grade": "Middle",
                         "skills": {"design": 2, "speaking": 1, "unrelated": 0},
                         "career_goal": None, "last_review_date": "2026-09-01"}
        self.skills = {"meta": {"as_of_date": "2026-10-01"},
                       "skills": [{"skill_id": x, "name": x} for x in ("design", "speaking", "unrelated")],
                       "role_profiles": [{"role": "Backend", "grade": "Senior",
                                          "required_skills": {"design": 4, "speaking": 3},
                                          "critical_skills": ["design"]}]}

    def call(self, events, history=(), **kwargs):
        return recommend(self.employee, events, self.skills, history, **kwargs)

    def test_critical_beats_weakest_and_unrelated_is_excluded(self):
        result = self.call([event("unrelated", "unrelated"), event("speaking", "speaking"), event()])
        self.assertEqual([r["event_id"] for r in result], ["design", "speaking"])
        self.assertGreater(result[0]["score"], result[1]["score"])

    def test_gain_cap_and_partial_closure(self):
        activity = event(develops_skills=[{"skill_id": "design", "gain": 3, "max_level": 3}])
        effect = self.call([activity])[0]["factors"]["skills"][0]
        self.assertEqual((effect["gain"], effect["effective_gain"], effect["gap_closed"], effect["projected_level"]), (3, 1, 1, 3))
        self.employee["skills"]["design"] = 3
        self.assertEqual(self.call([activity]), [])
        self.employee["skills"]["design"] = 4
        self.assertEqual(self.call([event()]), [])

    def test_real_gain_can_exceed_gap_but_coverage_cannot(self):
        self.employee["skills"]["design"] = 3
        activity = event(develops_skills=[{"skill_id": "design", "gain": 2, "max_level": 5}])
        effect = self.call([activity])[0]["factors"]["skills"][0]
        self.assertEqual((effect["effective_gain"], effect["gap_closed"], effect["projected_level"]), (2, 1, 5))

    def test_zero_gain_and_missing_skill(self):
        self.employee["skills"].pop("design")
        self.assertEqual(self.call([event()])[0]["factors"]["skills"][0]["current_level"], 0)
        self.assertEqual(self.call([event(develops_skills=[{"skill_id": "design", "gain": 0, "max_level": 5}])]), [])

    def test_multiple_skills_improve_impact(self):
        one = event("one")
        both = event("both", develops_skills=one["develops_skills"] + [{"skill_id": "speaking", "gain": 1, "max_level": 4}])
        result = self.call([one, both])
        self.assertEqual(result[0]["event_id"], "both")
        self.assertEqual(len(result[0]["factors"]["skills"]), 2)
        self.assertGreater(result[0]["factors"]["components"]["event_impact"], result[1]["factors"]["components"]["event_impact"])

    def test_eligibility_filters(self):
        variants = [dict(mandatory=True), dict(target_roles=["Sales"]),
                    dict(target_grades=["Lead"]), dict(prerequisites={"design": 3}),
                    dict(upcoming_sessions=[]), dict(upcoming_sessions=["2026-09-30"])]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertEqual(self.call([event(**changes)]), [])
        self.assertEqual(len(self.call([event(format="self_paced", upcoming_sessions=[])])), 1)
        result = self.call([event(upcoming_sessions=["2026-12-01", "2026-09-30", "2026-10-01"])])
        self.assertEqual(result[0]["next_session"], "2026-10-01")

    def test_completed_and_in_progress_excluded_club_repeatable(self):
        for status in ("completed", "in_progress"):
            self.assertEqual(self.call([event()], [participation("design", status)]), [])
        club = event("EV_036", "speaking")
        self.assertEqual(len(self.call([club], [participation("EV_036")])), 1)
        self.assertEqual(self.call([club], [participation("EV_036", "in_progress")]), [])

    def test_only_post_review_completions_adjust_skills_and_unlock_prerequisite(self):
        training = event("training")
        advanced = event("advanced", prerequisites={"design": 3})
        result = self.call([training, advanced], [participation("training", when="2026-09-02")])
        self.assertEqual([r["event_id"] for r in result], ["advanced"])
        effect = result[0]["factors"]["skills"][0]
        self.assertEqual((effect["reviewed_level"], effect["current_level"], effect["gap"]), (2, 3, 1))
        self.assertEqual(self.call([training, advanced], [participation("training", when="2026-09-01")]), [])
        result = self.call([training], [participation("training", when="2026-10-02")])
        self.assertEqual(result[0]["factors"]["skills"][0]["current_level"], 2)

    def test_chronological_replay_and_no_negative_gain(self):
        low = event("low", develops_skills=[{"skill_id": "design", "gain": 1, "max_level": 2}])
        high = event("high")
        history = [participation("low", when="2026-09-20", record_id="R2"),
                   participation("high", when="2026-09-10")]
        result = self.call([low, high, event()], history)
        self.assertEqual(result[0]["factors"]["skills"][0]["current_level"], 3)

    def test_negative_history_penalizes_without_banning(self):
        activities = [event(), event("past")]
        baseline = self.call(activities)[0]["score"]
        for status in ("no_show", "dropped", "declined"):
            history = [participation("past", status, record_id=f"R{i}") for i in range(3)]
            result = self.call(activities, history)
            self.assertEqual(len(result), 2)
            self.assertLess(result[0]["score"], baseline)
            self.assertEqual(result[0]["factors"]["history"]["counts"][status], 3)

    def test_positive_history_and_other_employees(self):
        activities = [event(), event("past")]
        baseline = self.call(activities)
        self.assertGreater(self.call(activities, [participation("past")])[0]["score"], baseline[0]["score"])
        self.assertEqual(self.call(activities, [participation("past", employee_id="OTHER")]), baseline)

    def test_annual_mandatory_history_does_not_affect_fit(self):
        activities = [event(), event("mandatory", mandatory=True)]
        history = [participation("mandatory", record_id="R1"), participation("mandatory", record_id="R2")]
        self.assertEqual(self.call(activities, history), self.call(activities))

    def test_goal_role_and_lead_policy(self):
        self.skills["role_profiles"].append({"role": "Sales", "grade": "Junior",
                                           "required_skills": {"speaking": 3}, "critical_skills": ["speaking"]})
        self.employee["career_goal"] = {"target_role": "Sales", "target_grade": "Junior"}
        result = self.call([event("sales", "speaking", target_roles=["Sales"])])
        self.assertEqual(result[0]["factors"]["target_role"], "Sales")
        self.assertEqual(result[0]["factors"]["target_grade"], "Junior")
        self.employee["career_goal"] = None
        self.employee["grade"] = "Lead"
        self.skills["role_profiles"][0]["grade"] = "Lead"
        result = self.call([event(target_grades=["Lead"])])
        self.assertEqual(result[0]["factors"]["target_source"], "current_grade")

    def test_empty_complete_and_limits(self):
        self.assertEqual(self.call([]), [])
        activities = [event(str(i)) for i in range(5)]
        self.assertEqual(len(self.call(activities)), 3)
        self.assertEqual(len(self.call(activities, limit=1)), 1)
        self.employee["skills"].update(design=5, speaking=5)
        self.assertEqual(self.call(activities), [])
        for limit in (0, 4, True):
            with self.assertRaises(ValueError):
                self.call(activities, limit=limit)

    def test_deterministic_no_mutation_and_score_accounting(self):
        activities = [event("z"), event("a")]
        inputs = copy.deepcopy((self.employee, activities, self.skills))
        first = self.call(activities)
        self.assertEqual(first, self.call(list(reversed(activities))))
        self.assertEqual(first[0]["event_id"], "a")
        self.assertEqual(inputs, (self.employee, activities, self.skills))
        for item in first:
            self.assertAlmostEqual(item["score"], sum(item["factors"]["contributions"].values()))
            self.assertTrue(all(0 <= x <= 1 for x in item["factors"]["components"].values()))

    def test_invalid_data_is_not_silently_scored(self):
        self.employee["skills"]["design"] = float("nan")
        with self.assertRaises(ValueError):
            self.call([event()])
        self.employee["skills"]["design"] = 2
        with self.assertRaises(ValueError):
            self.call([event(), event()])
        with self.assertRaises(ValueError):
            self.call([event()], [participation("design", status="skipped")])
        with self.assertRaises(ValueError):
            self.call([event()], [participation("design"), participation("design")])

    def test_llm_is_supplementary_compact_and_falls_back(self):
        result = self.call([event()])
        before = copy.deepcopy(result)
        prompts = []
        def generate(prompt):
            prompts.append(prompt)
            return "Критический навык приближается к целевому уровню."
        explained = with_llm_explanations(result, generate)
        self.assertEqual(result, before)
        self.assertEqual(explained[0]["reason"], result[0]["reason"])
        self.assertIn("llm_reason", explained[0])
        self.assertNotIn("full_name", prompts[0])
        self.assertNotIn("employee_id", prompts[0])
        def failing(prompt):
            raise TimeoutError("provider unavailable")
        for generator in (failing, lambda p: " ", lambda p: None, lambda p: "x" * 2001):
            self.assertEqual(with_llm_explanations(result, generator), result)


class DatasetIntegrationTests(unittest.TestCase):
    def test_all_200_profiles_and_independent_eligibility_checks(self):
        employees, events, skills, history = load_dataset(Path(__file__).with_name("data"))
        original = copy.deepcopy((employees, events, skills, history))
        self.assertEqual((len(employees["employees"]), len(events["events"]), len(skills["skills"]), len(history)), (200, 40, 60, 2743))
        by_event = {e["event_id"]: e for e in events["events"]}
        for employee in employees["employees"]:
            with self.subTest(employee=employee["employee_id"]):
                rows = [r for r in history if r["employee_id"] == employee["employee_id"]]
                levels = dict(employee["skills"])
                for row in sorted(rows, key=lambda r: (r["date"], r["record_id"])):
                    if row["status"] == "completed" and employee["last_review_date"] < row["date"] <= "2026-10-01":
                        for effect in by_event[row["event_id"]]["develops_skills"]:
                            sid = effect["skill_id"]
                            levels[sid] = max(levels.get(sid, 0), min(levels.get(sid, 0) + effect["gain"], effect["max_level"]))
                result = recommend(employee, events, skills, history)
                goal = employee["career_goal"]
                target_role = goal["target_role"] if goal else employee["role"]
                next_grade = {"Junior": "Middle", "Middle": "Senior", "Senior": "Lead", "Lead": "Lead"}
                target_grade = goal["target_grade"] if goal else next_grade[employee["grade"]]
                target = next(p for p in skills["role_profiles"] if (p["role"], p["grade"]) == (target_role, target_grade))
                # Independently enumerate eligible positive-impact activities to
                # verify empty/short outputs are not unexplained lost candidates.
                eligible = set()
                for activity in events["events"]:
                    if activity["mandatory"] or employee["grade"] not in activity["target_grades"]:
                        continue
                    if not {employee["role"], target_role}.intersection(activity["target_roles"]):
                        continue
                    activity_rows = sorted([r for r in rows if r["event_id"] == activity["event_id"]], key=lambda r: (r["date"], r["record_id"]))
                    if activity_rows and activity_rows[-1]["status"] == "in_progress":
                        continue
                    if activity["event_id"] != "EV_036" and any(r["status"] == "completed" for r in activity_rows):
                        continue
                    if any(levels.get(sid, 0) < need for sid, need in activity["prerequisites"].items()):
                        continue
                    if activity["format"] != "self_paced" and not any(d >= "2026-10-01" for d in activity["upcoming_sessions"]):
                        continue
                    if any(effect["gain"] > 0 and levels.get(effect["skill_id"], 0) < min(effect["max_level"], target["required_skills"].get(effect["skill_id"], 0)) for effect in activity["develops_skills"]):
                        eligible.add(activity["event_id"])
                self.assertEqual(len(result), min(3, len(eligible)))
                self.assertTrue({r["event_id"] for r in result}.issubset(eligible))
                self.assertLessEqual(len(result), 3)
                self.assertEqual(len({r["event_id"] for r in result}), len(result))
                self.assertEqual(result, recommend(employee, list(reversed(events["events"])), skills, list(reversed(history))))
                json.dumps(result, allow_nan=False)
                for item in result:
                    activity = by_event[item["event_id"]]
                    self.assertFalse(activity["mandatory"])
                    self.assertIn(employee["grade"], activity["target_grades"])
                    self.assertTrue({employee["role"], item["factors"]["target_role"]}.intersection(activity["target_roles"]))
                    self.assertTrue(all(levels.get(sid, 0) >= need for sid, need in activity["prerequisites"].items()))
                    if activity["format"] != "self_paced":
                        self.assertGreaterEqual(item["next_session"], "2026-10-01")
                        self.assertIn(item["next_session"], activity["upcoming_sessions"])
                    if activity["event_id"] != "EV_036":
                        self.assertFalse(any(r["event_id"] == activity["event_id"] and r["status"] == "completed" for r in rows))
                    profile = next(p for p in skills["role_profiles"] if (p["role"], p["grade"]) == (item["factors"]["target_role"], item["factors"]["target_grade"]))
                    self.assertTrue(0 <= item["score"] <= 1)
                    self.assertTrue(item["reason"])
                    self.assertAlmostEqual(item["score"], sum(item["factors"]["contributions"].values()))
                    for effect in item["factors"]["skills"]:
                        sid = effect["skill_id"]
                        self.assertEqual(effect["current_level"], levels.get(sid, 0))
                        self.assertEqual(effect["required_level"], profile["required_skills"][sid])
                        self.assertGreater(effect["gap_closed"], 0)
                        self.assertLessEqual(effect["gap_closed"], effect["gap"])
                        self.assertLessEqual(effect["projected_level"], effect["max_level"])
                        self.assertIn(f'{effect["current_level"]:g} → {effect["projected_level"]:g}', item["reason"])
        self.assertEqual(original, (employees, events, skills, history))


if __name__ == "__main__":
    unittest.main()
