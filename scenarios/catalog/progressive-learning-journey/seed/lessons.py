
LESSONS = {"addition": ("What is 2 + 3?", "5"), "multiplication": ("What is 3 * 4?", "12")}


def open_lesson(lesson):
    return LESSONS[lesson][0]


def answer(path, lesson, response):
    raise NotImplementedError("The lesson journey is not implemented")


def next_lesson(path):
    raise NotImplementedError("Recommendations are not implemented")
