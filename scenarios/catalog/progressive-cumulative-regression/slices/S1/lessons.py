import progress

LESSONS = {"addition": ("What is 2 + 3?", "5"), "multiplication": ("What is 3 * 4?", "12")}


def open_lesson(lesson):
    return LESSONS[lesson][0]


def answer(path, lesson, response):
    if response != LESSONS[lesson][1]:
        return False
    progress.save(path, lesson)
    return True


def next_lesson(path):
    raise NotImplementedError("Recommendations remain declared future work")
