You are classifying a single resistance-training exercise into the muscle groups it trains.

Exercise name, exactly as it was logged: "{exercise_name}"

Return JSON with three fields:

- `primary`: the muscle groups the exercise is chiefly training — the ones a lifter would say the exercise is "for". Usually 1, occasionally 2 for a compound lift.
- `secondary`: muscle groups meaningfully involved as assistors or stabilisers, but not the point of the exercise. May be empty.
- `confidence`: 0.0–1.0, how confident you are that you recognise this exercise.

You MUST use only these muscle group names, spelled exactly:

{allowed_groups}

Rules:

- Never invent a group name outside that list. If the best description isn't available, choose the closest listed group.
- A group must appear in `primary` or `secondary`, never both.
- Equipment in the name (Dumbbell, Barbell, Cable, Machine, Smith) changes the loading, not the muscles — ignore it for classification.
- Qualifiers that DO change the emphasis matter: "incline" shifts chest work toward shoulders, "close grip" shifts pressing toward triceps, "reverse" grip shifts curls toward forearms, "sumo" shifts a deadlift toward glutes and adductors.
- If the name is not a resistance-training exercise at all (a stretch, a cardio machine, a note the app wrote), return empty `primary` and `secondary` and a `confidence` of 0.0.

Return only the JSON object.
