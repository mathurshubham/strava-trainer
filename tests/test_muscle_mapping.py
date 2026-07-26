from app.metrics.muscle_mapping import muscle_groups_for, normalize_exercise_name


def test_normalize_strips_equipment_parens_and_snake_cases():
    assert normalize_exercise_name("Bench Press (Barbell)") == "bench_press"
    assert normalize_exercise_name("  Overhead Press (Dumbbell)  ") == "overhead_press"
    assert normalize_exercise_name("Lat Pulldown (Cable)") == "lat_pulldown"


def test_known_exercise_returns_weighted_groups():
    weights, found = muscle_groups_for("Bench Press (Barbell)")
    assert found is True
    assert weights["chest"] == 1.0
    assert weights["triceps"] == 0.5
    assert weights["shoulders"] == 0.5


def test_unknown_exercise_reports_not_found():
    weights, found = muscle_groups_for("Made Up Exercise Nobody Logs")
    assert found is False
    assert weights == {}
