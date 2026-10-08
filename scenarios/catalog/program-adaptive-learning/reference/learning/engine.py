"""Differential scoring, independent of storage."""
def evaluate(lesson, answer, hinted):
    if str(answer).strip() != lesson["answer"]:
        return {"outcome": "wrong", "points": 0, "mastered": False}
    if hinted:
        return {"outcome": "hint-assisted", "points": 1, "mastered": False}
    return {"outcome": "independent", "points": 2, "mastered": True}
